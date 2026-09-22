"""Phase-D answer contract and deterministic trust boundary.

The retrieval service owns candidate recall.  This module deliberately keeps
the answer boundary small and provider-neutral: ACL/effectivity/integrity are
hard gates, uncertain relevance is retained as context, and every generated
claim must point at supporting evidence before it can be rendered.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.legal_structured_answer import parse_structured_answer_resilient


PHASE_D_VERSION = "answer-d-v1"
EvidenceUse = Literal["supporting", "context_only", "blocked"]

_TRUE = {"1", "true", "yes", "on"}
_HARD_STATES = {
    "deleted",
    "exclude",
    "excluded",
    "blocked",
    "unreviewed",
    "pending_review",
    "not_approved",
    "invalid",
}
_EXPIRED_STATES = {
    "expired",
    "not_yet_effective",
    "not_effective",
    "revoked",
    "repealed",
}
_HISTORICAL_STATES = {"historical_only", "historical", "replaced", "superseded"}
_ACTIVE_STATES = {
    "active",
    "current",
    "current_retrievable",
    "effective",
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _fold(value: Any) -> str:
    value = unicodedata.normalize("NFKD", _text(value)).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    # Vietnamese ``đ`` is a letter, not a combining mark; preserve it as
    # ``d`` before the ASCII token projection used by quote verification.
    value = value.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _status(value: Any) -> str:
    """Normalize enum-like metadata without conflating it with prose text."""

    return _fold(value).replace(" ", "_")


def _hash_payload(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _row_identifier(row: Mapping[str, Any], fallback: str) -> str:
    # Retrieval adapters may leave a provider-facing ``evidence_id`` on a
    # row.  Prefer backend-owned identities first so ``evidence-N`` can never
    # become the packet's canonical ID; retain it only as a final compatibility
    # fallback for legacy rows that have no source/chunk identity.
    for key in (
        "canonical_chunk_id",
        "source_id",
        "chunk_id",
        "id",
        "evidence_id",
    ):
        value = _text(row.get(key))
        if value:
            return value[:96]
    return fallback


def _row_content(row: Mapping[str, Any]) -> str:
    for key in (
        "clean_content",
        "clean_matched_child_content",
        "matched_child_content",
        "exact_article_assembled_content",
        "article_content",
        "content",
        "evidence_capsule",
    ):
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _explicit_false(row: Mapping[str, Any], keys: Sequence[str]) -> bool:
    return any(key in row and row.get(key) is False for key in keys)


@dataclass(frozen=True)
class EvidenceUnitD:
    evidence_id: str
    issue_id: str
    content: str
    use: EvidenceUse
    reason_code: str
    score: float = 0.0
    # Preserve the retrieval identity and citation eligibility metadata across
    # the packet -> provider context projection.  The provider-facing
    # ``evidence-N`` ID is intentionally separate from these backend-owned
    # fields, but the final citation validator still needs the original source
    # identity, authority, scope and title after verification.
    source_id: str = ""
    canonical_chunk_id: str = ""
    chunk_id: str = ""
    document_title: str = ""
    domain: str = ""
    domain_slug: str = ""
    scope: str = ""
    official: bool = False
    official_level: str = ""
    issuing_agency: str = ""
    document_status: str = ""
    effective_article_status: str = ""
    legal_as_of: str = ""
    article_title: str = ""
    authority_level: str = ""
    authority_label: str = ""
    law_number: str = ""
    article_number: str = ""
    clause_number: str = ""
    point_number: str = ""
    document_id: str = ""
    source_url: str = ""
    effective_status: str = ""
    document_serving_state: str = ""
    verification_level: str = "content_quote"
    release_id: str = ""
    request_id: str = ""
    exact_article_packet_ref: str = ""
    exact_article_packet_status: str = ""
    exact_article_serving_mode: str = ""
    exact_article_full_article_character_count: int = 0
    exact_article_assembled_content: str = ""
    replacement_of: str = ""
    replaced_by: str = ""

    def to_payload(self, *, include_content: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "evidence_id": self.evidence_id,
            "issue_id": self.issue_id,
            "use": self.use,
            "reason_code": self.reason_code,
            "score": self.score,
            "source_id": self.source_id,
            "canonical_chunk_id": self.canonical_chunk_id,
            "chunk_id": self.chunk_id,
            "document_title": self.document_title,
            "domain": self.domain,
            "domain_slug": self.domain_slug,
            "scope": self.scope,
            "official": self.official,
            "official_level": self.official_level,
            "issuing_agency": self.issuing_agency,
            "document_status": self.document_status,
            "effective_article_status": self.effective_article_status,
            "legal_as_of": self.legal_as_of,
            "article_title": self.article_title,
            "authority_level": self.authority_level,
            "authority_label": self.authority_label,
            "law_number": self.law_number,
            "article_number": self.article_number,
            "clause_number": self.clause_number,
            "point_number": self.point_number,
            "document_id": self.document_id,
            "source_url": self.source_url,
            "effective_status": self.effective_status,
            "document_serving_state": self.document_serving_state,
            "verification_level": self.verification_level,
            "release_id": self.release_id,
            "request_id": self.request_id,
            "exact_article_packet_ref": self.exact_article_packet_ref,
            "exact_article_packet_status": self.exact_article_packet_status,
            "exact_article_serving_mode": self.exact_article_serving_mode,
            "exact_article_full_article_character_count": self.exact_article_full_article_character_count,
            "replacement_of": self.replacement_of,
            "replaced_by": self.replaced_by,
        }
        if include_content:
            payload["content"] = self.content
            payload["exact_article_assembled_content"] = self.exact_article_assembled_content
        return payload


@dataclass(frozen=True)
class EvidencePacketD:
    issue_id: str
    role: str
    as_of: str
    release_id: str
    acl_scope_hash: str
    status: Literal["complete", "partial", "insufficient"]
    units: tuple[EvidenceUnitD, ...]
    blocked_units: tuple[EvidenceUnitD, ...] = ()
    missing_facets: tuple[str, ...] = ()
    evidence_hash: str = ""

    @property
    def supporting(self) -> tuple[EvidenceUnitD, ...]:
        return tuple(unit for unit in self.units if unit.use == "supporting")

    @property
    def context_only(self) -> tuple[EvidenceUnitD, ...]:
        return tuple(unit for unit in self.units if unit.use == "context_only")

    @property
    def may_go_to_llm(self) -> bool:
        # Partial packets are safe when at least one issue/facet has support;
        # the missing facet is carried into the prompt and final status.
        return bool(self.supporting)

    def to_payload(self, *, include_content: bool = True) -> dict[str, Any]:
        return {
            "version": PHASE_D_VERSION,
            "issue_id": self.issue_id,
            "role": self.role,
            "as_of": self.as_of,
            "release_id": self.release_id,
            "acl_scope_hash": self.acl_scope_hash,
            "status": self.status,
            "missing_facets": list(self.missing_facets),
            "evidence_hash": self.evidence_hash,
            "may_go_to_llm": self.may_go_to_llm,
            "units": [unit.to_payload(include_content=include_content) for unit in self.units],
            "blocked_units": [
                unit.to_payload(include_content=False) for unit in self.blocked_units
            ],
        }


def classify_evidence_row(
    row: Mapping[str, Any],
    *,
    temporal_scope: str = "current",
) -> tuple[EvidenceUse, str]:
    """Apply only non-negotiable safety gates to one retrieval row."""

    if not _row_content(row):
        return "blocked", "empty_content"
    if _explicit_false(row, ("acl_allowed", "authorized", "allowed_by_acl")):
        return "blocked", "acl_denied"
    if _explicit_false(row, ("approved", "reviewed", "serving_allowed")):
        return "blocked", "not_approved"
    if _explicit_false(row, ("integrity_ok", "source_integrity_ok")):
        return "blocked", "integrity_failed"

    serving_state = _status(
        row.get("document_serving_state") or row.get("document_status")
    )
    if serving_state in _HARD_STATES:
        return "blocked", f"serving_state_{serving_state}"

    # A historical/replaced row may still carry the source document's old
    # `effective_status=active`.  Its serving state is authoritative for the
    # current scope, so never promote it to present-day legal proof merely
    # because a legacy status field says active.
    if serving_state in _HISTORICAL_STATES:
        if _status(temporal_scope) in {"historical", "historical_only"}:
            return "supporting", "historical_scope"
        return "context_only", "historical_context_only"

    effective = _status(row.get("effective_status") or row.get("validity_status"))
    if effective in _EXPIRED_STATES:
        if _status(temporal_scope) in _HISTORICAL_STATES or _status(temporal_scope) == "historical":
            return "supporting", "historical_scope"
        return "blocked", f"effectivity_{effective}"
    if _status(row.get("verification_level") or row.get("citation_level")) == "metadata_only":
        return "context_only", "metadata_only"
    if effective in _ACTIVE_STATES or serving_state in _ACTIVE_STATES or not effective:
        return "supporting", "eligible"
    # Unknown status is retained for interpretation but not silently promoted
    # to legal proof. This avoids false refusal while keeping the verifier in
    # charge of final legal claims.
    return "context_only", "effectivity_unknown"


def build_evidence_packet_d(
    rows: Sequence[Mapping[str, Any]],
    *,
    issue_id: str,
    role: str = "citizen",
    as_of: str = "",
    release_id: str = "",
    acl_scope: str = "public",
    temporal_scope: str = "current",
    missing_facets: Sequence[str] = (),
) -> EvidencePacketD:
    """Build a stable, issue-bound packet without over-filtering relevance."""

    units: list[EvidenceUnitD] = []
    blocked_units: list[EvidenceUnitD] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows, start=1):
        row = dict(raw)
        row_issue = _text(row.get("issue_id")) or issue_id
        if row_issue != issue_id:
            # Cross-issue rows are not useful context for this packet. They
            # remain in the caller's sibling packet instead of being blended.
            continue
        evidence_id = _row_identifier(row, f"row-{index}")
        if evidence_id in seen:
            evidence_id = f"{evidence_id}-{index}"
        seen.add(evidence_id)
        use, reason = classify_evidence_row(row, temporal_scope=temporal_scope)
        unit = EvidenceUnitD(
            evidence_id=evidence_id,
            issue_id=issue_id,
            content=_row_content(row),
            use=use,
            reason_code=reason,
            score=float(row.get("score") or 0.0),
            source_id=_text(row.get("source_id") or row.get("chunk_id") or row.get("id")),
            canonical_chunk_id=_text(row.get("canonical_chunk_id")),
            chunk_id=_text(row.get("chunk_id")),
            document_title=_text(row.get("document_title")),
            domain=_text(row.get("domain")),
            domain_slug=_text(row.get("domain_slug")),
            scope=_text(row.get("scope")),
            official=bool(row.get("official")),
            official_level=_text(row.get("official_level")),
            issuing_agency=_text(row.get("issuing_agency")),
            document_status=_text(row.get("document_status")),
            effective_article_status=_text(
                row.get("effective_article_status") or row.get("article_status")
            ),
            legal_as_of=_text(row.get("legal_as_of")),
            article_title=_text(row.get("article_title")),
            authority_level=_text(row.get("authority_level")),
            authority_label=_text(row.get("authority_label")),
            law_number=_text(row.get("law_number")),
            article_number=_text(row.get("article_number")),
            clause_number=_text(row.get("clause_number")),
            point_number=_text(row.get("point_number")),
            document_id=_text(row.get("document_id")),
            source_url=_text(row.get("source_url")),
            effective_status=_text(row.get("effective_status") or row.get("validity_status")),
            document_serving_state=_text(row.get("document_serving_state") or row.get("document_status")),
            verification_level=_text(row.get("verification_level") or row.get("citation_level")) or "content_quote",
            release_id=_text(row.get("release_id") or release_id),
            request_id=_text(row.get("request_id")),
            exact_article_packet_ref=_text(row.get("exact_article_packet_ref")),
            exact_article_packet_status=_text(row.get("exact_article_packet_status")),
            exact_article_serving_mode=_text(row.get("exact_article_serving_mode")),
            exact_article_full_article_character_count=(
                int(row.get("exact_article_full_article_character_count") or 0)
                if str(row.get("exact_article_full_article_character_count") or "0").strip().isdigit()
                else 0
            ),
            exact_article_assembled_content=_text(
                row.get("exact_article_assembled_content")
            ),
            replacement_of=_text(row.get("replacement_of")),
            replaced_by=_text(row.get("replaced_by")),
        )
        if use == "blocked":
            blocked_units.append(unit)
        else:
            units.append(unit)

    support = [unit for unit in units if unit.use == "supporting"]
    normalized_missing = tuple(dict.fromkeys(_text(item).casefold() for item in missing_facets if _text(item)))
    status: Literal["complete", "partial", "insufficient"]
    if not support:
        status = "insufficient"
    elif normalized_missing:
        status = "partial"
    else:
        status = "complete"
    resolved_release = _text(release_id) or next((unit.release_id for unit in units if unit.release_id), "")
    acl_hash = _hash_payload({"role": _text(role).casefold() or "citizen", "scope": _text(acl_scope) or "public"})
    hash_rows = [
        {
            "id": unit.evidence_id,
            "use": unit.use,
            "reason": unit.reason_code,
            "issue": unit.issue_id,
            "release": unit.release_id or resolved_release,
            "passage": _hash_payload(unit.content),
            "law": unit.law_number,
            "article": unit.article_number,
        }
        for unit in (*units, *blocked_units)
    ]
    evidence_hash = _hash_payload(
        {
            "version": PHASE_D_VERSION,
            "issue_id": issue_id,
            "as_of": _text(as_of),
            "acl_scope_hash": acl_hash,
            "rows": hash_rows,
            "missing_facets": list(normalized_missing),
        }
    )
    return EvidencePacketD(
        issue_id=issue_id,
        role=_text(role).casefold() or "citizen",
        as_of=_text(as_of),
        release_id=resolved_release,
        acl_scope_hash=acl_hash,
        status=status,
        units=tuple(units),
        blocked_units=tuple(blocked_units),
        missing_facets=normalized_missing,
        evidence_hash=evidence_hash,
    )


def build_d_context(
    packets: Sequence[EvidencePacketD],
    *,
    max_chars: int = 16_000,
    full_units_per_issue: int = 16,
    context_units_per_issue: int = 24,
) -> tuple[str, dict[str, EvidenceUnitD]]:
    """Pack broad eligible evidence while keeping blocked rows out entirely."""

    selected: dict[str, EvidenceUnitD] = {}
    blocks: list[str] = [
        "PHASE D EVIDENCE PACKET",
        "Chỉ dùng supporting để tạo kết luận. context_only chỉ được dùng mô tả bối cảnh; không dùng làm căn cứ pháp lý.",
    ]
    for packet in packets:
        support = sorted(packet.supporting, key=lambda item: item.score, reverse=True)[:full_units_per_issue]
        context_only = sorted(packet.context_only, key=lambda item: item.score, reverse=True)[:context_units_per_issue]
        blocks.append(
            f"ISSUE {packet.issue_id} status={packet.status} missing_facets={','.join(packet.missing_facets) or 'none'} release={packet.release_id or 'unknown'}"
        )
        for unit in (*support, *context_only):
            selected[unit.evidence_id] = unit
            content = unit.content if unit.use == "supporting" else unit.content[:360]
            header = (
                f"[{unit.evidence_id} use={unit.use} issue={unit.issue_id} "
                f"law={unit.law_number or 'unknown'} article={unit.article_number or 'unknown'} "
                f"status={unit.effective_status or unit.document_serving_state or 'unknown'}]"
            )
            blocks.append(f"{header}\n{content}")
    context = "\n\n".join(blocks)
    if len(context) > max_chars:
        context = context[:max_chars].rsplit("\n\n", 1)[0]
    return context, selected


class DClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=96)
    issue_id: str = Field(min_length=1, max_length=96)
    claim_text: str = Field(min_length=1, max_length=1200)
    claim_type: str = Field(min_length=1, max_length=64)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    support_quote: str = Field(min_length=1, max_length=1200)
    facet: str | None = Field(default=None, max_length=64)


class DIssueAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    issue_id: str = Field(min_length=1, max_length=96)
    claims: list[DClaim] = Field(default_factory=list, max_length=20)
    guidance: str | None = Field(default=None, max_length=2000)
    clarifying_question: str | None = Field(default=None, max_length=500)


class DAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[DIssueAnswer] = Field(default_factory=list, max_length=8)


@dataclass(frozen=True)
class DVerificationResult:
    answer_status: Literal["verified", "partial", "cannot_verify"]
    accepted_claims: tuple[DClaim, ...]
    rejected_claims: tuple[dict[str, Any], ...]
    missing_facets: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": PHASE_D_VERSION,
            "answer_status": self.answer_status,
            "accepted_claims": [claim.model_dump() for claim in self.accepted_claims],
            "rejected_claims": list(self.rejected_claims),
            "missing_facets": list(self.missing_facets),
            "unsupported_claim_count": len(self.rejected_claims),
        }


def parse_d_answer(raw: Any) -> DAnswer:
    text = _text(raw)
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value = json.loads(text)
        return DAnswer.model_validate(value)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ValueError("phase_d_structured_output_invalid") from exc


def parse_d_answer_resilient(
    raw: Any,
    packets: Sequence[EvidencePacketD],
) -> tuple[DAnswer, list[dict[str, Any]]]:
    """Adapt the shared web resilient parser to the D verifier schema.

    Parsing, JSON recovery, alias lookup and bounded quote recovery live in
    ``parse_structured_answer_resilient``.  This adapter only converts the
    validated shared claims into ``DClaim`` objects and canonicalizes IDs for
    the packet verifier, preventing benchmark/web parser drift.
    """

    evidence_rows = {
        unit.evidence_id: unit.to_payload()
        for packet in packets
        for unit in packet.units
    }
    structured, rejected = parse_structured_answer_resilient(
        raw,
        evidence_by_id=evidence_rows,
    )
    aliases: dict[str, str] = {}
    for packet in packets:
        for unit in packet.units:
            canonical = unit.evidence_id
            for alias in (
                unit.evidence_id,
                unit.source_id,
                unit.canonical_chunk_id,
                unit.chunk_id,
            ):
                normalized = _text(alias)
                if normalized:
                    aliases.setdefault(normalized, canonical)

    parsed_issues: list[DIssueAnswer] = []
    for issue in structured.issues:
        claims: list[DClaim] = []
        for claim_index, claim in enumerate(issue.claims):
            evidence_id = aliases.get(_text(claim.evidence_id), _text(claim.evidence_id))
            try:
                claims.append(
                    DClaim(
                        claim_id=f"{issue.issue_id}:{claim_index}",
                        issue_id=issue.issue_id,
                        claim_text=claim.claim_text,
                        claim_type=claim.claim_type,
                        evidence_ids=[evidence_id],
                        support_quote=claim.support_quote,
                        facet=claim.facet,
                    )
                )
            except (ValidationError, TypeError, ValueError) as exc:
                rejected.append(
                    {
                        "scope": "claim",
                        "issue_id": issue.issue_id,
                        "index": claim_index,
                        "reason": "claim_validation_failed",
                        "error_type": exc.__class__.__name__,
                    }
                )
        parsed_issues.append(
            DIssueAnswer(
                issue_id=issue.issue_id,
                claims=claims,
                guidance=issue.guidance,
                clarifying_question=issue.clarifying_question,
            )
        )
    if not parsed_issues:
        raise ValueError("phase_d_structured_output_invalid")
    return DAnswer(issues=parsed_issues), rejected


def _quote_in_content(quote: str, content: str) -> bool:
    quote_folded = _fold(quote)
    content_folded = _fold(content)
    return len(quote_folded) >= 6 and quote_folded in content_folded


def _claim_supported_by_quote(claim_text: str, quote: str) -> bool:
    """Require the displayed proposition to be present in its quoted proof.

    Checking only ``support_quote in evidence`` is insufficient: a provider
    can quote a packet header/instruction and attach an unrelated fluent
    conclusion.  A conservative material-token subset still permits harmless
    wording changes (for example, dropping ``Cơ quan``) while rejecting that
    class of unsupported claim.
    """

    claim_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", _fold(claim_text))
        if len(token) >= 2
        and token
        not in {
            "va", "la", "cua", "cho", "voi", "duoc", "theo",
            "trong", "mot", "cac", "nhung", "nay", "nhu", "sau",
        }
    }
    quote_tokens = set(re.findall(r"[a-z0-9]+", _fold(quote)))
    return bool(claim_tokens) and claim_tokens.issubset(quote_tokens)


def verify_d_answer(
    answer: DAnswer,
    packets: Sequence[EvidencePacketD],
) -> DVerificationResult:
    """Verify every generated claim against supporting evidence only."""

    packet_by_issue = {packet.issue_id: packet for packet in packets}
    evidence_by_id = {
        unit.evidence_id: unit
        for packet in packets
        for unit in packet.units
    }
    accepted: list[DClaim] = []
    rejected: list[dict[str, Any]] = []
    for issue in answer.issues:
        packet = packet_by_issue.get(issue.issue_id)
        for claim in issue.claims:
            reason = ""
            if packet is None:
                reason = "issue_not_in_packet"
            elif claim.issue_id != issue.issue_id:
                reason = "claim_issue_mismatch"
            elif not claim.evidence_ids:
                reason = "claim_without_evidence"
            else:
                units = [evidence_by_id.get(item) for item in claim.evidence_ids]
                if any(unit is None for unit in units):
                    reason = "evidence_id_not_found"
                elif any(unit.issue_id != issue.issue_id for unit in units if unit):
                    reason = "cross_issue_evidence"
                elif any(unit.use != "supporting" for unit in units if unit):
                    reason = "context_only_or_blocked_evidence"
                elif not any(
                    _quote_in_content(claim.support_quote, unit.content)
                    for unit in units
                    if unit
                ):
                    reason = "support_quote_not_found"
                elif not any(
                    _claim_supported_by_quote(claim.claim_text, claim.support_quote)
                    and _quote_in_content(claim.support_quote, unit.content)
                    for unit in units
                    if unit
                ):
                    # The quoted text itself may be an internal packet label
                    # or a provider instruction.  Do not let that text prove
                    # a separate legal conclusion; the claim must be
                    # materially represented in the same bounded quote.
                    reason = "claim_text_not_supported_by_quote"
            if reason:
                rejected.append(
                    {
                        "claim_id": claim.claim_id,
                        "issue_id": claim.issue_id,
                        "reason": reason,
                    }
                )
            else:
                accepted.append(claim)
    missing = tuple(
        dict.fromkeys(
            facet
            for packet in packets
            for facet in packet.missing_facets
        )
    )
    status: Literal["verified", "partial", "cannot_verify"]
    if not accepted:
        status = "cannot_verify"
    elif missing or rejected or any(packet.status == "partial" for packet in packets):
        status = "partial"
    else:
        status = "verified"
    return DVerificationResult(
        answer_status=status,
        accepted_claims=tuple(accepted),
        rejected_claims=tuple(rejected),
        missing_facets=missing,
    )


def render_d_answer(
    result: DVerificationResult,
    packets: Sequence[EvidencePacketD],
    *,
    role: str = "citizen",
) -> tuple[str, list[dict[str, Any]]]:
    """Render only verified claims; citations remain backend-owned metadata."""

    labels = {
        "citizen": "Kết quả tra cứu",
        "officer": "Kết luận chuyên môn",
        "admin": "Kết quả và trạng thái dữ liệu",
    }
    evidence_by_id = {
        unit.evidence_id: unit
        for packet in packets
        for unit in packet.units
    }
    lines: list[str] = [f"## {labels.get(role, labels['citizen'])}"]
    citations: list[dict[str, Any]] = []
    seen_citations: set[tuple[str, str, str]] = set()
    for claim in result.accepted_claims:
        lines.append(f"- {claim.claim_text}")
        for evidence_id in claim.evidence_ids:
            unit = evidence_by_id.get(evidence_id)
            if unit is None:
                continue
            key = (unit.law_number, unit.article_number, unit.source_url)
            if key in seen_citations:
                continue
            seen_citations.add(key)
            citations.append(
                {
                    "evidence_id": unit.evidence_id,
                    "law_number": unit.law_number,
                    "article_number": unit.article_number,
                    "source_url": unit.source_url,
                    "effective_status": unit.effective_status or unit.document_serving_state,
                    "verification_level": unit.verification_level,
                }
            )
    if result.missing_facets:
        lines.append(
            "\nChưa đủ căn cứ để xác minh: "
            + ", ".join(result.missing_facets)
            + "."
        )
    if not result.accepted_claims:
        lines.append("\nChưa đủ căn cứ pháp lý trong nguồn hiện có để kết luận.")
    return "\n".join(lines), citations


def source_only_fallback(packet: EvidencePacketD) -> str:
    """Return a bounded source view without adding legal conclusions."""

    if not packet.supporting:
        return "Chưa đủ căn cứ pháp lý trong nguồn hiện có để kết luận."
    lines = ["Nguồn đã xác minh:"]
    for unit in sorted(packet.supporting, key=lambda item: item.score, reverse=True)[:8]:
        lines.append(f"- {unit.content[:900]}")
    if packet.missing_facets:
        lines.append("Chưa đủ căn cứ để xác minh: " + ", ".join(packet.missing_facets) + ".")
    return "\n".join(lines)


def answer_cache_key(
    *,
    question: str,
    role: str,
    as_of: str,
    evidence_hash: str,
    prompt_version: str,
    model_version: str,
    acl_scope_hash: str,
) -> str:
    return _hash_payload(
        {
            "question": _fold(question),
            "role": _text(role).casefold(),
            "as_of": _text(as_of),
            "evidence_hash": _text(evidence_hash),
            "prompt_version": _text(prompt_version),
            "model_version": _text(model_version),
            "acl_scope_hash": _text(acl_scope_hash),
        }
    )


class PhaseDAnswerCache:
    """Small process-local cache for deterministic evidence-bound answers.

    The cache stores only the provider's structured payload under a hash key;
    the key includes question, role, as-of date, evidence release/hash, ACL
    scope, prompt version and model version.  It is intentionally bounded and
    can be disabled with a zero TTL, so a deployment can use a shared cache
    later without changing the answer contract.
    """

    def __init__(self, *, ttl_seconds: float = 300.0, max_entries: int = 256):
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._items: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "PhaseDAnswerCache":
        values = environ or {}
        try:
            ttl = float(values.get("LEGAL_ANSWER_D_CACHE_TTL_SECONDS", "300"))
        except (TypeError, ValueError):
            ttl = 300.0
        try:
            max_entries = int(values.get("LEGAL_ANSWER_D_CACHE_MAX_ENTRIES", "256"))
        except (TypeError, ValueError):
            max_entries = 256
        return cls(ttl_seconds=ttl, max_entries=max_entries)

    def get(self, key: str) -> str | None:
        if self.ttl_seconds <= 0:
            self.misses += 1
            return None
        item = self._items.get(str(key))
        if item is None:
            self.misses += 1
            return None
        expires_at, value = item
        if expires_at <= time.monotonic():
            self._items.pop(str(key), None)
            self.misses += 1
            return None
        self._items.move_to_end(str(key))
        self.hits += 1
        return value

    def put(self, key: str, value: str) -> None:
        if self.ttl_seconds <= 0 or not str(value).strip():
            return
        cache_key = str(key)
        self._items[cache_key] = (time.monotonic() + self.ttl_seconds, str(value))
        self._items.move_to_end(cache_key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def stats(self) -> dict[str, int | float]:
        return {
            "entries": len(self._items),
            "max_entries": self.max_entries,
            "ttl_seconds": self.ttl_seconds,
            "hits": self.hits,
            "misses": self.misses,
        }


__all__ = [
    "DAnswer",
    "DClaim",
    "DIssueAnswer",
    "DVerificationResult",
    "EvidencePacketD",
    "EvidenceUnitD",
    "PHASE_D_VERSION",
    "PhaseDAnswerCache",
    "answer_cache_key",
    "build_d_context",
    "build_evidence_packet_d",
    "classify_evidence_row",
    "parse_d_answer",
    "parse_d_answer_resilient",
    "render_d_answer",
    "source_only_fallback",
    "verify_d_answer",
]
