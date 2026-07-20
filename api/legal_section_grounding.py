"""Deterministic primitives for Feature 005 section-level legal grounding.

The module has no network, model, corpus or persistence dependency.  It only
plans request-local issues, evaluates supplied source metadata and creates
privacy-safe measurements.  Router and Ask-graph integration remain behind
``LEGAL_SECTION_GROUNDING_ENABLED`` and are deliberately implemented elsewhere.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import os
import re
import unicodedata
from typing import Any, Iterable, Literal, Mapping, Sequence

from api.models import AnswerSection, CitationDisplayItem


IssueIntent = Literal[
    "rule",
    "condition",
    "authority",
    "documents",
    "procedure",
    "deadline",
    "fee",
    "dispute",
    "form",
    "unknown",
]
EvidenceStatus = Literal["accepted", "limited", "excluded"]

_TRUE_VALUES = {"1", "true", "yes", "on"}
_ACTIVE_STATUSES = {"active", "effective", "current", "con_hieu_luc"}
_KNOWN_SECTION_STATUSES = {
    "sufficiently_evidenced",
    "partially_evidenced",
    "insufficiently_evidenced",
}
_KNOWN_ERROR_CATEGORIES = {
    "none",
    "cancelled",
    "retrieval_unavailable",
    "provider_failure",
    "validation_failed",
    "timeout",
    "unknown",
}
_ALLOWED_TIMING_STAGES = {"retrieval", "generation", "validation", "persisting", "end_to_end"}
_FORBIDDEN_METRIC_KEY_PARTS = {
    "question",
    "answer",
    "citation",
    "attachment",
    "credential",
    "exception",
    "trace",
    "chunk",
    "packet",
}
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_PUBLIC_CITATION_FIELDS = (
    "document_title",
    "law_number",
    "article_number",
    "clause_number",
    "point_number",
    "effective_status",
    "legal_as_of",
    "source_url",
    "label",
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold()).replace("đ", "d")
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def _normalise_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,;:.?!")


@dataclass(frozen=True)
class LegalIssue:
    """One deterministic, request-local retrieval unit derived from the question."""

    issue_id: str
    request_id: str = ""
    text: str = ""
    domain: str = "unknown"
    intent: IssueIntent = "unknown"
    title: str = ""
    query_text: str = ""
    split_confidence: Literal["high", "low"] = "low"

    def __post_init__(self) -> None:
        query_text = self.query_text or self.text
        object.__setattr__(self, "query_text", query_text)
        object.__setattr__(self, "text", self.text or query_text)
        object.__setattr__(self, "title", self.title or "Nội dung cần xem xét")


@dataclass(frozen=True)
class EvidenceEligibilityDecision:
    """The non-generative reason a candidate may or may not support one issue."""

    source_id: str
    request_id: str
    issue_id: str
    status: EvidenceStatus
    reason: str
    source_metadata: Mapping[str, Any]

    @property
    def eligible(self) -> bool:
        return self.status == "accepted"


@dataclass
class CurrentRequestEvidencePacket:
    """Bounded evidence owned by a single current request and issue.

    Candidate evidence from a different request/issue is rejected here rather
    than being silently allowed to flow into an answer section.
    """

    request_id: str
    issue_id: str
    sources: list[Mapping[str, Any]] = field(default_factory=list)
    retrieval_tier: Literal["core", "expanded"] = "core"
    stage_timings_ms: Mapping[str, int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _is_opaque_id(self.request_id) or not _is_opaque_id(self.issue_id):
            raise ValueError("Evidence packet IDs must be opaque identifiers")
        for source in self.sources:
            if not evidence_matches_current_issue(source, self.request_id, self.issue_id):
                raise ValueError("Evidence packet source must match its current request and issue")

    @property
    def eligible_sources(self) -> list[Mapping[str, Any]]:
        return list(self.sources)


def is_section_grounding_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return false unless the explicit rollout flag is enabled."""

    values = os.environ if environ is None else environ
    return str(values.get("LEGAL_SECTION_GROUNDING_ENABLED", "false")).strip().casefold() in _TRUE_VALUES


def classify_issue_domain(text: str) -> str:
    """Use deterministic, conservative domain hints; unknown never proves relevance."""

    folded = _fold(text)
    if any(term in folded for term in ("quyen su dung dat", "tranh chap dat", "thua dat", "dat dai", "dia chinh")):
        return "land"
    if any(term in folded for term in ("lao dong", "hop dong lao dong", "nguoi lao dong", "tien luong")):
        return "labour"
    if any(term in folded for term in ("ho tich", "khai sinh", "ket hon", "khai tu")):
        return "civil_status"
    if any(term in folded for term in ("ubnd", "phuong", "ho so", "thu tuc", "hanh chinh", "noi nop")):
        return "administrative"
    return "unknown"


def _classify_intent(text: str) -> IssueIntent:
    folded = _fold(text)
    if "tranh chap" in folded or "hoa giai" in folded:
        return "dispute"
    if "tham quyen" in folded or "co quan" in folded or "ubnd" in folded:
        return "authority"
    if "ho so" in folded or "giay to" in folded:
        return "documents"
    if "le phi" in folded or "phi" in folded:
        return "fee"
    if "thoi han" in folded or "bao lau" in folded:
        return "deadline"
    if "bieu mau" in folded or "to khai" in folded:
        return "form"
    if "thu tuc" in folded or "trinh tu" in folded or "nop" in folded:
        return "procedure"
    if "dieu kien" in folded:
        return "condition"
    if folded:
        return "rule"
    return "unknown"


def plan_legal_issues(question: str, *, max_issues: int = 4) -> list[LegalIssue]:
    """Split a current question using only bounded, deterministic separators.

    A non-specific question remains exactly one low-confidence issue.  This
    prevents the planner from inventing issue boundaries or adding model calls.
    """

    if max_issues < 1:
        raise ValueError("max_issues must be positive")
    raw_question = re.sub(r"\s+", " ", question or "").strip()
    clean_question = _normalise_space(raw_question)
    if not clean_question:
        return [
            LegalIssue(
                issue_id="issue-1",
                title="Nội dung cần làm rõ",
                query_text="",
                intent="unknown",
                domain="unknown",
                split_confidence="low",
            )
        ]

    spans = [_normalise_space(part) for part in re.split(r"[;\n]+", clean_question)]
    unique_spans: list[str] = []
    seen: set[str] = set()
    for span in spans:
        key = _fold(span)
        if span and key not in seen:
            seen.add(key)
            unique_spans.append(span)

    confidence: Literal["high", "low"] = "high" if len(unique_spans) > 1 else "low"
    if confidence == "low":
        unique_spans = [raw_question]

    issues: list[LegalIssue] = []
    for index, span in enumerate(unique_spans[:max_issues], start=1):
        intent = _classify_intent(span)
        domain = classify_issue_domain(span)
        title = {
            "authority": "Thẩm quyền giải quyết",
            "documents": "Hồ sơ, giấy tờ",
            "fee": "Lệ phí",
            "deadline": "Thời hạn",
            "dispute": "Tranh chấp",
            "form": "Biểu mẫu",
            "procedure": "Trình tự, thủ tục",
        }.get(intent, "Nội dung cần xem xét")
        issues.append(
            LegalIssue(
                issue_id=f"issue-{index}",
                title=title,
                query_text=span,
                intent=intent,
                domain=domain,
                split_confidence=confidence,
            )
        )
    return issues


def evidence_matches_current_issue(
    source: Mapping[str, Any], request_or_issue: str | LegalIssue, issue_id: str | None = None
) -> bool:
    """Require exact server-side request and issue provenance before use."""

    if isinstance(request_or_issue, LegalIssue):
        request_id = request_or_issue.request_id
        issue_id = request_or_issue.issue_id
    else:
        request_id = request_or_issue
    return str(source.get("request_id") or "") == request_id and str(source.get("issue_id") or "") == str(issue_id or "")


def _has_required_metadata(source: Mapping[str, Any]) -> bool:
    has_source = bool(str(source.get("source_id") or source.get("id") or source.get("chunk_id") or "").strip())
    has_domain = bool(str(source.get("domain") or source.get("domain_slug") or "").strip())
    has_status = bool(str(source.get("effective_status") or source.get("document_status") or "").strip())
    has_authority = bool(source.get("official")) or bool(str(source.get("official_level") or "").strip()) or bool(str(source.get("issuing_agency") or "").strip())
    required = ("request_id", "issue_id", "scope", "source_url")
    return has_source and has_domain and has_status and has_authority and all(bool(str(source.get(key) or "").strip()) for key in required)


def _source_domains(source: Mapping[str, Any]) -> set[str]:
    value = source.get("domain") or source.get("domain_slug")
    if isinstance(value, (list, tuple, set)):
        return {_fold(item).replace(" ", "_") for item in value if str(item).strip()}
    return {_fold(item).replace(" ", "_") for item in str(value or "").split(",") if item.strip()}


def _is_effective(source: Mapping[str, Any]) -> bool:
    document_status = _fold(source.get("effective_status") or source.get("document_status")).replace(" ", "_")
    article_status = _fold(source.get("article_status") or "active").replace(" ", "_")
    return document_status in _ACTIVE_STATUSES and article_status in _ACTIVE_STATUSES


def _scope_rank(scope: Any) -> int:
    normalized = _fold(scope).replace(" ", "_")
    if normalized in {"central", "trung_uong", "national"}:
        return 0
    if normalized in {"haiphong", "hai_phong"}:
        return 1
    return 2


def _domains_match(issue_domain: str, source_domains: set[str]) -> bool:
    aliases = {
        "land": {"land", "dat_dai_moi_truong", "dat_dai", "dat_dai_xay_dung"},
        "dat_dai_moi_truong": {"land", "dat_dai_moi_truong", "dat_dai", "dat_dai_xay_dung"},
        "dat_dai_xay_dung": {"land", "dat_dai_moi_truong", "dat_dai", "dat_dai_xay_dung"},
        "labour": {"labour", "lao_dong"},
        "lao_dong": {"labour", "lao_dong"},
        "civil_status": {"civil_status", "ho_tich_chung_thuc", "tu_phap_ho_tich"},
        "ho_tich_chung_thuc": {"civil_status", "ho_tich_chung_thuc", "tu_phap_ho_tich"},
        "tu_phap_ho_tich": {"civil_status", "ho_tich_chung_thuc", "tu_phap_ho_tich"},
        "administrative": {"administrative", "hanh_chinh", "thu_tuc_hanh_chinh"},
        "hanh_chinh": {"administrative", "hanh_chinh", "thu_tuc_hanh_chinh"},
    }
    return bool(aliases.get(issue_domain, {issue_domain}).intersection(source_domains))


def _has_unresolved_conflict(source: Mapping[str, Any]) -> bool:
    if bool(source.get("conflicts_higher_authority")) or bool(source.get("unresolved_conflict")):
        return True
    relationships = source.get("relationships") or []
    return any(
        isinstance(item, Mapping)
        and str(item.get("relation_type") or "") in {"superseded_by", "conflicts_with"}
        and str(item.get("status") or "active") == "active"
        for item in relationships
    )


def _evaluate_candidate(
    *, request_id: str, issue: LegalIssue, source: Mapping[str, Any]
) -> EvidenceEligibilityDecision:
    source_id = str(source.get("source_id") or source.get("id") or source.get("chunk_id") or "unknown")
    common = {"source_id": source_id, "request_id": request_id, "issue_id": issue.issue_id, "source_metadata": dict(source)}
    if not evidence_matches_current_issue(source, request_id, issue.issue_id):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="request_or_issue_mismatch")
    if not _has_required_metadata(source):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="missing_required_metadata")
    if issue.domain == "unknown" or not _domains_match(issue.domain, _source_domains(source)):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="wrong_domain")
    if not _is_effective(source):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="not_effective")
    if _has_unresolved_conflict(source):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="unresolved_conflict")
    rank = _scope_rank(source.get("official_level") or source.get("scope"))
    if rank == 2:
        return EvidenceEligibilityDecision(**common, status="excluded", reason="unsupported_scope")
    if rank == 1 and bool(source.get("conflicts_higher_authority")):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="local_not_compatible")
    return EvidenceEligibilityDecision(**common, status="accepted", reason="eligible_current_issue_source")


def evaluate_evidence_eligibility(
    source: Mapping[str, Any], issue: LegalIssue, *, legal_as_of: str | None = None
) -> EvidenceEligibilityDecision:
    """Evaluate one candidate using the metadata present at retrieval time.

    ``legal_as_of`` is accepted for the orchestration contract.  Effective-date
    comparison is intentionally deferred until source metadata contains a
    normalized date range; explicit inactive status always fails closed now.
    """

    del legal_as_of
    return _evaluate_candidate(request_id=issue.request_id, issue=issue, source=source)


def select_eligible_evidence(
    *args: Any,
    request_id: str | None = None,
    issue: LegalIssue | None = None,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    legal_as_of: str | None = None,
) -> list[Any]:
    """Evaluate all candidates and order accepted central law before local detail."""

    legacy_rows = bool(args)
    if args:
        if len(args) < 2:
            raise TypeError("legacy select_eligible_evidence requires candidates and issue")
        candidates = args[0]
        issue = args[1]
        request_id = issue.request_id
    if issue is None or candidates is None or request_id is None:
        raise TypeError("request_id, issue and candidates are required")
    del legal_as_of
    decisions = [_evaluate_candidate(request_id=request_id, issue=issue, source=candidate) for candidate in candidates]
    indexed = list(enumerate(decisions))
    indexed.sort(
        key=lambda pair: (
            0 if pair[1].status == "accepted" else 1,
            _scope_rank(pair[1].source_metadata.get("official_level") or pair[1].source_metadata.get("scope")),
            pair[0],
        )
    )
    ordered = [decision for _, decision in indexed]
    if legacy_rows:
        return [dict(decision.source_metadata) for decision in ordered if decision.eligible]
    return ordered


def build_section_grounding_metric(
    *,
    request_id: str,
    statuses: Iterable[str],
    stage_timings_ms: Mapping[str, int | float],
    repair_count: int,
    completed: bool,
    error_category: str,
) -> dict[str, Any]:
    """Create a whitelist-shaped operational metric with no legal/user content."""

    if not _is_opaque_id(request_id):
        raise ValueError("request_id must be an opaque identifier")
    if repair_count < 0:
        raise ValueError("repair_count cannot be negative")
    status_counts = Counter(status for status in statuses if status in _KNOWN_SECTION_STATUSES)
    timings = {
        stage: int(value)
        for stage, value in stage_timings_ms.items()
        if stage in _ALLOWED_TIMING_STAGES and isinstance(value, (int, float)) and value >= 0
    }
    return {
        "request_id": request_id,
        "section_status_counts": dict(status_counts),
        "stage_timings_ms": timings,
        "repair_count": int(repair_count),
        "completed": bool(completed),
        "error_category": error_category if error_category in _KNOWN_ERROR_CATEGORIES else "unknown",
    }


def repair_rate(metrics: Iterable[Mapping[str, Any]]) -> float:
    """Return repairs/completed-non-cancelled metrics without reading any content."""

    completed = [
        item
        for item in metrics
        if item.get("completed") is True and item.get("error_category") != "cancelled"
    ]
    if not completed:
        return 0.0
    return sum(1 for item in completed if int(item.get("repair_count", 0)) > 0) / len(completed)


def format_public_citation(source: Mapping[str, Any]) -> dict[str, str]:
    """Project verified source metadata into the only supported public shape.

    This is deliberately allow-list based: retrieval IDs, trace keys and model
    marker syntax have no route from a candidate source to a public response.
    It formats metadata only and never manufactures a legal citation.
    """

    rendered: dict[str, str] = {}
    for field in _PUBLIC_CITATION_FIELDS:
        value = source.get(field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            rendered[field] = text
    return rendered


_GUIDANCE_LEGAL_CLAIM_RE = re.compile(
    r"\b(?:dieu\s*\d+|khoan\s*\d+|le\s*phi|phi\s*[:=]?\s*\d|"
    r"thoi\s*han|ngay\s*(?:lam\s*viec)?|ubnd\s*(?:phai|co\s*tham\s*quyen)|"
    r"co\s*tham\s*quyen|phai\s+(?:nop|giai\s*quyet|thuc\s*hien))\b",
    re.IGNORECASE,
)


def _section_sources(
    *, request_id: str, issue_id: str, sources: Iterable[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    """Retain only already eligible, exact request/issue evidence for a section."""

    return [
        source
        for source in sources
        if evidence_matches_current_issue(source, request_id, issue_id)
        and _has_required_metadata(source)
        and _is_effective(source)
        and not _has_unresolved_conflict(source)
        and _scope_rank(source.get("official_level") or source.get("scope")) < 2
    ]


def validate_answer_section(
    *,
    request_id: str,
    issue_id: str,
    title: str,
    sources: Iterable[Mapping[str, Any]],
    answer: str | None = None,
    guidance: str | None = None,
    limitation: str | None = None,
    clarifying_question: str | None = None,
) -> AnswerSection:
    """Build one fail-closed public section without generating legal content.

    A legal answer is permitted only when its packet has exact current-request
    evidence.  Guidance is deliberately stricter: it cannot include legal
    identifiers, mandatory authority, time or fee language.
    """

    eligible = _section_sources(
        request_id=request_id,
        issue_id=issue_id,
        sources=sources,
    )
    clean_answer = str(answer or "").strip()
    clean_guidance = str(guidance or "").strip()
    clean_limitation = str(limitation or "").strip()
    clean_question = str(clarifying_question or "").strip()

    if clean_answer and eligible:
        citations = [CitationDisplayItem(**format_public_citation(source)) for source in eligible]
        return AnswerSection(
            issue_id=issue_id,
            title=title,
            status="sufficiently_evidenced",
            answer=clean_answer,
            citations=citations,
        )
    if clean_guidance:
        if _GUIDANCE_LEGAL_CLAIM_RE.search(_fold(clean_guidance)):
            raise ValueError("partial guidance cannot contain a legal claim")
        return AnswerSection(
            issue_id=issue_id,
            title=title,
            status="partially_evidenced",
            guidance=clean_guidance,
            limitation=clean_limitation or "Chưa có đủ căn cứ để đưa ra kết luận pháp lý.",
        )
    return AnswerSection(
        issue_id=issue_id,
        title=title,
        status="insufficiently_evidenced",
        limitation=clean_limitation or "Chưa có nguồn hiện hành đủ để xác minh nội dung này.",
        clarifying_question=clean_question or None,
    )


def aggregate_answer_sections(sections: Sequence[AnswerSection]) -> dict[str, Any]:
    """Create legacy fields from validated sections without global fallback."""

    if not sections:
        return {"answer": "Chưa có nguồn hiện hành đủ để xác minh nội dung này.", "citations": [], "grounding_status": "insufficient_evidence"}
    has_sufficient = any(item.status == "sufficiently_evidenced" for item in sections)
    all_sufficient = all(item.status == "sufficiently_evidenced" for item in sections)
    chunks: list[str] = []
    citations: list[dict[str, Any]] = []
    seen_citations: set[tuple[str, str, str]] = set()
    for section in sections:
        body = section.answer if section.status == "sufficiently_evidenced" else section.guidance or section.limitation
        chunks.append(f"## {section.title}\n{body}")
        if section.clarifying_question:
            chunks.append(section.clarifying_question)
        if section.status == "sufficiently_evidenced":
            for citation in section.citations:
                rendered = citation.model_dump(mode="json", exclude_none=True)
                key = (
                    str(rendered.get("law_number") or ""),
                    str(rendered.get("article_number") or ""),
                    str(rendered.get("source_url") or ""),
                )
                if key not in seen_citations:
                    seen_citations.add(key)
                    citations.append(rendered)
    return {
        "answer": "\n\n".join(chunks),
        "citations": citations,
        "grounding_status": "fully_grounded" if all_sufficient else "partially_grounded" if has_sufficient else "insufficient_evidence",
    }


def build_sectioned_answer(
    *,
    question: str,
    request_id: str,
    generated_answers: Mapping[str, str],
    evidence_by_issue: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[list[AnswerSection], dict[str, Any]]:
    """Combine issue-local generated text only when its own evidence proves it.

    This adapter is fail-closed.  It intentionally never borrows an answer or
    a source from another issue to avoid turning a useful section into a broad
    but unsupported conclusion.
    """

    sections: list[AnswerSection] = []
    for issue in plan_legal_issues(question):
        sources = list(evidence_by_issue.get(issue.issue_id) or [])
        candidate = str(generated_answers.get(issue.issue_id) or "").strip()
        source_laws = [str(item.get("law_number") or "").strip() for item in sources]
        has_matching_citation = bool(candidate and any(law and law in candidate for law in source_laws))
        sections.append(
            validate_answer_section(
                request_id=request_id,
                issue_id=issue.issue_id,
                title=issue.title,
                answer=candidate if has_matching_citation else None,
                limitation=(
                    "Nội dung này chưa có căn cứ cùng lượt hỏi và cùng phần để đưa ra kết luận pháp lý."
                ),
                sources=sources,
            )
        )
    return sections, aggregate_answer_sections(sections)


def _is_opaque_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_OPAQUE_ID_RE.fullmatch(value))


def is_privacy_safe_metric(metric: Mapping[str, Any]) -> bool:
    """Reject metric payloads outside the explicit privacy-safe shape."""

    allowed = {
        "request_id",
        "section_status_counts",
        "stage_timings_ms",
        "repair_count",
        "completed",
        "error_category",
    }
    if set(metric) != allowed or not _is_opaque_id(metric.get("request_id")):
        return False
    if any(any(part in _fold(key) for part in _FORBIDDEN_METRIC_KEY_PARTS) for key in metric):
        return False
    statuses = metric.get("section_status_counts")
    timings = metric.get("stage_timings_ms")
    if not isinstance(statuses, Mapping) or not isinstance(timings, Mapping):
        return False
    if set(statuses).difference(_KNOWN_SECTION_STATUSES) or set(timings).difference(_ALLOWED_TIMING_STAGES):
        return False
    if not all(isinstance(value, int) and value >= 0 for value in statuses.values()):
        return False
    if not all(isinstance(value, int) and value >= 0 for value in timings.values()):
        return False
    return (
        isinstance(metric.get("repair_count"), int)
        and metric["repair_count"] >= 0
        and isinstance(metric.get("completed"), bool)
        and metric.get("error_category") in _KNOWN_ERROR_CATEGORIES
    )
