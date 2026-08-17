"""Deterministic primitives for Feature 005 section-level legal grounding.

The module has no network, model, corpus or persistence dependency.  It only
plans request-local issues, evaluates supplied source metadata and creates
privacy-safe measurements.  Router and Ask-graph integration remain behind
``LEGAL_SECTION_GROUNDING_ENABLED`` and are deliberately implemented elsewhere.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Literal, Mapping, Sequence
from urllib.parse import urlparse

from api.legal_taxonomy import classify_topic, topics_allow_source

from api.legal_exact_retrieval import normalize_exact_identifier, plan_exact_lookup
from api.legal_citation_provenance import enrich_public_citation
from api.models import AnswerSection, CitationDisplayItem

IssueIntent = Literal[
    "rule",
    "condition",
    "authority",
    "documents",
    "procedure",
    "verification",
    "recording",
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
    "provider_circuit_open",
    "provider_rate_limit",
    "provider_timeout",
    "invalid_output",
    "validation_failed",
    "timeout",
    "unknown",
}
_ALLOWED_TIMING_STAGES = {
    "retrieval",
    "provisioning",
    "generation",
    "validation",
    "persisting",
    "end_to_end",
}
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
    "authority_level",
    "authority_label",
)
_PUBLIC_VALIDITY_SYNC_FIELDS = (
    "status",
    "serving_action",
    "verified_at",
    "source_url",
    "effective_from",
    "effective_to",
    "warning_code",
    "reason_code",
    "would_block",
    "current_answer_eligible",
    "historical_lookup_allowed",
    "display_label",
)
_INTENT_EVIDENCE_TERMS: dict[str, tuple[str, ...]] = {
    "authority": (
        "tham quyen",
        "uy ban",
        "ubnd",
        "co quan dang ky",
        "co quan co tham quyen",
        "cong an xa",
        "cong an phuong",
        "noi nop",
    ),
    "documents": (
        "thanh phan ho so",
        "ho so bao gom",
        "giay to",
        "to khai",
        "xuat trinh",
        "kem theo",
        "ban sao",
        "ban chinh",
    ),
    "procedure": (
        "trinh tu",
        "thu tuc",
        "cac buoc",
        "xu ly",
        "thuc hien",
        "lap bien ban",
        "bien phap khac phuc",
        "ap dung",
        "xu phat",
    ),
    "verification": (
        "xac minh", "kiem tra", "doi chieu", "tra cuu", "trach nhiem",
        "gui van ban", "phoi hop",
    ),
    "recording": (
        "noi dung", "ghi vao", "ghi nhan", "cap nhat", "thong tin",
        "so ho tich", "co so du lieu ho tich",
    ),
    "deadline": ("thoi han", "ngay lam viec", "trong ngay", "trong thoi gian"),
    "fee": ("le phi", "muc thu", "mien le phi", "chi phi", "phi "),
    "form": ("bieu mau", "to khai", "mau don", "mau so", "theo mau"),
}

_PROCEDURE_TOPIC_TERMS: tuple[
    tuple[tuple[str, ...], tuple[str, ...]],
    ...,
] = (
    (("tinh trang hon nhan",), ("tinh trang hon nhan",)),
    (("khai sinh", "tre sinh"), ("khai sinh",)),
    (("ket hon",), ("ket hon", "dang ky hon nhan")),
    (
        ("sang ten", "chuyen nhuong nha dat", "bien dong dat dai"),
        ("sang ten", "chuyen nhuong", "bien dong dat dai", "dang ky bien dong"),
    ),
    (
        ("giay phep xay dung", "cap phep xay dung"),
        ("giay phep xay dung", "cap phep xay dung"),
    ),
    (
        ("xay dung",),
        ("xay dung", "trat tu xay dung"),
    ),
    (("khieu nai",), ("khieu nai",)),
    (("nguoi co cong",), ("nguoi co cong",)),
    (
        ("huu tri xa hoi", "tro cap huu tri xa hoi"),
        ("huu tri xa hoi", "tro cap huu tri xa hoi"),
    ),
    (
        ("thuong tru", "tam tru", "dang ky cu tru"),
        (
            "thuong tru",
            "tam tru",
            "dang ky cu tru",
            "cho o hop phap",
            "thong tin cu tru",
        ),
    ),
    (
        (
            "can cuoc",
            "the can cuoc",
            "can cuoc cong dan",
            "cccd",
            "dinh danh",
            "dinh danh dien tu",
            "vneid",
            "chung minh nhan dan",
            "cmnd",
            "cap the can cuoc",
            "doi the can cuoc",
            "cap lai the can cuoc",
            "xac thuc dien tu",
        ),
        (
            "can cuoc",
            "the can cuoc",
            "can cuoc cong dan",
            "dinh danh",
            "dinh danh dien tu",
            "vneid",
            "chung minh nhan dan",
            "so dinh danh ca nhan",
            "co so du lieu can cuoc",
        ),
    ),
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold()).replace("đ", "d")
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def _normalise_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" ,;:.?!")


def _supports_issue_intent(source: Mapping[str, Any], intent: str) -> bool:
    explicit_facets = {
        str(value or "").strip().casefold()
        for value in source.get("supported_facets") or ()
        if str(value or "").strip()
    }
    if str(intent or "").strip().casefold() in explicit_facets:
        # Reviewed adapters bind an exact passage to one issue/facet before
        # this generic lexical gate.  Re-running broad regexes here can reject
        # legally direct wording such as "người đã ra quyết định" merely
        # because the sentence does not repeat the word "thẩm quyền".
        return True
    terms = _INTENT_EVIDENCE_TERMS.get(intent)
    if not terms:
        return True
    body_fields = tuple(
        str(source.get(field) or "")
        for field in (
            "clean_matched_child_content",
            "matched_child_content",
            "clean_content",
            "content",
        )
    )
    text_fields = body_fields + tuple(
        str(source.get(field) or "")
        for field in (
            "chunk_heading",
            "heading",
            "article_title",
            "document_title",
        )
    )
    if not any(text_fields):
        return True
    searchable = _fold(" ".join(text_fields))
    if intent == "authority":
        body = _fold(" ".join(body_fields))
        if not body:
            return True
        direct_authority_patterns = (
            r"\btham quyen\b",
            r"\bco quan tiep nhan\b",
            r"\bco quan thuc hien thu tuc\b",
            r"\bnoi nop\b",
            r"\bnop\b.{0,100}\b(?:tai|den)\b.{0,120}\b(?:co quan|uy ban|ubnd|cong an|bo phan|van phong)\b",
            # Merely naming an agency is not proof that it receives or
            # decides the procedure.  In particular, sentences requiring a
            # notice to be sent "cho UBND cấp xã" must not become the answer
            # to a submission-place/authority question.
            r"\b(?:uy ban nhan dan|ubnd|chu tich uy ban|chu tich ubnd)\b.{0,120}\b(?:co tham quyen|giai quyet|tiep nhan|thuc hien thu tuc)\b",
            r"\b(?:uy ban nhan dan|ubnd|chu tich uy ban|chu tich ubnd)\b.{0,120}\b(?:ky cap|cap (?:giay|giay phep|giay chung nhan|giay xac nhan|giay khai sinh))\b",
            r"\b(?:uy ban nhan dan|ubnd|chu tich uy ban|chu tich ubnd)\b.{0,120}\bthuc hien\b.{0,40}\b(?:viec )?cap\b",
            r"\b(?:uy ban nhan dan|ubnd|chu tich uy ban|chu tich ubnd)\b.{0,120}\bthuc hien\b.{0,40}\b(?:viec )?dang ky\b",
            # Some official HTML loses the word "dân" while joining inline
            # nodes ("Ủy ban nhân thành phố..."). Accept that narrow source
            # artifact only for an explicit assignment followed by a direct
            # review/decision verb; a bare agency mention still cannot pass.
            r"\b(?:giao )?(?:uy ban nhan(?: dan)?|ubnd|chu tich uy ban|chu tich ubnd)\b.{0,160}\b(?:xem xet|quyet dinh)\b",
            r"\bnguoi da ra quyet dinh\b.{0,120}\bgiai quyet\b",
            r"\b(?:do|thuoc)\b.{0,80}\b(?:co quan|uy ban|ubnd|cong an)\b.{0,80}\bthuc hien\b",
        )
        return any(re.search(pattern, body) for pattern in direct_authority_patterns)
    if intent == "documents":
        # A title mentioning "hồ sơ" is not proof of what the applicant must
        # submit. Inspect the legal body and require a direct composition or
        # applicant-submission pattern. This excludes internal sentences such
        # as "Sở Tư pháp thẩm tra hồ sơ và văn bản kèm theo".
        body = _fold(" ".join(body_fields))
        if not body:
            # Metadata-only fixtures and legacy callers remain eligible; the
            # content gate cannot make a reliable negative decision here.
            return True
        direct_document_patterns = (
            r"\bthanh phan ho so\b",
            r"\bho so\b.{0,120}\b(?:gom|bao gom)\b",
            r"\bho so de nghi\b",
            r"\bho so (?:can )?nop\b",
            r"\bnguoi\b.{0,100}\bnop\b.{0,100}\b(?:to khai|don|ban sao|ban chinh|giay to|giay chung|tai lieu|ho so)\b",
            r"\bnop\b.{0,80}\b(?:to khai|don|ban sao|ban chinh|giay to|giay chung|tai lieu|ho so)\b",
            r"\bxuat trinh\b",
            r"\b(?:giay to|tai lieu)\b.{0,80}\bchung minh\b",
        )
        if any(re.search(pattern, body) for pattern in direct_document_patterns):
            return True
        if all(
            marker in body
            for marker in (
                "trinh tu dang ky bien dong",
                "chuyen quyen su dung dat",
                "thu tuc dang ky",
            )
        ):
            # Existing transfer-procedure compatibility path. Expanded support
            # still requires direct dossier composition before full coverage.
            return True
        return False
    # Metadata-only fixtures and legacy callers may not carry passage text;
    # intent filtering is a content-quality gate, not a reason to discard an
    # otherwise eligible metadata record when no text is available.
    return any(term in searchable for term in terms)


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
    priority: Literal["critical", "high", "normal"] = "normal"
    subject: str = ""
    location: str = ""
    facts: tuple[str, ...] = ()
    relevance_topics: tuple[str, ...] = ()
    applied_date: str | None = None
    expected_sources: tuple[Any, ...] = ()
    expected_form: Mapping[str, Any] | None = None
    procedure_family: str | None = None

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
    retrieval_tier: Literal["core", "expanded", "full_corpus"] = "core"
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
    if any(
        term in folded
        for term in (
            "quyen su dung dat",
            "tranh chap dat",
            "thua dat",
            "dat dai",
            "dia chinh",
            "khu dat",
            "dat xen ket",
            "thu hoi dat",
            "so do",
            "so hong",
            "cap giay chung nhan",
            "cap so",
            "dat",
            "xay dung",
            "nha o",
            "giay phep xay dung",
            "chung cu",
        )
    ):
        return "land"
    if any(term in folded for term in ("lao dong", "hop dong lao dong", "nguoi lao dong", "tien luong")):
        return "labour"
    if any(
        term in folded
        for term in (
            "ho tich",
            "khai sinh",
            "tre sinh",
            "ket hon",
            "khai tu",
            "trich luc",
            "chung thuc",
            "bao hiem y te",
            "bhyt",
            "thuong tru",
            "tam tru",
            "cu tru",
            "can cuoc",
            "lien thong",
        )
    ):
        return "civil_status"
    if any(term in folded for term in ("khieu nai", "to cao", "xu phat", "vi pham hanh chinh")):
        return "khieu_nai_to_cao_xu_phat"
    return "unknown"


def retrieval_domain_slug(
    issue_domain: str,
    selected_domain: str | None,
    query: str | None = None,
) -> str | None:
    """Translate policy-level issue domains into indexed runtime slugs."""

    exact = plan_exact_lookup(query or "")
    if selected_domain and exact.law_number and exact.article_number:
        # The reviewed/requested domain is authoritative. Words inside an
        # exact Article quotation (for example "khai tử" in a social-security
        # rule) must not silently reroute the issue to another field.
        return selected_domain

    # The runtime-selected domain is already a canonical serving scope. Do
    # not let a broad planner label remap it to a different legacy slug. This
    # matters for multi-issue questions where the planner assigns the same
    # generic label to every issue while the request explicitly selected a
    # scope.
    if selected_domain and issue_domain in {
        "",
        "unknown",
        "administrative",
        "civil_status",
        "land",
        "labour",
    }:
        return selected_domain

    if issue_domain == "unknown":
        return selected_domain or None
    aliases = {
        "civil_status": "ho_tich_chung_thuc",
        "land": "dat_dai_xay_dung",
        "labour": "lao_dong",
        "administrative": selected_domain,
    }
    if (issue_domain in ("administrative", "unknown") or not issue_domain) and not selected_domain:
        folded = _fold(query)
        if any(
            term in folded
            for term in (
                "khieu nai",
                "xu phat",
                "quyet dinh xu",
                "vi pham hanh chinh",
                "bi phat",
                "lap bien ban",
                "bien ban vi pham",
            )
        ):
            return "khieu_nai_to_cao_xu_phat"
        if any(
            term in folded
            for term in (
                "dat",
                "dat dai",
                "so do",
                "so hong",
                "gcn",
                "giay chung nhan",
                "thua dat",
                "xay dung",
                "cap phep",
                "nha o",
                "chung cu",
                "nha chung cu",
                "ban quan tri",
                "cong nhan ban quan tri",
            )
        ):
            return "dat_dai_xay_dung"
        if any(
            term in folded
            for term in (
                "khai sinh",
                "khai tu",
                "ket hon",
                "ly hon",
                "ho tich",
                "chung thuc",
                "sao y",
                "trich luc",
                "tinh trang hon nhan",
            )
        ):
            return "ho_tich_chung_thuc"
        if any(
            term in folded
            for term in (
                "cu tru",
                "thuong tru",
                "tam tru",
                "nhap khau",
                "tach ho",
                "can cuoc",
                "cccd",
                "dinh danh",
                "bien so",
                "dang ky xe",
            )
        ):
            return "cu_tru_an_ninh"
        if any(term in folded for term in ("nguoi co cong", "tro cap", "an sinh", "bao hiem y te", "bhyt")):
            return "an_sinh_y_te_giao_duc"
        return None
    res = aliases.get(issue_domain, issue_domain)
    if res in ("administrative", "unknown"):
        return selected_domain or None
    return res or None


def _classify_intent(text: str) -> IssueIntent:
    folded = _fold(text)
    if "tranh chap" in folded or "hoa giai" in folded:
        return "dispute"
    if (
        "tham quyen" in folded
        or "co quan" in folded
        or "ubnd" in folded
        or "noi nop" in folded
        or "nop o dau" in folded
    ):
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


def _legacy_plan_legal_issues(question: str, *, max_issues: int = 6) -> list[LegalIssue]:
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

    facet_signal = (
        r"(?:điều\s+kiện|quyền|thẩm\s+quyền|nơi\s+nộp|cơ\s+quan|"
        r"hồ\s+sơ|giấy\s+tờ|các\s+bước|trình\s+tự|thủ\s+tục|"
        r"thời\s+hạn|bao\s+lâu|lệ\s+phí|phí|biểu\s+mẫu|tờ\s+khai|"
        r"tranh\s+chấp|hòa\s+giải)"
    )
    split_pattern = re.compile(
        rf"(?:[;,\n]+|\b(?:và|nhưng)\b)\s*(?={facet_signal}\b)",
        re.IGNORECASE,
    )
    # Semicolon/newline is always an explicit boundary, even when a later
    # comma/conjunction also matches the facet lookahead. Split it first so a
    # rule clause cannot accidentally absorb the next legal question.
    explicit_parts = re.split(r"[;\n]+", clean_question)
    spans = [
        _normalise_space(part)
        for explicit_part in explicit_parts
        for part in split_pattern.split(explicit_part)
    ]
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

    # The first span may already contain one or more facets (for example
    # "nộp ở đâu, cần hồ sơ gì").  It is still the shared subject/fact anchor
    # for every later issue and must not be discarded merely because it has an
    # intent keyword of its own.
    anchor = ""
    anchor_was_removed = False
    if len(unique_spans) > 1:
        first_span = unique_spans[0]
        first_intent = _classify_intent(first_span)
        # A natural comma-separated subject ("Tôi đăng ký ..., hồ sơ ...")
        # is an anchor, not a standalone rule issue.  An explicit semicolon
        # or newline, however, is a deliberate boundary and must preserve a
        # first rule clause (for example "xây dựng không phép; lập biên bản").
        explicit_boundary = bool(re.search(r"[;\n]", clean_question))
        if first_intent == "unknown" or (
            first_intent == "rule" and not explicit_boundary
        ):
            anchor = first_span
            unique_spans = unique_spans[1:]
            anchor_was_removed = True
        else:
            anchor = first_span

    issues: list[LegalIssue] = []
    for index, span in enumerate(unique_spans[:max_issues], start=1):
        intent = _classify_intent(span)
        query_text = (
            span
            if not anchor or (index == 1 and not anchor_was_removed)
            else _normalise_space(f"{anchor}, {span}")
        )
        domain = classify_issue_domain(query_text)
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
                query_text=query_text,
                intent=intent,
                domain=domain,
                split_confidence=confidence,
            )
        )
    return issues


def _planner_context_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return (value,)


def _planner_infer_location(question: str) -> str:
    match = re.search(
        r"\b(?:tại|ở|thuộc|cư trú)\s+([^,;.?!]+)",
        question,
        re.IGNORECASE,
    )
    return _normalise_space(match.group(1)) if match else ""


def _planner_infer_date(question: str) -> str | None:
    match = re.search(
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b",
        question,
    )
    return match.group(0) if match else None


def _planner_infer_topic_subject(question: str) -> str:
    """Carry a deterministic procedure topic into every split facet."""

    folded = _fold(question)
    if "dang ky thuong tru" in folded and "tam tru" in folded:
        # Retrieval metadata uses folded procedure labels.  Keeping both
        # alternatives prevents later facet splits from silently collapsing
        # the citizen's question to only permanent residence.
        return "dang ky thuong tru tam tru"
    specific_subjects = (
        (
            ("giay xac nhan tinh trang hon nhan",),
            "Giấy xác nhận tình trạng hôn nhân",
        ),
        (("dang ky lai khai sinh",), "đăng ký lại khai sinh"),
        (("dang ky khai sinh", "khai sinh cho con"), "đăng ký khai sinh"),
        (
            ("giay phep xay dung", "cap phep xay dung"),
            (
                "cấp giấy phép xây dựng nhà ở riêng lẻ"
                if "nha o rieng le" in folded
                else "cấp giấy phép xây dựng"
            ),
        ),
        (("dang ky tam tru",), "đăng ký tạm trú"),
        (("dang ky thuong tru",), "đăng ký thường trú"),
        (
            ("khieu nai lan dau",),
            (
                "khiếu nại lần đầu quyết định hành chính"
                if "quyet dinh hanh chinh" in folded
                else "khiếu nại lần đầu"
            ),
        ),
        (
            ("tro cap huu tri xa hoi", "huu tri xa hoi"),
            "trợ cấp hưu trí xã hội",
        ),
    )
    for markers, label in specific_subjects:
        if any(marker in folded for marker in markers):
            return label

    matched: list[str] = []
    for query_terms, _ in _PROCEDURE_TOPIC_TERMS:
        available = [term for term in query_terms if term in folded]
        if available:
            most_specific = max(available, key=len)
            if most_specific not in matched:
                matched.append(most_specific)
    return " ".join(matched)


def _planner_meaningful(value: str) -> bool:
    return len(re.findall(r"\w+", _fold(value), flags=re.UNICODE)) >= 2


def _planner_infer_facts(question: str) -> tuple[str, ...]:
    """Preserve explicit user facts, especially absence and negation.

    These values only focus retrieval. They never become legal conclusions or
    trusted metadata.
    """

    negative_markers = (
        "khong con", "khong co", "chua co", "khong luu", "khong hop tac",
        "da mat", "bi mat", "that lac", "khong xuat trinh", "khong cung cap",
    )
    positive_fact_markers = (
        "hop dong co chu ky",
        "co chu ky chu nha",
    )
    output_markers = (
        "the nao", "ra sao", "nhu the nao", "bao lau", "bao nhieu",
        "noi dung ghi nhan", "trach nhiem xac minh", "dieu kien", "giay to",
        "ho so", "trinh tu", "tham quyen", "noi nop", "le phi",
    )
    fragments = re.split(r"[;,:?]+|\b(?:và|nhưng|đồng thời)\b", question or "", flags=re.IGNORECASE)
    facts: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        cleaned = _normalise_space(fragment)
        folded = _fold(cleaned)
        if not cleaned or not any(
            marker in folded
            for marker in (*negative_markers, *positive_fact_markers)
        ):
            continue
        # Drop trailing requests while preserving the factual negative clause.
        cut_positions = [folded.find(marker) for marker in output_markers if folded.find(marker) > 0]
        if cut_positions:
            words = cleaned.split()
            folded_words = _fold(cleaned).split()
            cut_word = min(
                len(folded_words),
                min(len(folded[:position].split()) for position in cut_positions),
            )
            cleaned = " ".join(words[:cut_word]).strip(" ,;:.") or cleaned
            folded = _fold(cleaned)
        if folded and folded not in seen:
            seen.add(folded)
            facts.append(cleaned)
    return tuple(facts[:6])


def _planner_intent(text: str) -> IssueIntent:
    folded = _fold(text)
    if any(
        marker in folded
        for marker in (
            "trach nhiem xac minh", "trach nhiem kiem tra", "noi dung xac minh",
            "phai xac minh", "kiem tra xac minh", "doi chieu thong tin",
        )
    ):
        return "verification"
    if any(
        marker in folded
        for marker in (
            "noi dung ghi nhan", "noi dung can ghi", "ghi vao so",
            "ghi nhung noi dung", "thong tin duoc ghi", "cap nhat noi dung",
        )
    ):
        return "recording"
    if re.search(r"\b(neu|truong hop|khi|dieu kien)\b", folded) or "ap dung" in folded:
        return "condition"
    if "tranh chap" in folded or "hoa giai" in folded:
        return "dispute"
    if any(term in folded for term in ("tham quyen", "co quan", "ubnd", "noi nop", "nop o dau")):
        return "authority"
    if any(term in folded for term in ("bieu mau", "to khai", "mau so", "mau 09", "mau 01")):
        return "form"
    if "ho so" in folded or "giay to" in folded:
        return "documents"
    if "le phi" in folded or "phi" in folded:
        return "fee"
    if "thoi han" in folded or "bao lau" in folded:
        return "deadline"
    if any(term in folded for term in ("thu tuc", "trinh tu", "cac buoc", "nop")):
        return "procedure"
    if folded:
        return "rule"
    return "unknown"


_NUMBERED_ISSUE_MARKER_RE = re.compile(
    r"(?<!\w)(?:\((?P<paren>[1-8])\)|(?P<plain>[1-8])[.)])\s+"
)
_QUOTED_ISSUE_RE = re.compile(r"[\u201c\"](?P<body>[^\u201d\"]+)[\u201d\"]")


def _numbered_issue_spans(question: str) -> list[str]:
    """Extract an explicitly numbered issue list without its preface."""

    matches = list(_NUMBERED_ISSUE_MARKER_RE.finditer(question))
    numbers = [
        int(match.group("paren") or match.group("plain")) for match in matches
    ]
    if len(matches) < 2 or numbers != list(range(1, len(numbers) + 1)):
        return []
    output: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(question)
        value = _normalise_space(question[match.end() : end].strip(" ;"))
        if value:
            output.append(value)
    return output


def _parenthetical_suffix(question: str, start: int) -> str:
    cursor = start
    while cursor < len(question) and question[cursor].isspace():
        cursor += 1
    if cursor >= len(question) or question[cursor] != "(":
        return ""
    depth = 0
    for end in range(cursor, len(question)):
        if question[end] == "(":
            depth += 1
        elif question[end] == ")":
            depth -= 1
            if depth == 0:
                return question[cursor : end + 1]
    return ""


def _quoted_multi_issue_spans(question: str) -> list[str]:
    """Extract quoted items from the reviewed multi-issue question form."""

    folded = _fold(question)
    if not any(
        marker in folded
        for marker in ("hai van de", "tung van de", "cac van de")
    ):
        return []
    matches = list(_QUOTED_ISSUE_RE.finditer(question))
    if len(matches) < 2:
        return []
    return [
        _normalise_space(
            f"{match.group('body')} {_parenthetical_suffix(question, match.end())}"
        )
        for match in matches
        if _normalise_space(match.group("body"))
    ]


def _split_top_level_facets(question: str) -> list[str]:
    """Split ordinary facets while protecting quoted/parenthetical content."""

    output: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    index = 0

    def flush() -> None:
        value = _normalise_space("".join(current))
        current.clear()
        if value:
            output.append(value)

    while index < len(question):
        character = question[index]
        if character in {"\u201c", "\u201d", '"'}:
            if quote is None:
                quote = character
            elif character == '"' or (quote == "\u201c" and character == "\u201d"):
                quote = None
            current.append(character)
            index += 1
            continue
        if quote is None:
            if character == "(":
                depth += 1
            elif character == ")" and depth:
                depth -= 1
            if depth == 0 and character in {",", ";", "\n"}:
                flush()
                index += 1
                continue
            if depth == 0:
                conjunction = next(
                    (
                        token
                        for token in (" và ", " nhưng ", " hoặc ")
                        if question[index : index + len(token)].casefold()
                        == token.casefold()
                    ),
                    None,
                )
                if conjunction:
                    flush()
                    index += len(conjunction)
                    continue
        current.append(character)
        index += 1
    flush()
    return output


def _planner_is_clarification_request(question: str) -> bool:
    """Detect questions whose primary task is collecting missing facts.

    These are deliberately narrow, deterministic markers. They prevent the
    issue splitter from turning one incomplete legal request into several
    near-identical retrieval jobs; they do not collapse explicitly numbered or
    quoted legal issues.
    """

    folded = _fold(question)
    markers = (
        "chua neu thu tuc cu the",
        "chua neu tuoi",
        "chua cung cap ngay",
        "chua cung cap thong tin",
        "chua cho biet noi",
        "chua xac dinh dieu kien",
        "thong tin mau thuan",
        "anh bi cat mat",
        "co the ket luan ngay khong",
        "khong co van ban chinh thuc",
        "tu van lua chon mang tinh ca nhan",
        "khong phai cau hoi co the ket luan",
        "chua co ho so va chung cu",
        "chua biet co quan",
    )
    return any(marker in folded for marker in markers)


def plan_legal_issues(
    question: str,
    *,
    max_issues: int = 6,
    context: Mapping[str, Any] | None = None,
    explicit_only: bool = False,
) -> list[LegalIssue]:
    """Plan bounded issues while copying request context to every issue.

    ``explicit_only`` is the V2 answer-router mode. In that mode commas and
    conjunctions describe requested coverage inside one issue; only an
    explicitly numbered or reviewed quoted list creates sibling issues. The
    legacy facet splitter remains available to the rollback pipeline.
    """

    if max_issues < 1:
        raise ValueError("max_issues must be positive")
    raw_question = re.sub(r"\s+", " ", question or "").strip()
    clean_question = _normalise_space(raw_question)
    values = dict(context or {})
    subject = _normalise_space(
        str(values.get("subject") or _planner_infer_topic_subject(clean_question))
    )
    location = _normalise_space(
        str(
            values["location"]
            if "location" in values
            else _planner_infer_location(clean_question)
        )
    )
    explicit_facts = tuple(
        str(item).strip()
        for item in _planner_context_tuple(values.get("facts"))
        if str(item).strip()
    )
    facts = explicit_facts or _planner_infer_facts(clean_question)
    applied_date = str(
        values.get("applied_date") or _planner_infer_date(clean_question) or ""
    ).strip() or None
    expected_sources = _planner_context_tuple(values.get("expected_sources"))
    expected_form = values.get("expected_form")
    domain_override = str(values.get("domain") or "").strip()

    if not clean_question:
        return [
            LegalIssue(
                issue_id="issue-1",
                title="Nội dung cần làm rõ",
                query_text="",
                intent="unknown",
                domain=domain_override or "unknown",
                split_confidence="low",
                subject=subject,
                location=location,
                facts=facts,
                applied_date=applied_date,
                expected_sources=expected_sources,
                expected_form=expected_form if isinstance(expected_form, Mapping) else None,
            )
        ]

    explicit_spans = _numbered_issue_spans(clean_question)
    if not explicit_spans:
        explicit_spans = _quoted_multi_issue_spans(clean_question)
    explicit_issue_groups = bool(explicit_spans)
    conditional = re.match(
        r"^(?P<prefix>(?:nếu|trường hợp|khi)\b.+?)\s+thì\s+(?P<body>.+)$",
        clean_question,
        re.IGNORECASE,
    )
    exact_plan = plan_exact_lookup(clean_question)
    clarification_only = (
        not explicit_spans
        and _planner_is_clarification_request(clean_question)
    )
    if explicit_issue_groups:
        raw_spans = explicit_spans
    elif explicit_only:
        raw_spans = [clean_question]
    elif clarification_only:
        # Missing facts, truncated identifiers, contradictory dates and
        # requests for a certainty/personal recommendation are one
        # clarification task. Splitting them into rule/deadline/condition
        # siblings only repeats the same broad retrieval and multiplies
        # latency without adding legal coverage.
        raw_spans = [clean_question]
    elif exact_plan.law_number and exact_plan.article_number:
        # A named Article is one structural request. Conditions, requirements,
        # fees, and other requested facets are coverage requirements inside
        # that packet, not independent retrieval issues.
        raw_spans = [clean_question]
    elif conditional:
        prefix = _normalise_space(conditional.group("prefix"))
        body = _normalise_space(conditional.group("body"))
        raw_spans = [prefix] + _split_top_level_facets(body)
    else:
        raw_spans = _split_top_level_facets(clean_question)
    spans: list[str] = []
    seen: set[str] = set()
    for span in raw_spans:
        clean_span = _normalise_space(span)
        key = _fold(clean_span)
        if clean_span and key not in seen and _planner_meaningful(clean_span):
            seen.add(key)
            spans.append(clean_span)
    if not spans:
        spans = [clean_question]

    # The subject/facts/date anchor is retained in every query. The explicit
    # condition prefix is also a shared anchor for the remaining facets.
    anchor_parts = [
        value for value in (subject, location, applied_date, *facts) if value
    ]
    anchor = ", ".join(
        value
        for value in anchor_parts
        if value in facts or _fold(value) not in _fold(clean_question)
    )
    anchor_issue_kept = False
    if explicit_issue_groups:
        anchor = ""
        anchor_issue_kept = True
    elif conditional and spans:
        anchor = _normalise_space(f"{spans[0]}, {anchor}" if anchor else spans[0])
        anchor_issue_kept = True
    elif len(spans) > 1:
        first_intent = _planner_intent(spans[0])
        anchor = _normalise_space(f"{spans[0]}, {anchor}" if anchor else spans[0])
        if first_intent in {"rule", "unknown"}:
            spans = spans[1:]
        else:
            anchor_issue_kept = True

    # Comma-separated factual setup (family, rental contract, location) must
    # remain retrieval context, not consume issue slots as standalone rule
    # sections. Keep an explicit legal-topic rule clause as an issue.
    subject_terms = set(_fold(subject).split())
    while (
        not explicit_issue_groups
        and len(spans) > 1
        and _planner_intent(spans[0]) in {"rule", "unknown"}
    ):
        span_terms = set(_fold(spans[0]).split())
        if subject_terms and subject_terms.intersection(span_terms):
            break
        anchor = _normalise_space(
            f"{anchor}, {spans[0]}" if anchor else spans[0]
        )
        spans = spans[1:]

    specs: list[tuple[str, IssueIntent]] = []
    for span_index, span in enumerate(spans):
        intent = _planner_intent(span)
        query = (
            anchor
            if anchor and anchor_issue_kept and span_index == 0
            else span if not anchor else _normalise_space(f"{anchor}, {span}")
        )
        specs.append((query, intent))
    if len(specs) == 1 and not context:
        specs[0] = (raw_question, specs[0][1])
    if len(specs) > max_issues:
        fee_index = next((i for i, item in enumerate(specs) if item[1] == "fee"), None)
        form_index = next((i for i, item in enumerate(specs) if item[1] == "form"), None)
        if fee_index is not None and form_index is not None:
            first, second = sorted((fee_index, form_index))
            specs[first] = (_normalise_space(f"{specs[first][0]}, {specs[second][0]}"), "fee")
            specs.pop(second)
    specs = specs[:max_issues]
    confidence: Literal["high", "low"] = "high" if len(specs) > 1 else "low"
    titles = {
        "authority": "Thẩm quyền giải quyết",
        "documents": "Hồ sơ, giấy tờ",
        "fee": "Lệ phí / biểu mẫu",
        "deadline": "Thời hạn",
        "dispute": "Tranh chấp",
        "form": "Biểu mẫu",
        "procedure": "Trình tự, thủ tục",
        "verification": "Trách nhiệm kiểm tra, xác minh",
        "recording": "Nội dung cần ghi nhận",
        "condition": "Điều kiện",
    }
    issues: list[LegalIssue] = []
    for index, (query_text, intent) in enumerate(specs, start=1):
        issue_domain = classify_issue_domain(query_text)
        issues.append(
            LegalIssue(
                issue_id=f"issue-{index}",
                title=titles.get(intent, "Nội dung cần xem xét"),
                query_text=query_text,
                intent=intent,
                domain=domain_override or issue_domain,
                split_confidence=confidence,
                subject=subject,
                location=location,
                facts=facts,
                applied_date=applied_date,
                expected_sources=expected_sources,
                expected_form=expected_form if isinstance(expected_form, Mapping) else None,
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


def _has_approved_source_provenance(source: Mapping[str, Any]) -> bool:
    """Reject local/test sources before they can support a displayed claim.

    Runtime legal evidence must resolve to an HTTPS official-government source.
    Explicit provenance flags remain available for approved mirrors used by a
    controlled deployment, but they never override an unsafe/local URL.
    """

    raw_url = str(source.get("source_url") or "").strip()
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    if parsed.scheme.casefold() != "https" or not host:
        return False
    if (
        host == "localhost"
        or host.endswith(".localhost")
        or host.endswith(".local")
        or host.endswith(".test")
        or host in {"127.0.0.1", "::1"}
    ):
        return False
    if (
        host in {"vbpl.vn", "www.vbpl.vn", "chinhphu.vn"}
        or host.endswith(".chinhphu.vn")
        or host.endswith(".gov.vn")
    ):
        return True
    return bool(
        source.get("official")
        or source.get("provenance_verified")
        or source.get("confirmed_official_source")
    )


def _source_domains(source: Mapping[str, Any]) -> set[str]:
    value = source.get("domain") or source.get("domain_slug")
    if isinstance(value, (list, tuple, set)):
        return {_fold(item).replace(" ", "_") for item in value if str(item).strip()}
    return {_fold(item).replace(" ", "_") for item in str(value or "").split(",") if item.strip()}


def _parse_legal_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _is_effective(
    source: Mapping[str, Any],
    legal_as_of: str | date | None = None,
) -> bool:
    document_status = _fold(source.get("effective_status") or source.get("document_status")).replace(" ", "_")
    article_status = _fold(
        source.get("effective_article_status")
        or source.get("article_status")
        or "active"
    ).replace(" ", "_")
    if document_status not in _ACTIVE_STATUSES or article_status not in _ACTIVE_STATUSES:
        return False
    as_of = _parse_legal_date(legal_as_of)
    if as_of is None:
        return True
    validity_sync = source.get("validity_sync")
    sync = validity_sync if isinstance(validity_sync, Mapping) else {}
    effective_from = _parse_legal_date(
        source.get("effective_from")
        or source.get("effective_date")
        or sync.get("effective_from")
    )
    effective_to = _parse_legal_date(
        source.get("effective_to")
        or source.get("expired_date")
        or sync.get("effective_to")
    )
    if effective_from and as_of < effective_from:
        return False
    if effective_to and as_of > effective_to:
        return False
    return True


def _scope_rank(scope: Any) -> int:
    normalized = _fold(scope).replace(" ", "_")
    if normalized in {
        "central",
        "trung_uong",
        "national",
        "nationwide",
        "toan_quoc",
        "ca_nuoc",
    }:
        return 0
    if normalized in {
        "haiphong",
        "hai_phong",
        "thanh_pho_hai_phong",
        "hai_phong_city",
    }:
        return 1
    return 2


def _domains_match(issue_domain: str, source_domains: set[str]) -> bool:
    if not source_domains:
        return True
    if not issue_domain or issue_domain == "unknown":
        return True
    aliases = {
        "land": {"land", "dat_dai_moi_truong", "dat_dai", "dat_dai_xay_dung", "xay_dung_do_thi"},
        "dat_dai_moi_truong": {"land", "dat_dai_moi_truong", "dat_dai", "dat_dai_xay_dung"},
        "dat_dai_xay_dung": {"land", "dat_dai_moi_truong", "dat_dai", "dat_dai_xay_dung", "xay_dung_do_thi"},
        "labour": {"labour", "lao_dong", "an_sinh_y_te_giao_duc"},
        "lao_dong": {"labour", "lao_dong", "an_sinh_y_te_giao_duc"},
        "an_sinh_y_te_giao_duc": {
            "an_sinh_y_te_giao_duc",
            "an_sinh_y_te",
            "giao_duc_van_hoa",
            "labour",
            "lao_dong",
            "civil_status",
            "ho_tich_chung_thuc",
        },
        "civil_status": {"civil_status", "ho_tich_chung_thuc", "tu_phap_ho_tich", "an_sinh_y_te_giao_duc", "cu_tru_an_ninh"},
        "ho_tich_chung_thuc": {"civil_status", "ho_tich_chung_thuc", "tu_phap_ho_tich", "an_sinh_y_te_giao_duc", "cu_tru_an_ninh"},
        "tu_phap_ho_tich": {"civil_status", "ho_tich_chung_thuc", "tu_phap_ho_tich"},
        "administrative": {"administrative", "hanh_chinh", "thu_tuc_hanh_chinh"},
        "hanh_chinh": {"administrative", "hanh_chinh", "thu_tuc_hanh_chinh"},
    }
    return bool(aliases.get(issue_domain, {issue_domain}).intersection(source_domains))


def _issue_matches_exact_legal_identity(
    issue: LegalIssue,
    source: Mapping[str, Any],
) -> bool:
    """Allow an unknown-domain issue only when the user named this exact law.

    A question that contains only a document number often has no domain words.
    Treating ``unknown`` as a real domain correctly fails closed for fuzzy
    questions, but it must not hide a document whose verified number was
    supplied verbatim by the user.  Article identity is also enforced when the
    question names an article.
    """

    exact = plan_exact_lookup(issue.query_text)
    requested_laws = set(exact.law_numbers)
    if not requested_laws:
        return False
    if normalize_exact_identifier(source.get("law_number")) not in requested_laws:
        return False
    if exact.article_law_pairs:
        return (
            normalize_exact_identifier(source.get("law_number")),
            normalize_exact_identifier(source.get("article_number")),
        ) in set(exact.article_law_pairs)
    if exact.article_numbers:
        return (
            normalize_exact_identifier(source.get("article_number"))
            in set(exact.article_numbers)
        )
    return True


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


def _source_is_foreign_representation_only(source: Mapping[str, Any]) -> bool:
    jurisdiction = _fold(source.get("source_jurisdiction")).replace(" ", "_")
    if jurisdiction == "foreign_representation":
        return True
    metadata = _fold(
        " ".join(
            str(source.get(field) or "")
            for field in (
                "document_title",
                "applicability_info",
                "issuing_agency",
                "collection_source",
            )
        )
    )
    return any(
        marker in metadata
        for marker in (
            "co quan dai dien ngoai giao",
            "co quan dai dien lanh su",
            "co quan dai dien viet nam o nuoc ngoai",
        )
    )


def _issue_explicitly_uses_foreign_representation(issue: LegalIssue) -> bool:
    query = _fold(issue.query_text)
    return any(
        marker in query
        for marker in (
            "co quan dai dien",
            "dai su quan",
            "lanh su quan",
            "co quan lanh su",
        )
    )


def _issue_is_birth_registration(issue: LegalIssue) -> bool:
    query = _fold(issue.query_text)
    return "khai sinh" in query or "tre sinh" in query


def _issue_has_foreign_birth_factor(issue: LegalIssue) -> bool:
    query = _fold(issue.query_text)
    return any(
        marker in query
        for marker in (
            "nuoc ngoai",
            "yeu to nuoc ngoai",
            "nguoi nuoc ngoai",
            "quoc tich nuoc ngoai",
            "dieu 35",
            "dieu 36",
        )
    )


def _source_is_foreign_birth_provision(source: Mapping[str, Any]) -> bool:
    law_number = str(source.get("law_number") or "").upper().replace("Đ", "D")
    article_number = str(source.get("article_number") or "").strip()
    if law_number == "60/2014/QH13" and article_number in {"35", "36"}:
        return True
    if law_number in {"123/2015/ND-CP", "123/2015/NĐ-CP"} and article_number == "29":
        return True
    searchable = _fold(
        " ".join(
            str(source.get(field) or "")
            for field in ("article_title", "chunk_heading", "applicability_info")
        )
    )
    return "khai sinh" in searchable and (
        "yeu to nuoc ngoai" in searchable or "sinh o nuoc ngoai" in searchable
    )


def _source_matches_procedure_topic(
    issue: LegalIssue,
    source: Mapping[str, Any],
) -> bool:
    raw_query = " ".join(
        (
            issue.query_text,
            issue.subject,
            *issue.facts,
        )
    )
    # Parenthetical legal excerpts are evidence descriptors, not necessarily
    # the citizen's requested procedure. Ignoring them here prevents quoted
    # words such as “khiếu nại” from poisoning a separate “hành vi bị cấm” or
    # residence-rights issue, while the full text remains available to
    # retrieval and answer generation.
    query = _fold(re.sub(r"\([^()]*\)", " ", raw_query))
    searchable = _fold(
        " ".join(
            str(source.get(field) or "")
            for field in (
                "content",
                "chunk_heading",
                "article_title",
                "document_title",
                "procedure_id",
                "applicability_info",
            )
        )
    )
    asks_birth_reregistration = "dang ky lai khai sinh" in query
    source_is_birth_reregistration = any(
        marker in searchable
        for marker in (
            "dang ky lai khai sinh", "dang ky lai viec sinh", "khai sinh duoc dang ky lai",
        )
    )
    operational_birth_facets = {
        "condition", "documents", "procedure", "verification", "authority", "deadline", "fee", "form",
    }
    if asks_birth_reregistration and issue.intent in operational_birth_facets:
        if not source_is_birth_reregistration:
            return False
    if (
        not asks_birth_reregistration
        and source_is_birth_reregistration
        and issue.intent in operational_birth_facets
    ):
        return False
    if "giay phep xay dung" in query or "cap phep xay dung" in query:
        if not any(
            marker in searchable
            for marker in ("giay phep xay dung", "cap phep xay dung")
        ):
            return False
    if any(
        marker in query
        for marker in ("thuong tru", "tam tru", "dang ky cu tru")
    ):
        topic_searchable = _fold(
            " ".join(
                str(source.get(field) or "")
                for field in (
                    "content",
                    "chunk_heading",
                    "article_title",
                    "procedure_id",
                    "applicability_info",
                )
            )
        )
        if not any(
            marker in topic_searchable
            for marker in (
                "thuong tru",
                "tam tru",
                "dang ky cu tru",
                "cho o hop phap",
                "thong tin cu tru",
            )
        ):
            return False
    if any(marker in query for marker in ("sang ten", "chuyen nhuong nha dat")):
        if not any(
            marker in searchable
            for marker in (
                "sang ten",
                "chuyen nhuong",
                "chuyen quyen",
                "hop dong mua ban",
                "hop dong chuyen quyen",
            )
        ):
            return False
    if "dang ky tam tru" in query:
        if not any(
            marker in searchable
            for marker in ("dang ky tam tru", "ho so dang ky tam tru")
        ):
            return False
        if "xoa dang ky tam tru" in searchable and "xoa dang ky" not in query:
            return False
        if "gia han tam tru" in searchable and "gia han" not in query:
            return False
    if any(
        marker in query
        for marker in ("xay dung khong phep", "xay khong phep", "khong co giay phep")
    ):
        law_number = (
            str(source.get("law_number") or "")
            .upper()
            .replace("NĐ", "ND")
            .replace("Đ", "D")
        )
        article_number = str(source.get("article_number") or "").strip()
        if law_number == "15/2012/QH13":
            return True
        if law_number == "16/2022/ND-CP" and article_number == "16":
            return True
        if not any(
            marker in searchable
            for marker in (
                "xay dung khong phep",
                "xay dung khong co giay phep",
                "cong trinh khong co giay phep",
                "vi pham trat tu xay dung",
            )
        ):
            return False
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
            return False
    if any(
        marker in query
        for marker in (
            "can cuoc",
            "the can cuoc",
            "can cuoc cong dan",
            "cccd",
            "dinh danh",
            "dinh danh dien tu",
            "vneid",
            "chung minh nhan dan",
            "cmnd",
            "so dinh danh",
        )
    ):
        if not any(
            marker in searchable
            for marker in (
                "can cuoc",
                "the can cuoc",
                "can cuoc cong dan",
                "dinh danh",
                "dinh danh dien tu",
                "vneid",
                "chung minh nhan dan",
                "cmnd",
                "so dinh danh",
                "co so du lieu can cuoc",
            )
        ):
            return False

    # Exclude Law on Residence when query does not ask about residence
    asks_residence = any(
        marker in query
        for marker in (
            "thuong tru",
            "tam tru",
            "cu tru",
            "noi cu tru",
            "luu tru",
            "tam vang",
            "ho khau",
            "so ho khau",
            "dang ky cu tru",
            "xoa dang ky",
            "thong tin cu tru",
        )
    )
    is_residence_law = (
        "68/2020/QH14" in str(source.get("law_number") or "").upper().replace("Đ", "D")
        or "luat cu tru" in _fold(source.get("document_title") or "")
    )
    if is_residence_law and not asks_residence and "68/2020" not in query and "luat cu tru" not in query:
        return False

    topic_terms = next(
        (
            source_terms
            for query_terms, source_terms in _PROCEDURE_TOPIC_TERMS
            if any(term in query for term in query_terms)
        ),
        None,
    )
    if topic_terms is None:
        return True
    return any(term in searchable for term in topic_terms)


def issue_requires_expanded_support(
    issue: LegalIssue,
    selected_sources: Sequence[Mapping[str, Any]],
) -> bool:
    """Return true when core evidence lacks a reviewed direct instrument.

    Cross-cutting administrative-sanctions law is useful for procedure, but it
    cannot replace the construction-specific sanction instrument for an
    unlicensed-building question.
    """

    query = _fold(issue.query_text)
    if issue.intent == "documents" and any(
        marker in query
        for marker in ("ho so", "giay to", "can nop", "can chuan bi")
    ):
        # A provision that merely mentions a file or states what happens
        # after receipt is not enough to answer "which documents?". Ask for
        # the one allowed support pass unless core contains an actual dossier
        # composition/submission clause.
        direct_document_markers = (
            "thanh phan ho so",
            "ho so gom",
            "nop to khai",
            "nop giay",
            "xuat trinh",
            "kem theo",
            "giay chung sinh",
            "ban chinh",
            "ban sao",
        )
        searchable = " ".join(
            _fold(source.get("content") or source.get("text") or "")
            for source in selected_sources
        )
        if not any(marker in searchable for marker in direct_document_markers):
            return True
    if not any(
        marker in query
        for marker in (
            "xay dung khong phep",
            "xay khong phep",
            "khong co giay phep",
        )
    ):
        return False
    law_numbers = {
        str(source.get("law_number") or "")
        .upper()
        .replace("NĐ", "ND")
        .replace("Đ", "D")
        for source in selected_sources
    }
    return "16/2022/ND-CP" not in law_numbers


def _evaluate_candidate(
    *,
    request_id: str,
    issue: LegalIssue,
    source: Mapping[str, Any],
    legal_as_of: str | date | None = None,
) -> EvidenceEligibilityDecision:
    source_id = str(source.get("source_id") or source.get("id") or source.get("chunk_id") or "unknown")
    common = {"source_id": source_id, "request_id": request_id, "issue_id": issue.issue_id, "source_metadata": dict(source)}
    if not evidence_matches_current_issue(source, request_id, issue.issue_id):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="request_or_issue_mismatch")
    if not _has_required_metadata(source):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="missing_required_metadata")
    if not _has_approved_source_provenance(source):
        return EvidenceEligibilityDecision(
            **common,
            status="excluded",
            reason="unapproved_source_provenance",
        )
    exact_identity_match = _issue_matches_exact_legal_identity(issue, source)
    exact_plan = plan_exact_lookup(issue.query_text)
    if exact_plan.law_numbers and not exact_identity_match:
        # An identifier supplied by the user is a hard legal-source boundary,
        # not merely a ranking hint. Other same-domain or higher-authority
        # instruments may be useful in broad research, but they cannot answer
        # a question explicitly scoped to this document/provision.
        return EvidenceEligibilityDecision(
            **common,
            status="excluded",
            reason="wrong_legal_identity",
        )
    elif (
        issue.domain
        and issue.domain != "unknown"
        and not _domains_match(issue.domain, _source_domains(source))
        and not exact_identity_match
    ):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="wrong_domain")
    if (
        _source_is_foreign_representation_only(source)
        and not _issue_explicitly_uses_foreign_representation(issue)
    ):
        return EvidenceEligibilityDecision(
            **common,
            status="excluded",
            reason="wrong_jurisdiction",
        )
    if (
        _issue_is_birth_registration(issue)
        and _source_is_foreign_birth_provision(source)
        and not _issue_has_foreign_birth_factor(issue)
    ):
        return EvidenceEligibilityDecision(
            **common,
            status="excluded",
            reason="wrong_jurisdiction",
        )
    source_family = str(source.get("procedure_family") or "").strip().casefold()
    issue_family = str(issue.procedure_family or "").strip().casefold()
    if issue_family and source_family and source_family != issue_family:
        return EvidenceEligibilityDecision(
            **common,
            status="excluded",
            reason="wrong_procedure_family",
        )
    # An explicit document + Article identity is a stronger boundary than the
    # heuristic procedure-topic classifier. The classifier can reject a
    # valid Article when its heading uses legal language different from the
    # citizen's paraphrase. Keep all hard gates above, but do not discard the
    # exact packet solely because of that secondary topic heuristic.
    exact_article_identity = bool(
        exact_identity_match
        and exact_plan.law_number
        and exact_plan.article_number
        and len(exact_plan.article_numbers or ()) == 1
    )
    if not exact_article_identity and not _source_matches_procedure_topic(issue, source):
        return EvidenceEligibilityDecision(
            **common,
            status="excluded",
            reason="wrong_procedure_topic",
        )
    if not _supports_issue_intent(source, issue.intent):
        return EvidenceEligibilityDecision(**common, status="excluded", reason="intent_not_supported")
    if not _is_effective(source, legal_as_of):
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

    return _evaluate_candidate(
        request_id=issue.request_id,
        issue=issue,
        source=source,
        legal_as_of=legal_as_of,
    )


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
    decisions = [
        _evaluate_candidate(
            request_id=request_id,
            issue=issue,
            source=candidate,
            legal_as_of=legal_as_of,
        )
        for candidate in candidates
    ]
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


def format_public_validity_sync(value: Any) -> dict[str, Any]:
    """Allow-list the legal-validity fields safe for citizen/officer display."""

    if not isinstance(value, Mapping):
        return {}
    rendered: dict[str, Any] = {}
    for field_name in _PUBLIC_VALIDITY_SYNC_FIELDS:
        if field_name not in value:
            continue
        field_value = value.get(field_name)
        if field_value is None:
            rendered[field_name] = None
            continue
        text = str(field_value).strip()
        if text:
            rendered[field_name] = text
    return rendered


def format_public_citation(source: Mapping[str, Any]) -> dict[str, Any]:
    """Project verified source metadata into the only supported public shape.

    This is deliberately allow-list based: retrieval IDs, trace keys and model
    marker syntax have no route from a candidate source to a public response.
    It formats metadata only and never manufactures a legal citation.
    """

    rendered: dict[str, str] = {}
    for citation_field in _PUBLIC_CITATION_FIELDS:
        value = source.get(citation_field)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            rendered[citation_field] = text
    if "effective_status" not in rendered:
        runtime_status = str(source.get("document_status") or "").strip()
        if runtime_status:
            rendered["effective_status"] = runtime_status
    validity_sync = format_public_validity_sync(source.get("validity_sync"))
    if validity_sync:
        rendered["validity_sync"] = validity_sync
    enriched = enrich_public_citation({**source, **rendered})
    return {
        **rendered,
        **{
            key: value
            for key, value in enriched.items()
            if key
            in {
                "verification_level",
                "verification_status",
                "verification_reason",
                "viewer_url",
                "proof",
            }
        },
    }


_GUIDANCE_LEGAL_CLAIM_RE = re.compile(
    r"\b(?:dieu\s*\d+|khoan\s*\d+|le\s*phi|phi\s*[:=]?\s*\d|"
    r"thoi\s*han|ngay\s*(?:lam\s*viec)?|ubnd\s*(?:phai|co\s*tham\s*quyen)|"
    r"co\s*tham\s*quyen|phai\s+(?:nop|giai\s*quyet|thuc\s*hien))\b",
    re.IGNORECASE,
)


def _section_sources(
    *, request_id: str, issue_id: str, sources: Iterable[Mapping[str, Any]],
    relevance_topics: Sequence[str] = (),
) -> list[Mapping[str, Any]]:
    """Retain only already eligible, exact request/issue evidence for a section."""

    eligible = []
    for source in sources:
        if not (evidence_matches_current_issue(source, request_id, issue_id)
                and _has_required_metadata(source)
                and _is_effective(source)
                and not _has_unresolved_conflict(source)
                and _scope_rank(source.get("official_level") or source.get("scope")) < 2):
            continue

        # Domain-aware cross-contamination guard (V1 taxonomy)
        query_topics = set(relevance_topics)
        if query_topics:
            from api.legal_taxonomy import LegalTopic as _LT
            question_topic_objs = [_LT(t) for t in query_topics if t in _LT._value2member_map_]
            src_domain = source.get("domain") or source.get("legal_domain")
            if question_topic_objs and src_domain and not topics_allow_source(
                question_topic_objs, src_domain
            ):
                continue

        eligible.append(source)
    return eligible



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
    facet: IssueIntent | None = None,
    priority: Literal["critical", "high", "normal"] | None = None,
    claim_types: Sequence[str] = (),
    relevance_topics: Sequence[str] = (),
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
        relevance_topics=relevance_topics,
    )
    clean_answer = str(answer or "").strip()
    clean_guidance = str(guidance or "").strip()
    clean_limitation = str(limitation or "").strip()
    clean_question = str(clarifying_question or "").strip()
    public_claim_types = list(
        dict.fromkeys(
            value
            for value in (str(item or "").strip() for item in claim_types)
            if value
            in {
                "rule",
                "condition",
                "authority",
                "documents",
                "procedure",
                "next_action",
                "deadline",
                "fee",
                "form",
                "exception",
                "warning",
            }
        )
    )
    presentation = {
        "facet": facet,
        "priority": priority,
        "claim_types": public_claim_types,
    }

    if clean_answer and eligible:
        citations: list[CitationDisplayItem] = []
        seen_citations: set[tuple[str, str, str, str, str]] = set()
        for source in eligible:
            rendered = format_public_citation(source)
            key = (
                str(rendered.get("law_number") or ""),
                str(rendered.get("article_number") or ""),
                str(rendered.get("clause_number") or ""),
                str(rendered.get("point_number") or ""),
                str(rendered.get("source_url") or ""),
            )
            if key in seen_citations:
                continue
            seen_citations.add(key)
            citations.append(CitationDisplayItem(**rendered))
        return AnswerSection(
            issue_id=issue_id,
            title=title,
            status="sufficiently_evidenced",
            answer=clean_answer,
            citations=citations,
            clarifying_question=clean_question or None,
            **presentation,
        )
    if clean_guidance:
        if _GUIDANCE_LEGAL_CLAIM_RE.search(_fold(clean_guidance)):
            raise ValueError("partial guidance cannot contain a legal claim")
        return AnswerSection(
            issue_id=issue_id,
            title=title,
            status="partially_evidenced",
            guidance=clean_guidance,
            clarifying_question=clean_question or None,
            limitation=clean_limitation or "Chưa có đủ căn cứ để đưa ra kết luận pháp lý.",
            **presentation,
        )
    return AnswerSection(
        issue_id=issue_id,
        title=title,
        status="insufficiently_evidenced",
        limitation=clean_limitation or "Chưa có nguồn hiện hành đủ để xác minh nội dung này.",
        clarifying_question=clean_question or None,
        **presentation,
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
    seen_claims: set[str] = set()
    for section in sections:
        body = section.answer if section.status == "sufficiently_evidenced" else section.guidance or section.limitation
        rendered_lines: list[str] = []
        for line in str(body or "").splitlines():
            plain = line.strip()
            claim = plain[2:].strip() if plain.startswith("- ") else ""
            claim = re.sub(r"^\*\*[^*]+:\*\*\s*", "", claim).strip()
            claim = re.sub(r"^[^:*]{2,80}:\s*", "", claim).strip()
            claim_key = _fold(claim)
            if claim_key and claim_key in seen_claims:
                continue
            if claim_key:
                seen_claims.add(claim_key)
            rendered_lines.append(line)
        rendered_body = "\n".join(rendered_lines).strip()
        if rendered_body:
            chunks.append(f"## {section.title}\n{rendered_body}")
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
                facet=issue.intent,
                priority=issue.priority,
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
