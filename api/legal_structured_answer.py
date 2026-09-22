"""Single-call structured legal answer contract and deterministic renderer."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import unicodedata
from copy import deepcopy
from typing import (
    Any,
    Awaitable,
    Callable,
    Hashable,
    Literal,
    Mapping,
    Sequence,
    TypeVar,
)

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.chat_behavior_policy import render_admin_style_addendum, render_behavior_policy
from api.legal_claim_validation import (
    contains_internal_citation_marker,
    strip_internal_citation_markers,
    validate_structured_claims,
)
from api.legal_determinism import semantic_claim_key
from api.legal_exact_retrieval import plan_exact_lookup
from api.legal_prompt_contract import build_shared_grounded_prompt, live_prompt_variant
from api.legal_section_grounding import (
    LegalIssue,
    aggregate_answer_sections,
    validate_answer_section,
)
from api.legal_text_cleaning import project_legal_evidence

_T = TypeVar("_T")


def project_backend_owned_answer_artifacts(
    *,
    citations: Sequence[Mapping[str, Any]] | None,
    recommended_forms: Sequence[Mapping[str, Any]] | None,
    provider_output: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return only server-validated citations and forms for public rendering.

    ``provider_output`` is accepted to make the trust boundary explicit, but
    its citation/form-like fields are intentionally ignored.  The provider is
    allowed to word validated claims; it cannot nominate identities or URLs.
    """

    del provider_output
    return {
        "citations": [
            deepcopy(dict(item)) for item in (citations or [])
            if isinstance(item, Mapping)
        ],
        "recommended_forms": [
            deepcopy(dict(item)) for item in (recommended_forms or [])
            if isinstance(item, Mapping)
        ],
    }


class AsyncModelCache:
    """Small process-local cache for expensive LangChain adapter creation."""

    def __init__(self) -> None:
        self._values: dict[Hashable, Any] = {}
        self._lock = asyncio.Lock()

    async def get(
        self, key: Hashable, factory: Callable[[], Awaitable[_T]]
    ) -> _T:
        cached = self._values.get(key)
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._values.get(key)
            if cached is not None:
                return cached
            value = await factory()
            self._values[key] = value
            return value

    def clear(self) -> None:
        self._values.clear()


async def await_with_hard_deadline(
    awaitable: Awaitable[_T], *, timeout: float
) -> _T:
    """Stop waiting at the deadline even when a provider cancels slowly."""
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if task in done:
        return task.result()

    task.cancel()

    def consume_background_result(completed: asyncio.Future[Any]) -> None:
        try:
            completed.exception()
        except BaseException:
            pass

    task.add_done_callback(consume_background_result)
    raise asyncio.TimeoutError


async def invoke_blocking_model_with_deadline(
    model: Any,
    prompt: str,
    *,
    timeout: float,
    structured_output: bool = True,
) -> Any:
    """Run adapters with blocking internals outside the API event loop."""
    invocation_options = structured_model_invocation_options(
        model,
        structured=structured_output,
    )
    from api.chat_execution import remaining_timeout
    # Native async transports cancel the HTTP request with the coroutine.
    if callable(getattr(model, "ainvoke", None)):
        async with asyncio.timeout(remaining_timeout(timeout)):
            return await model.ainvoke(prompt, **invocation_options)
    # Optional legacy/local adapters with no async protocol remain explicit.
    return await await_with_hard_deadline(
        asyncio.to_thread(model.invoke, prompt, **invocation_options),
        timeout=remaining_timeout(timeout),
    )


def remaining_generation_budget_seconds(
    started_at: float, total_seconds: float, *, now: float | None = None
) -> float:
    """Keep provisioning and invocation inside one generation deadline."""
    current = time.perf_counter() if now is None else now
    return max(0.0, total_seconds - (current - started_at))


def model_invocation_budget_seconds(total_seconds: float) -> float:
    """Leave a small wall-clock margin inside the selected normal/hard SLA."""
    if total_seconds <= 1.0:
        return max(0.0, total_seconds)
    return max(1.0, total_seconds - 0.25)


_OPTIMIZED_PROFILE_ROLES = {"citizen", "officer"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


def optimized_profile_enabled(
    role: str = "citizen", environ: Mapping[str, str] | None = None
) -> bool:
    values = os.environ if environ is None else environ
    enabled = str(values.get("LEGAL_ANSWER_OPTIMIZED_PROFILE_ENABLED", "false")).strip().casefold() in _TRUE_VALUES
    roles = {item.strip().casefold() for item in str(values.get("LEGAL_ANSWER_OPTIMIZED_PROFILE_ROLES", "citizen,officer")).split(",") if item.strip()}
    normalized_role = str(role or "citizen").strip().casefold()
    if not enabled or normalized_role not in roles & _OPTIMIZED_PROFILE_ROLES:
        return False
    if str(values.get("LEGAL_ANSWER_OPTIMIZED_PROFILE_ENFORCED", "false")).strip().casefold() in _TRUE_VALUES:
        # The persisted state is the canary authority once enforcement is on.
        # Import lazily to keep pure timeout helpers usable without runtime
        # state files in unit tests and offline tooling.
        from api.optimized_profile_rollout import enabled_roles

        return normalized_role in enabled_roles()
    return True


def structured_retrieval_timeout_seconds(
    tier: str,
    *,
    hard_question: bool = False,
    role: str = "citizen",
    environ: Mapping[str, str] | None = None,
) -> float:
    if optimized_profile_enabled(role, environ):
        # The canary profile follows the documented 4/6-second core and
        # 2/3-second expanded retrieval budgets (normal/hard).  Keeping the
        # tighter expanded budget is important because it is a bounded
        # recovery tier, not a second full-corpus attempt.
        return (3.0 if hard_question else 2.0) if tier == "expanded" else (6.0 if hard_question else 4.0)
    return (20.0 if hard_question else 15.0) if tier == "expanded" else (45.0 if hard_question else 35.0)


def structured_generation_timeout_seconds(
    environ: Mapping[str, str] | None = None,
    *,
    hard_question: bool = False,
    role: str = "citizen",
) -> float:
    """Return a configurable model budget for a complete grounded answer.

    The previous 18/38-second limits regularly forced a valid request onto the
    terse extractive fallback before a local model finished.  Normal and hard
    questions now have separate defaults and bounded overrides.  The validated
    extractive answer is still prepared first, so a provider failure remains
    safe and deterministic.
    """
    values = os.environ if environ is None else environ
    optimized = optimized_profile_enabled(role, values)
    default = (14.0 if hard_question else 12.0) if optimized else (120.0 if hard_question else 60.0)
    cap = (14.0 if hard_question else 12.0) if optimized else (240.0 if hard_question else 120.0)
    key = (
        "LEGAL_STRUCTURED_HARD_GENERATION_TIMEOUT_SECONDS"
        if hard_question
        else "LEGAL_STRUCTURED_GENERATION_TIMEOUT_SECONDS"
    )
    configured_value = values.get(key)
    if configured_value is None and hard_question:
        configured_value = values.get("LEGAL_STRUCTURED_GENERATION_TIMEOUT_SECONDS")
    try:
        configured = float(default if configured_value is None else configured_value)
    except (TypeError, ValueError):
        return default
    return min(cap, max(1.0, configured))


def structured_total_timeout_seconds(
    environ: Mapping[str, str] | None = None,
    *,
    hard_question: bool = False,
    role: str = "citizen",
) -> float:
    """Return the retrieval-to-answer wall-clock budget.

    Separate hard-question configuration keeps ordinary requests bounded while
    allowing multi-facet legal questions enough time to finish coherently.
    """

    values = os.environ if environ is None else environ
    optimized = optimized_profile_enabled(role, values)
    default = (24.0 if hard_question else 20.0) if optimized else (180.0 if hard_question else 90.0)
    cap = (24.0 if hard_question else 20.0) if optimized else (300.0 if hard_question else 180.0)
    key = (
        "LEGAL_STRUCTURED_HARD_TOTAL_TIMEOUT_SECONDS"
        if hard_question
        else "LEGAL_STRUCTURED_TOTAL_TIMEOUT_SECONDS"
    )
    configured_value = values.get(key)
    if configured_value is None and hard_question:
        configured_value = values.get("LEGAL_STRUCTURED_TOTAL_TIMEOUT_SECONDS")
    try:
        configured = float(default if configured_value is None else configured_value)
    except (TypeError, ValueError):
        return default
    return min(cap, max(5.0, configured))


def is_hard_legal_request(
    *,
    issues: Sequence[LegalIssue],
    required_facets_by_issue: Mapping[str, Sequence[str]],
) -> bool:
    """Classify the documented hard-question SLA without model judgment."""

    domains = {
        str(issue.domain or "").strip().casefold()
        for issue in issues
        if str(issue.domain or "").strip()
        and str(issue.domain or "").strip().casefold() != "unknown"
    }
    facets = {
        str(facet or "").strip().casefold()
        for values in required_facets_by_issue.values()
        for facet in values
        if str(facet or "").strip()
    }
    return len(issues) >= 3 or len(domains) >= 2 or len(facets) >= 5


def structured_model_options(
    timeout: float,
    environ: Mapping[str, str] | None = None,
    *,
    role: str = "citizen",
    hard_question: bool = False,
) -> dict[str, Any]:
    """Return deterministic options supported by the DeepSeek adapter.

    The output remains bounded, but the ceiling must accommodate all six
    deterministic issues. A 256-token cap allowed valid JSON containing only
    the first facet, which made grounded document/deadline claims disappear.
    The model still receives a minimal schema and stops naturally; this value
    is only a fail-safe ceiling.
    """
    values = os.environ if environ is None else environ
    default_output_tokens = 2048
    if optimized_profile_enabled(role, values):
        default_output_tokens = 2048 if hard_question else 1536
    try:
        configured_tokens = int(
            values.get("LEGAL_STRUCTURED_MAX_OUTPUT_TOKENS", str(default_output_tokens))
        )
    except (TypeError, ValueError):
        configured_tokens = default_output_tokens
    max_tokens = min(4096, max(512, configured_tokens))
    options = {
        "max_tokens": max_tokens,
        "timeout": timeout,
        "temperature": 0.0,
        "streaming": False,
        "structured": {"type": "json_object"},
    }
    if str(values.get("LEGAL_STRUCTURED_PROVIDER_JSON_MODE", "true")).strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        # Some OpenAI-compatible gateways accept the JSON contract in the
        # prompt but stall when response_format=json_object is sent.  Parser
        # and validator remain fail-closed if this diagnostic compatibility
        # mode is used.
        options.pop("structured", None)
    return options


def structured_model_invocation_options(
    model: Any,
    *,
    structured: bool = True,
) -> dict[str, Any]:
    """Disable provider reasoning for deterministic extraction-only JSON.

    DeepSeek V4 defaults to thinking mode. That mode is useful for open-ended
    reasoning but caused an evidence-to-JSON transformation to spend the full
    hard-question SLA in hidden reasoning. The provider's official per-request
    switch keeps the configured model unchanged.
    """

    if not structured:
        return {}

    model_name = str(
        getattr(model, "model_name", None)
        or getattr(model, "model", None)
        or ""
    ).strip().casefold()
    if model_name.startswith("deepseek-v4-"):
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    model_type = type(model)
    if (
        model_type.__module__.startswith("langchain_ollama")
        and model_name.startswith(("qwen3", "qwen3.5"))
    ):
        # Qwen thinking is enabled by default in Ollama.  The legal answer
        # stage is a deterministic evidence-to-JSON transformation and is
        # already protected by claim/citation validation; hidden reasoning
        # consumed ~2,000 tokens even for a one-word local probe.
        return {"reasoning": False}
    return {}


def structured_context_max_chars(
    environ: Mapping[str, str] | None = None,
    *,
    hard_question: bool = False,
    role: str = "citizen",
) -> int:
    """Return the bounded evidence context sent to one generation call."""

    values = os.environ if environ is None else environ
    key = (
        "LEGAL_STRUCTURED_HARD_CONTEXT_MAX_CHARS"
        if hard_question
        else "LEGAL_STRUCTURED_CONTEXT_MAX_CHARS"
    )
    # The rollout profile keeps the full 6k/12k context by default. The
    # smaller 4.5k/9k context is a benchmark candidate and must be opted into
    # separately after grounding/coverage gates have passed.
    benchmark_context = str(
        values.get("LEGAL_ANSWER_OPTIMIZED_PROFILE_CONTEXT_BENCHMARK", "false")
    ).strip().casefold() in _TRUE_VALUES
    if optimized_profile_enabled(role, values) and benchmark_context:
        default = 9_000 if hard_question else 4_500
    else:
        default = 12_000 if hard_question else 6_000
    try:
        configured = int(values.get(key, str(default)))
    except (TypeError, ValueError):
        configured = default
    return min(default, max(1200, configured))


class StructuredClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_text: str = Field(min_length=1, max_length=1200)
    claim_type: Literal[
        "rule", "condition", "authority", "documents", "procedure",
        "next_action", "deadline", "fee", "form", "exception", "warning",
    ]
    evidence_id: str = Field(min_length=1, max_length=64)
    support_quote: str = Field(min_length=1, max_length=1200)
    facet: str | None = Field(default=None, max_length=64)
    actor: str | None = Field(default=None, max_length=300)
    quote_id: str | None = Field(default=None, max_length=96)


class StructuredIssueAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    issue_id: str = Field(min_length=1, max_length=96)
    claims: list[StructuredClaim] = Field(default_factory=list, max_length=20)
    answer_markdown: str | None = Field(default=None, max_length=5000)
    guidance: str | None = Field(default=None, max_length=2000)
    clarifying_question: str | None = Field(default=None, max_length=500)


class StructuredLegalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[StructuredIssueAnswer] = Field(min_length=1, max_length=8)


def _evidence_text_for_quote(evidence: Mapping[str, Any] | None) -> str:
    """Return the canonical source text available to quote recovery.

    The provider may echo a whole evidence unit into ``support_quote``.  Quote
    recovery must search only text that crossed the provider boundary; it must
    never use model-generated text as its own evidence.
    """

    if not evidence:
        return ""
    for key in (
        "content",
        "evidence_capsule",
        "clean_content",
        "exact_article_assembled_content",
        "parent_context",
    ):
        value = evidence.get(key)
        if isinstance(value, str) and value.strip():
            return unicodedata.normalize("NFC", value)
    return ""


def _quote_occurs_in_source(quote: str, source: str) -> bool:
    """Match a quote using the same conservative normalization as verification."""

    if not quote or not source:
        return False
    quote_nfc = unicodedata.normalize("NFC", quote).strip()
    source_nfc = unicodedata.normalize("NFC", source)
    return quote_nfc in source_nfc or _fold(quote_nfc) in _fold(source_nfc)


def _recover_bounded_support_quote(
    quote: Any,
    evidence: Mapping[str, Any] | None,
    *,
    max_chars: int = 1200,
) -> str | None:
    """Recover a short, contiguous source excerpt from an overlong quote.

    Recovery is deliberately claim-local.  We prefer one or two sentence
    boundaries and return ``None`` when no bounded substring can be proven to
    occur in the evidence.  The caller then rejects only that claim.
    """

    raw_quote = unicodedata.normalize("NFC", str(quote or "")).strip()
    source = _evidence_text_for_quote(evidence)
    if not raw_quote or not source:
        return None
    if len(raw_quote) <= max_chars:
        return raw_quote

    # A model that copied the whole unit normally preserved sentence/newline
    # boundaries.  Prefer complete sentences, then bounded combinations of
    # adjacent sentences, and never fabricate a substring.
    parts = [
        part.strip()
        for part in re.split(r"(?<=[.!?;])\s+|\n+", raw_quote)
        if part.strip()
    ]
    candidates: list[str] = []
    for start in range(len(parts)):
        current = ""
        for end in range(start, len(parts)):
            candidate = (current + " " + parts[end]).strip()
            if len(candidate) > max_chars:
                break
            current = candidate
            candidates.append(candidate)
    # Longest bounded candidate gives the verifier useful context while still
    # respecting the field limit.
    for candidate in sorted(candidates, key=len, reverse=True):
        if _quote_occurs_in_source(candidate, source):
            return candidate

    # If punctuation was absent, use a bounded prefix only when it is itself a
    # contiguous source excerpt.  This prevents arbitrary truncation.
    prefix = raw_quote[:max_chars].rstrip()
    if _quote_occurs_in_source(prefix, source):
        return prefix
    return None


def recover_complete_issue_payload(raw: Any) -> dict[str, Any] | None:
    """Recover complete issue objects from a truncated provider JSON stream.

    Providers occasionally stop after finishing an issue but before closing the
    outer ``issues`` array/object.  We may retain only objects that the JSON
    decoder parsed completely; no braces, claims, or text are synthesized.
    Returning ``None`` keeps the fail-closed behavior when even one complete
    issue is unavailable.
    """

    text = re.sub(
        r"^```(?:json)?\s*|\s*```$",
        "",
        str(raw or "").strip(),
        flags=re.IGNORECASE,
    )
    marker = re.search(r"\"issues\"\s*:\s*\[", text)
    if marker is None:
        return None
    decoder = json.JSONDecoder()
    cursor = marker.end()
    recovered: list[Mapping[str, Any]] = []
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text) or text[cursor] == "]":
            break
        if text[cursor] == ",":
            cursor += 1
            continue
        try:
            value, cursor = decoder.raw_decode(text, cursor)
        except (json.JSONDecodeError, TypeError):
            break
        if isinstance(value, Mapping):
            recovered.append(value)
        else:
            break
    if not recovered:
        return None
    return {"issues": recovered}


def parse_structured_answer_resilient(
    raw: Any,
    *,
    evidence_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[StructuredLegalAnswer, list[dict[str, Any]]]:
    """Parse provider JSON without discarding valid sibling claims.

    Top-level JSON must still be valid.  Inside a valid object, each issue and
    claim is validated independently.  An overlong quote is recovered only
    from the matching evidence unit; otherwise that claim is reported in the
    returned rejection list and omitted from the public answer.  The normal
    strict parser remains unchanged for legacy callers.
    """

    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    top_level_recovered = False
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        # Providers sometimes wrap an otherwise valid JSON object in a short
        # preamble or trailing note despite ``json_object`` mode.  Recover
        # only a complete object with the standard decoder; never add missing
        # braces or invent fields for a truncated response.
        decoder = json.JSONDecoder()
        start = text.find("{")
        if start < 0:
            raise ValueError("model_output_is_not_valid_structured_json") from exc
        try:
            payload, _ = decoder.raw_decode(text[start:])
        except (json.JSONDecodeError, TypeError) as nested_exc:
            payload = recover_complete_issue_payload(text)
            if payload is None:
                raise ValueError("model_output_is_not_valid_structured_json") from nested_exc
            top_level_recovered = True
    if not isinstance(payload, Mapping):
        raise ValueError("model_output_is_not_valid_structured_json")

    rejected: list[dict[str, Any]] = []
    if top_level_recovered:
        rejected.append({"scope": "document", "reason": "top_level_json_recovered_complete_issues"})
    parsed_issues: list[StructuredIssueAnswer] = []
    raw_issues = payload.get("issues")
    if not isinstance(raw_issues, list):
        raise ValueError("model_output_is_not_valid_structured_json")

    allowed_claim_keys = {
        "claim_text",
        "claim_type",
        "evidence_id",
        "evidence_ids",
        "support_quote",
        "facet",
        "actor",
        "quote_id",
    }
    allowed_issue_keys = {
        "issue_id",
        "claims",
        "answer_markdown",
        "guidance",
        "clarifying_question",
    }
    for issue_index, raw_issue in enumerate(raw_issues[:8]):
        if not isinstance(raw_issue, Mapping):
            rejected.append({"scope": "issue", "index": issue_index, "reason": "issue_not_object"})
            continue
        issue_id = str(raw_issue.get("issue_id") or "").strip()
        if not issue_id or len(issue_id) > 96:
            rejected.append({"scope": "issue", "index": issue_index, "reason": "invalid_issue_id"})
            continue
        valid_claims: list[StructuredClaim] = []
        raw_claims = raw_issue.get("claims") or []
        if not isinstance(raw_claims, list):
            raw_claims = []
            rejected.append({"scope": "issue", "issue_id": issue_id, "reason": "claims_not_list"})
        for claim_index, raw_claim in enumerate(raw_claims[:20]):
            rejection_base = {
                "scope": "claim",
                "issue_id": issue_id,
                "index": claim_index,
            }
            if not isinstance(raw_claim, Mapping):
                rejected.append({**rejection_base, "reason": "claim_not_object"})
                continue
            candidate = {
                key: raw_claim.get(key)
                for key in allowed_claim_keys
                if key in raw_claim
            }
            # The public web contract uses singular ``evidence_id`` while
            # the standalone Phase-D contract historically used plural
            # ``evidence_ids``. Normalize both at this shared parser boundary
            # so web and benchmark callers exercise identical recovery and
            # claim-local rejection logic.
            if not str(candidate.get("evidence_id") or "").strip():
                raw_evidence_ids = raw_claim.get("evidence_ids")
                if isinstance(raw_evidence_ids, (list, tuple)):
                    candidate["evidence_id"] = next(
                        (
                            str(value).strip()
                            for value in raw_evidence_ids
                            if str(value).strip()
                        ),
                        "",
                    )
                elif isinstance(raw_evidence_ids, str):
                    candidate["evidence_id"] = raw_evidence_ids.strip()
            # It is an input alias only; the normalized web model remains
            # strict and must not receive the plural field as an extra key.
            candidate.pop("evidence_ids", None)
            candidate.setdefault("facet", None)
            candidate.setdefault("actor", None)
            candidate.setdefault("quote_id", None)
            evidence_id = str(candidate.get("evidence_id") or "").strip()
            quote = candidate.get("support_quote")
            evidence = (evidence_by_id or {}).get(evidence_id)
            if evidence is None:
                # Accept provider aliases for validation/recovery, while the
                # bridge still canonicalizes the ID before D verification.
                for context_id, candidate_evidence in (evidence_by_id or {}).items():
                    if any(
                        evidence_id == str(candidate_evidence.get(alias) or "").strip()
                        for alias in (
                            "evidence_id",
                            "source_id",
                            "chunk_id",
                            "canonical_chunk_id",
                        )
                    ):
                        evidence = candidate_evidence
                        break
            if isinstance(quote, str) and len(quote.strip()) > 1200:
                recovered = _recover_bounded_support_quote(quote, evidence)
                if recovered is None:
                    rejected.append({**rejection_base, "reason": "support_quote_too_long_no_exact_excerpt"})
                    continue
                candidate["support_quote"] = recovered
                rejected.append({
                    **rejection_base,
                    "reason": "support_quote_recovered",
                    "original_chars": len(quote),
                    "recovered_chars": len(recovered),
                })
            try:
                valid_claims.append(StructuredClaim.model_validate(candidate))
            except (ValidationError, TypeError, ValueError) as exc:
                rejected.append({
                    **rejection_base,
                    "reason": "claim_validation_failed",
                    "error_type": exc.__class__.__name__,
                })

        issue_candidate = {
            key: raw_issue.get(key)
            for key in allowed_issue_keys
            if key in raw_issue
        }
        issue_candidate["issue_id"] = issue_id
        issue_candidate["claims"] = valid_claims
        # Presentation-only fields are omitted when oversized; claims remain
        # independently verifiable and the renderer can build the final text.
        for field, limit in (
            ("answer_markdown", 5000),
            ("guidance", 2000),
            ("clarifying_question", 500),
        ):
            value = issue_candidate.get(field)
            if value is not None and len(str(value)) > limit:
                issue_candidate[field] = None
                rejected.append({
                    "scope": "issue",
                    "issue_id": issue_id,
                    "reason": f"{field}_omitted_too_long",
                })
        try:
            parsed_issues.append(StructuredIssueAnswer.model_validate(issue_candidate))
        except (ValidationError, TypeError, ValueError) as exc:
            rejected.append({
                "scope": "issue",
                "issue_id": issue_id,
                "reason": "issue_validation_failed",
                "error_type": exc.__class__.__name__,
            })

    if not parsed_issues:
        raise ValueError("model_output_has_no_valid_issues")
    return StructuredLegalAnswer(issues=parsed_issues), rejected


_ROLE_CLAIM_LABELS: dict[str, dict[str, str]] = {
    "citizen": {
        "rule": "Kết luận",
        "condition": "Điều kiện áp dụng",
        "authority": "Nơi nộp/cơ quan giải quyết",
        "documents": "Hồ sơ cần chuẩn bị",
        "procedure": "Các bước thực hiện",
        "next_action": "Việc cần làm tiếp theo",
        "deadline": "Mốc thời gian/thời hạn",
        "fee": "Lệ phí/chi phí",
        "form": "Biểu mẫu",
        "exception": "Trường hợp ngoại lệ",
        "warning": "Lưu ý",
    },
    "officer": {
        "rule": "Kết luận chuyên môn",
        "condition": "Điều kiện áp dụng",
        "authority": "Thẩm quyền",
        "documents": "Thành phần hồ sơ",
        "procedure": "Quy trình xử lý",
        "next_action": "Hành động nghiệp vụ tiếp theo",
        "deadline": "Thời hạn/mốc thời gian nghiệp vụ",
        "fee": "Lệ phí/mức thu",
        "form": "Hồ sơ/biểu mẫu",
        "exception": "Ngoại lệ cần kiểm tra",
        "warning": "Điểm cần xác minh",
    },
    "admin": {
        "rule": "Kết luận đã kiểm chứng",
        "condition": "Điều kiện áp dụng",
        "authority": "Thẩm quyền",
        "documents": "Hồ sơ",
        "procedure": "Quy trình",
        "next_action": "Hành động tiếp theo",
        "deadline": "Thời hạn",
        "fee": "Lệ phí",
        "form": "Biểu mẫu đã kiểm chứng",
        "exception": "Ngoại lệ",
        "warning": "Rủi ro/khoảng trống",
    },
}

_FACET_COMPATIBILITY: dict[str, set[str]] = {
    "rule": set(_ROLE_CLAIM_LABELS["citizen"]),
    "condition": {"condition", "exception", "rule"},
    "authority": {"authority", "rule", "procedure"},
    "documents": {"documents", "rule", "procedure", "form"},
    "procedure": {
        "procedure",
        "next_action",
        "documents",
        "authority",
        "fee",
        "form",
        "deadline",
        "rule",
        "condition",
        "exception",
        "warning",
    },
    "verification": {"procedure", "next_action", "warning", "rule"},
    "recording": {"rule", "procedure", "documents"},
    "deadline": {"deadline", "rule", "procedure"},
    # Fee and official-form requests share the sixth bounded planner slot when
    # all other administrative facets are also requested.
    "fee": {"fee", "form", "rule", "procedure"},
    "form": {"form", "documents", "procedure", "rule"},
    # Complaint/denunciation siblings are distinct retrieval issues but their
    # legal conclusion remains a grounded rule; authority and next-action
    # evidence stay eligible independently for each sibling.
    "dispute": {"rule", "procedure", "authority", "next_action"},
    "unknown": set(_ROLE_CLAIM_LABELS["citizen"]),
}

_FACET_CLAIM_TYPES: dict[str, set[str]] = {
    "rule": {"rule", "condition", "exception"},
    "condition": {"condition", "rule", "exception"},
    "authority": {"authority", "rule", "procedure"},
    "documents": {"documents", "procedure", "rule", "condition", "form"},
    "procedure": {"procedure", "next_action", "documents", "rule", "authority", "form"},
    "verification": {"procedure", "next_action", "warning", "rule"},
    "recording": {"rule", "procedure", "documents"},
    "next_action": {"next_action", "procedure", "rule"},
    "deadline": {"deadline", "procedure", "rule"},
    "fee": {"fee", "rule", "procedure", "form"},
    "form": {"form", "documents", "procedure", "rule"},
    "exception": {"exception", "condition", "rule"},
    "warning": {"warning", "rule", "procedure"},
}

_SECTION_TO_FACET = {
    "conclusion": "rule",
    "applicable_rule": "rule",
    "legal_basis_links": "rule",
    "citations": "rule",
    "conditions_or_rights": "condition",
    "condition": "condition",
    "exceptions": "exception",
    "exception": "exception",
    "submission_place": "authority",
    "authority": "authority",
    "documents": "documents",
    "steps": "procedure",
    "procedure": "procedure",
    "verification_duties": "verification",
    "verification": "verification",
    "recorded_content": "recording",
    "recording": "recording",
    "processing_time": "deadline",
    "deadline": "deadline",
    "fee": "fee",
    "official_forms": "form",
    "forms": "form",
    "form": "form",
    "rights_or_explanation": "rule",
    # The issue planner uses ``dispute`` for the complaint/denunciation
    # splitter. Normalize its coverage slot to a grounded legal rule rather
    # than inventing an unsupported claim type named "dispute".
    "dispute": "rule",
}

_FACET_EVIDENCE_MARKERS: dict[str, tuple[str, ...]] = {
    "authority": (
        "tham quyen", "uy ban", "ubnd", "co quan dang ky",
        "co quan co tham quyen", "cong an xa", "cong an phuong",
        "cong an cap xa", "chu tich uy ban", "chu tich ubnd",
        "noi nop", "nop tai", "nop ho so den", "gui ho so den",
        "bo phan mot cua",
    ),
    "documents": (
        "thanh phan ho so", "ho so gom", "ho so bao gom",
        "giay to ve", "giay to chung minh", "tai lieu chung minh",
        "to khai", "ban sao", "ban chinh", "don dang ky",
        "xuat trinh", "nop kem", "kem theo",
    ),
    "procedure": (
        "trinh tu", "thu tuc", "cac buoc", "thuc hien", "tiep nhan",
        "giai quyet",
    ),
    "next_action": (
        "thuc hien", "nop ho so", "tiep nhan", "giai quyet",
    ),
    "verification": (
        "xac minh", "kiem tra", "doi chieu", "tra cuu", "gui van ban",
        "phoi hop", "trach nhiem",
    ),
    "recording": (
        "noi dung", "ghi vao", "ghi nhan", "cap nhat", "thong tin",
        "so ho tich", "co so du lieu ho tich",
    ),
    "deadline": (
        "thoi han", "ngay lam viec", "trong ngay", "gio", "ke tu ngay",
    ),
    "fee": (
        "le phi", "muc thu", "mien phi", "khong thu",
    ),
    "form": (
        "bieu mau", "mau don", "to khai", "mau so",
    ),
    "condition": (
        "dieu kien", "truong hop", "phai", "duoc",
        "toi thieu", "khong vuot qua", "tro len",
    ),
    "exception": (
        "tru truong hop", "ngoai le", "khong ap dung",
    ),
}


def _clean_text(value: Any) -> str:
    # Normalize only the presentation copy.  Source payloads and their trace
    # remain untouched, while Vietnamese combining marks render consistently
    # in generated claims and citations.
    normalized = unicodedata.normalize("NFC", str(value or ""))
    return " ".join(normalized.split())


def _clean_extractive_text(value: Any) -> str:
    """Normalise a presentation copy while retaining legal list boundaries."""

    normalized = unicodedata.normalize("NFC", str(value or ""))
    return "\n".join(
        " ".join(line.split())
        for line in normalized.splitlines()
        if line.strip()
    )


def _split_structural_evidence_text(
    text: str,
    *,
    max_chars: int = 1100,
) -> list[str]:
    """Split long evidence at legal structure/sentence boundaries.

    The split is a prompt-packing boundary only.  The caller copies the
    document/article/clause metadata to every part, so a smaller provider
    unit never becomes a new legal source or loses its parent identity.
    """

    value = unicodedata.normalize("NFC", str(text or "")).strip()
    if not value or len(value) <= max_chars:
        return [value] if value else []

    structural_parts = [
        part.strip()
        for part in re.split(
            r"(?m)(?=^\s*(?:Điều\s+\d+|Khoản\s+\d+|Điểm\s+[a-zđ]\)|\d+\.|[a-zđ]\))\s*)",
            value,
            flags=re.IGNORECASE,
        )
        if part.strip()
    ]
    if len(structural_parts) <= 1:
        structural_parts = [value]

    parts: list[str] = []
    for structural in structural_parts:
        remaining = structural
        while len(remaining) > max_chars:
            boundary = max(
                remaining.rfind(". ", 0, max_chars + 1),
                remaining.rfind("; ", 0, max_chars + 1),
                remaining.rfind("\n", 0, max_chars + 1),
            )
            if boundary < max_chars // 2:
                boundary = remaining.rfind(" ", 0, max_chars + 1)
            if boundary < max_chars // 2:
                boundary = max_chars
            cut = boundary + (1 if remaining[boundary:boundary + 2] in {". ", "; "} else 0)
            piece = remaining[:cut].strip()
            if piece:
                parts.append(piece)
            remaining = remaining[cut:].lstrip()
        if remaining:
            parts.append(remaining)
    return [part for part in parts if part]


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", _clean_text(value).casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d")


_EXTRACTIVE_QUERY_STOPWORDS = {
    "bao", "biet", "can", "cho", "co", "cua", "gi", "hay", "hoac", "la",
    "lam", "nao", "nhung", "o", "sau", "tai", "the", "thi", "toi", "truoc",
    "va", "ve", "voi",
}


def _extractive_relevance_score(
    *,
    issue: LegalIssue,
    sentence: str,
    evidence: Mapping[str, Any],
    markers: Sequence[str],
) -> tuple[int, int, int]:
    """Prefer quotes matching the issue subject, not only generic facet words."""

    marker_tokens = {
        token
        for marker in markers
        for token in re.findall(r"[a-z0-9]+", _fold(marker))
    }
    query_tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", _fold(issue.query_text))
        if len(token) >= 3
        and token not in _EXTRACTIVE_QUERY_STOPWORDS
        and token not in marker_tokens
    ]
    subject_tokens = set(query_tokens)
    sentence_folded = _fold(sentence)
    sentence_tokens = set(re.findall(r"[a-z0-9]+", sentence_folded))
    sentence_overlap = len(subject_tokens & sentence_tokens)
    query_bigrams = {
        f"{left} {right}"
        for left, right in zip(query_tokens, query_tokens[1:])
        if left != right
    }
    bigram_overlap = sum(1 for phrase in query_bigrams if phrase in sentence_folded)
    metadata_folded = _fold(
        " ".join(
            str(evidence.get(field) or "")
            for field in (
                "article_title",
                "document_title",
                "procedure_id",
                "form_code",
            )
        )
    )
    metadata_tokens = set(re.findall(r"[a-z0-9]+", metadata_folded))
    metadata_overlap = len(subject_tokens & metadata_tokens)
    marker_hits = sum(1 for marker in markers if marker in sentence_folded)
    quantitative_intent_hits = 0
    if issue.intent == "condition":
        issue_folded = _fold(issue.query_text)
        quantitative_groups = (
            (
                ("dien tich toi thieu", "toi thieu"),
                ("toi thieu", "tu 1000", "tu 3000"),
            ),
            (
                ("ty le", "toi da"),
                ("ty le", "khong vuot qua", "phan tram", "%"),
            ),
        )
        quantitative_intent_hits = sum(
            1
            for query_markers, sentence_markers in quantitative_groups
            if any(marker in issue_folded for marker in query_markers)
            and any(marker in sentence_folded for marker in sentence_markers)
        )
    return (
        quantitative_intent_hits * 120
        + bigram_overlap * 25
        + sentence_overlap * 10
        + metadata_overlap * 3
        + marker_hits,
        sentence_overlap,
        metadata_overlap,
    )


def _is_extractive_heading(*, facet: str, sentence: str) -> bool:
    """Reject structural labels that do not themselves state a legal rule."""

    folded = _fold(sentence).strip(" .:;-")
    if not folded:
        return True
    if str(sentence).strip().endswith(":"):
        return True
    if re.fullmatch(r"(?:dieu|khoan|diem)\s+[a-z0-9.]+", folded):
        return True
    heading_prefixes = {
        "condition": ("dieu kien ",),
        "authority": ("tham quyen ",),
        "documents": ("ho so de nghi ", "thanh phan ho so"),
        "procedure": ("trinh tu ", "thu tuc "),
        "deadline": ("thoi han ",),
        "fee": ("le phi ", "muc thu "),
        "form": ("bieu mau ",),
    }
    starts_like_heading = any(
        folded.startswith(prefix) for prefix in heading_prefixes.get(facet, ())
    )
    normative_markers = (
        " phai ",
        " duoc ",
        " gom ",
        " bao gom ",
        " nop ",
        " thuc hien ",
        " trong thoi han ",
        " khong qua ",
        " ke tu ",
        " ngay lam viec ",
        " muc thu ",
    )
    padded = f" {folded} "
    return starts_like_heading and not any(
        marker in padded for marker in normative_markers
    )


def _is_incomplete_extractive_sentence(sentence: str) -> bool:
    """Reject obvious chunk-boundary/OCR fragments from public fallback text."""

    text = str(sentence or "").strip()
    if not text or len(text) > 900:
        return True
    first_alpha = next((char for char in text if char.isalpha()), "")
    list_item = bool(re.match(r"^(?:\d+[.)]|[a-zA-ZđĐ][.)])\s+", text))
    if first_alpha and first_alpha.islower() and not list_item:
        return True
    # A long legal row without a terminal marker is normally cut at a chunk
    # boundary. Failing closed prevents half a condition/consequence from
    # being displayed as a verified proposition.
    if len(text) >= 50 and not re.search(r"[.!?;:]$", text):
        return True
    return False


def _deterministic_clarifying_question(issue: LegalIssue) -> str | None:
    query = _fold(f"{issue.query_text} {issue.text}")
    if any(marker in query for marker in ("khong hop tac", "khong ky lai", "tu choi ky")) or (
        "nguoi thua ke" in query and "tranh chap" in query
    ):
        return (
            "Người thừa kế đã nộp đơn tranh chấp hoặc phản đối quyền sử dụng "
            "đất, hay mới chỉ từ chối ký lại giấy tờ?"
        )
    if "quy hoach" in query and "thu hoi" in query:
        return (
            "Thửa đất mới nằm trong quy hoạch hay đã có thông báo hoặc quyết "
            "định thu hồi đất?"
        )
    if "giay viet tay" in query and any(
        marker in query for marker in ("chu ky", "chuyen quyen", "mua dat")
    ):
        return "Giấy mua bán có chữ ký của cả bên bán và bên mua không?"
    if "nguoi co cong" in query and "tro cap" in query:
        benefit_subtypes = (
            "mai tang",
            "dieu duong",
            "giao duc",
            "thuong binh",
            "benh binh",
            "liet si",
            "ba me viet nam anh hung",
            "hoat dong khang chien",
            "di chuyen ho so",
            "tro cap mot lan",
            "tro cap hang thang",
        )
        if not any(marker in query for marker in benefit_subtypes):
            return (
                "Bạn đang đề nghị loại trợ cấp hoặc chế độ người có công nào "
                "(trợ cấp hằng tháng, một lần, mai táng, điều dưỡng hay hỗ trợ giáo dục)?"
            )
    return None


def _role_issue_title(issue: LegalIssue, role: str) -> str:
    if role != "officer":
        return issue.title
    prefix = {
        "rule": "Căn cứ và phân loại thủ tục",
        "condition": "Bảng kiểm điều kiện tiếp nhận",
        "authority": "Thẩm quyền và nơi tiếp nhận",
        "documents": "Hồ sơ và nội dung cần xác minh",
        "procedure": "Quy trình xử lý nghiệp vụ",
        "verification": "Trách nhiệm kiểm tra, xác minh",
        "recording": "Nội dung phải ghi nhận",
        "dispute": "Xử lý tranh chấp hoặc không hợp tác",
        "deadline": "Thời hạn nghiệp vụ",
        "fee": "Luân chuyển nghĩa vụ tài chính",
        "form": "Biểu mẫu nghiệp vụ",
    }.get(issue.intent, "Nội dung cần kiểm tra")
    if issue.intent in {"authority", "documents", "verification", "recording", "deadline", "fee", "form"}:
        return prefix
    normalized_title = _clean_text(issue.title).casefold()
    if normalized_title in {
        "kết luận và căn cứ",
        "nội dung cần xem xét",
        "nội dung cần kiểm tra",
    }:
        return prefix
    return f"{prefix}: {issue.title}"


def _normalized_facet(value: Any) -> str:
    facet = _SECTION_TO_FACET.get(str(value or ""), str(value or "unknown"))
    return facet if facet in _FACET_COMPATIBILITY else "rule"


_FACET_ISSUE_TITLES = {
    "rule": "Kết luận và căn cứ",
    "condition": "Điều kiện áp dụng",
    "authority": "Thẩm quyền giải quyết",
    "documents": "Hồ sơ, giấy tờ",
    "procedure": "Trình tự, thủ tục",
    "verification": "Trách nhiệm kiểm tra, xác minh",
    "recording": "Nội dung cần ghi nhận",
    "deadline": "Thời hạn",
    "fee": "Lệ phí, chi phí",
    "form": "Biểu mẫu",
    "exception": "Ngoại lệ",
}

_FACET_QUERY_CUES = {
    "rule": "quy định và căn cứ pháp lý",
    "condition": "điều kiện áp dụng",
    "authority": "thẩm quyền và nơi nộp",
    "documents": "hồ sơ giấy tờ",
    "procedure": "trình tự các bước thực hiện",
    "verification": "trách nhiệm kiểm tra xác minh đối chiếu thông tin",
    "recording": "nội dung thông tin phải ghi nhận",
    "deadline": "thời hạn giải quyết",
    "fee": "lệ phí chi phí mức thu",
    "form": "biểu mẫu tờ khai",
    "exception": "ngoại lệ trường hợp không áp dụng",
}


def _facet_retrieval_query(
    question: str,
    facet: str,
    *,
    subject: str = "",
    location: str = "",
    facts: Sequence[str] = (),
    applied_date: str | None = None,
) -> str:
    """Build a focused retrieval query without adding answer content."""

    cue = _FACET_QUERY_CUES.get(facet, facet)
    folded = _fold(question)
    anchor_parts: list[str] = []
    clean_subject = _clean_text(subject)
    if clean_subject:
        anchor_parts.append(clean_subject)
    if applied_date:
        anchor_parts.append(_clean_text(applied_date))
    for fact in facts[:3]:
        cleaned_fact = _clean_text(fact)
        if cleaned_fact and _fold(cleaned_fact) not in {
            _fold(item) for item in anchor_parts
        }:
            anchor_parts.append(cleaned_fact)
    # Location is useful for local authority/fee routing, but it needlessly
    # narrows central-law searches for conditions, dossiers and deadlines.
    if facet in {"authority", "fee"} and _clean_text(location):
        anchor_parts.append(_clean_text(location))
    query_anchor = "; ".join(anchor_parts) if anchor_parts else question
    # Official provisions usually lead with the legal facet. Put the
    # canonical procedure subject into that phrase so lexical retrieval can
    # find e.g. "Hồ sơ ... gồm" and "Thẩm quyền ..." without carrying every
    # unrelated request facet into the same query.
    if clean_subject:
        facet_phrases = {
            "rule": f"quy định chung về {clean_subject}",
            "condition": f"điều kiện {clean_subject}",
            "authority": (
                f"thẩm quyền {clean_subject}; cơ quan tiếp nhận; nơi nộp hồ sơ"
            ),
            "documents": (
                f"hồ sơ {clean_subject} gồm; hồ sơ giấy tờ người yêu cầu nộp"
            ),
            "procedure": f"trình tự thủ tục {clean_subject}",
            "verification": (
                f"trách nhiệm kiểm tra xác minh {clean_subject}; "
                "đối chiếu thông tin; gửi văn bản xác minh"
            ),
            "recording": (
                f"nội dung {clean_subject} phải ghi nhận; "
                "thông tin ghi vào sổ và cơ sở dữ liệu"
            ),
            "deadline": (
                f"thời hạn giải quyết {clean_subject}; "
                "kể từ ngày nhận đủ hồ sơ hợp lệ"
            ),
            "fee": f"lệ phí {clean_subject}; mức thu; miễn lệ phí",
            "form": f"biểu mẫu tờ khai {clean_subject}",
            "exception": f"ngoại lệ trường hợp không áp dụng {clean_subject}",
        }
        cue = facet_phrases.get(facet, cue)
        # The subject is already present in ``cue``. Retain only factual/date
        # and permitted location anchors to avoid overweighting duplicate
        # subject tokens.
        query_anchor = "; ".join(anchor_parts[1:])
    expansion = ""
    if (
        facet in {"rule", "authority"}
        and "khieu nai" in folded
        and "lan dau" in folded
        and "quyet dinh hanh chinh" in folded
    ):
        # This phrase is used only as a retrieval synonym. The answer still
        # has to quote and validate the returned current legal source.
        expansion = "; người đã ra quyết định hành chính"
    form_hint = ""
    if facet == "form":
        match = re.search(
            r"\bmẫu\s+(?:số\s+)?(?=[A-Za-zĐđ0-9/._-]*\d)"
            r"[A-Za-zĐđ0-9/._-]+",
            question,
            re.IGNORECASE,
        )
        if match:
            form_hint = f"; {match.group(0)}"
    # A content question often names one decisive field (for example
    # "quê quán") inside a broad procedure ("đăng ký khai sinh"). Do not
    # replace that field with the generic procedure subject, otherwise a
    # nearby step that merely says "ghi vào Sổ" can outrank the governing
    # content rule. This is retrieval context only; it never supplies answer
    # text or citation metadata.
    # A rule/legal-basis query must retain the complete citizen fact pattern;
    # otherwise a broad "quy định chung" rewrite can retrieve the right topic
    # but the wrong legal branch. Recording questions need the same treatment.
    content_focus = question if facet in {"recording", "rule"} else ""
    return _clean_text(
        "; ".join(
            item
            for item in (
                cue,
                query_anchor,
                content_focus,
                expansion.lstrip("; "),
                form_hint.lstrip("; "),
            )
            if item
        )
    )


def ensure_required_facet_issues(
    *,
    question: str,
    issues: Sequence[LegalIssue],
    required_sections: Sequence[str] | None,
    max_issues: int = 6,
) -> list[LegalIssue]:
    """Guarantee one deterministic retrieval unit for every requested facet.

    The question classifier defines the coverage contract. The free-text
    splitter may still miss comma-separated facets, so this function fills
    only those explicit contract gaps and never invents a new legal fact.
    """

    if max_issues < 1:
        raise ValueError("max_issues must be positive")
    planned = list(issues)
    required_facets = list(
        dict.fromkeys(
            _normalized_facet(section)
            for section in (required_sections or ())
        )
    )
    folded_question = _fold(question)
    explicit_rule_request = any(
        marker in _fold(question)
        for marker in ("can cu phap ly", "co so phap ly", "dieu luat nao")
    ) or (
        "van ban" in folded_question
        and "nao" in folded_question
    )
    # ``rule``/conclusion is an answer facet, not an independent retrieval
    # problem. When a concrete condition is requested, retrieve that first and
    # bind the short conclusion to the same issue. This also prevents a broad
    # rule query from consuming the one issue-bound copy of the dossier source
    # before the actual documents issue is evaluated.
    if (
        "rule" in required_facets
        and "condition" in required_facets
        and not explicit_rule_request
    ):
        required_facets = [
            "condition",
            *(
                facet
                for facet in required_facets
                if facet not in {"rule", "condition"}
            ),
        ]
    explicit_required_facets = set(required_facets)
    if not required_facets:
        required_facets = list(
            dict.fromkeys(_normalized_facet(issue.intent) for issue in planned)
        )

    # Keep the distinction between an explicit retrieval/coverage contract
    # named ``rule`` and the presentation aliases ``conclusion`` or
    # ``applicable_rule``. A multi-facet card should not gain an auxiliary
    # rule issue merely because it has a conclusion section, but an explicit
    # planner/evaluation contract for ``rule`` must still be assigned to an
    # existing substantive issue.
    explicit_rule_section = any(
        str(section or "").strip().casefold() == "rule"
        for section in (required_sections or ())
    )

    selected: list[LegalIssue] = []
    used_indexes: set[int] = set()
    base_domain = planned[0].domain if planned else "unknown"
    base_context = planned[0] if planned else None
    has_concrete_required_facet = any(
        facet != "rule" for facet in required_facets
    )
    for facet in required_facets[:max_issues]:
        source_index = next(
            (
                index
                for index, issue in enumerate(planned)
                if index not in used_indexes
                and _normalized_facet(issue.intent) == facet
            ),
            None,
        )
        # "Conclusion/legal basis" is an answer facet, not a second legal
        # issue. When the user asks a concrete administrative facet such as
        # documents, deadline or fee, bind the rule requirement to that issue
        # later in derive_required_facets_by_issue instead of creating a
        # duplicate synthetic retrieval unit.
        if (
            facet == "rule"
            and planned
            and has_concrete_required_facet
            and not explicit_rule_request
        ):
            if source_index is not None:
                used_indexes.add(source_index)
            continue
        if source_index is not None:
            used_indexes.add(source_index)
            source = planned[source_index]
            query_text = (
                _facet_retrieval_query(
                    question,
                    facet,
                    subject=source.subject,
                    location=source.location,
                    facts=source.facts,
                    applied_date=source.applied_date,
                )
                if facet == "rule" or str(source.subject or "").strip()
                else source.query_text
            )
            domain = source.domain
            confidence = source.split_confidence
        else:
            query_text = _facet_retrieval_query(
                question,
                facet,
                subject=base_context.subject if base_context else "",
                location=base_context.location if base_context else "",
                facts=base_context.facts if base_context else (),
                applied_date=base_context.applied_date if base_context else None,
            )
            domain = base_domain
            confidence = "high"
        selected.append(
            LegalIssue(
                issue_id=f"issue-{len(selected) + 1}",
                text=source.text if source_index is not None else question,
                title=_FACET_ISSUE_TITLES.get(facet, "Nội dung cần xem xét"),
                query_text=query_text,
                intent=facet if facet in _FACET_COMPATIBILITY else "rule",
                domain=domain,
                split_confidence=confidence,
                subject=(source.subject if source_index is not None else base_context.subject if base_context else ""),
                location=(source.location if source_index is not None else base_context.location if base_context else ""),
                facts=(source.facts if source_index is not None else base_context.facts if base_context else ()),
                applied_date=(source.applied_date if source_index is not None else base_context.applied_date if base_context else None),
                expected_sources=(
                    source.expected_sources
                    if source_index is not None
                    else base_context.expected_sources if base_context else ()
                ),
                expected_form=(
                    source.expected_form
                    if source_index is not None
                    else base_context.expected_form if base_context else None
                ),
                relevance_topics=(
                    source.relevance_topics
                    if source_index is not None
                    else base_context.relevance_topics if base_context else ()
                ),
                procedure_family=(
                    source.procedure_family
                    if source_index is not None
                    else base_context.procedure_family if base_context else None
                ),
                subject_anchor=(
                    source.retrieval_subject
                    if source_index is not None
                    else base_context.retrieval_subject if base_context else ""
                ),
                fact_anchors=(
                    source.retrieval_facts
                    if source_index is not None
                    else base_context.retrieval_facts if base_context else ()
                ),
            )
        )

    def issue_key(item: LegalIssue) -> tuple[str, tuple[str, ...]]:
        return (
            _normalized_facet(item.intent),
            tuple(sorted(item.relevance_topics)),
        )

    selected_issue_keys = {issue_key(issue) for issue in selected}
    for index, issue in enumerate(planned):
        if len(selected) >= max_issues:
            break
        key = issue_key(issue)
        if index in used_indexes or key in selected_issue_keys:
            continue
        if (
            required_sections
            and _normalized_facet(issue.intent) not in explicit_required_facets
        ):
            # The classifier is the answer-coverage contract. A splitter can
            # still mistake a stated place (for example "tại UBND phường")
            # for a separate authority request. Do not append that unrequested
            # facet after all explicit requirements have already been planned.
            # Complex deterministic problem maps add their substantive legal
            # branches later and are therefore unaffected by this facet guard.
            continue
        selected.append(
            LegalIssue(
                issue_id=f"issue-{len(selected) + 1}",
                text=issue.text,
                title=issue.title,
                query_text=issue.query_text,
                intent=issue.intent,
                domain=issue.domain,
                split_confidence=issue.split_confidence,
                subject=issue.subject,
                location=issue.location,
                facts=issue.facts,
                relevance_topics=issue.relevance_topics,
                applied_date=issue.applied_date,
                expected_sources=issue.expected_sources,
                expected_form=issue.expected_form,
                procedure_family=issue.procedure_family,
                subject_anchor=issue.retrieval_subject,
                fact_anchors=issue.retrieval_facts,
            )
        )
        selected_issue_keys.add(key)
    return selected


def derive_required_facets_by_issue(
    *,
    issues: Sequence[LegalIssue],
    required_sections: Sequence[str] | None,
) -> dict[str, list[str]]:
    """Assign every deterministic answer requirement to a concrete issue."""

    result: dict[str, list[str]] = {
        issue.issue_id: [_normalized_facet(issue.intent)] for issue in issues
    }
    explicit_rule_section = any(
        str(section or "").strip().casefold() == "rule"
        for section in (required_sections or ())
    )
    # Free-text facet inference is only a compatibility fallback for callers
    # that did not supply an explicit answer contract. Structured orchestration
    # deliberately carries the complete user question in every focused query;
    # scanning that repeated context would otherwise assign e.g. ``documents``
    # to the authority, deadline and condition issues as well.
    if not required_sections:
        for issue in issues:
            issue_text = _fold(f"{issue.title} {issue.query_text} {issue.text}")
            inferred_facets = []
            if any(
                marker in issue_text
                for marker in ("noi nop", "co quan tiep nhan", "tham quyen")
            ):
                inferred_facets.append("authority")
            if any(
                marker in issue_text
                for marker in ("ho so", "chung cu", "giay to")
            ):
                inferred_facets.append("documents")
            for facet in inferred_facets:
                if facet not in result[issue.issue_id]:
                    result[issue.issue_id].append(facet)
    for section in required_sections or ():
        facet = _normalized_facet(section)
        exact_matching = [
            issue
            for issue in issues
            if _normalized_facet(issue.intent) == facet
        ]
        if (
            facet == "rule"
            and not exact_matching
            and len(issues) > 1
            and not explicit_rule_section
        ):
            # Each explicit facet card already states its verified legal
            # proposition.  Attaching a generic "conclusion" rule to the
            # first condition/dossier issue creates a second retrieval target
            # and allowed unrelated nearby law to leak into that card.
            continue
        if facet == "rule" and not exact_matching:
            # Prefer the substantive eligibility issue for a short conclusion;
            # fall back to the first concrete issue for one-facet questions.
            preferred = [
                issue
                for preferred_intent in ("condition", "procedure", "dispute")
                for issue in issues
                if issue.intent == preferred_intent
            ]
            matching = preferred[:1]
        else:
            matching = exact_matching or [
                issue
                for issue in issues
                if facet in _FACET_COMPATIBILITY.get(issue.intent, {issue.intent})
            ]
        targets = matching or list(issues[:1])
        for issue in targets:
            if facet not in result[issue.issue_id]:
                result[issue.issue_id].append(facet)
    return result


def _facet_has_evidence(
    facet: str,
    rows: Sequence[tuple[str, Mapping[str, Any]]],
) -> tuple[bool, list[str]]:
    explicit_rows: list[tuple[str, Mapping[str, Any]]] = []
    legacy_rows: list[tuple[str, Mapping[str, Any]]] = []
    for evidence_id, row in rows:
        raw_facets = row.get("supported_facets")
        if isinstance(raw_facets, (list, tuple, set)) and raw_facets:
            explicit_rows.append((evidence_id, row))
        else:
            legacy_rows.append((evidence_id, row))

    explicit_matches = [
        evidence_id
        for evidence_id, row in explicit_rows
        if facet
        in {
            _normalized_facet(str(value))
            for value in row.get("supported_facets") or ()
        }
    ]
    if explicit_matches:
        # A reviewed, issue-bound adapter is direct evidence for this exact
        # facet.  Do not mix broad legacy keyword matches back into the same
        # packet; doing so allowed authority text to become a condition and a
        # second-level complaint rule to become a first-level deadline.
        return True, explicit_matches
    if facet in {"rule", "warning"}:
        # Legacy corpus rows predate facet metadata and retain the former broad
        # rule/warning compatibility.  New official-procedure rows must opt in
        # explicitly so a dossier excerpt cannot cover an authority/rule facet.
        ids = [evidence_id for evidence_id, _ in legacy_rows]
        return bool(ids), ids
    markers = _FACET_EVIDENCE_MARKERS.get(facet, ())
    matched = [
        evidence_id
        for evidence_id, row in legacy_rows
        if any(
            marker in _fold(
                " ".join(
                    str(row.get(field) or "")
                    for field in (
                        "evidence_capsule", "clean_content", "content",
                        "article_title", "document_title",
                        "procedure_id", "form_code",
                    )
                )
            )
            for marker in markers
        )
    ]
    return bool(matched), matched


def build_issue_coverage_matrix(
    *,
    issues: Sequence[LegalIssue],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    required_facets_by_issue: Mapping[str, Sequence[str]],
) -> dict[str, list[dict[str, Any]]]:
    """Describe source availability for every requested facet before generation."""

    matrix: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        issue_rows = [
            (evidence_id, row)
            for evidence_id, row in evidence_by_id.items()
            if str(row.get("request_id") or "") == str(issue.request_id or "")
            and str(row.get("issue_id") or "") == issue.issue_id
        ]
        facets = list(dict.fromkeys(
            _normalized_facet(item)
            for item in required_facets_by_issue.get(
                issue.issue_id, (issue.intent,)
            )
        ))
        matrix[issue.issue_id] = []
        for facet in facets:
            available, evidence_ids = _facet_has_evidence(facet, issue_rows)
            matrix[issue.issue_id].append(
                {
                    "facet": facet,
                    "required": True,
                    "evidence_available": available,
                    "evidence_ids": evidence_ids,
                    "status": "pending" if available else "not_available",
                    "coverage_status": (
                        "partially_covered" if available else "missing_evidence"
                    ),
                    "priority": issue.priority,
                }
            )
    return matrix


def render_coverage_matrix_for_prompt(
    coverage_matrix: Mapping[str, Sequence[Mapping[str, Any]]],
) -> str:
    rows = []
    for issue_id, facets in coverage_matrix.items():
        for facet in facets:
            rows.append(
                {
                    "issue_id": issue_id,
                    "facet": facet.get("facet"),
                    "evidence_available": bool(facet.get("evidence_available")),
                    "evidence_ids": list(facet.get("evidence_ids") or []),
                }
            )
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def build_fallback_quality_trace(
    *,
    issues: Sequence[LegalIssue],
    coverage_matrix: Mapping[str, Sequence[Mapping[str, Any]]],
    reason: str,
) -> dict[str, Any]:
    """Record fail-closed coverage without treating source excerpts as claims."""

    issue_trace: list[dict[str, Any]] = []
    available_count = 0
    required_count = 0
    for issue in issues:
        rows: list[dict[str, Any]] = []
        for raw in coverage_matrix.get(issue.issue_id, ()):
            required_count += 1
            available = bool(raw.get("evidence_available"))
            if available:
                available_count += 1
            rows.append(
                {
                    **dict(raw),
                    "facet": _normalized_facet(raw.get("facet")),
                    "status": "missing" if available else "not_available",
                    "coverage_status": "missing_evidence",
                }
            )
        issue_trace.append(
            {
                "issue_id": issue.issue_id,
                "accepted_claim_count": 0,
                "rejected_claim_count": 0,
                "rejection_reasons": [reason],
                "status": "source_excerpt_only",
                "requested_facets": [item["facet"] for item in rows],
                "covered_facets": [],
                "missing_facets": [
                    item["facet"] for item in rows if item["status"] == "missing"
                ],
                "unavailable_facets": [
                    item["facet"]
                    for item in rows
                    if item["status"] == "not_available"
                ],
                "coverage_matrix": rows,
            }
        )
    trace = {
        "accepted_claim_count": 0,
        "rejected_claim_count": 0,
        "coverage_ratio": 0.0 if required_count else 1.0,
        "required_facet_count": required_count,
        "available_required_facet_count": available_count,
        "covered_required_facet_count": 0,
        "displayed_legal_claim_count": 0,
        "displayed_claims_with_valid_evidence": 0,
        "claim_grounding_ratio": 1.0,
        "fallback_reason": reason,
        "issues": issue_trace,
    }
    trace["quality_gate"] = evaluate_structured_quality_gate(trace)
    return trace


def evaluate_structured_quality_gate(
    trace: Mapping[str, Any],
    *,
    minimum_coverage: float = 0.9,
) -> dict[str, Any]:
    """Apply the claim gate and report coverage as an advisory signal.

    Coverage describes whether every requested facet was answered.  It must not
    erase separately grounded claims when another facet is unavailable.  A
    response still fails closed when it contains no displayable legal claim or
    any displayed claim lacks valid evidence.
    """

    displayed = int(trace.get("displayed_legal_claim_count") or 0)
    grounded = int(trace.get("displayed_claims_with_valid_evidence") or 0)
    grounding_ratio = 1.0 if displayed == 0 else grounded / displayed
    coverage_ratio = float(trace.get("coverage_ratio") or 0.0)
    has_displayable_claim = displayed > 0
    claims_pass = has_displayable_claim and grounding_ratio == 1.0
    coverage_pass = coverage_ratio >= minimum_coverage
    return {
        "pass": claims_pass,
        "claims_with_valid_evidence": claims_pass,
        "has_displayable_claim": has_displayable_claim,
        "coverage_at_least_90_percent": coverage_pass,
        "coverage_warning": not coverage_pass,
        "claim_grounding_ratio": round(grounding_ratio, 4),
        "coverage_ratio": round(coverage_ratio, 4),
        "minimum_coverage": minimum_coverage,
    }


def build_compact_evidence_context(
    *,
    issues: Sequence[LegalIssue],
    evidence_rows: Sequence[Mapping[str, Any]],
    max_chars: int = 9000,
    preserve_structural_units: bool = False,
    max_evidence_units: int | None = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Create a bounded, deduplicated context and opaque evidence lookup.

    ``max_evidence_units`` is an optional provider-boundary cap.  It is kept
    separate from the retrieval packet so deterministic fallback and audit can
    retain the full packet while a remote model receives only a small,
    round-robin, issue-balanced evidence set.
    """

    issue_ids = {issue.issue_id for issue in issues}
    request_ids = {issue.request_id for issue in issues if issue.request_id}
    seen: set[tuple[str, str, str, str]] = set()
    seen_sources: set[tuple[str, str]] = set()
    seen_exact_article_packets: set[tuple[str, str]] = set()
    evidence: dict[str, dict[str, Any]] = {}
    blocks: list[str] = []
    issue_plan = "\n".join(
        f"- {issue.issue_id}: {issue.title} ({issue.intent})"
        for issue in issues
    )
    prefix = f"ISSUES\n{issue_plan}\n\nEVIDENCE\n"
    consumed = len(prefix)
    # Reserve room for two distinct provisions per issue when the budget
    # permits. A single long first excerpt repeated across several facets can
    # otherwise crowd out an explicitly requested comparison provision.
    target_sources_per_issue = 2
    first_source_excerpt_cap = max(
        220,
        min(
            700,
            (
                max_chars
                - len(prefix)
                - max(1, len(issues)) * 160
            )
            // max(1, len(issues) * target_sources_per_issue),
        ),
    )
    grouped_rows: dict[str, list[Mapping[str, Any]]] = {
        issue.issue_id: [] for issue in issues
    }
    for row in evidence_rows:
        issue_id = str(row.get("issue_id") or "")
        if issue_id in grouped_rows:
            grouped_rows[issue_id].append(row)
    ordered_rows: list[Mapping[str, Any]] = []
    max_rows = max((len(rows) for rows in grouped_rows.values()), default=0)
    for rank in range(max_rows):
        for issue in issues:
            rows = grouped_rows[issue.issue_id]
            if rank < len(rows):
                ordered_rows.append(rows[rank])

    per_issue_limit = None
    if max_evidence_units is not None:
        configured_limit = max(1, int(max_evidence_units))
        per_issue_limit = max(1, configured_limit // max(1, len(issues)))
    units_by_issue: dict[str, int] = {}
    for row in ordered_rows:
        item = project_legal_evidence(row)
        item_issue_id = str(item.get("issue_id") or "")
        if item_issue_id not in issue_ids:
            continue
        if max_evidence_units is not None and len(evidence) >= max(1, int(max_evidence_units)):
            break
        if per_issue_limit is not None and units_by_issue.get(item_issue_id, 0) >= per_issue_limit:
            continue
        if request_ids and str(item.get("request_id") or "") not in request_ids:
            continue
        exact_packet_ref = _clean_text(item.get("exact_article_packet_ref"))
        exact_packet_complete = (
            item.get("exact_article_packet_status") == "complete"
            and bool(exact_packet_ref)
        )
        exact_packet_key = (
            str(item.get("issue_id") or ""),
            exact_packet_ref,
        )
        if exact_packet_complete and exact_packet_key in seen_exact_article_packets:
            continue
        if exact_packet_complete:
            # A named document+Article request is one indivisible evidence
            # packet. Do not apply the normal 700-character per-hit excerpt
            # cap or repeat the same Article once per child chunk.
            content = _clean_extractive_text(
                item.get("exact_article_assembled_content")
                or item.get("parent_context")
            )
            if item.get("exact_article_serving_mode") == "bounded_long_article_window":
                # Keep the scope note as metadata, never as source content.
                # Otherwise a provider can quote this internal instruction and
                # accidentally attach an unrelated legal conclusion to it.
                item["evidence_scope_note"] = "bounded_long_article_window"
            item.update(
                {
                    "content": content,
                    "clean_content": content,
                    "clean_parent_context": content,
                    "evidence_capsule": content,
                    "exact_article_context_complete": False,
                    "exact_article_full_article_loaded": (
                        item.get("exact_article_serving_mode") != "bounded_long_article_window"
                    ),
                }
            )
            seen_exact_article_packets.add(exact_packet_key)
        else:
            raw_unit = (
                item.get("evidence_capsule")
                or item.get("clean_content")
                or item.get("content")
            )
            content = (
                _clean_extractive_text(raw_unit)
                if preserve_structural_units
                else _clean_text(raw_unit)
            )
        if not content:
            continue
        content_parts = _split_structural_evidence_text(content, max_chars=1100)
        for part_index, content_part in enumerate(content_parts, start=1):
            if max_evidence_units is not None and len(evidence) >= max(1, int(max_evidence_units)):
                break
            if per_issue_limit is not None and units_by_issue.get(item_issue_id, 0) >= per_issue_limit:
                break
            part_item = dict(item)
            part_item["content"] = content_part
            part_item["clean_content"] = content_part
            part_item["evidence_capsule"] = content_part
            if len(content_parts) > 1:
                part_item["exact_article_context_chunked"] = True
            if len(content_parts) > 1:
                part_item["evidence_parent_id"] = str(
                    item.get("source_id")
                    or item.get("chunk_id")
                    or item.get("document_id")
                    or ""
                )
                part_item["evidence_part_index"] = part_index
                part_item["evidence_part_count"] = len(content_parts)
            source_identity = str(
                item.get("chunk_id")
                or item.get("canonical_chunk_id")
                or item.get("source_id")
                or ""
            )
            source_key = (
                str(item.get("issue_id") or ""),
                f"{source_identity}#part-{part_index}" if source_identity else "",
            )
            if source_key[1] and source_key in seen_sources:
                continue
            if source_key[1]:
                seen_sources.add(source_key)
            dedupe_key = (
                str(item.get("issue_id") or ""),
                str(item.get("document_id") or item.get("law_number") or ""),
                str(item.get("article_id") or item.get("article_number") or ""),
                content_part.casefold(),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            evidence_id = f"evidence-{len(evidence) + 1}"
            title_parts = [
                part.strip()
                for part in re.split(r"[>|]", _clean_text(item.get("document_title")))
                if part.strip()
            ]
            compact_title = title_parts[-1] if title_parts else ""
            header = " | ".join(
                part
                for part in (
                    evidence_id,
                    f"issue={item.get('issue_id')}",
                    compact_title,
                    _clean_text(item.get("law_number")),
                    f"Điều {_clean_text(item.get('article_number'))}" if item.get("article_number") else "",
                    f"phần {part_index}/{len(content_parts)}" if len(content_parts) > 1 else "",
                )
                if part
            )
            remaining = max_chars - consumed - len(header) - 4
            if remaining < 120:
                break
            if exact_packet_complete:
                # A complete exact packet is still bound to the original
                # Article, but long Articles are exposed to the provider as
                # bounded structural parts so one quote cannot echo 10k chars.
                if len(content_part) > remaining:
                    if item.get("exact_article_serving_mode") != "bounded_long_article_window":
                        continue
                    excerpt = content_part[:remaining]
                    part_item["exact_article_context_truncated"] = True
                else:
                    excerpt = content_part
                part_item["exact_article_context_complete"] = True
            else:
                if preserve_structural_units:
                    # Simplified serving never crops a verified structural part
                    # in the middle. If it does not fit, omit that part.
                    if len(content_part) > remaining:
                        continue
                    excerpt = content_part
                else:
                    excerpt = content_part[: min(remaining, first_source_excerpt_cap)]
            part_item["quote_id"] = str(
                item.get("quote_id") or f"{evidence_id}-quote-1"
            )
            block = f"[{header}]\n{excerpt}"
            blocks.append(block)
            consumed += len(block) + 2
            evidence[evidence_id] = part_item
            units_by_issue[item_issue_id] = units_by_issue.get(item_issue_id, 0) + 1
    rendered_context = prefix + "\n\n".join(blocks)
    context = (
        rendered_context
        if preserve_structural_units
        else rendered_context[:max_chars]
    )
    return context, evidence


def build_structured_answer_prompt(
    *,
    question: str,
    role: str,
    context: str,
    coverage_matrix: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    system_prompt_addendum: str | None = None,
    prompt_variant: str | None = None,
    standalone_queries: Mapping[str, str] | None = None,
) -> str:
    """Return the shared evidence-only prompt used by live generation.

    The previous prompt contained a realistic-looking sample answer with
    invented forms, portals, deadlines and fees.  A model could copy those
    placeholders even when the packet did not support them.  The output
    contract below is intentionally schematic; deterministic backend code
    owns procedure/form metadata.
    """

    coverage_contract = render_coverage_matrix_for_prompt(coverage_matrix or {})
    output_contract = """{
  \"issues\": [
    {
      \"issue_id\": \"issue-id-from-packet\",
      \"answer_markdown\": null,
      \"claims\": [
        {
          \"claim_text\": \"Nguyên văn hoặc diễn đạt tối thiểu từ quote.\",
          \"claim_type\": \"rule|condition|authority|documents|procedure|next_action|deadline|fee|form|exception|warning\",
          \"facet\": \"facet đang được trả lời\",
          \"actor\": \"chủ thể/cơ quan xuất hiện trong cùng quote hoặc null\",
          \"evidence_id\": \"evidence-id\",
          \"quote_id\": \"quote-id của evidence\",
          \"support_quote\": \"một đoạn trích liên tục ngắn, tối đa 600 ký tự, từ cùng evidence\"
        }
      ],
      \"guidance\": null,
      \"clarifying_question\": null
    }
  ]
}
"""
    few_shot_contract = """
FEW-SHOT VỀ SUPPORT_QUOTE (dùng đúng quy tắc, không sao chép nội dung ví dụ):
Evidence: "Cơ quan tiếp nhận hồ sơ tại Bộ phận Một cửa của Ủy ban nhân dân cấp xã."
ĐÚNG: {"evidence_id":"evidence-1","support_quote":"Cơ quan tiếp nhận hồ sơ tại Bộ phận Một cửa của Ủy ban nhân dân cấp xã."}
SAI: {"evidence_id":"evidence-1","support_quote":"<chép toàn bộ khối evidence hoặc toàn bộ Điều luật>"}
Chỉ tạo tối đa 4 claims cho mỗi issue và chỉ tạo claim khi có evidence trực tiếp. Để answer_markdown, guidance và clarifying_question là null trừ khi thật sự cần hỏi lại.
support_quote phải là 1–2 câu liên tục, khoảng 100–200 ký tự khi có thể, và phải xuất hiện nguyên văn trong đúng evidence_id. Không đưa suy luận, markdown, hay toàn bộ văn bản vào trường này.
claim_text phải là câu/đoạn ngắn nguyên văn trong support_quote (hoặc chỉ bỏ phần dẫn nhập không mang nội dung pháp lý); không tự tóm tắt bằng cách ghép nhiều điều khoản. Nếu không thể giữ nguyên căn cứ trong quote, bỏ claim đó.
"""
    prompt = build_shared_grounded_prompt(
        question=_clean_text(question),
        role=str(role or "citizen"),
        evidence_text=context,
        coverage_text=coverage_contract,
        output_contract=output_contract + few_shot_contract,
        system_addendum=system_prompt_addendum or "",
        prompt_variant=prompt_variant or live_prompt_variant(),
        context_label="EVIDENCE PACKET V2",
    )
    standalone_block = ""
    if standalone_queries:
        standalone_block = "\n\nSTANDALONE ISSUES\n" + "\n".join(
            f"- {issue_id}: {_clean_text(query)}"
            for issue_id, query in standalone_queries.items()
            if _clean_text(issue_id) and _clean_text(query)
        )
    max_prompt_chars = 24_000 if standalone_queries else 16_000
    return (
        prompt
        + standalone_block
        + "\n\nCác claim được phép gồm: next_action (việc cần làm tiếp theo), "
        "exception (ngoại lệ) và warning (cảnh báo) khi evidence có căn cứ; "
        "nếu không có evidence thì không tạo claim. "
        "Mỗi support_quote phải là một đoạn liên tục ngắn (tối đa 600 ký tự); "
        "không chép nguyên điều luật hoặc toàn bộ evidence vào support_quote."
    )[:max_prompt_chars]


_DIRECT_PROMPT_FACET_LABELS: dict[str, str] = {
    "rule": "quy tắc hoặc kết luận pháp lý áp dụng",
    "condition": "điều kiện áp dụng",
    "eligibility": "điều kiện hoặc đối tượng được thực hiện",
    "documents": "hồ sơ và giấy tờ cần chuẩn bị",
    "authority": "cơ quan hoặc người có thẩm quyền",
    "procedure": "trình tự và cách thực hiện",
    "process": "trình tự và cách thực hiện",
    "deadline": "thời hạn thực hiện hoặc giải quyết",
    "processing_time": "thời hạn giải quyết",
    "fee": "phí và lệ phí",
    "form": "biểu mẫu cần sử dụng",
    "legal_basis": "căn cứ pháp lý",
    "validity": "hiệu lực của quy định",
    "next_action": "việc cần làm tiếp theo",
    "verification": "nội dung cán bộ cần kiểm tra",
    "exception": "ngoại lệ hoặc trường hợp đặc biệt",
    "warning": "lưu ý và rủi ro cần tránh",
}


def _direct_prompt_facet_label(value: object) -> str:
    normalized = _clean_text(value).casefold().replace("-", "_").replace(" ", "_")
    return _DIRECT_PROMPT_FACET_LABELS.get(
        normalized,
        normalized.replace("_", " ") if normalized else "nội dung được hỏi",
    )


_DETERMINISTIC_FACT_FACETS = {"authority", "deadline"}


def _normalized_search_text(value: object) -> str:
    """Return accent-insensitive text used only for duplicate detection."""

    text = unicodedata.normalize("NFD", _clean_text(value).casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _indexed_fact_parts(value: object) -> tuple[str, str] | None:
    raw = _clean_text(value)
    prefix, separator, fact = raw.partition(":")
    if not separator or not fact.strip():
        return None
    facet = _normalized_facet(prefix.strip())
    if facet not in _DETERMINISTIC_FACT_FACETS:
        return None
    return facet, fact.strip()


def _verified_fact_already_present(*, answer: str, facet: str, fact: str) -> bool:
    answer_key = _normalized_search_text(answer)
    fact_key = _normalized_search_text(fact)
    if fact_key and fact_key in answer_key:
        return True

    if facet == "deadline":
        # Treat 03 and 3 as the same value, but keep the original spelling in
        # the deterministic supplement shown to the user.
        deadline_match = re.search(
            r"\b0*(\d+)\s+(ngay|thang|nam|gio)(?:\s+lam\s+viec)?\b",
            fact_key,
        )
        if deadline_match:
            amount = str(int(deadline_match.group(1)))
            unit = deadline_match.group(2)
            answer_deadlines = re.findall(
                r"\b0*(\d+)\s+(ngay|thang|nam|gio)(?:\s+lam\s+viec)?\b",
                answer_key,
            )
            if any(str(int(number)) == amount and found_unit == unit for number, found_unit in answer_deadlines):
                return True

    if facet == "authority":
        # Official capsules commonly use ``Cơ quan thực hiện: ...``. Compare
        # the value after that label so a natural model paraphrase does not
        # create a duplicate supplement.
        _, separator, value = fact.partition(":")
        authority_key = _normalized_search_text(value if separator else fact)
        if authority_key and authority_key in answer_key:
            return True

    return False


def _format_verified_fact(facet: str, fact: str) -> str:
    label, separator, value = fact.partition(":")
    if separator and label.strip() and value.strip() and len(label.strip()) <= 80:
        return f"- **{label.strip()}:** {value.strip()}"
    fallback_label = "Thời hạn giải quyết" if facet == "deadline" else "Cơ quan có thẩm quyền"
    return f"- **{fallback_label}:** {fact.strip()}"


def append_missing_verified_facts(
    *,
    answer_markdown: str,
    required_facets_by_issue: Mapping[str, Sequence[str]] | None,
    available_facts_by_issue: Mapping[str, Sequence[str]] | None,
) -> tuple[str, list[dict[str, str]]]:
    """Append short verified facts that the model omitted.

    The model-authored Markdown remains byte-for-byte unchanged. Only facts
    copied from role/validity-eligible evidence are appended, and only when
    the corresponding facet was required for the issue. This is deliberately
    not a claim validator and never removes model prose.
    """

    answer = str(answer_markdown or "").strip()
    if not answer or not required_facets_by_issue or not available_facts_by_issue:
        return answer, []

    appended: list[dict[str, str]] = []
    rendered: list[str] = []
    seen: set[tuple[str, str]] = set()
    comparison_answer = answer
    for issue_id, raw_facts in available_facts_by_issue.items():
        required = {
            _normalized_facet(facet)
            for facet in required_facets_by_issue.get(issue_id, ())
        }
        for raw_fact in raw_facts:
            parsed = _indexed_fact_parts(raw_fact)
            if parsed is None:
                continue
            facet, fact = parsed
            fact_key = _normalized_search_text(fact)
            key = (facet, fact_key)
            if facet not in required or not fact_key or key in seen:
                continue
            seen.add(key)
            if _verified_fact_already_present(
                answer=comparison_answer,
                facet=facet,
                fact=fact,
            ):
                continue
            rendered.append(_format_verified_fact(facet, fact))
            appended.append(
                {
                    "issue_id": str(issue_id),
                    "facet": facet,
                    "fact": fact,
                }
            )
            comparison_answer += "\n" + fact

    if not rendered:
        return answer, []
    return (
        answer
        + "\n\n## Thông tin quan trọng đã xác minh\n\n"
        + "\n".join(rendered),
        appended,
    )


def _build_legacy_direct_markdown_answer_prompt(
    *,
    question: str,
    role: str,
    context: str,
    system_prompt_addendum: str | None = None,
    standalone_queries: Mapping[str, str] | None = None,
    required_facets_by_issue: Mapping[str, Sequence[str]] | None = None,
    available_facets_by_issue: Mapping[str, Sequence[str]] | None = None,
    available_facts_by_issue: Mapping[str, Sequence[str]] | None = None,
    communication_preferences: Mapping[str, str] | None = None,
    suggestion_envelope: bool = False,
    conversation_patch_envelope: bool = False,
    conversation_context: str | None = None,
    conversation_is_first_turn: bool = True,
    suggestion_limit: int = 4,
) -> str:
    """Build the raw retrieval-to-LLM prompt for simplified serving.

    This contract deliberately requests ordinary Markdown rather than claim,
    facet, evidence-ID or quote-ID JSON. The backend has already limited the
    context to role-eligible, effective sources before this prompt is built.
    """

    audience = "cán bộ" if str(role).casefold() == "officer" else "người dân"
    standalone_block = ""
    if standalone_queries:
        standalone_block = "\n\nCÁC VẤN ĐỀ ĐỘC LẬP\n" + "\n".join(
            f"- {issue_id}: {_clean_text(query)}"
            for issue_id, query in standalone_queries.items()
            if _clean_text(issue_id) and _clean_text(query)
        )
    required_block = ""
    if required_facets_by_issue:
        required_lines: list[str] = []
        for issue_id, facets in required_facets_by_issue.items():
            labels = list(
                dict.fromkeys(
                    _direct_prompt_facet_label(facet)
                    for facet in facets
                    if _clean_text(facet)
                )
            )
            if labels:
                required_lines.append(
                    f"- {_clean_text(issue_id)}: " + "; ".join(labels)
                )
        if required_lines:
            required_block = (
                "\n\nCÁC NỘI DUNG BẮT BUỘC PHẢI BAO PHỦ\n"
                + "\n".join(required_lines)
            )
    available_block = ""
    if available_facets_by_issue:
        available_lines: list[str] = []
        for issue_id, facets in available_facets_by_issue.items():
            labels = list(
                dict.fromkeys(
                    _direct_prompt_facet_label(facet)
                    for facet in facets
                    if _clean_text(facet)
                )
            )
            if labels:
                available_lines.append(
                    f"- {_clean_text(issue_id)}: " + "; ".join(labels)
                )
        if available_lines:
            available_block = (
                "\n\nCÁC NỘI DUNG NGUỒN ĐÃ CÓ\n"
                + "\n".join(available_lines)
                + "\nMỗi nội dung trong danh sách này phải được trả lời từ nguồn; "
                "không được kết luận là thiếu nguồn trước khi rà soát toàn bộ block nguồn; "
                "không bỏ sót ý có nguồn."
            )
    fact_index_block = ""
    if available_facts_by_issue:
        fact_lines: list[str] = []
        for issue_id, facts in available_facts_by_issue.items():
            for fact in facts:
                clean_fact = _clean_text(fact)
                if clean_fact:
                    fact_lines.append(f"- {_clean_text(issue_id)}: {clean_fact}")
        if fact_lines:
            fact_index_block = (
                "\n\nCHỈ MỤC TRÍCH NGUYÊN VĂN THEO NỘI DUNG\n"
                + "\n".join(fact_lines)
                + "\nĐây là các dòng ngắn lấy nguyên văn từ evidence bên dưới. "
                "Phải dùng chúng khi trả lời nội dung tương ứng. Với mọi dòng "
                "`deadline:`, phải chép đúng thời hạn vào câu trả lời; nói rằng "
                "nguồn không có thời hạn khi dòng này tồn tại là trái yêu cầu."
            )
    role_guidance = (
        "Với cán bộ, ưu tiên kết luận nghiệp vụ, nội dung cần kiểm tra, "
        "cách xử lý và ranh giới thẩm quyền."
        if audience == "cán bộ"
        else "Với người dân, ưu tiên kết luận dễ hiểu và các bước có thể thực hiện ngay."
    )
    preference_block = ""
    if communication_preferences:
        preference_lines: list[str] = []
        preferred_address = _clean_text(
            communication_preferences.get("preferred_address") or ""
        )[:120]
        response_style = _clean_text(
            communication_preferences.get("response_style") or ""
        )[:120]
        if preferred_address:
            preference_lines.append(
                f"- Cách xưng hô khi cần đại từ: {preferred_address}"
            )
        if response_style:
            preference_lines.append(f"- Phong cách trình bày: {response_style}")
        if preference_lines:
            preference_block = (
                "\n\nSỞ THÍCH GIAO TIẾP ĐÃ ĐƯỢC NGƯỜI DÙNG CHO PHÉP\n"
                + "\n".join(preference_lines)
                + "\nCác dòng này chỉ điều chỉnh cách trình bày, không phải căn cứ pháp luật."
            )
    patch_contract = (
        " Thêm trường `conversation_patch` gồm `topic_summary` (tối đa 1.200 ký tự), "
        "`current_goal` (tối đa 300 ký tự), tối đa 8 `topics`, 10 `user_facts`, "
        "8 `open_questions` và `referenced_turn_ids`. Mỗi user fact phải có "
        "`text`, `source_message_id` lấy đúng ID trong lịch sử và `status=user_stated`. "
        "Patch chỉ ghi mục tiêu/chủ đề/dữ kiện người dùng tự nêu; tuyệt đối không ghi URL, "
        "citation, số hiệu văn bản, thời hạn, phí hoặc kết luận pháp luật."
        if conversation_patch_envelope
        else ""
    )
    bounded_suggestions = max(0, min(4, int(suggestion_limit)))
    output_contract = (
        "Xuất đúng một JSON object hợp lệ với các trường: "
        "`answer_markdown` là toàn bộ câu trả lời Markdown; "
        f"`suggested_questions` là mảng từ 0 đến {bounded_suggestions} object gồm `text`, `issue_id`, `facet`. "
        "Gợi ý phải là câu hỏi nối tiếp ngắn, cùng issue, chỉ hướng tới nội dung trong nguồn "
        "hoặc nội dung còn thiếu; không chứa URL hay kết luận pháp luật. "
        f"{patch_contract} "
        "Không đặt JSON trong code fence và không thêm chữ ngoài JSON."
        if suggestion_envelope
        else "Không xuất JSON, evidence_id, quote_id hay mô tả pipeline."
    )
    output_label = (
        "JSON ANSWER ENVELOPE"
        if suggestion_envelope
        else "TRẢ LỜI MARKDOWN"
    )
    conversation_block = ""
    if _clean_text(conversation_context or ""):
        conversation_block = (
            "\n\nLỊCH SỬ HỘI THOẠI — KHÔNG PHẢI CĂN CỨ PHÁP LUẬT\n"
            + _clean_text(conversation_context or "")
            + "\nLịch sử chỉ dùng để hiểu đại từ, câu nối tiếp và phong cách. "
            "Không được dùng phát biểu cũ của trợ lý làm căn cứ pháp luật; "
            "NGUỒN ĐÃ TRUY XUẤT bên dưới là căn cứ duy nhất."
        )
    address_guidance = (
        "- Đây là lượt trả lời đầu tiên: mở đầu ngắn bằng “Thưa anh/chị,”."
        if conversation_is_first_turn
        else "- Đây là lượt tiếp nối: không lặp lời chào dài; xưng hô “anh/chị” tự nhiên khi cần."
    )
    return (
        "Bạn là trợ lý pháp luật Việt Nam. Trả lời trực tiếp cho "
        f"{audience} bằng Markdown tự nhiên, đầy đủ và dễ hiểu.\n"
        "Phân tích âm thầm ý định, tham chiếu hội thoại, nội dung còn thiếu và "
        "bước tiếp theo hữu ích; không xuất chain-of-thought hoặc thẻ <think>. "
        "Chỉ dùng nội dung trong NGUỒN ĐÃ TRUY XUẤT bên dưới. "
        f"{output_contract} "
        "Nếu nguồn không đủ cho một ý thì nói ngắn gọn ý đó chưa đủ nguồn; "
        "không tự tạo điều luật, thời hạn, cơ quan, biểu mẫu hoặc URL.\n\n"
        "YÊU CẦU NGHIỆP VỤ & NỘI DUNG (ÁP DỤNG MỌI LĨNH VỰC)\n"
        "- Mặc định phục vụ công dân/tổ chức trong nước tại cấp cơ sở (UBND cấp xã / Công an cấp xã). Tuyệt đối không tự suy diễn hoặc áp dụng quy định có yếu tố nước ngoài hoặc cơ quan cấp huyện/tỉnh trừ khi câu hỏi yêu cầu rõ hoặc quy định chỉ do cấp đó giải quyết.\n"
        "- Trả lời thẳng vào trọng tâm, phân tích rõ ràng, thiết thực và dễ hiểu; không chép lại nguyên văn cả điều luật dài dòng nếu không cần thiết.\n"
        "- Chỉ hướng dẫn phương án xử lý, bước tiếp theo hoặc ngoại lệ khi chính nguồn tương ứng có nêu; không dùng ví dụ nghiệp vụ làm quy tắc mặc định.\n"
        "- Tự bỏ qua nguồn khác chủ thể, khác hành vi hoặc khác thủ tục; khi nguồn chỉ đủ một phần, trả lời phần đã có căn cứ và nêu ngắn gọn phần còn thiếu.\n"
        "- Tuyệt đối không tự tạo Điều/Khoản, thời hạn, lệ phí, biểu mẫu, URL, cơ quan hoặc dữ kiện ngoài nguồn.\n"
        "- Giữ nguyên đầy đủ điều kiện, ngoại lệ và phạm vi của quy định. Không biến việc không cần trực tiếp có mặt thành không cần đồng ý, xác nhận hoặc ủy quyền.\n"
        "- Phân biệt NGUỒN ĐÃ TÌM THẤY với NGUỒN DÙNG LÀM CĂN CỨ: chỉ đưa nguồn hỗ trợ trực tiếp kết luận vào câu trả lời. Mỗi kết luận về điều kiện, đối tượng, phủ định, thời hạn, phí, giấy tờ hoặc biểu mẫu phải có đoạn nguồn tương ứng.\n\n"
        "QUY CHUẨN TRÌNH BÀY MARKDOWN (BẮT BUỘC ĐẸP, RÕ RÀNG & DỄ ĐỌC)\n"
        "- Luôn mở đầu bằng 1-2 câu kết luận trực tiếp, đi thẳng vào câu trả lời.\n"
        "- Bố cục câu trả lời thành các phần/bước rõ ràng, có dòng trống giữa các đoạn; TUYỆT ĐỐI KHÔNG viết thành một đoạn văn dính liền.\n"
        "- Khi hướng dẫn thủ tục, BẮT BUỘC dùng danh sách gạch đầu dòng (`- `) hoặc đánh số thứ tự (`1.`, `2.`, `3.`) cho từng bước/từng loại giấy tờ.\n"
        "- In đậm (`**...**`) các thông tin cốt lõi: **Cơ quan giải quyết / Nơi nộp**, **Hồ sơ cần chuẩn bị**, **Trình tự thực hiện**, **Thời hạn**, **Lệ phí**, **Lưu ý quan trọng**.\n"
        "- Chỉ dùng Markdown thuần trong `answer_markdown`: tuyệt đối không dùng bất kỳ thẻ HTML nào (`<br>`, `<u>`, `<table>` hoặc thẻ dạng `<...>` khác); không đặt ký tự `|` ở đầu dòng trừ khi tạo một bảng Markdown hoàn chỉnh; dùng dòng trống để ngắt đoạn.\n"
        "- Luôn đóng đủ cặp ký hiệu Markdown như `**`; không bọc toàn bộ câu trả lời trong một khối in đậm.\n"
        f"{address_guidance}\n"
        "- Khi cần đại từ, dùng “anh/chị”; không gọi người dùng là “bạn”.\n"
        f"- {role_guidance}\n"
        f"{_clean_text(system_prompt_addendum or '')}\n\n"
        f"CÂU HỎI\n{_clean_text(question)}"
        f"{conversation_block}"
        f"{preference_block}"
        f"{standalone_block}"
        f"{required_block}"
        f"{available_block}"
        f"{fact_index_block}\n\n"
        f"NGUỒN ĐÃ TRUY XUẤT\n{context}\n\n"
        f"{output_label}"
    )


def build_unified_chat_answer_prompt(
    *,
    question: str,
    role: str,
    context: str = "",
    route: str | None = None,
    system_prompt_addendum: str | None = None,
    standalone_queries: Mapping[str, str] | None = None,
    required_facets_by_issue: Mapping[str, Sequence[str]] | None = None,
    available_facets_by_issue: Mapping[str, Sequence[str]] | None = None,
    available_facts_by_issue: Mapping[str, Sequence[str]] | None = None,
    communication_preferences: Mapping[str, str] | None = None,
    conversation_context: str | None = None,
    active_document_context: str | None = None,
    conversation_is_first_turn: bool = True,
    suggestion_limit: int = 2,
    suggestion_envelope: bool = True,
    conversation_patch_envelope: bool = False,
    answer_depth: str = "balanced",
) -> str:
    from api.chat_prompt import build_answer_prompt
    return build_answer_prompt(
        question=question, role=role, sources=context,
        history=conversation_context or "", natural_chat=route in {"chat_meta", "out_of_scope"},
        style=system_prompt_addendum, preferences=communication_preferences, depth=answer_depth,
        active_document=active_document_context or "",
        suggestion_envelope=suggestion_envelope,
    )


def _citation_labels_from_evidence_context(context: str) -> tuple[str, ...]:
    """Extract display-ready, evidence-bound citation labels for the prompt.

    The compact packet already renders each evidence header as
    ``[evidence-N | issue=... | title | law number | Điều N]``.  Repeating
    only that already-admitted metadata directly before the answer rules gives
    every provider an explicit, finite citation vocabulary without adding a
    model-specific prompt or trusting model-invented legal references.
    """

    labels: list[str] = []
    seen: set[str] = set()
    for raw_header in re.findall(r"^\[([^\]\r\n]+)\]", str(context or ""), flags=re.MULTILINE):
        parts = [part.strip() for part in raw_header.split("|") if part.strip()]
        visible = [
            part
            for part in parts
            if not part.casefold().startswith("evidence-")
            and not part.casefold().startswith("issue=")
        ]
        label = " | ".join(visible)
        if not label or "điều" not in label.casefold():
            continue
        key = label.casefold()
        if key not in seen:
            seen.add(key)
            labels.append(label)
    return tuple(labels[:8])


def unified_chat_prompt_enabled(
    role: str = "citizen",
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Feature flag for the provider-neutral answer prompt.

    It defaults on for citizen/officer so the active conversational path uses
    one contract; setting the flag off restores the previous builders for
    rollback without changing retrieval or model configuration.
    """

    values = os.environ if environ is None else environ
    enabled = str(values.get("CHAT_UNIFIED_PROMPT_V1_ENABLED", "true")).strip().casefold()
    if enabled not in _TRUE_VALUES:
        return False
    roles = {
        item.strip().casefold()
        for item in str(values.get("CHAT_UNIFIED_PROMPT_V1_ROLES", "citizen,officer")).split(",")
        if item.strip()
    }
    return str(role or "citizen").strip().casefold() in roles


def build_direct_markdown_answer_prompt(
    *,
    question: str,
    role: str,
    context: str,
    route: str | None = None,
    system_prompt_addendum: str | None = None,
    standalone_queries: Mapping[str, str] | None = None,
    required_facets_by_issue: Mapping[str, Sequence[str]] | None = None,
    available_facets_by_issue: Mapping[str, Sequence[str]] | None = None,
    available_facts_by_issue: Mapping[str, Sequence[str]] | None = None,
    communication_preferences: Mapping[str, str] | None = None,
    suggestion_envelope: bool = False,
    conversation_patch_envelope: bool = False,
    conversation_context: str | None = None,
    conversation_is_first_turn: bool = True,
    suggestion_limit: int = 4,
) -> str:
    """Compatibility entry point; active implementation is the unified builder."""

    # Keep the public helper backwards-compatible for small/unit callers that
    # only request the old raw Markdown contract. Runtime chat passes a route
    # or conversation context; the memory/router callers also pass the
    # envelope flags. This makes rollout explicit without allowing a runtime
    # .env toggle to silently alter an unrelated prompt test.
    use_unified = bool(
        unified_chat_prompt_enabled(role)
        and (
            route
            or conversation_context
            or suggestion_envelope
            or conversation_patch_envelope
        )
    )
    if not use_unified:
        return _build_legacy_direct_markdown_answer_prompt(
            question=question,
            role=role,
            context=context,
            system_prompt_addendum=system_prompt_addendum,
            standalone_queries=standalone_queries,
            required_facets_by_issue=required_facets_by_issue,
            available_facets_by_issue=available_facets_by_issue,
            available_facts_by_issue=available_facts_by_issue,
            communication_preferences=communication_preferences,
            suggestion_envelope=suggestion_envelope,
            conversation_patch_envelope=conversation_patch_envelope,
            conversation_context=conversation_context,
            conversation_is_first_turn=conversation_is_first_turn,
            suggestion_limit=suggestion_limit,
        )

    return build_unified_chat_answer_prompt(
        question=question,
        role=role,
        context=context,
        route=route,
        system_prompt_addendum=system_prompt_addendum,
        standalone_queries=standalone_queries,
        required_facets_by_issue=required_facets_by_issue,
        available_facets_by_issue=available_facets_by_issue,
        available_facts_by_issue=available_facts_by_issue,
        communication_preferences=communication_preferences,
        conversation_context=conversation_context,
        conversation_is_first_turn=conversation_is_first_turn,
        suggestion_limit=suggestion_limit,
        suggestion_envelope=True,
        conversation_patch_envelope=False,
    )


def build_qwen_conversational_answer_prompt(base_prompt: str) -> str:
    """Compatibility alias for the now provider-neutral prompt contract.

    DeepSeek and Qwen must receive byte-identical application prompts for an
    identical question/history/evidence packet. Provider adapters may enforce
    transport using native JSON schema, but must not append model-specific
    content instructions.
    """

    return base_prompt


def parse_structured_answer(raw: Any) -> StructuredLegalAnswer:
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        payload = json.loads(text)
        return StructuredLegalAnswer.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ValueError("model_output_is_not_valid_structured_json") from exc


def build_extractive_structured_answer(
    *,
    issues: Sequence[LegalIssue],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    coverage_matrix: Mapping[str, Sequence[Mapping[str, Any]]],
) -> StructuredLegalAnswer:
    """Build atomic claims from exact source text when generation fails.

    This is not a second model pass. It copies one bounded source sentence for
    each available facet and sends the result through the same claim validator
    and renderer as model output.
    """

    issue_answers: list[StructuredIssueAnswer] = []
    for issue in issues:
        claims: list[StructuredClaim] = []
        seen: set[tuple[str, str]] = set()
        for raw_facet in coverage_matrix.get(issue.issue_id, ()):
            if not raw_facet.get("evidence_available"):
                continue
            facet = _normalized_facet(raw_facet.get("facet"))
            claim_type = {
                "verification": "procedure",
                "recording": "rule",
            }.get(
                facet,
                facet if facet in _ROLE_CLAIM_LABELS["citizen"] else "rule",
            )
            markers = _FACET_EVIDENCE_MARKERS.get(facet, ())
            ranked_candidates: list[
                tuple[tuple[int, int, int], int, StructuredClaim]
            ] = []
            candidate_order = 0
            for evidence_id in raw_facet.get("evidence_ids") or ():
                evidence = evidence_by_id.get(str(evidence_id))
                if evidence is None:
                    continue
                # Presentation labels in an evidence capsule (for example
                # "Cấu trúc" and "Ngữ cảnh chi phối") are prompt scaffolding,
                # never legal propositions. Build the extractive pool from the
                # clean child and parent projections themselves so those labels
                # cannot leak into a public claim.
                child_text = _clean_extractive_text(
                    evidence.get("clean_matched_child_content")
                    or evidence.get("clean_content")
                    or evidence.get("content")
                )
                parent_text = _clean_extractive_text(
                    evidence.get("clean_parent_context")
                )
                content_parts = [child_text] if child_text else []
                if (
                    parent_text
                    and _fold(parent_text) != _fold(child_text)
                ):
                    content_parts.append(parent_text)
                content = "\n".join(content_parts) or _clean_text(
                    evidence.get("evidence_capsule")
                )
                if not content:
                    continue
                explicit_facets = {
                    _normalized_facet(str(value))
                    for value in evidence.get("supported_facets") or ()
                }
                facet_is_deterministically_bound = facet in explicit_facets
                sentence_pattern = (
                    r"(?<=[.!?])\s+|\n+"
                    if facet_is_deterministically_bound
                    else r"(?<=[.!?;])\s+|\n+"
                )
                sentences = [
                    item.strip()
                    for item in re.split(sentence_pattern, content)
                    if item.strip()
                ]
                for sentence in sentences:
                    folded = _fold(sentence)
                    if _is_extractive_heading(facet=facet, sentence=sentence):
                        continue
                    if _is_incomplete_extractive_sentence(sentence):
                        continue
                    if (
                        markers
                        and not facet_is_deterministically_bound
                        and not any(marker in folded for marker in markers)
                    ):
                        continue
                    minimum_words = {
                        "authority": 6,
                        "documents": 6,
                        "deadline": 7,
                        "fee": 5,
                    }.get(facet, 4)
                    if len(sentence.split()) < minimum_words:
                        continue
                    quote = sentence.strip()
                    candidate = StructuredClaim(
                        claim_text=quote,
                        claim_type=claim_type,
                        evidence_id=str(evidence_id),
                        support_quote=quote,
                        facet=facet,
                        actor=(
                            str((evidence.get("actor_anchors") or [""])[0])
                            or None
                        ),
                        quote_id=(str(evidence.get("quote_id") or "") or None),
                    )
                    # A relevant sentence can cite a different provision than
                    # the hydrated chunk metadata. The public validator must
                    # reject that citation, so try the next relevant sentence
                    # instead of letting one unsafe excerpt erase the facet.
                    preflight = validate_structured_claims(
                        request_id=str(evidence.get("request_id") or issue.request_id),
                        claims=[
                            {
                                **candidate.model_dump(),
                                "issue_id": issue.issue_id,
                            }
                        ],
                        evidence_by_id={str(evidence_id): evidence},
                        issue_context=" ".join(
                            value
                            for value in (
                                issue.query_text,
                                issue.text,
                                issue.retrieval_subject,
                                *issue.retrieval_facts,
                            )
                            if value
                        ),
                        issue_intent=issue.intent,
                        relevance_topics=issue.relevance_topics,
                    )
                    if preflight.accepted:
                        relevance_score = _extractive_relevance_score(
                            issue=issue,
                            sentence=sentence,
                            evidence=evidence,
                            markers=markers,
                        )
                        if facet_is_deterministically_bound:
                            # Reviewed facet adapters already guarantee subject
                            # and facet identity.  Preserve official clause/list
                            # order instead of letting lexical overlap move the
                            # last condition or attachment ahead of the first.
                            relevance_score = (1, 0, 0)
                        ranked_candidates.append(
                            (
                                relevance_score,
                                -candidate_order,
                                candidate,
                            )
                        )
                    candidate_order += 1
            if not ranked_candidates:
                continue
            ordered_candidates = sorted(
                ranked_candidates,
                key=lambda item: (item[0], item[1]),
                reverse=True,
            )
            selection_limit = (
                4
                if facet == "condition"
                else 3
                if facet
                in {"rule", "procedure", "next_action", "documents", "authority"}
                else 1
            )
            selected_provisions: set[tuple[str, str, str, str]] = set()
            selected_count = 0
            selected_by_evidence: dict[str, int] = {}
            distinct_evidence_count = len(
                {
                    str(item[2].evidence_id)
                    for item in ordered_candidates
                }
            )
            for _score, _order, selected_claim in ordered_candidates:
                selected_evidence = evidence_by_id.get(
                    selected_claim.evidence_id
                ) or {}
                provision = (
                    _clean_text(
                        selected_evidence.get("law_number")
                    ).casefold(),
                    _clean_text(
                        selected_evidence.get("article_number")
                    ).casefold(),
                    _clean_text(
                        selected_evidence.get("clause_number")
                    ).casefold(),
                    _clean_text(
                        selected_evidence.get("point_number")
                    ).casefold(),
                )
                selected_facets = {
                    _normalized_facet(str(value))
                    for value in selected_evidence.get("supported_facets") or ()
                }
                allow_multiple_from_bound_facet = (
                    facet in {
                        "rule",
                        "condition",
                        "documents",
                        "procedure",
                        "next_action",
                    }
                    and (
                        facet in selected_facets
                        or (facet == "rule" and distinct_evidence_count == 1)
                    )
                )
                if (
                    facet == "documents"
                    and distinct_evidence_count > 1
                    and selected_by_evidence.get(selected_claim.evidence_id, 0) >= 2
                ):
                    # Give a separately approved clause/point a fair slot in
                    # the bounded dossier list instead of allowing one long
                    # clause with an exceptional sub-item to consume all three.
                    continue
                if provision in selected_provisions and not allow_multiple_from_bound_facet:
                    continue
                key = (claim_type, semantic_claim_key(selected_claim.claim_text))
                if key in seen:
                    continue
                seen.add(key)
                selected_provisions.add(provision)
                claims.append(selected_claim)
                selected_count += 1
                selected_by_evidence[selected_claim.evidence_id] = (
                    selected_by_evidence.get(selected_claim.evidence_id, 0) + 1
                )
                if selected_count >= selection_limit:
                    break
        issue_answers.append(
            StructuredIssueAnswer(
                issue_id=issue.issue_id,
                claims=claims,
                clarifying_question=_deterministic_clarifying_question(issue),
            )
        )
    return StructuredLegalAnswer(issues=issue_answers)


def enforce_explicit_facet_claims(
    *,
    issues: Sequence[LegalIssue],
    output: StructuredLegalAnswer,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    coverage_matrix: Mapping[str, Sequence[Mapping[str, Any]]],
) -> StructuredLegalAnswer:
    """Replace model claims for reviewed facets with deterministic extracts.

    ``supported_facets`` is an adapter-level legal routing decision.  Once an
    issue has direct evidence explicitly bound to a facet, a generative model
    may phrase other, legacy evidence attractively but must not override that
    binding.  Facets that still rely on legacy corpus rows remain model-driven
    and pass through the normal validator unchanged.
    """

    extractive = build_extractive_structured_answer(
        issues=issues,
        evidence_by_id=evidence_by_id,
        coverage_matrix=coverage_matrix,
    )
    generated_by_issue = {item.issue_id: item for item in output.issues}
    extractive_by_issue = {item.issue_id: item for item in extractive.issues}
    merged_issues: list[StructuredIssueAnswer] = []

    for issue in issues:
        generated = generated_by_issue.get(issue.issue_id) or StructuredIssueAnswer(
            issue_id=issue.issue_id
        )
        explicit_facets: set[str] = set()
        for matrix_row in coverage_matrix.get(issue.issue_id, ()):
            if not matrix_row.get("evidence_available"):
                continue
            facet = _normalized_facet(matrix_row.get("facet"))
            for evidence_id in matrix_row.get("evidence_ids") or ():
                supported = {
                    _normalized_facet(str(value))
                    for value in (
                        evidence_by_id.get(str(evidence_id), {}).get(
                            "supported_facets"
                        )
                        or ()
                    )
                }
                if facet in supported:
                    explicit_facets.update(supported)

        # When the user names an exact instrument/provision, deterministic
        # source extraction is also the binding decision. This prevents a
        # model from selecting an easier nearby sentence while still passing
        # the broad facet-level coverage check.
        if plan_exact_lookup(issue.query_text).law_numbers:
            explicit_facets.update(
                _normalized_facet(matrix_row.get("facet"))
                for matrix_row in coverage_matrix.get(issue.issue_id, ())
                if matrix_row.get("evidence_available")
            )

        if not explicit_facets:
            merged_issues.append(generated)
            continue

        claims = [
            claim
            for claim in generated.claims
            if _normalized_facet(claim.claim_type) not in explicit_facets
        ]
        deterministic = extractive_by_issue.get(issue.issue_id)
        if deterministic is not None:
            claims.extend(
                claim
                for claim in deterministic.claims
                if _normalized_facet(claim.claim_type) in explicit_facets
            )
        merged_issues.append(generated.model_copy(update={"claims": claims}))

    return StructuredLegalAnswer(issues=merged_issues)


def supplement_rule_source_diversity(
    *,
    request_id: str,
    issues: Sequence[LegalIssue],
    output: StructuredLegalAnswer,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    coverage_matrix: Mapping[str, Sequence[Mapping[str, Any]]],
    maximum_documents_per_issue: int = 3,
) -> StructuredLegalAnswer:
    """Add only validated source excerpts omitted by a successful model call.

    A single generation can cover the requested legal facet while mentioning
    only one of several independently selected primary instruments.  Public
    citations are claim-bound, so silently appending the other source metadata
    would be misleading.  This helper instead reuses the deterministic
    extractive path, adds at most one already-preflighted rule claim per
    unrepresented document, and leaves final acceptance to the normal claim
    validator in ``render_structured_answer``.

    No model repair is performed and no metadata or legal proposition is
    inferred.
    """

    maximum_documents = max(1, min(3, int(maximum_documents_per_issue)))
    extractive = build_extractive_structured_answer(
        issues=issues,
        evidence_by_id=evidence_by_id,
        coverage_matrix=coverage_matrix,
    )
    generated_by_issue = {item.issue_id: item for item in output.issues}
    extractive_by_issue = {item.issue_id: item for item in extractive.issues}

    def document_key(evidence: Mapping[str, Any]) -> str:
        document_id = _clean_text(evidence.get("document_id"))
        if document_id:
            return f"id:{document_id}"
        law_number = _fold(evidence.get("law_number"))
        if law_number:
            return f"law:{law_number}"
        source_url = _clean_text(evidence.get("source_url")).casefold()
        return f"url:{source_url}" if source_url else ""

    supplemented: list[StructuredIssueAnswer] = []
    for issue in issues:
        generated = generated_by_issue.get(issue.issue_id) or StructuredIssueAnswer(
            issue_id=issue.issue_id
        )
        matrix_rows = coverage_matrix.get(issue.issue_id, ())
        has_available_rule_facet = any(
            _normalized_facet(row.get("facet")) == "rule"
            and bool(row.get("evidence_available"))
            for row in matrix_rows
        )
        issue_wording = _fold(f"{issue.title} {issue.query_text} {issue.text}")
        explicitly_requests_rule_diversity = issue.intent == "rule" or any(
            marker in issue_wording
            for marker in ("can cu nao", "phan biet", "so sanh")
        )
        if not has_available_rule_facet or not explicitly_requests_rule_diversity:
            supplemented.append(generated)
            continue

        raw_claims = [
            {**claim.model_dump(), "issue_id": issue.issue_id}
            for claim in generated.claims
        ]
        validation = validate_structured_claims(
            request_id=request_id,
            claims=raw_claims,
            evidence_by_id=evidence_by_id,
            expected_form=issue.expected_form,
            issue_context=" ".join(
                value
                for value in (
                    issue.query_text,
                    issue.text,
                    issue.retrieval_subject,
                    *issue.retrieval_facts,
                )
                if value
            ),
            issue_intent=issue.intent,
            relevance_topics=issue.relevance_topics,
        )
        represented_documents = {
            key
            for item in validation.accepted
            if str(item.get("claim_type") or "") == "rule"
            if (key := document_key(item.get("evidence") or {}))
        }
        accepted_rule_claims = [
            item
            for item in validation.accepted
            if str(item.get("claim_type") or "") == "rule"
        ]
        accepted_rule_characters = sum(
            len(_clean_text(item.get("claim_text")))
            for item in accepted_rule_claims
        )
        requests_detailed_rule = any(
            marker in issue_wording
            for marker in (
                "chi tiet",
                "lan luot",
                "tung quy tac",
                "chuyen sau",
                "cac y chinh",
            )
        )
        needs_more_rule_detail = (
            accepted_rule_characters < 160 or requests_detailed_rule
        )
        rule_claim_count = len(accepted_rule_claims)
        merged_claims = list(generated.claims)
        existing_claims = {
            (
                claim.evidence_id,
                claim.claim_type,
                semantic_claim_key(claim.claim_text),
            )
            for claim in merged_claims
        }
        candidates = extractive_by_issue.get(issue.issue_id)
        for candidate in (() if candidates is None else candidates.claims):
            if candidate.claim_type != "rule" or len(merged_claims) >= 20:
                continue
            evidence = evidence_by_id.get(candidate.evidence_id) or {}
            key = document_key(evidence)
            if not key:
                continue
            same_document = key in represented_documents
            if same_document and not (
                needs_more_rule_detail and rule_claim_count < 3
            ):
                continue
            candidate_key = (
                candidate.evidence_id,
                candidate.claim_type,
                semantic_claim_key(candidate.claim_text),
            )
            if candidate_key in existing_claims:
                continue
            merged_claims.append(candidate)
            existing_claims.add(candidate_key)
            represented_documents.add(key)
            rule_claim_count += 1
            if (
                len(represented_documents) >= maximum_documents
                and (not needs_more_rule_detail or rule_claim_count >= 3)
            ):
                break

        supplemented.append(
            generated.model_copy(update={"claims": merged_claims})
        )

    # Preserve any model issue that is not part of the deterministic planner;
    # the renderer will reject it because it cannot bind to a planned issue.
    planned_ids = {issue.issue_id for issue in issues}
    supplemented.extend(
        item for item in output.issues if item.issue_id not in planned_ids
    )
    return StructuredLegalAnswer(issues=supplemented)


def render_structured_answer(
    *,
    request_id: str,
    issues: Sequence[LegalIssue],
    output: StructuredLegalAnswer,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    role: str = "citizen",
    coverage_matrix: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    preserve_distinct_claims: bool = False,
    require_extended_binding: bool = False,
    hallucination_only_validation: bool = False,
) -> tuple[list[Any], dict[str, Any], dict[str, Any]]:
    """Validate claims independently and render only accepted material."""

    output_by_issue = {item.issue_id: item for item in output.issues}
    matrix = (
        {key: [dict(item) for item in value] for key, value in coverage_matrix.items()}
        if coverage_matrix is not None
        else build_issue_coverage_matrix(
            issues=issues,
            evidence_by_id=evidence_by_id,
            required_facets_by_issue={
                issue.issue_id: [issue.intent] for issue in issues
            },
        )
    )
    sections: list[Any] = []
    accepted_count = 0
    rejected_count = 0
    issue_trace: list[dict[str, Any]] = []
    available_required_count = 0
    covered_required_count = 0
    total_required_count = 0
    critical_required_count = 0
    critical_covered_count = 0
    displayed_claim_count = 0
    normalized_role = role if role in _ROLE_CLAIM_LABELS else "citizen"
    labels = _ROLE_CLAIM_LABELS[normalized_role]
    for issue in issues:
        generated = output_by_issue.get(issue.issue_id)
        raw_claims = [] if generated is None else [
            {**claim.model_dump(), "issue_id": issue.issue_id}
            for claim in generated.claims
        ]
        binding_facets = tuple(
            _normalized_facet(raw_facet.get("facet"))
            for raw_facet in matrix.get(issue.issue_id, ())
            if raw_facet.get("facet")
        )
        validation = validate_structured_claims(
            request_id=request_id,
            claims=raw_claims,
            evidence_by_id=evidence_by_id,
            expected_form=issue.expected_form,
            issue_context=" ".join(
                value
                for value in (
                    issue.query_text,
                    issue.text,
                    issue.retrieval_subject,
                    *issue.retrieval_facts,
                )
                if value
            ),
            issue_intent=issue.intent,
            relevance_topics=issue.relevance_topics,
            required_facets=binding_facets,
            require_extended_binding=require_extended_binding,
            validation_mode=(
                "hallucination_only"
                if hallucination_only_validation
                else "strict"
            ),
        )
        accepted_count += len(validation.accepted)
        rejected_count += len(validation.rejected)
        accepted_claim_types = {
            str(item.get("claim_type") or "rule") for item in validation.accepted
        }
        issue_matrix: list[dict[str, Any]] = []
        requested_claim_types: set[str] = set()
        for raw_facet in matrix.get(issue.issue_id, ()):
            total_required_count += 1
            if issue.priority == "critical":
                critical_required_count += 1
            facet = _normalized_facet(raw_facet.get("facet"))
            compatible_types = _FACET_CLAIM_TYPES.get(facet, {facet})
            requested_claim_types.update(compatible_types)
            available = bool(raw_facet.get("evidence_available"))
            requires_user_fact = bool(raw_facet.get("requires_user_fact"))
            covered = available and not requires_user_fact and bool(
                accepted_claim_types.intersection(compatible_types)
            )
            if available:
                available_required_count += 1
                if covered:
                    covered_required_count += 1
                    if issue.priority == "critical":
                        critical_covered_count += 1
            issue_matrix.append(
                {
                    **dict(raw_facet),
                    "facet": facet,
                    "status": (
                        "covered"
                        if covered
                        else "missing"
                        if available
                        else "not_available"
                    ),
                    "coverage_status": (
                        "requires_user_fact"
                        if requires_user_fact
                        else "covered"
                        if covered
                        else "partially_covered"
                        if available
                        else "missing_evidence"
                    ),
                }
            )
        usable_claims = [
            item
            for item in validation.accepted
            if not requested_claim_types
            or str(item.get("claim_type") or "rule") in requested_claim_types
            or str(item.get("claim_type") or "rule") in {
                "rule", "procedure", "condition", "documents", "authority",
                "fee", "deadline", "form", "next_action", "exception", "warning"
            }
        ]
        # A facet normally needs one best supported proposition, not every
        # nearby sentence in the packet. A direct rule may expose up to three
        # distinct claims when they all bind to one verified provision (or the
        # question explicitly requests comparison/detail); other facets keep
        # their stricter anti-drift limit.
        concise_claims: list[dict[str, Any]] = []
        displayed_type_counts: dict[str, int] = {}
        displayed_propositions: set[str] = set()
        issue_wording = _fold(f"{issue.title} {issue.query_text} {issue.text}")
        available_rule_claims = [
            item
            for item in usable_claims
            if str(item.get("claim_type") or "rule") == "rule"
        ]
        available_rule_provisions = {
            (
                _fold((item.get("evidence") or {}).get("law_number")),
                _clean_text(
                    (item.get("evidence") or {}).get("article_number")
                ).casefold(),
            )
            for item in available_rule_claims
        }
        allow_rule_diversity = (
            len(available_rule_claims) > 1
            and len(available_rule_provisions) == 1
        ) or any(
            marker in issue_wording
            for marker in (
                "can cu nao",
                "phan biet",
                "so sanh",
                "chi tiet",
                "lan luot",
                "tung quy tac",
                "chuyen sau",
                "cac y chinh",
            )
        )
        for item in usable_claims:
            claim_type = str(item.get("claim_type") or "rule")
            proposition_key = semantic_claim_key(item.get("claim_text"))
            if proposition_key and proposition_key in displayed_propositions:
                continue
            evidence = item.get("evidence") or {}
            explicit_facets = {
                _normalized_facet(str(value))
                for value in evidence.get("supported_facets") or ()
            }
            maximum_for_type = 20 if preserve_distinct_claims else 1
            if preserve_distinct_claims:
                pass
            elif (
                claim_type in {"condition", "documents", "procedure", "next_action"}
                and _normalized_facet(claim_type) in explicit_facets
            ):
                maximum_for_type = 4 if claim_type == "condition" else 3
            elif claim_type == "rule" and allow_rule_diversity:
                maximum_for_type = 3
            elif claim_type == "condition":
                quantitative_groups = (
                    ("dien tich toi thieu", "toi thieu"),
                    ("ty le", "toi da"),
                )
                requested_quantitative_groups = sum(
                    1
                    for markers in quantitative_groups
                    if any(marker in issue_wording for marker in markers)
                )
                maximum_for_type = max(1, requested_quantitative_groups)
            if displayed_type_counts.get(claim_type, 0) >= maximum_for_type:
                continue
            displayed_type_counts[claim_type] = (
                displayed_type_counts.get(claim_type, 0) + 1
            )
            if proposition_key:
                displayed_propositions.add(proposition_key)
            concise_claims.append(item)
        usable_claims = concise_claims
        # Public citations must bind to a proposition the user can actually
        # see. Sources for same-type claims removed by the concise renderer
        # remain available in admin trace, but cannot appear as unexplained
        # public citations.
        citation_claims = list(usable_claims)
        displayed_claim_count += len(usable_claims)
        # Bind each public citation to the same exact quote that passed claim
        # validation.  The provenance verifier may promote this to a physical
        # span when the ingestion sidecar has page/offset hashes; otherwise it
        # remains a verified content quote.  It is never model-declared proof.
        accepted_sources = [
            {
                **item["evidence"],
                "support_quote": item.get("support_quote"),
                # Use the same canonical text projection that the claim
                # validator used. Exact-Article packets may intentionally keep
                # the complete provision in evidence_capsule while ``content``
                # is empty or only contains one structural child.
                "source_text": (
                    item["evidence"].get("evidence_capsule")
                    or item["evidence"].get("clean_content")
                    or item["evidence"].get("content")
                ),
            }
            for item in citation_claims
        ]
        grouped_claims: dict[str, list[str]] = {}
        claim_type_order: list[str] = []
        for item in usable_claims:
            claim_type = str(item.get("claim_type") or "rule")
            cleaned_claim = _clean_text(item.get("claim_text"))
            if not cleaned_claim:
                continue
            if claim_type not in grouped_claims:
                grouped_claims[claim_type] = []
                claim_type_order.append(claim_type)
            grouped_claims[claim_type].append(cleaned_claim)
        rendered_groups: list[str] = []
        for claim_type in claim_type_order:
            claim_label = labels.get(claim_type, "Nội dung")
            claim_texts = [text for text in grouped_claims[claim_type] if text]
            if not claim_texts:
                continue
            if len(claim_texts) == 1:
                rendered_groups.append(
                    f"- **{claim_label}:** {claim_texts[0]}"
                )
            else:
                rendered_groups.append(
                    f"**{claim_label}**\n"
                    + "\n".join(f"- {claim_text}" for claim_text in claim_texts)
                )
        # ``answer_markdown`` is model-authored prose and has no independent
        # evidence binding.  Render only the claim lines that passed the
        # validator; this keeps every public proposition citation-backed.
        answer = "\n\n".join(rendered_groups) or None
        guidance = None
        clarification = (
            _clean_text(generated.clarifying_question)
            if generated and generated.clarifying_question
            and not contains_internal_citation_marker(generated.clarifying_question)
            else _deterministic_clarifying_question(issue)
        )
        public_title = _role_issue_title(issue, normalized_role)
        requires_user_fact = any(
            bool(item.get("requires_user_fact")) for item in issue_matrix
        ) or bool(clarification)
        limitation = (
            f"Chưa thể kết luận về “{public_title}” trước khi xác minh dữ kiện dưới đây."
            if requires_user_fact
            else f"Chưa tìm thấy nguồn hiện hành hỗ trợ trực tiếp cho “{public_title}” trong lượt tra cứu này."
        )
        section = validate_answer_section(
            request_id=request_id,
            issue_id=issue.issue_id,
            title=public_title,
            sources=accepted_sources,
            answer=answer,
            guidance=guidance if not answer else None,
            limitation=limitation,
            clarifying_question=clarification,
            facet=issue.intent,
            priority=issue.priority,
            claim_types=[
                str(item.get("claim_type") or "rule") for item in usable_claims
            ],
            relevance_topics=issue.relevance_topics,
        )
        sections.append(section)
        issue_trace.append(
            {
                "issue_id": issue.issue_id,
                "accepted_claim_count": len(validation.accepted),
                "rejected_claim_count": len(validation.rejected),
                "rejection_reasons": [item.reason for item in validation.rejected],
                "status": section.status,
                "requested_facets": [
                    item["facet"] for item in issue_matrix
                ],
                "covered_facets": [
                    item["facet"]
                    for item in issue_matrix
                    if item["status"] == "covered"
                ],
                "missing_facets": [
                    item["facet"]
                    for item in issue_matrix
                    if item["status"] == "missing"
                ],
                "unavailable_facets": [
                    item["facet"]
                    for item in issue_matrix
                    if item["status"] == "not_available"
                ],
                "coverage_matrix": issue_matrix,
            }
        )
    coverage_ratio = (
        covered_required_count / total_required_count
        if total_required_count
        else 1.0
    )
    displayed_claims_with_valid_evidence = displayed_claim_count
    claim_grounding_ratio = (
        displayed_claims_with_valid_evidence / displayed_claim_count
        if displayed_claim_count
        else 1.0
    )
    trace = {
        "accepted_claim_count": accepted_count,
        "rejected_claim_count": rejected_count,
        "coverage_ratio": round(coverage_ratio, 4),
        "required_facet_count": total_required_count,
        "available_required_facet_count": available_required_count,
        "covered_required_facet_count": covered_required_count,
        "critical_coverage_rate": round(
            critical_covered_count / critical_required_count,
            4,
        ) if critical_required_count else 1.0,
        "displayed_legal_claim_count": displayed_claim_count,
        "displayed_claims_with_valid_evidence": displayed_claims_with_valid_evidence,
        "claim_grounding_ratio": claim_grounding_ratio,
        "issues": issue_trace,
    }
    trace["quality_gate"] = evaluate_structured_quality_gate(trace)
    return (
        sections,
        aggregate_answer_sections(sections),
        trace,
    )


def build_packet_timeout_fallback(
    *,
    question: str,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    max_units: int | None = None,
    max_chars: int = 6000,
) -> str:
    """Render source units when the selected model times out.

    This is deliberately not a claim validator or a second answer model.  It
    only exposes the complete structural units already admitted to the current
    evidence packet, preserving their source labels so the user can continue
    from the same material without losing a successful retrieval.  The display
    budget is character-based; there is no fixed four-passage fallback.
    """

    rows = list(evidence_by_id.values())
    if not rows:
        return (
            "Tôi chưa thể hoàn tất câu trả lời trong thời gian quy định và "
            "chưa có đoạn nguồn phù hợp để trích xuất. Anh/chị vui lòng thử lại."
        )
    blocks: list[str] = []
    consumed = 0
    source_rows = (
        rows
        if max_units is None
        else rows[: max(1, int(max_units))]
    )
    for index, row in enumerate(source_rows, start=1):
        content = _clean_extractive_text(
            row.get("evidence_capsule")
            or row.get("clean_content")
            or row.get("content")
            or row.get("parent_context")
        )
        if not content:
            continue
        title = _clean_text(
            row.get("document_title")
            or row.get("law_number")
            or "Nguồn pháp luật"
        )
        law_number = _clean_text(row.get("law_number"))
        article = _clean_text(row.get("article_number"))
        label = " — ".join(
            item
            for item in (
                title,
                law_number if law_number and law_number not in title else "",
                f"Điều {article}" if article else "",
            )
            if item
        )
        header = f"{index}. {label or 'Nguồn đã truy xuất'}"
        remaining = max_chars - consumed - len(header) - 20
        if remaining < 160:
            break
        # A timeout fallback is still a source view. Do not cut a legal unit
        # in the middle of its lead-in, condition, exception or cross-reference
        # merely to fill the character budget; skip that unit and keep the
        # units that fit completely.
        if len(content) > remaining:
            continue
        excerpt = content
        blocks.append(f"**{header}**\n> {excerpt}")
        consumed += len(blocks[-1]) + 2
    if not blocks:
        return (
            "Tôi đã tìm thấy nguồn nhưng chưa thể trích xuất trong ngân sách "
            "hiển thị. Anh/chị vui lòng thử lại."
        )
    return (
        "Model chưa hoàn tất phần diễn giải trong thời gian quy định. "
        "Dưới đây là các đoạn nguồn đã được retrieval và kiểm tra hiệu lực; "
        "chưa phải là kết luận pháp lý tự động:\n\n"
        + "\n\n".join(blocks)
    )


def build_and_render_extractive_answer(
    *,
    request_id: str,
    issues: Sequence[LegalIssue],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    role: str,
    coverage_matrix: Mapping[str, Sequence[Mapping[str, Any]]],
    preserve_distinct_claims: bool = False,
    require_extended_binding: bool = False,
    hallucination_only_validation: bool = False,
) -> tuple[list[Any], dict[str, Any], dict[str, Any]]:
    """Build and validate the deterministic timeout fallback as one unit.

    The router executes this CPU-bound validation/rendering unit in a worker
    thread. Under concurrent timeouts it must not block the event loop and
    delay the deadlines of other requests.
    """

    output = build_extractive_structured_answer(
        issues=issues,
        evidence_by_id=evidence_by_id,
        coverage_matrix=coverage_matrix,
    )
    return render_structured_answer(
        request_id=request_id,
        issues=issues,
        output=output,
        evidence_by_id=evidence_by_id,
        role=role,
        coverage_matrix=coverage_matrix,
        preserve_distinct_claims=preserve_distinct_claims,
        require_extended_binding=require_extended_binding,
        hallucination_only_validation=hallucination_only_validation,
    )


def safe_extractive_fallback(
    *,
    request_id: str,
    issues: Sequence[LegalIssue],
    evidence_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    role: str = "citizen",
) -> tuple[list[Any], dict[str, Any]]:
    """Return a fail-closed source-view result after unsafe generation.

    Candidate eligibility proves that a source belongs to the current issue;
    it does not prove that the first excerpt answers the requested facet.  The
    claim-aware deterministic renderer is used earlier when every claim is
    actually bound.  This final fallback therefore emits no legal claim,
    excerpt, or citation-as-proof; top-level source cards remain available for
    manual viewing.
    """

    rows = list((evidence_by_id or {}).values())
    sections = []
    for issue in issues:
        sources = [
            row for row in rows
            if str(row.get("issue_id") or "") == issue.issue_id
        ][:2]
        limitation = (
            "Đã tìm thấy nguồn có liên quan nhưng chưa xác minh đủ từng ý, "
            "đúng phần văn bản và trích đoạn hỗ trợ để tạo câu trả lời pháp lý. "
            "Chỉ nên xem thẻ nguồn và thử lại."
            if sources
            else f"Chưa tìm thấy nguồn hiện hành hỗ trợ trực tiếp cho “{issue.title}” "
            "trong lượt tra cứu này."
        )
        sections.append(
            validate_answer_section(
                request_id=request_id,
                issue_id=issue.issue_id,
                title=_role_issue_title(
                    issue,
                    role if role in _ROLE_CLAIM_LABELS else "citizen",
                ),
                sources=sources,
                limitation=limitation,
                clarifying_question=_deterministic_clarifying_question(issue),
                facet=issue.intent,
                priority=issue.priority,
                relevance_topics=issue.relevance_topics,
            )
        )
    return sections, aggregate_answer_sections(sections)
