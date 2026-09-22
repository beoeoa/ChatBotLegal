"""Request-scoped evidence and post-generation guardrails.

This module is deliberately deterministic.  It does not decide what the law
means and it does not manufacture missing legal facts.  Its job is narrower:
bind a retrieved source to the current question, preserve the source unit,
and remove an unsupported model sentence while retaining supported material.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
import unicodedata
from typing import Any, Mapping, Sequence

from api.legal_domains import canonicalize_legal_domain


_TRUE_STATUS = {"active", "effective", "current", "con_hieu_luc"}
_BAD_STATUS = {"expired", "repealed", "superseded", "inactive", "het_hieu_luc"}
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"(?![A-Za-z0-9_-])"
)

_FACET_ALIASES = {
    "conditions": "condition",
    "condition": "condition",
    "documents": "documents",
    "document": "documents",
    "authority": "authority",
    "place": "authority",
    "submission_place": "authority",
    "deadline": "deadline",
    "processing_time": "deadline",
    "fee": "fee",
    "fees": "fee",
    "form": "form",
    "forms": "form",
    "procedure": "procedure",
    "process": "procedure",
    "next_action": "procedure",
    "legal_basis": "legal_basis",
    "rule": "rule",
    "exceptions": "exception",
    "exception": "exception",
}

_FACET_MARKERS: dict[str, tuple[str, ...]] = {
    "condition": (
        "dieu kien",
        "du dieu kien",
        "truong hop",
        "doi tuong",
        "ap dung doi voi",
    ),
    "documents": (
        "ho so",
        "giay to",
        "thanh phan ho so",
        "to khai",
        "ban sao",
        "ban chinh",
    ),
    "authority": (
        "nop o dau",
        "nop tai",
        "nop ho so tai",
        "noi nop",
        "co quan co tham quyen",
        "co quan tiep nhan",
        "tham quyen",
    ),
    "deadline": (
        "thoi han",
        "ngay lam viec",
        "trong thoi han",
        "thoi gian giai quyet",
        "bao lau",
    ),
    "fee": (
        "le phi",
        "muc thu",
        "mien le phi",
        "chi phi",
        " phi ",
    ),
    "form": (
        "bieu mau",
        "mau don",
        "mau so",
        "to khai theo mau",
        " mau ",
    ),
    "procedure": (
        "trinh tu",
        "thu tuc",
        "cac buoc",
        "tiep nhan",
        "xu ly",
    ),
    "legal_basis": ("can cu phap ly", "dieu ", "khoan ", "diem "),
    "exception": ("ngoai tru", "tru truong hop", "ngoai le", "khong ap dung"),
    "rule": ("quy dinh", "duoc", "phai", "khong duoc"),
}

_SUBJECT_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("khai sinh", ("dang ky lai khai sinh", "dang ky khai sinh", "khai sinh")),
    ("ket hon", ("dang ky ket hon", "ket hon")),
    ("khai tu", ("dang ky khai tu", "khai tu")),
    ("cu tru", ("dang ky tam tru", "dang ky thuong tru", "tam tru", "thuong tru", "cu tru")),
    ("can cuoc", ("the can cuoc", "can cuoc cong dan", "can cuoc", "cccd")),
    ("giay phep xay dung", ("giay phep xay dung", "gpxd")),
    ("dat dai", ("quyen su dung dat", "tach thua", "sang ten", "dat dai", "thua dat")),
    ("khieu nai", ("khieu nai lan dau", "khieu nai")),
    ("to cao", ("to cao",)),
    ("tro cap huu tri xa hoi", ("tro cap huu tri xa hoi", "huu tri xa hoi")),
    ("bao tro xa hoi", ("bao tro xa hoi", "tro cap xa hoi")),
    ("bao hiem y te", ("bao hiem y te",)),
)

_GROUP_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("nguoi cao tuoi", ("nguoi cao tuoi",)),
    ("ho ngheo", ("ho ngheo", "nguoi ngheo")),
    ("nguoi khuyet tat", ("nguoi khuyet tat",)),
    ("nguoi co cong", ("nguoi co cong",)),
    ("nguoi nuoc ngoai", ("nguoi nuoc ngoai", "nguoi nuoc ngoai")),
    ("nguoi chua thanh nien", ("nguoi chua thanh nien", "tre em")),
    ("doanh nghiep", ("doanh nghiep",)),
    ("chu dau tu", ("chu dau tu",)),
)

_ACTOR_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chu tich ubnd phuong", ("chu tich ubnd phuong", "chu tich uy ban nhan dan phuong", "chu tich ubnd cap xa")),
    ("chu tich ubnd cap xa", ("chu tich ubnd cap xa", "chu tich ubnd phuong", "chu tich uy ban nhan dan cap xa")),
    ("thu truong co quan cong an", ("thu truong co quan cong an",)),
    ("cong an cap xa", ("cong an cap xa", "cong an phuong", "cong an xa")),
)

_CLAIM_MARKERS = (
    "ho so",
    "giay to",
    "nop ",
    "thoi han",
    "ngay lam viec",
    "le phi",
    "muc thu",
    "dong",
    "tham quyen",
    "co quan",
    "dieu kien",
    "truong hop",
    "thu tuc",
    "trinh tu",
    "buoc",
    "bieu mau",
    "mau ",
    "to khai",
    "quy dinh",
    "duoc ",
    "khong duoc",
    "phai ",
    "can cu",
    "dieu ",
    "khoan ",
)

_GAP_MARKERS = (
    "chua du can cu",
    "chua xac minh",
    "tai lieu hien tai khong du",
    "nguon hien co chua",
    "chua co du lieu",
    "khong du can cu da duoc loai",
)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.replace("đ", "d").split())


def _contains(text: str, marker: str) -> bool:
    return marker in f" {text} "


def _normalise_facet(value: Any) -> str:
    folded = _fold(value).replace("-", "_").replace(" ", "_")
    return _FACET_ALIASES.get(folded, folded)


def _source_id(row: Mapping[str, Any], index: int = 0) -> str:
    return str(
        row.get("source_id")
        or row.get("chunk_id")
        or row.get("evidence_id")
        or row.get("id")
        or f"candidate-{index + 1}"
    ).strip()


def _source_text(row: Mapping[str, Any]) -> str:
    fields = (
        "evidence_capsule",
        "exact_article_assembled_content",
        "parent_context",
        "clean_content",
        "content",
        "clean_matched_child_content",
        "matched_child_content",
        "chunk_heading",
        "heading",
        "article_title",
        "document_title",
        "law_number",
        "article_number",
        "clause_number",
        "point_number",
        "issuing_agency",
        "authority_label",
    )
    return _fold(" ".join(str(row.get(field) or "") for field in fields))


def _source_content_text(row: Mapping[str, Any]) -> str:
    """Return the admitted passage without metadata-only facet labels.

    Metadata is still used for identity and effectivity checks. It must not,
    however, turn a row tagged ``fee`` or ``procedure`` into proof that the
    passage actually states the requested fee or procedure.
    """

    fields = (
        "evidence_capsule",
        "exact_article_assembled_content",
        "parent_context",
        "clean_content",
        "content",
        "clean_matched_child_content",
        "matched_child_content",
        "chunk_heading",
        "heading",
        "article_title",
    )
    return _fold(" ".join(str(row.get(field) or "") for field in fields))


def _parse_date(value: Any) -> date | None:
    token = str(value or "").strip()[:10]
    if not token:
        return None
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def _subject_anchors(value: Any) -> tuple[str, ...]:
    folded = _fold(value)
    return tuple(
        label
        for label, markers in _SUBJECT_MARKERS
        if any(_contains(folded, marker) for marker in markers)
    )


def _group_anchors(value: Any) -> tuple[str, ...]:
    folded = _fold(value)
    return tuple(
        label
        for label, markers in _GROUP_MARKERS
        if any(_contains(folded, marker) for marker in markers)
    )


def _actor_anchors(value: Any) -> tuple[str, ...]:
    folded = _fold(value)
    return tuple(
        label
        for label, markers in _ACTOR_MARKERS
        if any(_contains(folded, marker) for marker in markers)
    )


def _facets_in_text(value: Any) -> tuple[str, ...]:
    folded = _fold(value)
    return tuple(
        facet
        for facet, markers in _FACET_MARKERS.items()
        if any(_contains(folded, marker) for marker in markers)
    )


def analyze_legal_question(
    question: str,
    *,
    required_facets: Sequence[str] = (),
) -> dict[str, Any]:
    """Extract request-local facets and scope anchors without answering it."""

    inferred = list(_facets_in_text(question))
    explicit = [_normalise_facet(item) for item in required_facets if str(item).strip()]
    facets = tuple(dict.fromkeys([*inferred, *explicit]))
    return {
        "version": "legal-question-facets-v1",
        "required_facets": list(facets),
        "subject_anchors": list(_subject_anchors(question)),
        "group_anchors": list(_group_anchors(question)),
        "actor_anchors": list(_actor_anchors(question)),
        "temporal_markers": re.findall(
            r"\b(?:19|20)\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b",
            str(question or ""),
        ),
    }


def _issue_text(issue: Any) -> str:
    if isinstance(issue, Mapping):
        return " ".join(
            str(issue.get(key) or "")
            for key in ("query_text", "text", "subject", "retrieval_subject", "facts")
        )
    return " ".join(
        str(getattr(issue, key, "") or "")
        for key in ("query_text", "text", "subject", "retrieval_subject", "facts", "retrieval_facts")
    )


def _issue_domain(issue: Any) -> str | None:
    if isinstance(issue, Mapping):
        value = (
            issue.get("canonical_domain")
            or issue.get("domain_candidate")
            or issue.get("domain")
            or issue.get("domain_slug")
        )
    else:
        value = (
            getattr(issue, "canonical_domain", None)
            or getattr(issue, "domain_candidate", None)
            or getattr(issue, "domain", None)
            or getattr(issue, "domain_slug", None)
        )
    canonical = canonicalize_legal_domain(value)
    return None if canonical in {None, "unknown", "all", "general"} else canonical


def _source_domain(row: Mapping[str, Any]) -> str | None:
    metadata = row.get("metadata")
    nested = metadata if isinstance(metadata, Mapping) else {}
    return canonicalize_legal_domain(
        row.get("canonical_domain")
        or row.get("domain_slug")
        or row.get("domain")
        or nested.get("canonical_domain")
        or nested.get("domain_slug")
        or nested.get("domain")
    )


@dataclass(frozen=True)
class ScopedEvidenceSelection:
    selected: tuple[dict[str, Any], ...]
    decisions: tuple[dict[str, Any], ...]
    found_source_ids: tuple[str, ...]
    missing_facets: tuple[str, ...]


def select_scoped_evidence(
    issue: Any,
    candidates: Sequence[Mapping[str, Any]],
    *,
    required_facets: Sequence[str] = (),
    legal_as_of: Any | None = None,
) -> ScopedEvidenceSelection:
    """Apply explicit subject/group/actor/facet scope after legal eligibility.

    No retrieval score threshold is used.  A source either satisfies a
    request-boundary rule or it does not; rank remains an ordering signal.
    The source mapping is copied without changing its legal text, so lead-ins,
    conditions, exceptions and cross-references remain available downstream.
    """

    request_text = _issue_text(issue)
    analysis = analyze_legal_question(request_text, required_facets=required_facets)
    requested_facets = tuple(analysis["required_facets"])
    requested_subjects = set(analysis["subject_anchors"])
    requested_groups = set(analysis["group_anchors"])
    requested_actors = set(analysis["actor_anchors"])
    requested_domain = _issue_domain(issue)
    applied_date = _parse_date(legal_as_of)
    selected: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    found_ids: list[str] = []
    seen: set[str] = set()
    available_facets: set[str] = set()

    for index, raw in enumerate(candidates):
        row = dict(raw)
        source_id = _source_id(row, index)
        if source_id in found_ids:
            continue
        found_ids.append(source_id)
        source_text = _source_text(row)
        source_subjects = set(_subject_anchors(source_text))
        source_groups = set(_group_anchors(source_text))
        source_actors = set(_actor_anchors(source_text))
        source_facets = {
            _normalise_facet(value)
            for value in row.get("supported_facets") or ()
            if str(value).strip()
        }
        source_facets.update(_facets_in_text(source_text))
        reason = "SCOPED_SOURCE"
        expected_issue_id = str(
            getattr(issue, "issue_id", "")
            or (issue.get("issue_id") if isinstance(issue, Mapping) else "")
            or ""
        ).strip()
        row_issue_id = str(row.get("issue_id") or "").strip()
        row_domain = _source_domain(row)
        effective_from = _parse_date(row.get("effective_from"))
        effective_to = _parse_date(row.get("effective_to"))
        if expected_issue_id and row_issue_id and row_issue_id != expected_issue_id:
            reason = "ISSUE_BINDING_MISMATCH"
        elif requested_domain and row_domain and row_domain != requested_domain:
            reason = "DOMAIN_SCOPE_MISMATCH"
        elif str(row.get("effective_status") or "").strip().casefold() in _BAD_STATUS:
            reason = "EFFECTIVE_STATUS_NOT_SERVABLE"
        elif applied_date is not None and (
            (effective_from is not None and applied_date < effective_from)
            or (effective_to is not None and applied_date > effective_to)
        ):
            reason = "EFFECTIVE_DATE_OUT_OF_SCOPE"
        elif requested_subjects and source_groups and not requested_groups:
            reason = "SUBJECT_MISMATCH"
        elif requested_subjects and source_subjects and not requested_subjects.intersection(source_subjects):
            reason = "SUBJECT_MISMATCH"
        elif source_groups and not requested_groups:
            reason = "GROUP_SCOPE_MISMATCH"
        elif requested_groups and source_groups and not requested_groups.intersection(source_groups):
            reason = "GROUP_SCOPE_MISMATCH"
        elif requested_actors and source_actors and not requested_actors.intersection(source_actors):
            reason = "ACTOR_MISMATCH"
        elif (
            source_facets
            and requested_facets
            and not source_facets.intersection(requested_facets)
            and not (requested_subjects and source_subjects.intersection(requested_subjects))
        ):
            reason = "FACET_NOT_SUPPORTED"

        if reason != "SCOPED_SOURCE":
            decisions.append({"source_id": source_id, "decision": "reject", "reason": reason})
            continue
        if source_id in seen:
            continue
        seen.add(source_id)
        available_facets.update(source_facets)
        selected.append(row)
        decisions.append({"source_id": source_id, "decision": "keep", "reason": reason})

    missing = tuple(facet for facet in requested_facets if facet not in available_facets)
    return ScopedEvidenceSelection(
        selected=tuple(selected),
        decisions=tuple(decisions),
        found_source_ids=tuple(found_ids),
        missing_facets=missing,
    )


def build_targeted_supplement_plan(
    issue: Any,
    *,
    missing_facets: Sequence[str],
    pass_index: int = 0,
) -> dict[str, Any]:
    """Build the sole optional retrieval pass, scoped to missing facets."""

    facets = list(dict.fromkeys(_normalise_facet(item) for item in missing_facets if str(item).strip()))
    if pass_index >= 1 or not facets:
        return {"allowed": False, "round": 1, "facets": facets, "issues": []}
    text = _issue_text(issue).strip()
    labels = {
        "condition": "điều kiện",
        "documents": "hồ sơ giấy tờ",
        "authority": "cơ quan có thẩm quyền nơi nộp",
        "deadline": "thời hạn thời gian giải quyết",
        "fee": "lệ phí mức thu",
        "form": "biểu mẫu",
        "procedure": "trình tự thủ tục",
        "legal_basis": "căn cứ điều khoản",
        "exception": "ngoại lệ",
        "rule": "quy định áp dụng",
    }
    suffix = "; chỉ tìm bổ sung: " + ", ".join(labels.get(facet, facet) for facet in facets)
    query = (text + suffix).strip(" ;")[:2400]
    issue_id = str(getattr(issue, "issue_id", "") or (issue.get("issue_id") if isinstance(issue, Mapping) else "issue-1"))
    return {
        "allowed": True,
        "round": 1,
        "facets": facets,
        "query": query,
        "issues": [{"issue_id": issue_id, "query": query, "facets": facets}],
    }


@dataclass(frozen=True)
class AnswerPostcheckResult:
    answer: str
    found_source_ids: tuple[str, ...]
    used_source_ids: tuple[str, ...]
    checked_claims: tuple[dict[str, Any], ...]
    rejected_claims: tuple[dict[str, Any], ...]
    missing_facets: tuple[str, ...]
    repair_count: int
    recheck_passed: bool
    grounding_status: str
    unverified_explanations: tuple[dict[str, Any], ...] = ()
    missing_facets_by_issue: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "found_source_ids": list(self.found_source_ids),
            "used_source_ids": list(self.used_source_ids),
            "checked_claims": list(self.checked_claims),
            "rejected_claims": list(self.rejected_claims),
            "missing_facets": list(self.missing_facets),
            "repair_count": self.repair_count,
            "recheck_passed": self.recheck_passed,
            "grounding_status": self.grounding_status,
            "unverified_explanations": list(self.unverified_explanations),
            "missing_facets_by_issue": list(self.missing_facets_by_issue),
        }


def _numbers(value: str) -> tuple[str, ...]:
    value = re.sub(r"\[?\s*legal\s*:\s*\d+\s*\]?", " ", str(value or ""), flags=re.IGNORECASE)
    values: list[str] = []
    for item in re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?", value or ""):
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", item):
            values.append(str(int(item.replace(".", "").replace(",", ""))))
        else:
            values.append(str(int(float(item.replace(",", ".")))))
    return tuple(values)


def _has_negative(value: str) -> bool:
    text = _fold(value)
    return any(marker in f" {text} " for marker in (" khong ", " chua ", " khong duoc ", " khong phai "))


def _is_gap(value: str) -> bool:
    folded = _fold(value)
    return any(marker in folded for marker in _GAP_MARKERS)


def _is_claim(value: str) -> bool:
    folded = _fold(value)
    if len(folded) < 10 or _is_gap(value):
        return False
    if folded.startswith(("thua anh", "xin chao", "cam on", "toi co the")):
        return False
    return any(marker in folded for marker in _CLAIM_MARKERS)


def _claim_facets(value: str) -> tuple[str, ...]:
    return _facets_in_text(value)


def _has_direct_marker(claim: str, source: str, facet: str) -> bool:
    if facet in {"rule", "legal_basis"}:
        # These are framing/identity facets.  The substantive predicate is
        # checked by the claim's other markers; article/law metadata is enough
        # to bind a citation label without requiring the source to repeat the
        # word "quy định".
        return True
    claim_folded = _fold(claim)
    source_folded = _fold(source)
    markers = _FACET_MARKERS.get(facet, ())
    if not any(_contains(claim_folded, marker) for marker in markers):
        return True
    return any(_contains(source_folded, marker) for marker in markers)


def _supports_claim(
    claim: str,
    source: Mapping[str, Any],
    *,
    question_analysis: Mapping[str, Any],
) -> tuple[bool, str]:
    claim_folded = _fold(claim)
    source_folded = _source_text(source)
    source_content_folded = _source_content_text(source)
    q_subjects = set(question_analysis.get("subject_anchors") or ())
    q_groups = set(question_analysis.get("group_anchors") or ())
    source_subjects = set(_subject_anchors(source_folded))
    source_groups = set(_group_anchors(source_folded))
    if q_subjects and source_subjects and not q_subjects.intersection(source_subjects):
        return False, "SUBJECT_MISMATCH"
    claim_groups = set(_group_anchors(claim_folded))
    if q_groups and (claim_groups and not q_groups.intersection(claim_groups)):
        return False, "GROUP_SCOPE_MISMATCH"
    if source_groups and not q_groups:
        return False, "GROUP_SCOPE_MISMATCH"
    if q_groups and source_groups and not q_groups.intersection(source_groups):
        return False, "GROUP_SCOPE_MISMATCH"
    if source_groups and q_groups and not q_groups.intersection(claim_groups):
        # The surrounding question is not enough to preserve a group-only
        # rule: the sentence itself must retain the limiting subject so it
        # cannot be copied into a later, broader context as a universal rule.
        return False, "GROUP_SCOPE_NOT_RETAINED"
    # A rule scoped to one group cannot support a universal sentence, even if
    # the sentence repeats a few generic words from the source.
    if source_groups and any(
        _contains(claim_folded, marker)
        for marker in (
            " moi cong dan ",
            " cong dan ",
            " tat ca ",
            " moi nguoi ",
            " moi doi tuong ",
            " bat ky ai ",
            " ap dung chung ",
        )
    ):
        return False, "GROUP_SCOPE_MISMATCH"
    q_actors = set(question_analysis.get("actor_anchors") or ())
    source_actors = set(_actor_anchors(source_folded))
    if q_actors and source_actors and not q_actors.intersection(source_actors):
        return False, "ACTOR_MISMATCH"
    claim_numbers = _numbers(claim)
    # Ignore article/law numbers in metadata when deciding whether a material
    # value is contradicted. A missing amount/deadline is unverified; it is
    # only contradicted when the admitted passage contains another value.
    source_numbers = set(_numbers(source_content_folded))
    if claim_numbers and not set(claim_numbers).issubset(source_numbers):
        return False, (
            "MATERIAL_VALUE_CONTRADICTED"
            if source_numbers
            else "MATERIAL_VALUE_NOT_IN_SOURCE"
        )
    if _has_negative(claim) and not _has_negative(source_folded):
        return False, "POLARITY_NOT_IN_SOURCE"
    if re.search(r"\bkhong\s+duoc\b|\bkhong\s+co\s+tham\s+quyen\b", source_folded) and not re.search(
        r"\bkhong\s+duoc\b|\bkhong\s+co\s+tham\s+quyen\b", claim_folded
    ) and not re.search(r"\bkhong\s+ap\s+dung\b", claim_folded):
        # Do not turn an exception into the general rule.  A source containing
        # both a positive lead-in and an exception can still support a claim
        # when the claim repeats the positive condition marker.
        if not any(marker in source_folded for marker in ("co quyen", "co tham quyen", "duoc huong", "duoc cap")):
            return False, "POLARITY_NOT_IN_SOURCE"

    source_facets = {
        _normalise_facet(value)
        for value in source.get("supported_facets") or ()
        if str(value).strip()
    }
    # Retrieval facet labels are useful hints, not a replacement for the
    # admitted passage.  A reviewed chunk may be indexed under ``procedure``
    # while its exact text states the deadline or documents.
    source_facets.update(_facets_in_text(source_folded))
    claim_facets = set(_claim_facets(claim))
    if source_facets and claim_facets and not source_facets.intersection(claim_facets):
        return False, "FACET_NOT_SUPPORTED"
    for facet in claim_facets:
        # A retrieval label is a routing hint only. Require the admitted
        # passage itself to express the requested predicate. This prevents a
        # row tagged ``fee``/``documents`` from validating an unrelated claim.
        marker_source = source_content_folded
        if facet == "legal_basis" and not marker_source:
            marker_source = source_folded
        if not _has_direct_marker(claim, marker_source, facet):
            return False, "FACET_NOT_SUPPORTED"
        if facet in {"rule", "legal_basis"} and not (
            any(_contains(source_content_folded, marker) for marker in _FACET_MARKERS[facet])
            or (
                facet == "legal_basis"
                and any(
                    str(source.get(key) or "").strip()
                    for key in ("law_number", "article_number", "clause_number", "point_number")
                )
            )
        ):
            return False, "NO_DIRECT_SOURCE_SUPPORT"
    # Exact source wording is strong evidence; otherwise require a direct
    # facet/predicate marker.  Shared nouns alone never prove a conclusion.
    normal_claim = " ".join(claim_folded.split())
    if normal_claim in source_folded:
        return True, "EXACT_SOURCE_UNIT"
    if not claim_facets:
        return False, "NO_DIRECT_SOURCE_SUPPORT"
    return True, "DIRECT_FACET_SUPPORT"


def _split_line(line: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+", line) if part]


def _claim_with_structural_context(parts: Sequence[str]) -> list[tuple[str, str]]:
    """Keep a leading condition attached while checking sentence fragments."""

    if len(parts) < 2:
        return [(part, part) for part in parts]
    first = _fold(parts[0])
    carries_condition = first.startswith((
        "neu ",
        "khi ",
        "trong truong hop ",
        "doi voi ",
        "truong hop ",
    ))
    context = parts[0] if carries_condition else ""
    output: list[tuple[str, str]] = []
    for index, part in enumerate(parts):
        checked = part
        if context and index > 0 and not _fold(part).startswith((
            "neu ", "khi ", "trong truong hop ", "doi voi ", "truong hop "
        )):
            checked = f"{context} {part}"
        output.append((part, checked))
    return output


def _rejection_status(reason: str) -> str:
    if reason in {
        "SUBJECT_MISMATCH",
        "GROUP_SCOPE_MISMATCH",
        "GROUP_SCOPE_NOT_RETAINED",
        "ACTOR_MISMATCH",
        "MATERIAL_VALUE_CONTRADICTED",
        "POLARITY_NOT_IN_SOURCE",
        "EFFECTIVE_STATUS_NOT_SERVABLE",
        "EFFECTIVE_DATE_OUT_OF_SCOPE",
    }:
        return "contradicted"
    return "unverified"


def _claim_issue_candidates(
    claim: str,
    issue_analyses: Mapping[str, Mapping[str, Any]],
) -> set[str]:
    """Infer a bounded issue binding from the claim itself.

    A Markdown answer has no trusted issue id on each sentence.  We therefore
    use only deterministic anchors already extracted from the question and
    the claim.  A unique facet/subject match binds the sentence; ambiguous or
    generic wording remains eligible for all matching issues and is still
    checked against the source passage below.
    """

    if not issue_analyses:
        return set()
    claim_analysis = analyze_legal_question(claim)
    claim_facets = set(claim_analysis.get("required_facets") or ())
    claim_subjects = set(claim_analysis.get("subject_anchors") or ())
    claim_groups = set(claim_analysis.get("group_anchors") or ())
    claim_actors = set(claim_analysis.get("actor_anchors") or ())
    candidates: list[tuple[str, int]] = []
    for issue_id, analysis in issue_analyses.items():
        issue_facets = set(analysis.get("required_facets") or ())
        issue_subjects = set(analysis.get("subject_anchors") or ())
        issue_groups = set(analysis.get("group_anchors") or ())
        issue_actors = set(analysis.get("actor_anchors") or ())
        score = 0
        if claim_facets and issue_facets.intersection(claim_facets):
            score += 2
        if claim_subjects and issue_subjects.intersection(claim_subjects):
            score += 3
        if claim_groups and issue_groups.intersection(claim_groups):
            score += 3
        if claim_actors and issue_actors.intersection(claim_actors):
            score += 3
        if score:
            candidates.append((issue_id, score))
    if not candidates:
        return set()
    best = max(score for _, score in candidates)
    winners = {issue_id for issue_id, score in candidates if score == best}
    # Do not turn a single generic facet into an artificial hard binding when
    # more than one issue has the same facet.
    return winners if len(winners) == 1 else set()


def _replace_unsupported(
    answer: str,
    rejected_indexes: Mapping[int, str],
) -> str:
    lines = answer.splitlines()
    output: list[str] = []
    cursor = 0
    contradicted_count = 0
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            output.append(line)
            continue
        prefix_match = re.match(r"^(\s*(?:[-*+] |\d+[.)] )?)(.*)$", line)
        prefix = prefix_match.group(1) if prefix_match else ""
        body = prefix_match.group(2) if prefix_match else line
        parts = _split_line(body)
        kept: list[str] = []
        for part in parts:
            if not _is_claim(part):
                kept.append(part)
                continue
            if cursor in rejected_indexes:
                if rejected_indexes[cursor] == "unverified":
                    kept.append(
                        "Diễn giải tham khảo — chưa xác minh: " + part
                    )
                else:
                    # A contradicted legal claim must not remain in the
                    # answer, even as a repeated placeholder.  Record one
                    # consolidated notice after the retained answer instead.
                    contradicted_count += 1
            else:
                kept.append(part)
            cursor += 1
        rendered = prefix + " ".join(kept)
        if rendered.strip().lstrip("-*+").strip():
            output.append(rendered)
    repaired = "\n".join(output).strip()
    if contradicted_count:
        # Keep the warning compact and stable. The old heading looked like a
        # second answer section when only one short model fragment was
        # removed; the targeted missing-facet warning is added by the caller.
        repaired = repaired.rstrip() + (
            "\n\nLưu ý: Một số nội dung không đủ căn cứ đã được loại khỏi "
            "câu trả lời."
        )
    return repaired


def _remove_unattributed_emails(
    answer: str,
    *,
    question: str,
    evidence_rows: Sequence[Mapping[str, Any]],
) -> str:
    """Remove contact addresses invented outside the current evidence packet."""

    allowed_text = "\n".join(
        [str(question or ""), *[_source_text(row) for row in evidence_rows]]
    ).casefold()
    cleaned = str(answer or "")
    for match in tuple(_EMAIL_RE.finditer(cleaned)):
        value = match.group(0)
        if value.casefold() not in allowed_text:
            cleaned = cleaned.replace(value, "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +([,.;:])", r"\1", cleaned)
    cleaned = re.sub(
        r"(?im)^\s*(?:trân trọng,?|\[your name\]|\[tên của bạn\])\s*$",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _rejected_claim_indexes(
    answer: str,
    rejected: Sequence[Mapping[str, Any]],
    *,
    statuses: set[str] | None = None,
) -> dict[int, str]:
    """Map deterministic claim positions to a rejection status."""

    rejected_claims = {
        str(item.get("claim") or ""): str(item.get("status") or "unverified")
        for item in rejected
        if statuses is None or str(item.get("status") or "unverified") in statuses
    }
    indexes: dict[int, str] = {}
    cursor = 0
    for line in answer.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        prefix_match = re.match(r"^(\s*(?:[-*+] |\d+[.)] )?)(.*)$", line)
        body = prefix_match.group(2) if prefix_match else line
        for part, _checked_part in _claim_with_structural_context(_split_line(body)):
            if _is_claim(part):
                if part in rejected_claims:
                    indexes[cursor] = rejected_claims[part]
                cursor += 1
    return indexes


def _run_postcheck(
    question: str,
    answer: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    required_facets: Sequence[str],
    required_facets_by_issue: Mapping[str, Sequence[str]] | None = None,
    issue_queries_by_id: Mapping[str, str] | None = None,
    legal_as_of: Any | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[str],
    tuple[str, ...],
    tuple[str, ...],
    tuple[dict[str, Any], ...],
]:
    issue_facets = {
        str(issue_id): tuple(
            _normalise_facet(facet)
            for facet in facets
            if str(facet).strip()
        )
        for issue_id, facets in (required_facets_by_issue or {}).items()
        if str(issue_id).strip()
    }
    scoped_required = tuple(
        dict.fromkeys(
            [
                *required_facets,
                *(facet for facets in issue_facets.values() for facet in facets),
            ]
        )
    )
    analysis = analyze_legal_question(question, required_facets=scoped_required)
    issue_analyses = {
        str(issue_id): analyze_legal_question(
            str(issue_queries_by_id.get(issue_id) or question),
            required_facets=facets,
        )
        for issue_id, facets in issue_facets.items()
    }
    scoped = select_scoped_evidence(
        {"query_text": question},
        rows,
        required_facets=analysis["required_facets"],
        legal_as_of=legal_as_of,
    )
    source_by_id = {_source_id(row, index): row for index, row in enumerate(scoped.selected)}
    checked: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    used: list[str] = []
    supported_facets: set[str] = set()
    supported_facets_by_issue: dict[str, set[str]] = {}
    cursor = 0
    for line in answer.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        prefix_match = re.match(r"^(\s*(?:[-*+] |\d+[.)] )?)(.*)$", line)
        body = prefix_match.group(2) if prefix_match else line
        for part, checked_part in _claim_with_structural_context(_split_line(body)):
            if not _is_claim(part):
                continue
            matched_id = None
            matched_reason = "NO_DIRECT_SOURCE_SUPPORT"
            claim_issue_candidates = _claim_issue_candidates(
                checked_part,
                issue_analyses,
            )
            for source_id, source in source_by_id.items():
                source_issue_id = str(source.get("issue_id") or "").strip()
                if (
                    source_issue_id
                    and claim_issue_candidates
                    and source_issue_id not in claim_issue_candidates
                ):
                    matched_reason = "ISSUE_BINDING_MISMATCH"
                    continue
                supported, reason = _supports_claim(
                    checked_part,
                    source,
                    question_analysis=analysis,
                )
                if supported:
                    matched_id = source_id
                    matched_reason = reason
                    break
                reason_priority = {
                    "SUBJECT_MISMATCH": 5,
                    "GROUP_SCOPE_MISMATCH": 5,
                    "GROUP_SCOPE_NOT_RETAINED": 5,
                    "ACTOR_MISMATCH": 5,
                    "MATERIAL_VALUE_CONTRADICTED": 5,
                    "POLARITY_NOT_IN_SOURCE": 5,
                    "EFFECTIVE_STATUS_NOT_SERVABLE": 5,
                    "EFFECTIVE_DATE_OUT_OF_SCOPE": 5,
                    "ISSUE_BINDING_MISMATCH": 5,
                    "FACET_NOT_SUPPORTED": 3,
                    "NO_DIRECT_SOURCE_SUPPORT": 2,
                    "MATERIAL_VALUE_NOT_IN_SOURCE": 2,
                }
                if reason_priority.get(reason, 1) >= reason_priority.get(matched_reason, 1):
                    matched_reason = reason
            if matched_id:
                used.append(matched_id)
                claim_facets = tuple(dict.fromkeys(_claim_facets(part)))
                source_issue_id = str(source_by_id[matched_id].get("issue_id") or "").strip()
                checked_item = {
                    "claim": part,
                    "checked_claim": checked_part,
                    "status": "supported",
                    "source_id": matched_id,
                    "reason": matched_reason,
                    "facets": list(claim_facets),
                    "issue_id": source_issue_id or None,
                }
                checked.append(checked_item)
                source_facets = {
                    _normalise_facet(value)
                    for value in source_by_id[matched_id].get("supported_facets") or ()
                    if str(value).strip()
                }
                source_facets.update(
                    _facets_in_text(_source_content_text(source_by_id[matched_id]))
                )
                covered_facets = set(claim_facets)
                if source_issue_id:
                    covered_facets.update(
                        source_facets.intersection(
                            set(issue_facets.get(source_issue_id) or ())
                        )
                    )
                supported_facets.update(covered_facets)
                if source_issue_id:
                    supported_facets_by_issue.setdefault(source_issue_id, set()).update(
                        covered_facets
                    )
            else:
                rejected.append({
                    "claim": part,
                    "checked_claim": checked_part,
                    "status": _rejection_status(matched_reason),
                    "reason": matched_reason,
                    "facets": list(dict.fromkeys(_claim_facets(part))),
                })
            cursor += 1
    # The question analyzer contributes inferred facets as well as explicit
    # section requirements.  Otherwise a request such as "thời hạn ... bao
    # lâu?" would have no missing-facet signal when its caller omitted the
    # optional section list.
    required = tuple(
        dict.fromkeys(
            _normalise_facet(item)
            for item in analysis.get("required_facets") or ()
            if str(item).strip()
        )
    )
    missing = [facet for facet in required if facet not in supported_facets]
    missing_by_issue: list[dict[str, Any]] = []
    for issue_id, facets in issue_facets.items():
        issue_supported = supported_facets_by_issue.get(issue_id, set())
        issue_missing = [facet for facet in facets if facet not in issue_supported]
        if issue_missing:
            missing_by_issue.append({"issue_id": issue_id, "missing_facets": issue_missing})
    return (
        checked,
        rejected,
        missing,
        tuple(dict.fromkeys(used)),
        scoped.found_source_ids,
        tuple(missing_by_issue),
    )


def postcheck_markdown_answer(
    question: str,
    answer: str,
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    required_facets: Sequence[str] = (),
    required_facets_by_issue: Mapping[str, Sequence[str]] | None = None,
    issue_queries_by_id: Mapping[str, str] | None = None,
    repair: bool = True,
    annotate: bool = True,
    legal_as_of: Any | None = None,
) -> AnswerPostcheckResult:
    """Check legal conclusions and optionally repair/annotate them.

    Serving can run this as an observation pass (``repair=False,
    annotate=False``) so a useful model answer is never rewritten merely
    because one claim needs review.  The checked/rejected claims remain in
    the result for telemetry and audit.
    """

    analysis = analyze_legal_question(question, required_facets=required_facets)
    facets = tuple(analysis["required_facets"])
    sanitized_answer = _remove_unattributed_emails(
        str(answer or "").strip(),
        question=question,
        evidence_rows=evidence_rows,
    )
    (
        checked,
        rejected,
        missing,
        used_ids,
        found_ids,
        missing_by_issue,
    ) = _run_postcheck(
        question,
        sanitized_answer,
        evidence_rows,
        required_facets=facets,
        required_facets_by_issue=required_facets_by_issue,
        issue_queries_by_id=issue_queries_by_id,
        legal_as_of=legal_as_of,
    )
    final_answer = sanitized_answer
    repair_count = 0
    recheck_passed = not rejected
    unverified_explanations = tuple(
        item for item in rejected if item.get("status") == "unverified"
    )
    if rejected and not repair and annotate:
        # Shadow mode does not remove or rewrite a model conclusion, but an
        # unverified interpretation must still be visibly marked next to the
        # original text. This keeps the public answer honest before the
        # intervention gate is enabled.
        unverified_indexes = _rejected_claim_indexes(
            final_answer,
            rejected,
            statuses={"unverified"},
        )
        if unverified_indexes:
            final_answer = _replace_unsupported(final_answer, unverified_indexes)
    if rejected and repair:
        # Replace unsupported material once, then run the complete checker on
        # the final text. No iterative repair loop is allowed.
        rejected_indexes = _rejected_claim_indexes(final_answer, rejected)
        final_answer = _replace_unsupported(final_answer, rejected_indexes)
        labels = {
            "condition": "điều kiện",
            "documents": "hồ sơ/giấy tờ",
            "authority": "nơi nộp/thẩm quyền",
            "deadline": "thời hạn",
            "fee": "lệ phí",
            "form": "biểu mẫu",
            "procedure": "trình tự thủ tục",
        }
        requested_gap_labels = [labels.get(facet, facet) for facet in missing]
        if requested_gap_labels:
            detail = ", ".join(dict.fromkeys(requested_gap_labels))
            final_answer = final_answer.rstrip() + (
                "\n\n## Nội dung chưa đủ căn cứ\n"
                f"Tài liệu hiện tại không đủ căn cứ để xác minh: {detail}."
            )
        repair_count = 1
        (
            checked,
            rejected,
            missing,
            used_ids,
            found_ids,
            missing_by_issue,
        ) = _run_postcheck(
            question,
            final_answer,
            evidence_rows,
            required_facets=facets,
            required_facets_by_issue=required_facets_by_issue,
            issue_queries_by_id=issue_queries_by_id,
            legal_as_of=legal_as_of,
        )
        recheck_passed = not rejected
    status = (
        "fully_grounded"
        # ``rejected`` and ``missing`` are recomputed after the one allowed
        # repair. A removed draft claim must not downgrade a final answer
        # whose remaining claims all pass the recheck.
        if used_ids and not rejected and not missing
        else "partially_grounded"
        if used_ids
        else "insufficient_evidence"
    )
    return AnswerPostcheckResult(
        answer=final_answer,
        found_source_ids=found_ids,
        used_source_ids=used_ids,
        checked_claims=tuple(checked),
        rejected_claims=tuple(rejected),
        missing_facets=tuple(missing),
        repair_count=repair_count,
        recheck_passed=recheck_passed,
        grounding_status=status,
        unverified_explanations=unverified_explanations,
        missing_facets_by_issue=missing_by_issue,
    )
