"""The single provider-neutral Direct RAG answer service.

The router owns classification and R28 owns source eligibility.  This module
performs the remaining serving work: one batch retrieval, one bounded
:class:`EvidencePacket`, one answer-model call by default, an optional single
evidence-bounded completion call, and request-local citation checks. Retrieval/model implementations are injected
so the service has a real runtime boundary without owning provider clients or
database connections.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Sequence

from api.answer_depth_profiles import answer_depth_profile
from api.administrative_query_signals import has_administrative_subject
from api.chat_behavior_policy import admin_style_addendum, render_admin_style_addendum
from api.legal_domains import canonicalize_legal_domain
from api.legal_evidence_delivery import (
    merge_ranked_sources,
    observe_delivery,
    pack_whole_sources,
    source_facets,
)
from api.legal_exact_article import (
    is_article_overview_request,
    is_full_article_request,
    is_single_exact_article_plan,
)
from api.legal_exact_retrieval import normalize_exact_identifier, plan_exact_lookup
from api.legal_answer_completeness import assess_requested_facets

_CITATION_RE = re.compile(r"\[(?:E|e)(\d{1,3})\]")

# The current reconciled collection predates these two canonical router
# buckets. Their documents are distributed across older slugs (business law
# under public administration; military law across residence and sanctions).
# Keeping the stale domain filter would deterministically return zero rows.
# We retain the canonical domain in the public/router contract and omit only
# the retrieval ranking filter until the corpus is reclassified and reindexed.
_UNFILTERED_LEGACY_CORPUS_DOMAINS = {"kinh_te", "quoc_phong_quan_su"}


def is_full_document_request(question: Any) -> bool:
    """Return true only for an explicit whole-instrument overview request."""

    text = str(question or "").replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    folded = "".join(char for char in text if unicodedata.category(char) != "Mn")
    folded = re.sub(r"\s+", " ", folded).strip().casefold()
    return bool(
        re.search(
            r"\b(?:toan bo|toan van|tong quan|tom tat|noi dung chinh|"
            r"quy dinh(?:\s+nhung)?\s+gi|noi dung gi)\b",
            folded,
        )
    )


@dataclass(frozen=True)
class CitationCheck:
    """Result of the one basic citation check after generation."""

    valid: bool
    cited_ids: tuple[str, ...] = ()
    unknown_ids: tuple[str, ...] = ()
    reason_code: str = "NONE"


@dataclass(frozen=True)
class GenerationOutput:
    """Normalized text plus optional provider completion metadata."""

    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectRagRequest:
    """Authoritative inputs supplied after Unified Router V1 has run."""

    request_id: str
    question: str
    role: str
    legal_as_of: str
    router_decision: Mapping[str, Any]
    provider_question: str | None = None
    issues: Sequence[Any] = ()
    conversation_context: str = ""
    # A bounded, backend-owned prior user question used only to make a
    # deictic legal continuation searchable.  It is never presented as a
    # second router decision and never replaces the current question in the
    # answer prompt.
    retrieval_context: str = ""
    active_document: Mapping[str, Any] | None = None
    organization_unit_id: str | None = None
    organization_routing_mode: str = "legacy"
    as_of_explicit: bool = False
    router_latency_ms: float = 0.0
    include_trace: bool = False
    system_prompt_addendum: str = ""
    prompt_variant: str = "strict-v20"
    prompt_revision: int = 1
    supplemental_evidence: Sequence[Mapping[str, Any]] = ()
    # Compatibility callers may still exercise one bounded facet recovery;
    # the public serving path explicitly passes False so indexed chat never
    # pays a hidden second retrieval.
    allow_faceted_recovery: bool = True
    # Serving may opt into one evidence-bounded completion pass. Compatibility
    # callers retain the historical single-call contract unless they ask for it.
    allow_completion_pass: bool = False
    # Application budget, not a claim about a provider's advertised window.
    context_token_budget: int = 32768
    prompt_suffix: str = ""
    answer_depth: str = "balanced"
    communication_preferences: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidencePacket:
    """The only evidence representation crossing the provider boundary."""

    request_id: str
    release_id: str | None
    manifest_hash: str | None
    legal_as_of: str
    router_decision: Mapping[str, Any]
    evidence_by_id: Mapping[str, Mapping[str, Any]]
    context: str
    scope: str
    missing_issue_ids: tuple[str, ...] = ()
    coverage: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DirectAnswerResult:
    """Complete Direct RAG result before API compatibility projection."""

    answer: str
    citations: tuple[dict[str, Any], ...]
    outcome: str
    reason_code: str
    retryable: bool
    scope: str
    release_id: str | None
    manifest_hash: str | None
    legal_as_of: str
    timing: Mapping[str, float | str | bool | None]
    evidence_rows: tuple[dict[str, Any], ...] = ()
    evidence_by_id: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    provider_error_code: str | None = None
    grounding_status: str = "source_view_only"
    trace: Mapping[str, Any] = field(default_factory=dict)


RetrieveBatch = Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]]
GenerateAnswer = Callable[..., Awaitable[str]]
ProjectCitation = Callable[[Mapping[str, Any]], Mapping[str, Any]]
EmitProgress = Callable[[str, dict[str, Any]], Awaitable[None]]


def _evidence_number(key: str, row: Mapping[str, Any], fallback: int) -> str:
    """Return the stable public E-number for a packet item."""

    match = re.fullmatch(r"evidence-(\d+)", str(key or "").strip(), re.I)
    if match:
        return f"E{int(match.group(1))}"
    value = str(row.get("direct_evidence_id") or row.get("evidence_id") or "").strip()
    match = re.fullmatch(r"E?(\d+)", value, re.I)
    if match:
        return f"E{int(match.group(1))}"
    return f"E{fallback}"


def packet_id_map(evidence_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Map model-facing ``E#`` markers to the current packet's context keys."""

    result: dict[str, str] = {}
    for index, (key, row) in enumerate(evidence_by_id.items(), start=1):
        public_id = _evidence_number(str(key), row, index)
        result[public_id] = str(key)
    return result


def render_packet_context(
    context: str,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    """Replace opaque context headers with the short ``E#`` vocabulary.

    The retrieval adapter remains the source of bounded excerpts and
    exact-article handling. Only its provider-facing identifier is changed;
    no legal text or metadata is rewritten.
    """

    mapping = packet_id_map(evidence_by_id)
    rendered = str(context or "")
    for public_id, context_id in mapping.items():
        rendered = re.sub(
            rf"(?<=\[){re.escape(context_id)}(?=\s*[|\]])",
            public_id,
            rendered,
            flags=re.I,
        )
    return rendered


def validate_citations(
    answer: str,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> CitationCheck:
    """Validate only marker existence and request-local packet membership.

    This is intentionally not a semantic claim verifier.  If a provider does
    not bind its legal answer to the packet, the caller must return a bounded
    source-only response instead of spending a repair/model call.
    """

    text = str(answer or "").strip()
    # The Direct RAG boundary is a Markdown answer, not a transport envelope.
    # A provider occasionally ignores the instruction and returns JSON or a
    # fenced payload containing a perfectly valid-looking ``[E1]`` marker.
    # Treat that as invalid rather than leaking an adapter envelope to the UI
    # or accepting a marker that was never rendered as an answer citation.
    if "```" in text:
        return CitationCheck(False, (), (), "OUTPUT_NOT_MARKDOWN")
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return CitationCheck(False, (), (), "OUTPUT_NOT_MARKDOWN")

    packet_ids = packet_id_map(evidence_by_id)
    found = tuple(dict.fromkeys(f"E{int(value)}" for value in _CITATION_RE.findall(str(answer or ""))))
    unknown = tuple(item for item in found if item not in packet_ids)
    if not found:
        return CitationCheck(False, (), (), "CITATION_MISSING")
    if unknown:
        return CitationCheck(False, found, unknown, "CITATION_OUTSIDE_PACKET")
    return CitationCheck(True, found, (), "NONE")


def normalize_provider_answer(
    answer: str,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> tuple[str, tuple[str, ...]]:
    """Normalize provider-declared output without inventing a legal source.

    Providers vary in transport wrappers and citation punctuation.  This
    adapter may unwrap an explicit ``answer`` field, normalize a declared E-id,
    or bind an exact, unique law number already named by the model.  It never
    chooses a source from topical similarity and never adds a source identity
    that the model did not mention.
    """

    text = str(answer or "").strip()
    applied: list[str] = []
    if not text:
        return text, ()

    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, Mapping) and isinstance(payload.get("answer"), str):
            candidate = str(payload["answer"]).strip()
            if candidate:
                text = candidate
                applied.append("json_answer_unwrapped")

    fence = re.fullmatch(
        r"```(?:markdown|md|text)?\s*\n(?P<body>[\s\S]*?)\n```",
        text,
        flags=re.I,
    )
    if fence:
        text = fence.group("body").strip()
        applied.append("markdown_fence_unwrapped")

    # A fenced example inside otherwise valid Markdown is a presentation
    # mistake, not a reason to discard every grounded paragraph. Remove only
    # the fence delimiters; citation membership is still validated below.
    if "```" in text:
        unfenced = re.sub(r"```(?:[a-zA-Z0-9_+.-]+)?[ \t]*(?:\r?\n)?", "", text)
        if unfenced != text:
            text = unfenced.strip()
            applied.append("embedded_code_fence_removed")

    packet_ids = packet_id_map(evidence_by_id)
    for public_id in packet_ids:
        number = public_id[1:]
        normalized = re.sub(
            rf"(?:\[\s*E\s*{number}\s*\]|[（(【]\s*E\s*{number}\s*[）)】])",
            f"[{public_id}]",
            text,
            flags=re.I,
        )
        if normalized != text:
            text = normalized
            applied.append("citation_punctuation_normalized")

    # A provider may cite the official instrument by exact number instead of
    # repeating the transport-local E-id. Bind only identities that are unique
    # in this packet; duplicate chunks/articles remain deliberately ambiguous.
    identities: dict[str, list[str]] = {}
    for public_id, context_id in packet_ids.items():
        row = evidence_by_id.get(context_id) or {}
        law_number = " ".join(str(row.get("law_number") or "").split())
        if len(law_number) >= 5:
            identities.setdefault(law_number.casefold(), []).append(public_id)
    existing = set(
        f"E{int(value)}" for value in _CITATION_RE.findall(text)
    )
    for folded_number, public_ids in identities.items():
        if len(public_ids) != 1 or public_ids[0] in existing:
            continue
        public_id = public_ids[0]
        row_number = next(
            (
                " ".join(
                    str((evidence_by_id.get(context_id) or {}).get("law_number") or "").split()
                )
                for candidate, context_id in packet_ids.items()
                if candidate == public_id
            ),
            "",
        )
        if not row_number or folded_number not in text.casefold():
            continue
        text, count = re.subn(
            re.escape(row_number),
            lambda match: f"{match.group(0)} [{public_id}]",
            text,
            count=1,
            flags=re.I,
        )
        if count:
            existing.add(public_id)
            applied.append("exact_law_identity_bound")

    return text, tuple(dict.fromkeys(applied))


def _question_term_hints(question: str) -> tuple[str, ...]:
    """Expand familiar abbreviations only in deterministic source-only cards."""

    text = " ".join(str(question or "").split()).casefold()
    hints: list[str] = []
    for marker, label in (
        ("gpxd", "giấy phép xây dựng"),
        ("qsdđ", "quyền sử dụng đất"),
        ("kn", "khiếu nại"),
        ("hs", "hồ sơ"),
        ("đổi mục đích", "chuyển mục đích"),
        ("sổ đỏ đứng tên", "chủ sở hữu"),
    ):
        if marker in text and label not in hints:
            hints.append(label)
    return tuple(hints)


def build_prompt(
    *,
    question: str,
    role: str,
    context: str,
    conversation_context: str = "",
    route: str = "legal_query",
    historical: bool = False,
    max_history_chars: int = 4000,
    max_evidence_chars: int = 8000,
    packet_coverage: Mapping[str, Any] | None = None,
    system_prompt_addendum: str = "",
    prompt_variant: str = "strict-v20",
    requested_facets: Sequence[str] = (),
    structured_output: bool = False,
    answer_depth: str = "balanced",
    communication_preferences: Mapping[str, str] | None = None,
) -> str:
    from api.chat_prompt import build_answer_prompt
    return build_answer_prompt(
        question=str(question or "").strip()[:4000], role=role,
        sources=str(context or "").strip()[:max(0, int(max_evidence_chars))],
        history=str(conversation_context or "").strip()[:max(0, int(max_history_chars))],
        style=system_prompt_addendum, preferences=communication_preferences,
        depth=answer_depth, historical=historical, structured=structured_output,
        whole_document_digest=bool(
            (packet_coverage or {}).get("whole_document_digest")
        ),
        whole_document_digest_complete=bool(
            (packet_coverage or {}).get("whole_document_digest_complete")
        ),
    )


def cited_rows(
    check: CitationCheck,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return backend-owned rows cited by a valid answer, preserving rank."""

    mapping = packet_id_map(evidence_by_id)
    cited = set(check.cited_ids)
    return [dict(row) for public_id, context_id in mapping.items() if public_id in cited for row in [evidence_by_id[context_id]]]


def _declared_evidence_gap(answer: str) -> str | None:
    """Keep an explicit provider abstention out of the successful outcome.

    A nonempty retrieval packet and valid E-markers establish source binding,
    not that the packet answers the question. This is a bounded interpretation
    of the provider's own stated limitation, not a semantic claim verifier.
    Keep partial answers intact and distinguish a whole-question refusal from
    a missing local facet. Ordinary legal conditions ("chưa đủ 18 tuổi") are
    not evidence limitations.
    """

    # Quoted source language is not the provider's own disposition. Remove
    # Markdown blockquotes and inline quotes before matching refusal language.
    text = re.sub(r"(?m)^\s*>[^\n]*", " ", str(answer or ""))
    for quoted in (r'"[^"\n]*"', r"“[^”]*”", r"‘[^’]*’", r"'[^'\n]*'", r"`[^`]*`"):
        text = re.sub(quoted, " ", text)
    text = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
        if not unicodedata.combining(char)
    )
    text = " ".join(text.split())
    no_basis = (
        r"(?:chua|khong)\s+(?:(?:co\s+)?du|co)\s+"
        r"(?:co so|can cu|thong tin|bang chung|du lieu)(?:\s+phap ly)?"
    )
    if re.search(
        no_basis
        + r"\s+(?:de\s+)?tra loi\s+(?:(?:truc tiep|chinh xac)\s+)?"
        + r"(?:cau hoi|yeu cau)(?:\s|[:.,!?]|$)",
        text,
    ):
        return "whole_question"
    if re.search(
        r"(?:chua|khong)\s+the\s+tra loi\s+truc tiep\s+(?:cau hoi|yeu cau)",
        text,
    ):
        return "whole_question"
    # "Không có căn cứ để thu khoản phí này" can itself be a supported
    # negative legal conclusion. Treat absence wording as an evidence gap
    # only when the provider scopes it to its available sources/knowledge.
    partial_shortage = (
        r"(?:chua|khong)\s+(?:co\s+)?du\s+"
        r"(?:co so|can cu|thong tin|bang chung|du lieu)"
    )
    if re.search(partial_shortage, text) or any(
        marker in text
        for marker in (
            "nguon hien co chua",
            "nguon hien co khong",
            "tai lieu hien co chua",
            "tai lieu hien co khong",
            "chua co nguon",
            "chi ho tro mot phan",
            "chi ho tro van de",
        )
    ):
        return "partial"
    return None


def build_source_only_answer(
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    *,
    question: str | None = None,
    max_units: int = 10,
    max_chars: int = 6000,
) -> str:
    """Render a neutral source viewer message for a non-answer outcome."""

    question_line = ""
    compact_question = " ".join(str(question or "").split())[:300]
    # Preserve the user's wording while expanding common citizen shorthand
    # in the source-only card.  This is presentation-only (it does not create
    # a legal assertion) and makes the fallback understandable/searchable.
    for abbreviation, expanded in (
        ("GPXD", "giấy phép xây dựng"),
        ("QSDĐ", "quyền sử dụng đất"),
        ("KN", "khiếu nại"),
        ("HS", "hồ sơ"),
    ):
        compact_question = re.sub(
            rf"(?<![\wÀ-ỹĐđ]){re.escape(abbreviation)}(?![\wÀ-ỹĐđ])",
            expanded,
            compact_question,
            flags=re.IGNORECASE,
        )
    hints = _question_term_hints(compact_question)
    hint_line = (
        "\nThuật ngữ cần đối chiếu: " + ", ".join(hints) + ".\n"
        if hints
        else ""
    )
    if compact_question:
        question_line = f"\nCâu hỏi đang tra cứu: **{compact_question}**\n"
    if not evidence_by_id:
        exact_lookup = plan_exact_lookup(compact_question)
        if exact_lookup.law_number:
            return (
                f"Hiện không tìm thấy văn bản **{exact_lookup.law_number}** trong kho "
                "đang được phép tra cứu. Văn bản có thể chưa được duyệt/lập chỉ mục, "
                "đã bị thu hồi khỏi kho hoặc đã bị xóa. Tôi chưa thể trả lời nội dung "
                "của văn bản này khi không có nguồn trong kho."
            )
        return question_line + "Chưa tìm thấy nguồn pháp luật đủ điều kiện cho câu hỏi này. Anh/chị hãy thu hẹp câu hỏi hoặc nêu rõ số hiệu văn bản/Điều cần tra cứu."
    blocks: list[str] = []
    consumed = 0
    for index, row in enumerate(list(evidence_by_id.values())[: max(1, int(max_units))], start=1):
        content = " ".join(
            str(
                row.get("evidence_capsule")
                or row.get("clean_content")
                or row.get("content")
                or ""
            ).split()
        )
        label = " — ".join(
            value
            for value in (
                str(row.get("document_title") or row.get("law_number") or "Nguồn pháp luật").strip(),
                str(row.get("law_number") or "").strip(),
                f"Điều {str(row.get('article_number') or '').strip()}" if row.get("article_number") else "",
            )
            if value
        )
        url = str(row.get("source_url") or row.get("url") or "").strip()
        suffix = f" — [Mở nguồn]({url})" if url else ""
        # A full-text request that exceeds the answer context deliberately
        # carries metadata only.  Keep the official viewer link useful without
        # pretending that metadata itself is legal evidence.
        block = (
            f"**{index}. {label or 'Nguồn đã truy xuất'}**{suffix}"
            + (f"\n> {content}" if content else "\n> Mở trình xem để đọc toàn văn.")
        )
        if consumed + len(block) + 2 > max_chars:
            break
        blocks.append(block)
        consumed += len(block) + 2
    if not blocks:
        return "Đã tìm thấy nguồn nhưng chưa thể hiển thị phần trích dẫn. Anh/chị hãy mở nguồn chính thức để đối chiếu."
    return (
        question_line
        + hint_line
        + "Đã tìm thấy nguồn nhưng chưa thể tạo kết luận pháp lý đã kiểm chứng từ lượt này. "
        "Các nguồn dưới đây chỉ để đối chiếu:\n\n" + "\n\n".join(blocks)
    )


def build_effectivity_source_answer(
    active_document: Mapping[str, Any],
    *,
    question: str | None = None,
) -> str:
    """Report only the indexed effectivity status of an already bound source.

    This is intentionally metadata-only: an unknown/partial registry status
    is never upgraded to a legal conclusion and the official URL is retained
    for the user to verify.
    """

    title = str(
        active_document.get("title")
        or active_document.get("document_title")
        or active_document.get("law_number")
        or "văn bản đang trao đổi"
    ).strip()
    law_number = str(active_document.get("law_number") or "").strip()
    validity = active_document.get("validity_sync")
    validity = validity if isinstance(validity, Mapping) else {}
    status_label = str(
        validity.get("display_label")
        or active_document.get("effective_status")
        or "chưa xác minh hiệu lực"
    ).strip()
    url = str(
        validity.get("source_url")
        or active_document.get("source_url")
        or active_document.get("viewer_url")
        or ""
    ).strip()
    question_text = " ".join(str(question or "").split())[:300]
    prefix = f"Câu hỏi đang tra cứu: **{question_text}**\n" if question_text else ""
    label = f"{title} ({law_number})" if law_number and law_number not in title else title
    source = f"\n\n[Mở nguồn chính thức]({url})" if url else ""
    return (
        prefix
        + f"Nguồn đang được đối chiếu: **{label}**. "
        + f"Chỉ số hiệu lực hiện có ghi nhận: **{status_label}**. "
        + "Đây là trạng thái metadata của hệ thống, chưa thay thế việc đối chiếu văn bản chính thức."
        + source
    )


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


_FACET_QUERY_TERMS = {
    "rule": "quy định pháp luật áp dụng",
    "procedure": "trình tự thủ tục bước thực hiện",
    "documents": "hồ sơ giấy tờ cần nộp",
    "authority": "thẩm quyền cơ quan nơi nộp",
    "condition": "điều kiện trường hợp áp dụng",
    "deadline": "thời hạn giải quyết",
    "fee": "lệ phí mức thu miễn giảm",
    "next_action": "hành động tiếp theo",
    "form": "biểu mẫu tờ khai chính thức",
    "legal_basis": "căn cứ pháp lý điều khoản",
    "verification": "xác minh đối chiếu thông tin",
}


def _semantic_queries(
    issue_id: str,
    query: str,
    facets: Sequence[str],
    *,
    subject_anchor: str | None = None,
    fact_anchors: Sequence[str] = (),
    max_queries: int = 3,
) -> list[dict[str, Any]]:
    """Create a bounded deterministic query set inside one R28 batch.

    This preserves the Direct RAG invariant of one retrieval request while
    retaining the useful facet recall that the historical 50-question run
    relied on.  No query is generated by a model and no second retrieval
    endpoint is attempted.
    """

    base = " ".join(str(query or "").split())[:1800]
    queries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(text: str, query_type: str) -> None:
        normalized = " ".join(text.casefold().split())
        if not normalized or normalized in seen or len(queries) >= max_queries:
            return
        seen.add(normalized)
        queries.append(
            {
                "query_id": f"{issue_id}-q{len(queries) + 1}",
                "query_type": query_type,
                "query": text[:2000],
                "weight": 1.0 if not queries else 0.85,
            }
        )

    # R28's lexical side is intentionally strongest when it receives the
    # legal nouns rather than a full conversational sentence.  Keep this
    # reduction deterministic and local: it is a retrieval hint, not a second
    # classifier or a model-generated rewrite.  The original question remains
    # the first query so no user wording is lost.
    stopwords = {
        "anh", "chị", "tôi", "mình", "muốn", "đang", "đã", "sẽ", "có",
        "thì", "là", "và", "hay", "hoặc", "nào", "gì", "như", "thế",
        "này", "đó", "vừa", "một", "hai", "ba", "cho", "ở", "tại", "với",
        "theo", "trong", "ngoài", "được", "phải", "không", "bằng", "khi",
        "nếu", "sao", "ra", "sao", "nên", "cần", "xin", "hỏi", "về",
        "trường", "hợp", "nội", "dung", "thứ", "tự", "cơ", "quan", "vừa",
    }

    def compact_terms(text: str) -> str:
        tokens = re.findall(r"[0-9A-Za-zÀ-ỹĐđ]+(?:/[0-9A-Za-zÀ-ỹĐđ]+)?", text)
        kept: list[str] = []
        for token in tokens:
            folded = token.casefold()
            if folded in stopwords or len(folded) < 2:
                continue
            if folded not in {item.casefold() for item in kept}:
                kept.append(token)
        return " ".join(kept[:14])

    folded_base = base.casefold()
    folded_ascii = "".join(
        char
        for char in unicodedata.normalize("NFD", folded_base.replace("đ", "d"))
        if unicodedata.category(char) != "Mn"
    )
    focus_queries: list[str] = []
    if "giấy chứng sinh" in folded_base:
        focus_queries.append("giấy chứng sinh cam đoan khai sinh giấy tờ thay thế")
    if "gpxd" in folded_base or "giấy phép xây dựng" in folded_base:
        focus_queries.append(
            "Điều 89 giấy phép xây dựng nhà ở riêng lẻ nông thôn quy hoạch"
        )
    if "hợp thửa" in folded_base and "mục đích" in folded_base:
        focus_queries.append("trình tự hợp thửa và chuyển mục đích sử dụng đất")
    if "thời hiệu" in folded_base and ("kn" in folded_base or "khiếu nại" in folded_base):
        focus_queries.append(
            "thời hiệu khiếu nại 90 ngày trở ngại khách quan bệnh tật"
        )
    if "qsdđ" in folded_base or "qsdđ" in folded_base.replace(" ", ""):
        focus_queries.append("quyền sử dụng đất thế chấp tách thửa tặng cho")
    if "tạm trú" in folded_base and "lưu trú" in folded_base:
        focus_queries.append("đăng ký tạm trú thông báo lưu trú thời hạn")
    if "điều 20" in folded_base and "cư trú" in folded_base:
        focus_queries.append("Luật Cư trú Điều 20 thường trú chỗ ở hợp pháp")
    if any(term in folded_ascii for term in ("qua doi", "khai tu")) and any(
        term in folded_ascii for term in ("trach nhiem", "bao lau", "thoi han")
    ):
        focus_queries.append(
            "đăng ký khai tử trách nhiệm người thân thích công chức tư pháp "
            "hộ tịch thời hạn"
        )
    if "hoc phi" in folded_ascii and any(
        term in folded_ascii for term in ("mam non", "pho thong", "dai hoc")
    ):
        focus_queries.append(
            "học phí được thu tối đa tháng năm tình trạng khẩn cấp không thu "
            "học phí thời gian không tổ chức dạy học"
        )
    if all(term in folded_ascii for term in ("nguoi lao dong", "luong")) and any(
        term in folded_ascii for term in ("no luong", "tra cham", "cham tra")
    ):
        focus_queries.append(
            "người sử dụng lao động trả lương chậm bồi thường khoản tiền lãi "
            "tiền lương"
        )
    if "nguoi to cao" in folded_ascii and any(
        term in folded_ascii for term in ("bi mat", "danh tinh", "dia chi")
    ):
        focus_queries.append(
            "bảo vệ bí mật họ tên địa chỉ thông tin người tố cáo cơ quan tiếp "
            "nhận xác minh"
        )
    if (
        "khieu nai" in folded_ascii
        and any(term in folded_ascii for term in ("cach phuc vu", "thai do phuc vu"))
    ):
        focus_queries.append(
            "loại đơn đơn kiến nghị phản ánh cách phục vụ không yêu cầu xem "
            "xét lại quyết định hành chính hành vi hành chính"
        )
    if "nha o xa hoi" in folded_ascii and "thu nhap" in folded_ascii:
        focus_queries.append(
            "điều kiện thu nhập mua thuê mua nhà ở xã hội người độc thân "
            "người đã kết hôn xác nhận thu nhập"
        )
    if "bao ve moi truong" in folded_ascii and any(
        term in folded_ascii for term in ("thue", "phi", "muc thu")
    ):
        focus_queries.append(
            "thuế phí bảo vệ môi trường đối tượng hoạt động xả thải khai thác "
            "căn cứ xác định mức thu"
        )
    if "co so du lieu ho tich" in folded_ascii and "khai thac" in folded_ascii:
        focus_queries.append(
            "khai thác cơ sở dữ liệu hộ tịch điện tử bản sao trích lục xác nhận "
            "thông tin cơ quan đăng ký quản lý hộ tịch"
        )
    if "hau qua kho khac phuc" in folded_ascii and "khieu nai" in folded_ascii:
        focus_queries.append(
            "khiếu nại hậu quả khó khắc phục tạm đình chỉ thi hành quyết định "
            "thời hạn gửi hủy bỏ"
        )
    if "giay to nuoc ngoai" in folded_ascii and "quoc tich" in folded_ascii:
        focus_queries.append(
            "giấy tờ nước ngoài hồ sơ quốc tịch hợp pháp hóa lãnh sự miễn "
            "hợp pháp hóa dịch tiếng Việt chứng thực chữ ký người dịch"
        )
    if any(term in folded_ascii for term in ("kiem soat tai san", "kiem soat thu nhap")):
        focus_queries.append(
            "kiểm soát tài sản thu nhập người thân lệ thuộc xử phạt biện pháp "
            "khắc phục trả lại tài sản"
        )
    if "tro choi dien tu cong cong" in folded_ascii and "giay chung nhan" in folded_ascii:
        focus_queries.append(
            "thẩm quyền cấp gia hạn thu hồi giấy chứng nhận đủ điều kiện hoạt động "
            "điểm trò chơi điện tử công cộng"
        )
    # High-confidence natural-policy anchors. These are retrieval hints only:
    # they do not assert an article number, source identity, or legal result.
    # Keeping the distinctive nouns together prevents a broad semantic query
    # from drifting to a neighboring instrument in the same domain.
    if (
        all(term in folded_ascii for term in ("toa an", "vien kiem sat", "kiem toan"))
        and "to cao" in folded_ascii
    ):
        focus_queries.append(
            "trách nhiệm Tòa án Viện kiểm sát Kiểm toán nhà nước "
            "giải quyết tố cáo báo cáo"
        )
    if (
        all(term in folded_ascii for term in ("nhap", "tro lai", "thoi quoc tich"))
        and any(term in folded_ascii for term in ("truoc", "hieu luc", "sua doi"))
    ):
        focus_queries.append(
            "điều khoản chuyển tiếp hồ sơ quốc tịch trước thời điểm "
            "Luật có hiệu lực"
        )
    if (
        all(term in folded_ascii for term in ("hoc sinh", "noi tru", "bien gioi"))
        and any(term in folded_ascii for term in ("chinh sach", "muc huong"))
    ):
        focus_queries.append(
            "mức hưởng chính sách học sinh tiền ăn gạo đồng phục học phẩm"
        )
    if (
        "quy dinh quan ly theo quy hoach" in folded_ascii
        and "phe duyet" in folded_ascii
    ):
        focus_queries.append(
            "quy định quản lý theo quy hoạch cơ quan có thẩm quyền "
            "ban hành sau khi quy hoạch được phê duyệt"
        )
    # A recognized legal anchor is more discriminative than the conversational
    # sentence. Put it first in the same batch so R28's normal rank merge
    # sees the phrase/lexical hit before broad semantic neighbors. The full
    # user wording is still retained when the bounded query budget allows it.
    for focus in focus_queries:
        add(focus, "focus")
    add(base, "semantic")
    # Keep the user's wording and add one complementary procedural view.
    # This stays in the same batch and does not multiply every facet into
    # a separate embedding request.
    requested_terms = [_FACET_QUERY_TERMS[f] for f in facets if f in _FACET_QUERY_TERMS]
    if len(requested_terms) >= 4:
        add(f"{str(subject_anchor or base).strip()}: {'; '.join(requested_terms)}", "facet")
    anchor = " ".join(
        value
        for value in (
            str(subject_anchor or "").strip(),
            *(str(value).strip() for value in fact_anchors if str(value).strip()),
        )
        if value
    )
    compact_anchor = compact_terms(anchor)
    compact_question = compact_terms(base)
    if compact_anchor:
        add(compact_anchor, "anchor")
    if compact_question:
        add(compact_question, "keyword")
    for facet in facets:
        term = _FACET_QUERY_TERMS.get(str(facet).strip(), str(facet).strip())
        if term:
            add(f"{base}, {term}", "facet")
    return queries


def _issue_payloads(request: DirectRagRequest) -> tuple[list[dict[str, Any]], set[str]]:
    """Build one R28 batch body without rewrites or retrieval variants."""

    source_issues = list(request.issues or ())
    if not source_issues:
        source_issues = [
            {
                "issue_id": "issue-1",
                "query_text": request.question,
                "intent": "rule",
                "domain": request.router_decision.get("canonical_domain"),
            }
        ]
    payloads: list[dict[str, Any]] = []
    exact_issue_ids: set[str] = set()
    decision_facets = tuple(
        str(value).strip()
        for value in request.router_decision.get("facets") or ()
        if str(value).strip()
    )
    decision_procedure_id = (
        request.router_decision.get("procedure_candidate")
        or request.router_decision.get("procedure_id")
    )
    route_name = str(request.router_decision.get("legal_route") or "").strip()
    # Some older router fixtures classify a clear procedural question as
    # ``UNKNOWN`` and therefore carry no M4 facets.  Keep the router's one
    # decision authoritative, but fill the retrieval hint deterministically at
    # this boundary so R28 does not receive an unscoped semantic query.  This
    # is not a second route or a model call; it is the stable default facet
    # set for the already selected answer route.
    if not decision_facets:
        if route_name == "procedure_form":
            decision_facets = (
                "procedure",
                "documents",
                "authority",
                "condition",
                "deadline",
                "next_action",
            )
        elif route_name in {"exact_article", "historical"}:
            decision_facets = ("rule",)
        else:
            decision_facets = ("rule", "condition", "authority")
    for position, item in enumerate(source_issues, start=1):
        issue_id = str(_value(item, "issue_id") or f"issue-{position}")
        query = str(
            _value(item, "query_text")
            or _value(item, "query")
            or request.question
        ).strip()
        # Capture identity from the current turn before adding any conversation
        # anchor. Otherwise a new, explicit law number can be contaminated by
        # the previously active document and both identifiers reach retrieval.
        explicit_plan = plan_exact_lookup(query)
        if not explicit_plan.law_numbers and len(source_issues) == 1:
            explicit_plan = plan_exact_lookup(request.question)
        # A named article can omit the law number while the immediately bound
        # backend source already supplies that identity.  Reuse it only when
        # the current question explicitly contains the normalized document
        # title; this turns ``Điều 27 Luật Cư trú`` into an exact lookup without
        # capturing an unrelated article request from an older source.
        if (
            explicit_plan.article_numbers
            and not explicit_plan.law_numbers
            and isinstance(request.active_document, Mapping)
        ):
            active_title = str(
                request.active_document.get("document_title")
                or request.active_document.get("title")
                or ""
            ).strip()
            active_law = str(request.active_document.get("law_number") or "").strip()
            def _fold_identity(value: str) -> str:
                normalized = unicodedata.normalize("NFD", str(value or "").casefold())
                return " ".join(
                    "".join(
                        char
                        for char in normalized
                        if unicodedata.category(char) != "Mn"
                    ).replace("đ", "d").split()
                )
            if (
                active_law
                and active_title
                and _fold_identity(active_title) in _fold_identity(query)
            ):
                query = f"{query} {active_law}"[:2000]
                explicit_plan = plan_exact_lookup(query)
        # Contextual legal continuations ("hai khoản đó", "còn thời hạn?",
        # etc.) need the preceding user subject for lexical retrieval.  Keep
        # this bounded and deterministic; a complete standalone question is
        # left byte-for-byte intact.
        folded_query = query.casefold()
        contextual_markers = (
            "đó", "này", "vừa nêu", "vừa nói", "hai khoản", "phần còn lại",
            "thời hạn giải quyết", "cơ quan đó", "việc đó", "trường hợp đó",
            "bao nhiêu tiền", "mất bao lâu", "đi cùng", "còn phí", "còn lệ phí",
            # Natural continuations seen in real citizen sessions.  These do
            # not carry a searchable legal subject by themselves and must be
            # anchored to the preceding user question.
            "thế bao lâu", "bao lâu", "nộp ở đâu", "nộp chỗ nào",
            "cần giấy tờ gì", "cần hồ sơ gì", "hồ sơ gồm gì", "cần gì",
            "thế còn", "vậy còn", "còn điều kiện", "ai giải quyết",
            "chưa có file mẫu", "nội dung bắt buộc",
        )
        retrieval_anchor = str(request.retrieval_context or "").strip()
        router_domain = str(
            request.router_decision.get("canonical_domain") or ""
        ).strip()
        folded_anchor = retrieval_anchor.casefold()
        cross_field_followup = bool(
            router_domain == "an_sinh_y_te_giao_duc"
            and any(
                marker in folded_query
                for marker in (
                    "cơ sở dữ liệu dân cư",
                    "giấy xác nhận cư trú",
                    "xác nhận cư trú",
                )
            )
            and any(
                marker in folded_anchor
                for marker in ("hưu trí xã hội", "trợ cấp xã hội")
            )
        )
        if (
            retrieval_anchor
            and (
                any(marker in folded_query for marker in contextual_markers)
                or cross_field_followup
            )
            and (not has_administrative_subject(query) or cross_field_followup)
            and retrieval_anchor.casefold() not in folded_query
        ):
            query = (
                f"{retrieval_anchor[:1200]}. "
                f"Câu hỏi tiếp theo: {query}"
            )[:2000]
        if (
            str(request.router_decision.get("conversation_route") or "")
            == "document_followup"
            and isinstance(request.active_document, Mapping)
            and not explicit_plan.law_numbers
        ):
            anchors = [
                str(request.active_document.get(key) or "").strip()
                for key in ("law_number", "document_title", "article_number")
            ]
            query = " ".join(
                dict.fromkeys([query, *[value for value in anchors if value]])
            )[:2000]
        exact_plan = plan_exact_lookup(query)
        exact = is_single_exact_article_plan(exact_plan)
        if exact:
            exact_issue_ids.add(issue_id)
        raw_domain = str(_value(item, "domain") or "").strip()
        # Legacy planner labels combine several serving domains (notably
        # residence and civil status). For a single issue the unified router
        # has the more precise scope; explicit per-issue canonical domains
        # must remain independent for multi-domain questions.
        if raw_domain.casefold() in {"", "unknown", "administrative"} or (
            len(source_issues) == 1
            and raw_domain.casefold() in {"civil_status", "land", "labour"}
            and request.router_decision.get("canonical_domain")
            not in {None, "", "unknown", "administrative"}
        ):
            raw_domain = str(
                request.router_decision.get("canonical_domain") or ""
            ).strip()
        domain = raw_domain
        domain = canonicalize_legal_domain(domain) or domain
        retrieval_domain = (
            None if domain in _UNFILTERED_LEGACY_CORPUS_DOMAINS else domain
        )
        issue_facets = tuple(
            str(value).strip()
            for value in (_value(item, "facets", ()) or ())
            if str(value).strip()
        ) or decision_facets
        # Keep the single router decision authoritative while adding only
        # deterministic retrieval facets implied by the same question.  This
        # avoids sending a generic ``rule/condition/authority`` profile for a
        # deadline, form, or process query (which causes R28's broad semantic
        # neighbors to outrank the legal phrase hit).
        folded_for_facets = query.casefold()
        facet_additions: list[str] = []
        if any(marker in folded_for_facets for marker in (
            "thời hiệu", "thời hạn", "bao lâu", "mấy ngày", "mấy tháng",
            "bao nhiêu ngày", "bao nhiêu tháng", "chậm nhất",
        )):
            facet_additions.append("deadline")
        if any(marker in folded_for_facets for marker in (
            "ai có trách nhiệm", "cơ quan nào", "nộp ở đâu", "gửi đến đâu",
            "yêu cầu cơ quan nào", "thuộc về ai", "thẩm quyền",
        )):
            facet_additions.append("authority")
        if any(marker in folded_for_facets for marker in (
            "điều kiện", "trường hợp nào", "đối tượng nào", "mức thu nhập",
            "có phải", "được cấp lại", "phải cấp đổi",
        )):
            facet_additions.append("condition")
        if any(marker in folded_for_facets for marker in ("lệ phí", "mức phí", "bao nhiêu tiền", "chi phí")):
            facet_additions.append("fee")
        if any(marker in folded_for_facets for marker in ("ct01", "mẫu", "biểu mẫu", "tờ khai")):
            facet_additions.extend(("form", "documents"))
        elif any(marker in folded_for_facets for marker in ("hồ sơ", "giấy tờ")):
            facet_additions.append("documents")
        if any(marker in folded_for_facets for marker in ("trình tự", "thứ tự", "bước nào", "các bước")):
            facet_additions.append("process")
        if any(marker in folded_for_facets for marker in ("hiệu lực", "sửa đổi", "thay thế")):
            facet_additions.append("verification")
        if any(marker in folded_for_facets for marker in (
            "xử phạt", "biện pháp khắc phục", "phải làm gì", "tạm đình chỉ",
            "hủy bỏ", "trả lại",
        )):
            facet_additions.append("next_action")
        if any(marker in folded_for_facets for marker in (
            "tình trạng khẩn cấp", "không tổ chức dạy học", "trường hợp đặc biệt",
        )):
            facet_additions.append("exception")
        if facet_additions:
            issue_facets = tuple(dict.fromkeys((*issue_facets, *facet_additions)))[:12]
        procedure_id = _value(item, "procedure_id") or decision_procedure_id
        payloads.append(
            {
                "issue_id": issue_id,
                "query": query,
                # One semantic query per issue. Exact identity deliberately
                # leaves this empty so R28 takes its mmap/FTS exact path.
                # Keep one HTTP /search/batch call, while allowing R28 to
                # score a small deterministic set of facets in that batch.
                # Exact identity remains query-free and uses mmap/FTS.
                "queries": [] if exact else _semantic_queries(
                    issue_id,
                    query,
                    issue_facets,
                    subject_anchor=(
                        _value(item, "retrieval_subject")
                        or _value(item, "subject_anchor")
                        or None
                    ),
                    fact_anchors=(
                        _value(item, "retrieval_facts")
                        or _value(item, "fact_anchors")
                        or ()
                    ),
                    # Cross-domain/multi-issue questions still use one R28
                    # batch, but each issue gets one focused query to keep
                    # the bounded retrieval budget predictable.
                    # Direct RAG deliberately sends one deterministic query
                    # per issue.  A focus query (when one is available) is
                    # selected before the original wording by
                    # ``_semantic_queries``; otherwise the original wording
                    # is retained.  Sending several embedding variants in a
                    # single batch multiplies encoder work under c4 and
                    # defeats the serving latency budget even though the HTTP
                    # request count is still one.
                    # One resolved question is enough for current embedding
                    # models. Facet labels stay as ranking metadata and never
                    # create extra query variants.
                    max_queries=(
                        2
                        if len(
                            set(facet_additions)
                            & {
                                "authority", "deadline", "condition", "fee",
                                "next_action", "exception",
                            }
                        ) >= 2
                        else 1
                    ),
                ),
                "domain": (
                    retrieval_domain
                    if retrieval_domain not in {"", "unknown", "administrative"}
                    else None
                ),
                "intent": str(_value(item, "intent") or "rule"),
                "facets": list(issue_facets),
                "subject_anchor": (
                    _value(item, "retrieval_subject")
                    or _value(item, "subject_anchor")
                    or None
                ),
                "procedure_id": procedure_id,
                "exact_article": exact,
                # A remembered document scopes only a genuine continuation.
                # If the current turn explicitly names another instrument,
                # its legal identity must win over stale conversation state.
                "document_id": (
                    request.active_document.get("document_id")
                    if isinstance(request.active_document, Mapping)
                    and (
                        request.router_decision.get("conversation_route") == "document_followup"
                        or bool(exact_plan.law_numbers)
                    )
                    and (
                        not exact_plan.law_numbers
                        or normalize_exact_identifier(
                            request.active_document.get("law_number")
                        ) in set(exact_plan.law_numbers)
                    )
                    else None
                ),
                "full_document": bool(
                    exact_plan.law_numbers
                    and not exact_plan.article_numbers
                    and is_full_document_request(request.question)
                ),
                # Private orchestration metadata. `_retrieval_payload` strips
                # it before calling R28; after retrieval it becomes a
                # deterministic, fail-closed citation boundary.
                "_explicit_law_numbers": list(explicit_plan.law_numbers),
            }
        )
    if request.router_decision.get("semantic_plan"):
        # Keep all issues, sharing the serving contract's 16 query slots.
        # Facets stay on the issue even when an expansion does not get a slot.
        remaining = max(0, 16 - len(payloads))
        original_queries = [list(p.get("queries") or []) for p in payloads]
        for p, queries in zip(payloads, original_queries):
            p["queries"] = queries[:1]
        for depth in range(1, 4):
            for p, queries in zip(payloads, original_queries):
                if remaining and depth < len(queries):
                    p["queries"].append(queries[depth])
                    remaining -= 1
    return payloads, exact_issue_ids


def _retrieval_payload(
    request: DirectRagRequest,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    # R28's serving contract accepts only a concrete current/historical scope.
    # Unified Router V1 may deliberately leave an ambiguous past reference as
    # ``unknown`` while still allowing a fail-open legal lookup.  Resolve that
    # transport value to the request's effective date without changing the
    # router decision exposed to the caller; this prevents a malformed batch
    # from surfacing as an opaque 500.
    query_decision = dict(request.router_decision)
    if str(query_decision.get("temporal_scope") or "").strip().casefold() not in {
        "current", "historical"
    }:
        query_decision["temporal_scope"] = "current"
    # Exact law/article requests use the immutable SQLite/mmap identity path
    # first and may merge the PostgreSQL overlay as an additive source. R28
    # bounds that overlay probe; it can never replace a complete release
    # packet or trigger a second retrieval pass.
    return {
        "request_id": request.request_id,
        "as_of": request.legal_as_of,
        "as_of_explicit": bool(request.as_of_explicit),
        "query_decision": query_decision,
        "raw_query_mode": False,
        "issues": [
            {
                key: value
                for key, value in issue.items()
                if not str(key).startswith("_")
            }
            for issue in issues
        ],
        "retrieval_tier": "core",
        "audience": request.role,
        # This remains a ranking hint. R28/DB enforce the officer ACL.
        "organization_unit_id": request.organization_unit_id,
        "organization_routing_mode": request.organization_routing_mode,
        "include_trace": bool(request.include_trace),
        "include_overlay": True,
        # Tell R28 this is the unified Direct RAG serving contract.  Shadow
        # expansion/ladder code is disabled for this request so one batch
        # remains one bounded retrieval pass.
        "direct_rag": True,
        "ranking_strategy": "rrf_v2",
        "enable_learned_reranker": False,
    }


def _source_content(row: Mapping[str, Any]) -> str:
    if "direct_visible_content" in row:
        return str(row.get("direct_visible_content") or "").strip()
    return str(
        row.get("parent_context")
        or row.get("exact_article_assembled_content")
        or row.get("evidence_capsule")
        or row.get("clean_content")
        or row.get("content")
        or ""
    ).strip()


def _source_identity(row: Mapping[str, Any]) -> str:
    # source_id can identify a document, not a passage. Preserve distinct
    # points and physical/legal versions even when they share that source ID.
    primary = next((str(row.get(key)) for key in (
        "chunk_revision_id", "chunk_id", "canonical_chunk_id", "id", "source_id"
    ) if row.get(key)), "")
    stable = "|".join(
        str(row.get(key) or "").strip()
        for key in ("document_id", "law_number", "article_number", "clause_number",
                    "point_number", "effective_from", "effective_to")
    )
    digest = hashlib.sha256(_source_content(row).encode("utf-8")).hexdigest()
    return f"{primary}|{stable}|{digest}"


def _packet_source_row(
    packet: Mapping[str, Any],
    *,
    issue_id: str,
) -> dict[str, Any]:
    outline = packet.get("article_outline")
    outline = dict(outline) if isinstance(outline, Mapping) else {}
    # An outline packet is metadata plus a bounded, source-order opening
    # excerpt.  Keeping that excerpt on the backend-owned row gives the
    # citation projector a real proof substring; it does not turn the
    # projection into a synthetic legal claim or rewrite the corpus.
    opening_excerpt = str(outline.get("opening_excerpt") or "").strip()
    return {
        "issue_id": issue_id,
        "source_id": f"{packet.get('packet_ref') or issue_id}:article",
        "document_id": packet.get("document_id"),
        "article_id": packet.get("article_id"),
        "law_number": packet.get("law_number"),
        "article_number": packet.get("article_number"),
        "document_title": packet.get("document_title")
        or outline.get("document_title")
        or packet.get("law_number")
        or "Nguồn pháp luật",
        "article_title": outline.get("article_title"),
        "source_url": packet.get("source_url"),
        "effective_status": packet.get("effective_status") or packet.get("document_status") or "unknown",
        "validity_sync": packet.get("validity_sync"),
        "exact_article_outline": outline,
        "exact_article_serving_mode": packet.get("serving_mode"),
        "content": opening_excerpt,
    }


def _document_outline_source_row(
    outline: Mapping[str, Any],
    *,
    issue_id: str,
) -> dict[str, Any]:
    """Project a backend-built whole-document map as one grounded source."""

    document_id = outline.get("document_id")
    law_number = outline.get("law_number")
    return {
        "issue_id": issue_id,
        "source_id": f"document-outline:{document_id or law_number or issue_id}",
        "document_id": document_id,
        "law_number": law_number,
        "document_title": outline.get("document_title") or law_number or "Nguồn pháp luật",
        "source_url": outline.get("source_url"),
        "effective_status": outline.get("effective_status") or "unknown",
        "document_outline": True,
        "document_outline_article_count": outline.get("article_count"),
        "document_outline_chunk_count": outline.get("chunk_count"),
        "document_outline_covered_article_count": outline.get("covered_article_count"),
        "document_outline_covered_chunk_count": outline.get("covered_chunk_count"),
        "document_outline_coverage_ratio": outline.get("coverage_ratio"),
        "document_outline_content_truncated": bool(outline.get("content_truncated")),
        "document_outline_serving_mode": outline.get("serving_mode"),
        "content": str(outline.get("content") or "").strip(),
    }


def _scope_response_to_explicit_documents(
    response: Mapping[str, Any],
    issues: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Drop evidence that does not match an identifier named this turn.

    Semantic retrieval may return a neighboring (or previously discussed)
    instrument even when the citizen explicitly asks for a new law number.
    Letting such rows reach generation creates a convincing answer with stale
    citations. Identity is therefore enforced before evidence flattening.
    """

    allowed_by_issue = {
        str(issue.get("issue_id") or ""): {
            normalize_exact_identifier(value)
            for value in issue.get("_explicit_law_numbers") or ()
            if normalize_exact_identifier(value)
        }
        for issue in issues
        if issue.get("_explicit_law_numbers")
    }
    if not allowed_by_issue:
        return dict(response)

    scoped_issues: list[dict[str, Any]] = []
    for raw_issue in response.get("issues") or ():
        if not isinstance(raw_issue, Mapping):
            continue
        issue = dict(raw_issue)
        allowed = allowed_by_issue.get(str(issue.get("issue_id") or ""))
        if allowed:
            matching_results = [
                dict(row)
                for row in issue.get("results") or ()
                if isinstance(row, Mapping)
                and normalize_exact_identifier(row.get("law_number")) in allowed
            ]
            issue["results"] = matching_results
            issue["exact_document_outlines"] = [
                dict(row)
                for row in issue.get("exact_document_outlines") or ()
                if isinstance(row, Mapping)
                and normalize_exact_identifier(row.get("law_number")) in allowed
            ]
            # Some integrity packets omit repeated identity metadata because
            # their accompanying primary result already owns it. Keep such a
            # packet only when a matching result proves the requested law.
            issue["exact_article_packets"] = [
                dict(row)
                for row in issue.get("exact_article_packets") or ()
                if isinstance(row, Mapping)
                and (
                    normalize_exact_identifier(row.get("law_number")) in allowed
                    or (
                        not normalize_exact_identifier(row.get("law_number"))
                        and bool(matching_results)
                    )
                )
            ]
        scoped_issues.append(issue)
    return {**dict(response), "issues": scoped_issues}


def _flatten_retrieval(
    response: Mapping[str, Any],
    *,
    issue_order: Sequence[str],
    exact_issue_ids: set[str],
    limit: int = 10,
    issue_facets: Mapping[str, Sequence[str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], tuple[str, ...]]:
    """Keep every issue observable and reserve ranked rows across issues."""

    by_issue = {
        str(item.get("issue_id") or ""): item
        for item in response.get("issues") or []
        if isinstance(item, Mapping)
    }
    packet_by_issue: dict[str, list[dict[str, Any]]] = {}
    selected: list[dict[str, Any]] = []
    unique: dict[str, dict[str, Any]] = {}
    keys_by_issue: dict[str, list[str]] = {}
    missing: list[str] = []
    for issue_id in issue_order:
        issue_result = by_issue.get(issue_id) or {}
        packets = [
            dict(item)
            for item in issue_result.get("exact_article_packets") or []
            if isinstance(item, Mapping)
        ]
        packet_by_issue[issue_id] = packets
        rows = [
            {**dict(item), "issue_id": issue_id}
            for item in issue_result.get("results") or []
            if isinstance(item, Mapping)
        ]
        outlines = [
            _document_outline_source_row(item, issue_id=issue_id)
            for item in issue_result.get("exact_document_outlines") or []
            if isinstance(item, Mapping) and str(item.get("content") or "").strip()
        ]
        if outlines:
            rows = [*outlines, *rows]
        if issue_id not in exact_issue_ids and issue_facets:
            # Reserve complementary passages already returned by retrieval.
            # These signals select excerpts; they do not assert legal coverage.
            markers = {
                "documents": ("hồ sơ", "tờ khai", "giấy tờ"),
                "authority": ("nộp hồ sơ", "cơ quan đăng ký", "tiếp nhận"),
                "deadline": ("ngày làm việc", "thời hạn giải quyết"),
                "fee": ("lệ phí", "mức thu", "miễn phí"),
                "result": ("thông báo", "kết quả", "cập nhật thông tin"),
                "condition": ("điều kiện", "được đăng ký"),
                "consent": ("đồng ý", "chữ ký"),
                "form": ("mẫu", "tờ khai"),
                "rule": ("công chứng", "chứng thực", "quy định"),
            }
            reserved: list[dict[str, Any]] = []
            for facet in issue_facets.get(issue_id, ()):
                candidates = [(100 if facet in (row.get("supported_facets") or []) else sum(token in _source_content(row).casefold()
                                   for token in markers.get(facet, ())), row) for row in rows]
                best = max((score for score, _ in candidates), default=0)
                if best:
                    chosen = next(row for score, row in candidates if score == best)
                    if chosen not in reserved:
                        reserved.append(chosen)
            rows = reserved + [row for row in rows if row not in reserved]
        # Exact Article results carry the integrity-checked assembled body on
        # their primary row. Collapse the child chunks into that one unit.
        if issue_id in exact_issue_ids:
            primary = next((row for row in rows if _source_content(row) and row.get("parent_context_primary")), None)
            if primary is None:
                primary = next((row for row in rows if row.get("parent_context")), None)
            if primary is not None:
                rows = [primary]
        if not rows and packets:
            rows = [
                _packet_source_row(packet, issue_id=issue_id)
                for packet in packets
                if packet.get("source_url") or packet.get("document_id")
            ]
        if not rows:
            missing.append(issue_id)
        keys_by_issue[issue_id] = []
        for row in rows:
            identity = _source_identity(row)
            if identity not in unique:
                unique[identity] = {**row, "direct_issue_ids": [issue_id]}
            elif issue_id not in unique[identity]["direct_issue_ids"]:
                unique[identity]["direct_issue_ids"].append(issue_id)
            if identity not in keys_by_issue[issue_id]:
                keys_by_issue[issue_id].append(identity)
    seen: set[str] = set()
    maximum = max(1, min(24, int(limit)))
    depth = 0
    while len(selected) < maximum:
        progressed = False
        for issue_id in issue_order:
            keys = keys_by_issue.get(issue_id, [])
            if depth >= len(keys):
                continue
            progressed = True
            identity = keys[depth]
            if identity not in seen:
                seen.add(identity)
                selected.append(unique[identity])
            if len(selected) >= maximum:
                break
        if not progressed:
            break
        depth += 1
    for row in selected:
        row["direct_retrieved_evidence_count"] = len(unique)
    return selected, packet_by_issue, tuple(missing)


def _evidence_header(row: Mapping[str, Any], public_id: str, bounded_window: bool) -> str:
    metadata = [
        "Phục vụ vấn đề: " + ", ".join(row.get("direct_issue_ids") or [str(row.get("issue_id") or "")]),
        f"Văn bản: {str(row.get('document_title') or row.get('law_number') or 'Nguồn pháp luật').strip()}",
        f"Số hiệu: {str(row.get('law_number') or 'không có').strip()}",
    ]
    for key, label in (("article_number", "Điều"), ("clause_number", "Khoản"), ("point_number", "Điểm")):
        if row.get(key):
            metadata.append(f"{label}: {str(row[key]).strip()}")
    if row.get("effective_status") or row.get("document_status"):
        metadata.append("Metadata hiệu lực: " + str(row.get("effective_status") or row.get("document_status")))
    validity = row.get("validity_sync")
    if isinstance(validity, Mapping):
        status = str(validity.get("status") or "").strip().casefold()
        serving_action = str(validity.get("serving_action") or "").strip().casefold()
        would_block = validity.get("would_block") is True or str(
            validity.get("would_block") or ""
        ).strip().casefold() == "true"
        ineligible = validity.get("current_answer_eligible") is False or str(
            validity.get("current_answer_eligible") or ""
        ).strip().casefold() == "false"
        adverse_statuses = {
            "not_yet_effective", "expired", "expired_partial", "suspended",
            "suspended_partial", "replaced", "repealed",
        }
        # An unresolved registry identity that is explicitly allowed must not
        # override the document's stored legal status in the model prompt.
        # Surface validity text only when it changes serving eligibility or
        # identifies a concrete adverse lifecycle state.
        if serving_action == "block" or would_block or ineligible or status in adverse_statuses:
            label = validity.get("display_label") or validity.get("status") or "Cần kiểm tra hiệu lực"
            metadata.append("Xác minh hiệu lực: " + str(label))
            if validity.get("warning_code"):
                metadata.append("Lưu ý hiệu lực: " + str(validity["warning_code"]))
        if status and status != "unknown":
            if validity.get("effective_from"):
                metadata.append(
                    "Ngày bắt đầu hiệu lực đã xác minh: "
                    + str(validity["effective_from"])
                )
            if validity.get("effective_to"):
                metadata.append(
                    "Ngày kết thúc hiệu lực đã xác minh: "
                    + str(validity["effective_to"])
                )
    for key, label in (("effective_from", "Ngày bắt đầu theo metadata"), ("effective_to", "Ngày kết thúc theo metadata")):
        if row.get(key) and not (
            isinstance(validity, Mapping) and validity.get(key)
        ):
            metadata.append(f"{label}: {row[key]}")
    if row.get("dossier_application_status"):
        metadata.append("Đối chiếu sửa đổi: đọc riêng nguồn gốc và nguồn sửa đổi; không coi đoạn gốc là bản hợp nhất.")
    if row.get("dossier_group_truncated"):
        metadata.append("Phạm vi hồ sơ: chưa đưa hết các đoạn cùng nhóm vào gói.")
    if row.get("source_url"):
        metadata.append(f"URL nguồn: {str(row['source_url']).strip()}")
    if bounded_window:
        metadata.append("Phạm vi: phần liên quan trong Điều dài, không đại diện toàn Điều")
    return f"[{public_id}]\n" + "\n".join(metadata) + "\nTrích đoạn:\n"


def _render_evidence_packet(
    rows: Sequence[Mapping[str, Any]],
    *,
    max_chars: int,
    bounded_window: bool,
    whole_units: bool = False,
) -> tuple[str, dict[str, dict[str, Any]]]:
    if whole_units:
        return pack_whole_sources(rows, max_chars=max_chars, header=_evidence_header, bounded_window=bounded_window)
    blocks: list[str] = []
    evidence_by_id: dict[str, dict[str, Any]] = {}
    consumed = 0
    window = list(rows[:24])
    for position, row in enumerate(window):
        next_index = len(evidence_by_id) + 1
        public_id = f"E{next_index}"
        content = _source_content(row)
        header = _evidence_header(row, public_id, bounded_window)
        # Reserve room for later sources rather than letting the first long
        # excerpt silently swallow every other issue's evidence.
        reserve = sum(
            len(_evidence_header(later, f"E{index + 1}", bounded_window))
            + min(600, len(_source_content(later)) or 72) + 2
            for index, later in enumerate(window[position + 1:], start=position + 1)
        )
        remaining = max_chars - consumed - len(header) - 2 - reserve
        if remaining < min(240, len(content)):
            # If metadata alone makes all rows impossible, retain a bounded
            # source and expose the omitted issue through packet coverage.
            remaining = max_chars - consumed - len(header) - 2
        if remaining <= 0:
            break
        content_truncated = len(content) > remaining
        if content_truncated:
            if remaining < 240:
                break
            prefix = content[: max(0, remaining - 24)].rstrip()
            boundary = max(prefix.rfind("\n"), prefix.rfind(". "))
            if boundary >= 240:
                prefix = prefix[:boundary + (1 if prefix[boundary:boundary + 1] == "." else 0)]
            elif " " in prefix:
                prefix = prefix.rsplit(" ", 1)[0]
            content = prefix.rstrip() + "\n[…đã rút gọn…]"
        block = header + (content or "(Nguồn chỉ có metadata; mở URL để đọc nội dung.)")
        if consumed + len(block) + 2 > max_chars:
            break
        stored = dict(row)
        stored["direct_evidence_id"] = public_id
        stored["direct_content_truncated"] = content_truncated
        stored["content"] = content
        stored["direct_visible_content"] = content
        if content_truncated:
            # Citation proof must not cite a hidden tail through an older
            # parent/clean-content alias after the prompt was truncated.
            for alias in ("parent_context", "exact_article_assembled_content", "evidence_capsule", "clean_content"):
                if alias in stored:
                    stored[alias] = content
            stored["support_quote"] = content.removesuffix("\n[…đã rút gọn…]")[:1200]
        evidence_by_id[f"evidence-{next_index}"] = stored
        blocks.append(block)
        consumed += len(block) + 2
    return "\n\n".join(blocks), evidence_by_id


def _packet_coverage(
    issue_order: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    missing: Sequence[str],
) -> dict[str, Any]:
    represented: set[str] = set()
    partial: set[str] = set()
    for row in evidence_by_id.values():
        issues = set(map(str, row.get("direct_issue_ids") or [row.get("issue_id") or ""]))
        if _source_content(row):
            represented.update(issues)
        if row.get("direct_content_truncated") or row.get("dossier_group_truncated"):
            partial.update(issues)
    missing_set = set(missing) | (set(issue_order) - represented)
    document_digest_rows = [
        row for row in evidence_by_id.values() if bool(row.get("document_outline"))
    ]
    document_digest_complete = bool(document_digest_rows) and all(
        float(row.get("document_outline_coverage_ratio") or 0.0) >= 1.0
        and not bool(row.get("document_outline_content_truncated"))
        for row in document_digest_rows
    )
    return {
        "coverage_kind": "retrieval_presence_only",
        "requested_issue_ids": list(issue_order),
        "represented_issue_ids": [key for key in issue_order if key in represented],
        "missing_issue_ids": [key for key in issue_order if key in missing_set],
        "partial_issue_ids": [key for key in issue_order if key in partial and key not in missing_set],
        "dropped_evidence_count": max(0, max(
            [len(rows), *(int(row.get("direct_retrieved_evidence_count") or 0) for row in rows)]
        ) - len(evidence_by_id)),
        "truncated_evidence_count": sum(bool(row.get("direct_content_truncated")) for row in evidence_by_id.values()),
        "whole_document_digest": bool(document_digest_rows),
        "whole_document_digest_complete": document_digest_complete,
        "whole_document_coverage_ratio": min(
            (
                float(row.get("document_outline_coverage_ratio") or 0.0)
                for row in document_digest_rows
            ),
            default=0.0,
        ),
        "semantic_completeness_verified": False,
    }


def _project_citations(
    rows: Sequence[Mapping[str, Any]],
    project: ProjectCitation,
) -> tuple[dict[str, Any], ...]:
    citations: list[dict[str, Any]] = []
    citations_by_key: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        try:
            citation = dict(project(row))
        except Exception:
            continue
        evidence_id = str(row.get("direct_evidence_id") or "").strip().upper()
        key = tuple(
            str(citation.get(name) or "")
            for name in (
                "law_number",
                "article_number",
                "clause_number",
                "point_number",
                "source_url",
            )
        )
        existing = citations_by_key.get(key)
        if existing is not None:
            evidence_ids = list(existing.get("evidence_ids") or [])
            if evidence_id and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
                existing["evidence_ids"] = evidence_ids
            continue
        if evidence_id:
            citation["evidence_ids"] = [evidence_id]
        citations_by_key[key] = citation
        citations.append(citation)
    return tuple(citations)


def _outline_answer(row: Mapping[str, Any]) -> str | None:
    outline = row.get("exact_article_outline")
    if not isinstance(outline, Mapping) or int(outline.get("chunk_count") or 0) <= 0:
        return None
    article = str(row.get("article_number") or outline.get("article_number") or "được yêu cầu")
    law = str(row.get("law_number") or outline.get("law_number") or "văn bản được yêu cầu")
    # R28 outlines are immutable release projections.  A release created by
    # an older parser can still contain the same top-level label more than
    # once when a nested quoted khoản was mistaken for a sibling.  Normalize
    # that presentation at the serving boundary without changing the corpus
    # or the release artifact itself.
    groups: list[Mapping[str, Any]] = []
    seen_labels: set[str] = set()
    for item in outline.get("top_level_groups") or []:
        if not isinstance(item, Mapping):
            continue
        label = " ".join(str(item.get("label") or "").split()).strip()
        if not label:
            continue
        label_key = label.casefold()
        if label_key in seen_labels:
            continue
        seen_labels.add(label_key)
        groups.append(item)
    # Show representatives from the beginning, middle and end.  Taking the
    # first ten entries made a long amended Article look as if it only
    # contained the first amendment group, even though the outline was built
    # from the complete source-order structure.
    if len(groups) > 12:
        representative_indexes = [
            *range(0, 4),
            *range(max(4, len(groups) // 2 - 2), len(groups) // 2 + 2),
            *range(len(groups) - 4, len(groups)),
        ]
        # Keep source order even though the representative set spans the
        # beginning, middle and end of a long article.  This makes the outline
        # read as a faithful projection rather than an arbitrary rank list.
        selected_groups = [groups[index] for index in sorted(set(representative_indexes))]
    else:
        selected_groups = groups
    previews = []
    for item in selected_groups:
        label = str(item.get("label") or "").strip()
        preview = " ".join(str(item.get("preview") or "").split())[:180]
        previews.append(f"{label}: {preview}" if preview else label)
    details = (
        "\n\nCác nhóm nội dung chính:\n"
        + "\n".join(f"- {value}" for value in previews)
        if previews else ""
    )
    title = str(outline.get("article_title") or "").strip()
    title_line = f" Phạm vi/tiêu đề: {title}." if title else ""
    return (
        f"**Tổng quan toàn Điều:** Điều {article} của văn bản {law} có "
        f"{int(outline.get('chunk_count') or 0)} đoạn nguồn và "
        f"{int(outline.get('structural_unit_count') or 0)} đơn vị cấu trúc và "
        f"{len(groups)} nhóm/khoản cấp cao. "
        "Tổng quan này được dựng từ toàn bộ cấu trúc Điều, không phải một cửa sổ khoản/điểm riêng lẻ."
        f"{title_line}{details} [E1]"
    )


def _full_article_answer(row: Mapping[str, Any]) -> str | None:
    content = _source_content(row)
    if not content:
        return None
    article = str(row.get("article_number") or "được yêu cầu")
    law = str(row.get("law_number") or "văn bản được yêu cầu")
    # Imported point chunks may repeat the same parent clause introduction.
    # Remove only an identical repeated prefix; never renumber legal clauses.
    seen_prefixes: set[str] = set()
    lines = []
    source_lines = content.splitlines()
    for position, line in enumerate(source_lines):
        # Parent introductions also occur on a separate line before each
        # imported point. Only collapse an identical prefix followed by a
        # point; repeated substantive provisions are retained.
        next_line = next((s.strip() for s in source_lines[position + 1:] if s.strip()), "")
        if re.match(r"^\s*\d+\.\s*.*:\s*$", line) and re.match(r"^[a-zđ]\)", next_line):
            prefix = " ".join(line.split())
            if prefix in seen_prefixes:
                continue
            seen_prefixes.add(prefix)
        match = re.match(r"^(\s*\d+\.\s*.*?:)\s*(?=[a-zđ]\))", line)
        if match:
            prefix = " ".join(match.group(1).split())
            if prefix in seen_prefixes:
                line = line[match.end():]
            else:
                seen_prefixes.add(prefix)
                line = match.group(1) + "\n" + line[match.end():]
        # CommonMark otherwise renumbers source labels into an ordered list.
        line = re.sub(r"^(\s*\d+)\.", r"\1\\.", line)
        lines.append(line)
    content = "\n".join(lines)
    return f"**Nguyên văn Điều {article} — {law}:** [E1]\n\n{content}"


def _result(
    *,
    started: float,
    request: DirectRagRequest,
    packet: EvidencePacket,
    answer: str,
    citations: Sequence[Mapping[str, Any]],
    outcome: str,
    reason_code: str,
    retryable: bool,
    timing: Mapping[str, Any],
    provider_error_code: str | None = None,
) -> DirectAnswerResult:
    from api.chat_execution import current_turn
    turn = current_turn.get()
    applied_admin_style = admin_style_addendum(request.system_prompt_addendum)
    timing_contract: dict[str, Any] = {
        "context_ms": None,
        "planner_ms": None,
        "rewrite_ms": None,
        "retrieval_ms": None,
        "model_provision_ms": None,
        "queue_wait_ms": None,
        "first_content_ms": None,
        "generation_ms": None,
        "generation_total_ms": None,
        "citation_check_ms": None,
        "persistence_ms": None,
    }
    final_timing = {
        **timing_contract,
        "routing_ms": round(float(request.router_latency_ms or 0.0), 1),
        **{key: round(float(value), 1) if isinstance(value, (int, float)) and not isinstance(value, bool) else value for key, value in timing.items()},
        "direct_service_ms": round((time.perf_counter() - started) * 1000, 1),
        "end_to_end_ms": turn.elapsed_ms() if turn else round((time.perf_counter() - started) * 1000, 1),
    }
    grounding = "fully_grounded" if outcome == "answered" else (
        "partially_grounded" if outcome == "partial" else "source_view_only"
    )
    rows = tuple(dict(row) for row in packet.evidence_by_id.values())
    check = validate_citations(answer, packet.evidence_by_id)
    invalid = reason_code.startswith("CITATION_") or reason_code == "OUTPUT_NOT_MARKDOWN"
    logging.getLogger(__name__).info(
        "direct_answer_result request_id=%s role=%s outcome=%s reason=%s evidence=%s cited=%s",
        request.request_id, request.role, outcome, reason_code, len(rows), len(check.cited_ids),
    )
    return DirectAnswerResult(
        answer=answer,
        citations=tuple(dict(item) for item in citations),
        outcome=outcome,
        reason_code=reason_code,
        retryable=retryable,
        scope=packet.scope,
        release_id=packet.release_id,
        manifest_hash=packet.manifest_hash,
        legal_as_of=packet.legal_as_of,
        timing=final_timing,
        evidence_rows=rows,
        evidence_by_id=packet.evidence_by_id,
        provider_error_code=provider_error_code,
        grounding_status=grounding,
        trace={
            "pipeline_version": "direct-rag-v1",
            "direct_rag": True,
            "prompt_variant": request.prompt_variant,
            "prompt_revision": int(request.prompt_revision or 1),
            "system_prompt_addendum_applied": bool(applied_admin_style),
            "system_prompt_addendum_sha256": (
                hashlib.sha256(
                    applied_admin_style.encode("utf-8")
                ).hexdigest()
                if applied_admin_style
                else None
            ),
            "router_decision": dict(packet.router_decision),
            "answer_scope": packet.scope,
            "release_id": packet.release_id,
            "manifest_hash": packet.manifest_hash,
            "legal_as_of": packet.legal_as_of,
            "evidence_sent": len(packet.evidence_by_id),
            "missing_issue_ids": list(packet.missing_issue_ids),
            "packet_coverage": dict(packet.coverage),
            "citation_check": {
                "valid": check.valid and not invalid,
                "status": "invalid" if invalid else "valid" if check.valid else "missing",
                "cited_ids": list(check.cited_ids),
                "unknown_ids": list(check.unknown_ids),
            },
            "reason_code": reason_code,
            "post_generation_validation": False,
            "retrieval_calls": int(timing.get("retrieval_calls", 1)),
            "model_calls": int(
                timing.get("model_calls", 1 if timing.get("generation_ms", 0) else 0)
            ),
            "answer_completeness": {
                "requested_facets": [
                    value for value in str(timing.get("requested_content_facets") or "").split(",") if value
                ],
                "missing_facets": [
                    value for value in str(timing.get("missing_content_facets") or "").split(",") if value
                ],
                "completion_pass": str(timing.get("completion_pass") or "not_enabled"),
            },
            "timing": final_timing,
        },
    )


async def run_direct_legal_answer(
    request: DirectRagRequest,
    *,
    retrieve: RetrieveBatch,
    generate: GenerateAnswer,
    project_citation: ProjectCitation,
    emit_progress: EmitProgress | None = None,
    bind_sources: Callable[[Sequence[Mapping[str, Any]]], Awaitable[list[dict[str, Any]]]] | None = None,
    retrieval_timeout: float = 15.0,
    generation_timeout: float = 10.0,
    total_timeout: float = 20.0,
) -> DirectAnswerResult:
    """Execute the complete Direct RAG path.

    The default remains one retrieval and at most one answer-model call. A
    serving caller may opt into one additional, time-bounded completion pass,
    but only when deterministic checks prove that the existing evidence packet
    contains a requested facet omitted from the first cited answer.
    """

    started = time.perf_counter()
    depth_profile = answer_depth_profile(request.answer_depth)
    timing: dict[str, Any] = {
        "retrieval_calls": 1,
        "answer_depth": depth_profile.name,
        "depth_evidence_rows": depth_profile.evidence_rows,
        "depth_evidence_chars": depth_profile.evidence_chars,
    }
    batch_issues, exact_issue_ids = _issue_payloads(request)
    issue_order = [str(item["issue_id"]) for item in batch_issues]
    retrieval_started = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            retrieve(_retrieval_payload(request, batch_issues)),
            timeout=min(
                max(0.05, float(retrieval_timeout)),
                max(0.05, float(total_timeout) - 0.5),
            ),
        )
    except Exception as exc:
        timed_out = isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in type(exc).__name__
        failure = "retrieval_timeout" if timed_out else "retrieval_error"
        logging.getLogger(__name__).warning(
            "direct_retrieval_failed request_id=%s type=%s status=%s",
            request.request_id, type(exc).__name__,
            getattr(getattr(exc, "response", None), "status_code", None),
        )
        timing["retrieval_ms"] = (time.perf_counter() - retrieval_started) * 1000
        timing["retrieval_failure"] = failure
        # Only backend-reviewed supplements may survive a transport failure.
        # Exact text still requires the immutable complete article packet.
        usable = any(set(row.get("direct_issue_ids") or [row.get("issue_id")]) & (set(issue_order) - exact_issue_ids)
                     and _source_content(row) for row in request.supplemental_evidence)
        if not usable:
            empty_packet = EvidencePacket(
                request_id=request.request_id, release_id=None, manifest_hash=None,
                legal_as_of=request.legal_as_of, router_decision=request.router_decision,
                evidence_by_id={}, context="", scope="multi_source",
            )
            return _result(
                started=started, request=request, packet=empty_packet,
                answer=("Dịch vụ tra cứu nguồn pháp luật đang phản hồi chậm. Vui lòng thử lại sau." if timed_out
                        else "Dịch vụ tra cứu nguồn pháp luật gặp lỗi ở lượt này. Vui lòng thử lại."),
                citations=(), outcome="failed", reason_code="RETRIEVAL_UNAVAILABLE",
                retryable=True, timing=timing, provider_error_code=failure,
            )
        response = {"issues": []}
    timing["retrieval_ms"] = (time.perf_counter() - retrieval_started) * 1000
    retrieval_breakdown = response.get("timing_ms")
    if isinstance(retrieval_breakdown, Mapping):
        hydrate_value = retrieval_breakdown.get("hydrate")
        if isinstance(hydrate_value, (int, float)):
            timing["hydration_ms"] = float(hydrate_value)
            timing["hydrate_ms"] = float(hydrate_value)

    # Backend-reviewed local publications supplement corpus retrieval. Their
    # identities and original URLs are retained through the same packet.
    if request.supplemental_evidence:
        result_issues = {str(i.get("issue_id")): dict(i) for i in response.get("issues", [])}
        for issue_id in issue_order:
            if issue_id in exact_issue_ids:
                continue
            extra = [dict(row) for row in request.supplemental_evidence
                     if issue_id in (row.get("direct_issue_ids") or [row.get("issue_id")])]
            current = result_issues.setdefault(issue_id, {"issue_id": issue_id})
            current["results"] = merge_ranked_sources(list(current.get("results") or []), extra, _source_identity)
        response = {**response, "issues": list(result_issues.values())}
    # At most one targeted recovery; absence of facet tags on otherwise useful
    # corpus rows is UNKNOWN and must not cause a repeat of every original query.
    recovery = []
    if request.allow_faceted_recovery and depth_profile.allow_recovery and request.router_decision.get("semantic_plan") and not timing.get("retrieval_failure"):
        from api.conversation_turn_plan import FACET_TERMS
        by_issue = {str(i.get("issue_id")): i for i in response.get("issues", [])}
        for issue in batch_issues:
            issue_id = str(issue["issue_id"])
            if issue_id in exact_issue_ids:
                continue
            candidates = by_issue.get(issue_id, {}).get("results") or []
            known = set().union(*(source_facets(r) for r in candidates)) if candidates else set()
            missing_facets = [f for f in issue.get("facets", []) if f not in known and f in FACET_TERMS]
            if (not candidates or known) and missing_facets:
                query = str(issue.get("subject_anchor") or issue.get("query") or issue.get("query_text") or "").strip()
                recovery.append({**issue, "queries": [{"query_id": f"{issue_id}-recovery", "query_type": "facet",
                    "query": query + "; " + "; ".join(FACET_TERMS[f] for f in missing_facets),
                    "facets": missing_facets, "weight": 1.0}], "facets": missing_facets})
        remaining = min(6.0, float(retrieval_timeout) - (time.perf_counter() - retrieval_started),
                        float(total_timeout) - (time.perf_counter() - started) - float(generation_timeout) - 0.5)
        if recovery and remaining >= 1.0:
            timing["retrieval_calls"] = 2
            recovery_started = time.perf_counter()
            try:
                additional = await asyncio.wait_for(retrieve(_retrieval_payload(request, recovery)), timeout=remaining)
                # Never merge releases if deployment changed between calls.
                if additional.get("release_id") == response.get("release_id") and additional.get("manifest_hash") == response.get("manifest_hash"):
                    for extra_issue in additional.get("issues", []):
                        extra_id = str(extra_issue.get("issue_id"))
                        if extra_id in {str(i["issue_id"]) for i in recovery}:
                            original = by_issue.setdefault(extra_id, {"issue_id": extra_id})
                            original["results"] = merge_ranked_sources(original.get("results") or [], extra_issue.get("results") or [], _source_identity)
                    response = {**response, "issues": list(by_issue.values())}
                    timing["recovery_status"] = "completed"
                else:
                    timing["recovery_status"] = "release_changed"
            except Exception as exc:
                timing["recovery_status"] = "timeout" if isinstance(exc, TimeoutError) else "service_error"
            timing["recovery_ms"] = (time.perf_counter() - recovery_started) * 1000
    timing["retrieval_ms"] = (time.perf_counter() - retrieval_started) * 1000
    if request.answer_depth == "quick":
        timing["recovery_status"] = "skipped_quick_profile"
    response = _scope_response_to_explicit_documents(response, batch_issues)
    rows, packets_by_issue, missing = _flatten_retrieval(
        response,
        issue_order=issue_order,
        exact_issue_ids=exact_issue_ids,
        limit=depth_profile.evidence_rows,
        issue_facets={str(i["issue_id"]): i.get("facets", []) for i in batch_issues}
                     if request.router_decision.get("semantic_plan") else None,
    )
    # A document follow-up is intentionally source-bound.  R28 accepts the
    # active law/article as a ranking hint, but its semantic candidates can
    # still contain neighboring instruments.  Keep only rows belonging to
    # the backend-owned active document; if none match, return source-only
    # rather than silently switching the conversation to another law.
    if (
        str(request.router_decision.get("conversation_route") or "")
        == "document_followup"
        and isinstance(request.active_document, Mapping)
        and not any(issue.get("_explicit_law_numbers") for issue in batch_issues)
    ):
        wanted_ids = {
            str(request.active_document.get(key) or "").strip().casefold()
            for key in ("document_id", "law_number", "document_title")
            if str(request.active_document.get(key) or "").strip()
        }
        bound_rows = []
        for row in rows:
            row_ids = {
                str(row.get(key) or "").strip().casefold()
                for key in ("document_id", "law_number", "document_title")
                if str(row.get(key) or "").strip()
            }
            if wanted_ids & row_ids:
                bound_rows.append(row)
        rows = bound_rows
        if not rows:
            missing = tuple(dict.fromkeys((*missing, *issue_order)))
            # The source was explicitly bound by the previous backend answer,
            # so it is safe to show its metadata even when R28 has no matching
            # content row for this follow-up.  Do not turn metadata into a
            # legal claim or call the model; the public result remains
            # source-only and invites the user to open the official source.
            rows = [
                {
                    "issue_id": issue_order[0] if issue_order else "issue-1",
                    "source_id": "bound-document-metadata",
                    "document_id": request.active_document.get("document_id"),
                    "law_number": request.active_document.get("law_number"),
                    "document_title": (
                        request.active_document.get("document_title")
                        or request.active_document.get("title")
                        or request.active_document.get("law_number")
                    ),
                    "article_number": request.active_document.get("article_number"),
                    "source_url": request.active_document.get("source_url")
                    or request.active_document.get("viewer_url"),
                    "effective_status": request.active_document.get("effective_status"),
                    "validity_sync": request.active_document.get("validity_sync"),
                    "content": "",
                    "metadata_only_bound_source": True,
                }
            ]
    versions = response.get("versions")
    versions = versions if isinstance(versions, Mapping) else {}
    release_id = str(response.get("release_id") or versions.get("release_id") or "").strip() or None
    manifest_hash = str(response.get("manifest_hash") or versions.get("manifest_hash") or "").strip() or None
    single_exact_issue = len(batch_issues) == len(exact_issue_ids) == 1
    full_article = bool(exact_issue_ids and is_full_article_request(request.question))
    requested_clause = plan_exact_lookup(request.question).clause_number
    if single_exact_issue and requested_clause and not full_article:
        # A clause locator is emitted only when that label exists in the
        # backend-hydrated article. Keep an exact substring as citation proof.
        scoped_rows = []
        for source in rows:
            body = _source_content(source)
            match = re.search(rf"(?m)^\s*{re.escape(requested_clause)}\.\s+", body)
            if match:
                following = re.search(r"(?m)^\s*\d+\.\s+", body[match.end():])
                end = match.end() + following.start() if following else len(body)
                passage = body[match.start():end].strip()
                scoped_rows.append({**source, "clause_number": requested_clause,
                                    "direct_visible_content": passage, "content": passage,
                                    "support_quote": passage[:1200]})
        if scoped_rows:
            rows = scoped_rows
    overview = bool(single_exact_issue and is_article_overview_request(request.question))
    if overview:
        # A broad long-article question is answered from the complete outline,
        # not from the bounded child window that R28 hydrates for exact
        # integrity. Project one outline source row so the citation proof is
        # the source-order opening excerpt instead of (for example) a late
        # khoản 54 chunk selected by the exact window.
        outline_rows = [
            _packet_source_row(packet_item, issue_id=issue_id)
            for issue_id in exact_issue_ids
            for packet_item in packets_by_issue.get(issue_id, ())
            if isinstance(packet_item, Mapping)
            and str(packet_item.get("status") or "") == "complete"
        ]
        if outline_rows:
            rows = outline_rows[:1]
    if bind_sources is not None:
        binding_started = time.perf_counter()
        try:
            rows = await asyncio.wait_for(
                bind_sources(rows),
                timeout=max(0.1, total_timeout - (time.perf_counter() - started)),
            )
        except Exception:
            rows = []
            timing["source_binding_status"] = "unavailable"
        else:
            timing["source_binding_status"] = "verified" if rows else "empty"
        timing["source_binding_ms"] = (time.perf_counter() - binding_started) * 1000
    if not rows:
        missing = tuple(dict.fromkeys((*missing, *issue_order)))
    serving_modes = {
        str(packet.get("serving_mode") or "")
        for packets in packets_by_issue.values()
        for packet in packets
    }
    bounded_window = "bounded_long_article_window" in serving_modes or any(
        str(row.get("exact_article_serving_mode") or "") == "bounded_long_article_window"
        for row in rows
    )
    whole_document = any(bool(row.get("document_outline")) for row in rows)
    scope = (
        "article_outline" if overview
        else "full_article" if full_article
        else "whole_document_outline" if whole_document
        else "bounded_window" if bounded_window
        else "multi_source"
    )
    context_limit = 12_000 if overview else (
        min(24_000, max(12_000, sum(
            min(8000, len(_source_content(row))) + 600 for row in rows
        )))
        if request.router_decision.get("semantic_plan") else 8_000
    )
    if whole_document:
        context_limit = min(
            24_000,
            max(depth_profile.evidence_chars, len(_source_content(rows[0])) if rows else 0),
        )
    elif not full_article:
        context_limit = depth_profile.evidence_chars
    context_started = time.perf_counter()
    timing["answer_depth"] = depth_profile.name
    output_tokens = (min(4000, max(1800, 1200 + 240 * sum(len(i.get("facets", [])) for i in batch_issues)))
                     if request.router_decision.get("semantic_plan") else 1200)
    output_tokens = min(
        depth_profile.output_tokens_max,
        max(depth_profile.output_tokens_min, output_tokens),
        max(256, request.context_token_budget // 4),
    )
    from open_notebook.utils.token_utils import token_count
    history_char_limit = max(len(request.conversation_context), {
        "quick": 6000,
        "balanced": 8000,
        "deep": 12000,
    }.get(request.answer_depth, 8000))
    fixed_prompt = build_prompt(question=request.provider_question or request.question, role=request.role, context="",
        conversation_context=request.conversation_context,
        route=str(request.router_decision.get("legal_route") or "legal_query"),
        historical=str(request.router_decision.get("temporal_scope") or "") == "historical",
        max_history_chars=history_char_limit,
        system_prompt_addendum=request.system_prompt_addendum,
        prompt_variant=request.prompt_variant,
        requested_facets=tuple(dict.fromkeys(f for issue in batch_issues for f in issue.get("facets", []))),
        structured_output=bool(request.router_decision.get("structured_answer")),
        answer_depth=request.answer_depth,
        communication_preferences=request.communication_preferences)
    # Count the exact adapter-owned inventory rather than approximating a
    # different projection. It is never truncated as conversation history.
    inventory_reserve = token_count(request.prompt_suffix) + 256
    available_tokens = max(0, request.context_token_budget - output_tokens - token_count(fixed_prompt) - inventory_reserve)
    # A simple quick route must not truncate its highest-ranked article merely
    # to reserve slots for many lower-ranked candidates. Whole-source packing
    # keeps citation verification meaningful within the same context budget.
    whole_units = (bool(request.router_decision.get("semantic_plan"))
                   or (request.answer_depth == "quick" and len(batch_issues) == 1)) and not exact_issue_ids
    context, evidence_by_id = _render_evidence_packet(
        rows,
        max_chars=context_limit,
        bounded_window=bounded_window,
        whole_units=whole_units,
    )
    if whole_units:
        while context and token_count(context) > available_tokens:
            context_limit = max(0, min(context_limit - 1, int(context_limit * available_tokens / max(1, token_count(context))) - 64))
            context, evidence_by_id = _render_evidence_packet(rows, max_chars=context_limit,
                bounded_window=bounded_window, whole_units=True)
    timing["context_budget_tokens"] = request.context_token_budget
    timing["evidence_tokens_estimated"] = token_count(context) if context else 0
    timing["token_measurement"] = "o200k_reference_estimate_not_provider_tokenizer"
    if overview:
        # The overview is a deterministic projection of the complete outline,
        # not a claim about whichever bounded passage happened to be hydrated
        # first. Use the outline's source-order opening excerpt for the public
        # proof so the citation cannot look like evidence for only khoản 54–57.
        for row in evidence_by_id.values():
            outline = row.get("exact_article_outline")
            opening = (
                str(outline.get("opening_excerpt") or "").strip()
                if isinstance(outline, Mapping)
                else ""
            )
            if opening:
                row["support_quote"] = opening[:1200]
    coverage = _packet_coverage(issue_order, rows, evidence_by_id, missing)
    coverage["delivery"] = observe_delivery(batch_issues, rows, evidence_by_id)
    missing = tuple(coverage["missing_issue_ids"])
    timing["context_build_ms"] = (time.perf_counter() - context_started) * 1000
    packet = EvidencePacket(
        request_id=request.request_id,
        release_id=release_id,
        manifest_hash=manifest_hash,
        legal_as_of=request.legal_as_of,
        router_decision=request.router_decision,
        evidence_by_id=evidence_by_id,
        context=context,
        scope=scope,
        missing_issue_ids=missing,
        coverage=coverage,
    )
    source_citations = _project_citations(list(evidence_by_id.values()), project_citation)
    if emit_progress is not None:
        await emit_progress(
            "sources",
            {
                "citations": list(source_citations),
                "provisional": True,
                "label": "Nguồn đang được đối chiếu",
                "retrieval_timing_ms": timing.get("retrieval_ms", 0.0),
            },
        )

    if any(bool(row.get("metadata_only_bound_source")) for row in evidence_by_id.values()):
        followup_question = str(request.question or "").casefold()
        validity_followup = any(
            marker in followup_question
            for marker in ("hiệu lực", "sửa đổi", "thay thế", "còn hiệu lực")
        )
        if validity_followup and isinstance(request.active_document, Mapping):
            answer = build_effectivity_source_answer(
                request.active_document,
                question=request.question,
            ) + " [E1]"
        else:
            answer = build_source_only_answer(
                evidence_by_id,
                question=request.question,
                max_units=1,
            )
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer=answer,
            citations=source_citations[:1],
            outcome="source_only",
            reason_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
            timing=timing,
            provider_error_code="bound_source_metadata_only",
        )

    exact_integrity_failed = bool(
        exact_issue_ids
        and any(
            not any(str(item.get("status") or "") == "complete"
                    for item in packets_by_issue.get(issue_id, ()))
            for issue_id in exact_issue_ids
        )
    )
    if exact_integrity_failed:
        if full_article and evidence_by_id:
            # A whole-text request must hand off to the official viewer when
            # the immutable exact packet is incomplete. Never present a
            # bounded chunk as if it were the full Article.
            return _result(
                started=started,
                request=request,
                packet=packet,
                answer=build_source_only_answer(
                    evidence_by_id,
                    question=request.question,
                ),
                citations=source_citations,
                outcome="source_only",
                reason_code="SOURCE_VIEW_REQUIRED",
                retryable=False,
                timing=timing,
                provider_error_code="source_view_required",
            )
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer=(
                "Kho dữ liệu chưa có gói Điều đầy đủ và liên tục cho yêu cầu này. "
                "Hệ thống không dùng một đoạn rời để suy ra toàn bộ Điều luật."
            ),
            citations=source_citations,
            outcome="source_only",
            reason_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
            timing=timing,
            provider_error_code="retrieval_unavailable",
        )

    # Exact full text that is too large is intentionally a viewer response.
    too_large_full_text = bool(
        full_article
        and (
            not evidence_by_id
            or any(bool(row.get("direct_content_truncated")) for row in evidence_by_id.values())
            or any("too_large" in mode or "fail_closed" in mode for mode in serving_modes)
        )
    )
    if too_large_full_text:
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer=build_source_only_answer(evidence_by_id, question=request.question),
            citations=source_citations,
            outcome="source_only",
            reason_code="SOURCE_VIEW_REQUIRED",
            retryable=False,
            timing=timing,
            provider_error_code="source_view_required",
        )

    if not evidence_by_id:
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer=build_source_only_answer({}, question=request.question),
            citations=(),
            outcome="source_only",
            reason_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
            timing=timing,
            provider_error_code="retrieval_unavailable",
        )

    first_row = next(iter(evidence_by_id.values()))
    if overview:
        answer = _outline_answer(first_row)
        if answer and source_citations:
            return _result(
                started=started,
                request=request,
                packet=packet,
                answer=answer,
                citations=source_citations[:1],
                outcome="answered",
                reason_code="NONE",
                retryable=False,
                timing=timing,
            )
    if (
        single_exact_issue and (full_article or not request.router_decision.get("semantic_plan")
                               or all(_value(i, "action") == "document_read" for i in request.issues))
        and not bounded_window
        and not bool(first_row.get("direct_content_truncated"))
    ):
        answer = _full_article_answer(first_row)
        if answer and len(answer) <= 12_500 and source_citations:
            return _result(
                started=started,
                request=request,
                packet=packet,
                answer=answer,
                citations=source_citations[:1],
                outcome="answered",
                reason_code="NONE",
                retryable=False,
                timing=timing,
            )

    prompt_started = time.perf_counter()
    prompt = build_prompt(
        question=request.provider_question or request.question,
        role=request.role,
        context=context,
        conversation_context=request.conversation_context,
        route=str(request.router_decision.get("legal_route") or "legal_query"),
        historical=str(request.router_decision.get("temporal_scope") or "") == "historical",
        max_history_chars=history_char_limit,
        max_evidence_chars=context_limit,
        packet_coverage=coverage,
        system_prompt_addendum=request.system_prompt_addendum,
        prompt_variant=request.prompt_variant,
        requested_facets=tuple(dict.fromkeys(f for issue in batch_issues for f in issue.get("facets", []))),
        structured_output=bool(request.router_decision.get("structured_answer")),
        answer_depth=request.answer_depth,
        communication_preferences=request.communication_preferences,
    )
    prompt += request.prompt_suffix
    timing["prompt_tokens_estimated"] = token_count(prompt)
    output_tokens = min(output_tokens, request.context_token_budget - token_count(prompt) - 128)
    if output_tokens < 256:
        return _result(started=started, request=request, packet=packet,
            answer="Gói yêu cầu vượt ngân sách ngữ cảnh của mô hình đã chọn. Hệ thống giữ lại nguồn để đối chiếu; lượt này chưa sinh được câu trả lời.",
            citations=source_citations, outcome="failed", reason_code="PROMPT_CONTEXT_EXCEEDED",
            retryable=False, timing=timing)
    timing["prompt_build_ms"] = (time.perf_counter() - prompt_started) * 1000
    if emit_progress is not None:
        await emit_progress("status", {"stage": "generating"})
    # Reserve space for structured Markdown and per-facet delivery metadata.
    generation_started = time.perf_counter()
    remaining_total = float(total_timeout) - (generation_started - started) - 0.25
    if remaining_total <= 0.05:
        timing["generation_ms"] = 0.0
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer="Đã hết ngân sách thời gian của lượt trả lời. Các nguồn đã tìm được vẫn được giữ để đối chiếu.",
            citations=source_citations,
            outcome="failed",
            reason_code="PROVIDER_TIMEOUT",
            retryable=True,
            timing=timing,
            provider_error_code="provider_timeout",
        )
    invocation_timeout = min(
        max(0.05, float(generation_timeout)),
        remaining_total,
    )
    try:
        generated = await asyncio.wait_for(
            generate(
                prompt,
                max_tokens=output_tokens,
                timeout=invocation_timeout,
            ),
            timeout=invocation_timeout,
        )
    except asyncio.TimeoutError:
        logging.getLogger(__name__).warning(
            "direct_answer_provider_timeout request_id=%s timeout_ms=%d",
            request.request_id,
            int(invocation_timeout * 1000),
        )
        timing["generation_ms"] = (time.perf_counter() - generation_started) * 1000
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer="Mô hình trả lời đang phản hồi chậm. Các nguồn đã tìm được vẫn được giữ để đối chiếu.",
            citations=source_citations,
            outcome="failed",
            reason_code="PROVIDER_TIMEOUT",
            retryable=True,
            timing=timing,
            provider_error_code="provider_timeout",
        )
    except Exception as exc:
        # Keep diagnostics credential-free: exception type and public status
        # are sufficient to distinguish adapter/configuration errors from an
        # upstream outage without recording prompts, response bodies or keys.
        logging.getLogger(__name__).warning(
            "direct_answer_provider_unavailable request_id=%s error_type=%s status=%s",
            request.request_id,
            type(exc).__name__,
            getattr(exc, "status_code", None),
        )
        timing["generation_ms"] = (time.perf_counter() - generation_started) * 1000
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer="Mô hình trả lời hiện không khả dụng. Các nguồn đã tìm được vẫn được giữ để đối chiếu.",
            citations=source_citations,
            outcome="failed",
            reason_code="PROVIDER_UNAVAILABLE",
            retryable=True,
            timing=timing,
            provider_error_code="provider_unavailable",
        )
    timing["generation_ms"] = (time.perf_counter() - generation_started) * 1000
    timing["model_calls"] = 1
    if isinstance(generated, GenerationOutput):
        timing.update(dict(generated.metadata))
        raw_answer = generated.text
    else:
        raw_answer = generated
    provider_answer = str(raw_answer or "").strip()
    timing["provider_answer_chars"] = len(provider_answer)
    timing["provider_declared_citation"] = bool(_CITATION_RE.search(provider_answer))
    if not provider_answer:
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer=(
                "Mô hình đã chọn không sinh nội dung trả lời. Các nguồn đã tìm "
                "được vẫn được giữ để anh/chị đối chiếu."
            ),
            citations=source_citations,
            outcome="failed",
            reason_code=timing.get("provider_error_code") or "PROVIDER_EMPTY_RESPONSE",
            retryable=True,
            timing=timing,
            provider_error_code=str(timing.get("provider_error_code") or "provider_empty_response").lower(),
        )
    answer, output_normalizations = normalize_provider_answer(
        provider_answer,
        evidence_by_id,
    )
    if output_normalizations:
        timing["output_normalization"] = ",".join(output_normalizations)
    soft_grounding = str(
        os.getenv("LEGAL_SOFT_GROUNDING_ENABLED", "false")
    ).strip().casefold() in {"1", "true", "yes", "on"}
    citation_started = time.perf_counter()
    citation_check = validate_citations(answer, evidence_by_id)
    timing["citation_check_ms"] = (time.perf_counter() - citation_started) * 1000
    cited = cited_rows(citation_check, evidence_by_id) if citation_check.valid else []
    citations = _project_citations(cited, project_citation)
    if not citation_check.valid or not citations:
        if soft_grounding:
            # Citation validation is telemetry in the provider-neutral serving
            # path. Keep the selected model's answer and expose the retrieved
            # sources; do not replace useful prose with a source-only fallback.
            timing["citation_warning"] = citation_check.reason_code
            return _result(
                started=started,
                request=request,
                packet=packet,
                answer=answer,
                citations=source_citations,
                outcome="partial",
                reason_code="SOFT_GROUNDING_WARNING",
                retryable=False,
                timing=timing,
            )
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer=build_source_only_answer(evidence_by_id, question=request.question),
            citations=source_citations,
            outcome="source_only",
            reason_code=citation_check.reason_code,
            retryable=False,
            timing=timing,
            provider_error_code="invalid_output",
        )

    facet_assessment = assess_requested_facets(
        question=request.question,
        answer=answer,
        sources=list(evidence_by_id.values()),
    )
    timing["requested_content_facets"] = ",".join(
        facet_assessment["requested"]
    )
    timing["missing_content_facets"] = ",".join(
        facet_assessment["missing"]
    )
    timing["completion_pass"] = "not_needed"
    repairable_facets = list(facet_assessment["repairable_missing"])
    if request.allow_completion_pass and repairable_facets:
        remaining_for_completion = (
            float(total_timeout) - (time.perf_counter() - started) - 0.15
        )
        completion_labels = {
            "authority": "chủ thể hoặc cơ quan có trách nhiệm/thẩm quyền",
            "deadline": "thời hạn hoặc giới hạn thời gian được hỏi",
            "condition": "điều kiện hoặc phạm vi áp dụng",
            "exception": (
                "trường hợp ngoại lệ hoặc cách xử lý khi không thực hiện "
                "hoạt động"
            ),
            "feedback_classification": (
                "phân loại rõ nội dung về cách phục vụ là khiếu nại hay "
                "kiến nghị/phản ánh theo các dấu hiệu trong nguồn"
            ),
        }
        missing_descriptions = [
            completion_labels.get(facet, facet) for facet in repairable_facets
        ]
        completion_prompt = (
            prompt
            + "\n\n### LƯỢT HOÀN THIỆN CÓ GIỚI HẠN\n"
            + "Bản nháp dưới đây đã có trích dẫn hợp lệ nhưng còn thiếu các vế "
            + "người dùng đã hỏi: "
            + "; ".join(missing_descriptions)
            + ". Hãy trả lại TOÀN BỘ câu trả lời Markdown đã hoàn thiện. Chỉ "
            + "dùng GÓI BẰNG CHỨNG ở trên; không đoán nội dung, số điều, thời hạn "
            + "hoặc mức tiền. Mọi khẳng định pháp lý vẫn phải gắn [E#]. Nếu gói "
            + "nguồn không đủ thì nêu rõ vế chưa đủ căn cứ.\n\n"
            + "Bản nháp:\n"
            + answer[:12000]
        )
        completion_budget_ok = (
            token_count(completion_prompt) + 256 < request.context_token_budget
        )
        if remaining_for_completion >= 0.75 and completion_budget_ok:
            completion_started = time.perf_counter()
            timing["completion_pass"] = "attempted"
            timing["model_calls"] = 2
            completion_timeout = min(
                5.0,
                max(0.1, float(generation_timeout)),
                remaining_for_completion,
            )
            try:
                repaired_raw = await asyncio.wait_for(
                    generate(
                        completion_prompt,
                        max_tokens=output_tokens,
                        timeout=completion_timeout,
                    ),
                    timeout=completion_timeout,
                )
                repaired_text = (
                    repaired_raw.text
                    if isinstance(repaired_raw, GenerationOutput)
                    else repaired_raw
                )
                candidate, candidate_normalizations = normalize_provider_answer(
                    str(repaired_text or "").strip(),
                    evidence_by_id,
                )
                candidate_check = validate_citations(candidate, evidence_by_id)
                candidate_cited = (
                    cited_rows(candidate_check, evidence_by_id)
                    if candidate_check.valid
                    else []
                )
                candidate_citations = _project_citations(
                    candidate_cited, project_citation
                )
                candidate_assessment = assess_requested_facets(
                    question=request.question,
                    answer=candidate,
                    sources=list(evidence_by_id.values()),
                )
                improved = len(candidate_assessment["missing"]) < len(
                    facet_assessment["missing"]
                )
                if candidate and candidate_check.valid and candidate_citations and improved:
                    answer = candidate
                    citation_check = candidate_check
                    cited = candidate_cited
                    citations = candidate_citations
                    facet_assessment = candidate_assessment
                    timing["missing_content_facets"] = ",".join(
                        facet_assessment["missing"]
                    )
                    timing["completion_pass"] = "accepted"
                    if candidate_normalizations:
                        timing["completion_output_normalization"] = ",".join(
                            candidate_normalizations
                        )
                else:
                    timing["completion_pass"] = (
                        "rejected_no_improvement"
                        if candidate_check.valid
                        else f"rejected_{candidate_check.reason_code.casefold()}"
                    )
            except asyncio.TimeoutError:
                timing["completion_pass"] = "timeout_kept_first_answer"
            except Exception as exc:
                timing["completion_pass"] = "error_kept_first_answer"
                logging.getLogger(__name__).warning(
                    "direct_answer_completion_failed request_id=%s error_type=%s",
                    request.request_id,
                    type(exc).__name__,
                )
            completion_ms = (time.perf_counter() - completion_started) * 1000
            timing["completion_ms"] = completion_ms
            timing["generation_ms"] = float(timing.get("generation_ms") or 0.0) + completion_ms
            timing["generation_total_ms"] = timing["generation_ms"]
        else:
            timing["completion_pass"] = (
                "skipped_context_budget"
                if not completion_budget_ok
                else "skipped_time_budget"
            )
    declared_gap = _declared_evidence_gap(answer)
    if declared_gap == "whole_question":
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer=answer,
            citations=citations,
            outcome="source_only",
            reason_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
            timing=timing,
            provider_error_code="insufficient_evidence",
        )
    limitation_stated = bool(declared_gap) or any(
        marker in answer.casefold()
        for marker in ("chưa đủ", "chỉ hỗ trợ", "chưa có căn cứ", "phần liên quan")
    )
    # An unused, truncated search candidate does not invalidate a complete
    # answer supported by other excerpts. Missing issues and truncated cited
    # passages still require an explicit limitation.
    packet_incomplete = bool(
        missing
        or any(
            row.get("direct_content_truncated")
            or row.get("dossier_group_truncated")
            for row in cited
        )
    )
    if request.router_decision.get("semantic_plan") and missing and not any(row.get("direct_content_truncated") for row in cited):
        # Keep the generated supported portion. This is an observation about
        # the packet, not a fabricated legal answer or semantic certification.
        if not limitation_stated:
            labels = [str(_value(i, "question") or _value(i, "query_text") or _value(i, "issue_id"))
                      for i in request.issues if str(_value(i, "issue_id")) in missing]
            answer += "\n\nGói nguồn của lượt này chưa có nội dung cho: " + "; ".join(labels or missing) + "."
            limitation_stated = True
    if packet_incomplete and not limitation_stated and not soft_grounding:
        return _result(
            started=started,
            request=request,
            packet=packet,
            answer=build_source_only_answer(evidence_by_id, question=request.question),
            citations=source_citations,
            outcome="source_only",
            reason_code="INSUFFICIENT_EVIDENCE",
            retryable=False,
            timing=timing,
            provider_error_code="insufficient_evidence",
        )
    output_truncated = str(timing.get("finish_reason") or "").casefold() in {
        "length", "max_tokens", "max_output_tokens", "token_limit"
    }
    interrupted = timing.get("finish_reason") == "interrupted"
    partial = interrupted or output_truncated or bool(declared_gap) or (packet_incomplete and limitation_stated)
    return _result(
        started=started,
        request=request,
        packet=packet,
        answer=answer,
        citations=citations,
        outcome="partial" if partial else "answered",
        reason_code=(
            "PROVIDER_STREAM_INTERRUPTED" if interrupted else "PROVIDER_OUTPUT_TRUNCATED"
            if output_truncated or interrupted
            else "INSUFFICIENT_EVIDENCE" if partial else "NONE"
        ),
        retryable=output_truncated or interrupted,
        timing=timing,
    )


__all__ = [
    "CitationCheck",
    "DirectAnswerResult",
    "DirectRagRequest",
    "EvidencePacket",
    "GenerationOutput",
    "build_prompt",
    "build_effectivity_source_answer",
    "build_source_only_answer",
    "cited_rows",
    "packet_id_map",
    "normalize_provider_answer",
    "render_packet_context",
    "run_direct_legal_answer",
    "validate_citations",
]
