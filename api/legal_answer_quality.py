"""Deterministic evidence coverage and answer-quality helpers.

This module never invents legal conclusions. It only describes whether the
retrieved evidence contains observable support for answer sections and flags
claims already rejected by the existing safety guard.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Mapping, Sequence

from api.legal_claim_validation import _claim_text_supported_by_quote


AnswerMode = Literal[
    "answer",
    "answer_with_limitations",
    "clarify",
    "source_view_only",
    "deny",
]


@dataclass(frozen=True)
class AnswerDecision:
    """Canonical public decision assembled from already-validated signals."""

    mode: AnswerMode
    reason_codes: tuple[str, ...] = ()
    supported_sections: tuple[str, ...] = ()
    unsupported_sections: tuple[str, ...] = ()
    clarifying_questions: tuple[str, ...] = ()


def decide_answer_mode(
    *,
    grounding_status: str,
    section_statuses: Sequence[str] = (),
    source_gap: Sequence[str] = (),
    clarifying_questions: Sequence[str] = (),
    hard_block_reason: str | None = None,
) -> AnswerDecision:
    """Choose one response mode without mutating answer text or inventing law."""
    if hard_block_reason:
        return AnswerDecision(mode="deny", reason_codes=(hard_block_reason,))

    statuses = tuple(str(status) for status in section_statuses)
    supported = tuple(
        str(index)
        for index, status in enumerate(statuses)
        if status == "sufficiently_evidenced"
    )
    unsupported = tuple(dict.fromkeys(str(item) for item in source_gap if str(item)))
    questions = tuple(dict.fromkeys(str(item) for item in clarifying_questions if str(item)))

    if statuses and all(status == "sufficiently_evidenced" for status in statuses):
        return AnswerDecision(
            mode="answer",
            reason_codes=("ALL_REQUESTED_SECTIONS_GROUNDED",),
            supported_sections=supported,
        )
    if supported:
        return AnswerDecision(
            mode="answer_with_limitations",
            reason_codes=("SECTION_EVIDENCE_MISSING",),
            supported_sections=supported,
            unsupported_sections=unsupported,
            clarifying_questions=questions,
        )
    if questions or unsupported or grounding_status in {"unknown", "insufficient_evidence"}:
        return AnswerDecision(
            mode="clarify",
            reason_codes=("INSUFFICIENT_EVIDENCE",),
            unsupported_sections=unsupported,
            clarifying_questions=questions,
        )
    if grounding_status == "partially_grounded":
        return AnswerDecision(
            mode="source_view_only",
            reason_codes=("PARTIAL_GROUNDING",),
            unsupported_sections=unsupported,
        )
    return AnswerDecision(mode="answer_with_limitations", reason_codes=("CLAIM_NOT_SUPPORTED",))


EVIDENCE_SECTIONS = (
    "conclusion",
    "authority",
    "documents",
    "processing_time",
    "fee",
    "penalty",
    "remedial_measures",
    "forms",
    "citations",
)

SECTION_LABELS = {
    "conclusion": "kết luận",
    "submission_place": "nơi nộp",
    "authority": "thẩm quyền",
    "documents": "hồ sơ",
    "processing_time": "thời hạn giải quyết",
    "deadline": "thời hạn",
    "fee": "lệ phí",
    "penalty": "mức phạt",
    "remedial_measures": "biện pháp khắc phục",
    "steps": "các bước thực hiện",
    "official_forms": "biểu mẫu chính thức",
    "forms": "biểu mẫu",
    "legal_basis_links": "căn cứ và liên kết văn bản",
    "citations": "căn cứ pháp lý",
    "applicable_rule": "quy định áp dụng",
    "rule": "quy định pháp luật",
    "content": "nội dung quy định",
    "scope": "phạm vi áp dụng",
    "procedure": "trình tự thủ tục",
    "conditions_or_rights": "điều kiện hoặc quyền",
    "exceptions": "ngoại lệ",
    "rights_or_explanation": "quyền giải trình hoặc khiếu nại",
}

_PATTERNS = {
    "authority": ("thẩm quyền", "ủy ban nhân dân", "ubnd", "cơ quan", "bộ phận một cửa", "công an"),
    "documents": ("hồ sơ", "giấy tờ", "tờ khai", "đơn đề nghị", "bản chính", "bản sao"),
    "processing_time": ("thời hạn", "ngày làm việc", "giờ làm việc", "trả kết quả"),
    "fee": ("lệ phí", "mức thu", "miễn lệ phí", "không thu lệ phí"),
    "penalty": ("xử phạt", "mức phạt", "phạt tiền", "triệu đồng"),
    "remedial_measures": ("khắc phục hậu quả", "buộc tháo dỡ", "buộc khôi phục", "tạm dừng thi công"),
}


def fold(value: str) -> str:
    text = unicodedata.normalize("NFD", value or "").casefold()
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text.replace("đ", "d")).strip()


def effective_legal_date(*, legal_as_of: date | None, event_date: date | None) -> date:
    return legal_as_of or event_date or date.today()


def vietnamese_section_names(sections: list[str] | tuple[str, ...]) -> list[str]:
    return [SECTION_LABELS.get(item, item.replace("_", " ")) for item in sections]


def _evidence_id(item: dict[str, Any]) -> str:
    return str(item.get("chunk_id") or item.get("id") or "").replace("legal:", "").strip()


def _evidence_blob(item: dict[str, Any]) -> str:
    return fold(" ".join(str(item.get(key) or "") for key in (
        "law_number", "document_title", "article_number", "article_title",
        "chunk_heading", "content", "issuing_agency", "scope",
    )))


def _conclusion_fragments(answer: str | None) -> list[str]:
    """Return a direct answer under a conclusion or outcome-equivalent heading.

    Concise legal answers sometimes put their dispositive answer under an
    authority or submission-place heading instead of repeating it under a
    separate conclusion heading.  It is accepted only if it still matches the
    retrieved source text below.
    """

    fragments: list[str] = []
    in_conclusion = False
    for raw_line in str(answer or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        plain = re.sub(r"^[#>\s*-]+", "", line)
        plain = plain.replace("**", "").replace("__", "").strip()
        normalized = fold(plain)
        heading_like = bool(
            re.match(r"^#{1,6}\s+", line)
            or (
                (line.startswith("**") and line.endswith("**"))
                and len(normalized.split()) <= 10
            )
        )
        if re.match(r"^(?:[-*]\s*)?ket luan(?: da kiem chung)?\s*:", normalized):
            inline = plain.split(":", 1)[1].strip()
            if inline:
                fragments.extend(
                    item.strip()
                    for item in re.split(r"(?<=[.!?])\s+", inline)
                    if item.strip()
                )
            in_conclusion = False
            continue
        if normalized.startswith("ket luan") and len(normalized.split()) <= 12:
            in_conclusion = True
            if ":" in plain:
                inline = plain.split(":", 1)[1].strip()
                if inline:
                    fragments.extend(
                        item.strip()
                        for item in re.split(r"(?<=[.!?])\s+", inline)
                        if item.strip()
                    )
            continue
        if (
            normalized.startswith((
                "tham quyen giai quyet",
                "co quan giai quyet",
                "noi nop ho so",
                "noi nop",
            ))
            and len(normalized.split()) <= 12
        ):
            in_conclusion = True
            if ":" in plain:
                inline = plain.split(":", 1)[1].strip()
                if inline:
                    fragments.extend(
                        item.strip()
                        for item in re.split(r"(?<=[.!?])\s+", inline)
                        if item.strip()
                    )
            continue
        if heading_like:
            in_conclusion = False
            continue
        if not in_conclusion:
            continue
        fragments.extend(
            item.strip()
            for item in re.split(r"(?<=[.!?])\s+", line)
            if item.strip()
        )
    return fragments


def _direct_conclusion_evidence_ids(
    answer: str | None,
    results: list[dict[str, Any]],
) -> list[str]:
    """Bind an extractive conclusion to the exact source content it uses."""

    fragments = _conclusion_fragments(answer)
    if not fragments:
        return []
    labelled_conclusions: list[str] = []
    for fragment in fragments:
        plain = re.sub(r"^[\s>*-]+", "", fragment).replace("**", "").strip()
        label, separator, claim = plain.partition(":")
        if separator and fold(label) == "ket luan" and claim.strip():
            labelled_conclusions.append(claim.strip())
    if labelled_conclusions:
        fragments = labelled_conclusions
    matched: list[str] = []
    for fragment in fragments:
        cleaned = re.sub(r"\[?\s*legal\s*:\s*[^\]\s]+\s*\]?", "", fragment, flags=re.I)
        cleaned = re.sub(r"^[\s>*-]+", "", cleaned).strip()
        # Numbered provisions are split at ``1.`` / ``2.`` by the sentence
        # splitter. They are structural labels, not standalone legal claims.
        if re.fullmatch(r"\d+\.", cleaned):
            continue
        needle = fold(cleaned)
        if len(needle) < 12 or any(
            marker in needle
            for marker in ("chua xac minh", "chua du can cu", "nguon hien co khong neu")
        ):
            return []
        fragment_ids = [
            _evidence_id(item)
            for item in results
            if _evidence_id(item)
            and (
                needle in fold(str(item.get("content") or ""))
                or _claim_text_supported_by_quote(
                    cleaned,
                    str(item.get("content") or ""),
                )
            )
        ]
        if not fragment_ids:
            return []
        matched.extend(fragment_ids)
    return list(dict.fromkeys(matched))


def extractive_conclusion_from_evidence(
    results: list[dict[str, Any]],
    *,
    required_sections: list[str] | None = None,
) -> str | None:
    """Build a short source-verbatim outcome for an already retrieved facet.

    This is deliberately extractive.  It chooses at most two retrieved
    passages that directly match the requested authority/submission/documents
    facets; it never converts source text into a new legal conclusion.
    """
    required = set(required_sections or [])
    wants_authority = bool({"authority", "submission_place"} & required)
    wants_documents = "documents" in required
    candidates: list[tuple[int, dict[str, Any]]] = []
    for item in results:
        content = " ".join(str(item.get("content") or "").split())
        normalized = fold(content)
        matches_authority = wants_authority and any(
            phrase in normalized
            for phrase in ("tham quyen", "uy ban nhan dan", "co quan")
        )
        matches_documents = wants_documents and any(
            phrase in normalized
            for phrase in ("ho so", "to khai", "giay chung sinh", "giay to")
        )
        if not content or not _evidence_id(item) or not (matches_authority or matches_documents):
            continue
        authority_is_direct = any(
            phrase in normalized
            for phrase in ("co tham quyen", "thuc hien dang ky")
        )
        # First choose the direct authority/submission rule, then the dossier
        # rule. Generic mentions of an agency (for example, issuing a result)
        # cannot displace the actual place-of-submission provision.
        priority = 0 if matches_authority and authority_is_direct else 1 if matches_documents else 2
        candidates.append((priority, item))

    has_direct_authority = any(priority == 0 for priority, _ in candidates)
    if has_direct_authority:
        # Do not append a merely topical agency reference after the actual
        # competence rule. Such a reference can be a different provision and
        # would make an otherwise extractive conclusion over-broad.
        candidates = [
            (priority, item)
            for priority, item in candidates
            if priority in {0, 1}
        ]

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for _, item in sorted(candidates, key=lambda pair: pair[0]):
        item_id = _evidence_id(item)
        if item_id in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(item_id)
        if len(selected) >= 2:
            break

    if not selected:
        return None

    fragments: list[str] = []
    for item in selected:
        content = " ".join(str(item.get("content") or "").split())
        fragments.append(f"- {content}")
    return "## Kết luận đã kiểm chứng\n" + "\n".join(fragments)


def prepend_extractive_conclusion_when_supported(
    answer: str,
    results: list[dict[str, Any]],
    *,
    required_sections: list[str] | None = None,
) -> str:
    """Add a short source-verbatim outcome when the model omitted one."""
    output = str(answer or "").strip()
    if _direct_conclusion_evidence_ids(output, results):
        return output
    conclusion = extractive_conclusion_from_evidence(
        results,
        required_sections=required_sections,
    )
    if not conclusion:
        return output

    # A model sometimes emits a bare "not verified" conclusion even though a
    # retrieved passage above answers the same requested facet. Remove only
    # that contradictory placeholder before adding the extractive outcome.
    absence_markers = (
        "chua xac minh duoc noi dung nay tu nguon hien co",
        "nguon hien co khong neu noi dung nay",
        "chua du can cu trong kho hien tai",
    )
    match = re.search(
        r"(?ims)^#{1,6}\s*kết luận[^\n]*\n(.*?)(?=^#{1,6}\s|\Z)",
        output,
    )
    if match and any(marker in fold(match.group(1)) for marker in absence_markers):
        output = (output[:match.start()] + output[match.end():]).strip()
    return f"{conclusion}\n\n{output}".strip()


def build_evidence_coverage(
    results: list[dict[str, Any]],
    *,
    required_sections: list[str] | None = None,
    recommended_forms: list[dict[str, Any]] | None = None,
    answer: str | None = None,
    validated_claims: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Report observable source coverage without making a legal judgment.

    ``validated_claims`` is an optional projection from the same postcheck
    that produced the final answer. It lets a direct Markdown answer carry an
    issue/facet-bound supported claim even when it does not use a ``Kết luận``
    heading, without treating a bare facet label or arbitrary citation as
    proof.
    """
    required = set(required_sections or [])
    blobs = [(_evidence_id(item), _evidence_blob(item)) for item in results if isinstance(item, dict)]
    coverage: dict[str, dict[str, Any]] = {}

    for section in EVIDENCE_SECTIONS:
        applicable = section in {"conclusion", "citations"}
        if section == "authority":
            applicable = bool({"authority", "submission_place"} & required)
        elif section == "forms":
            applicable = bool({"official_forms", "forms"} & required)
        elif section in required:
            applicable = True
        if not applicable:
            coverage[section] = {"status": "not_applicable", "evidence_ids": [], "reason": "not_required_for_question_type"}
            continue

        if section == "conclusion":
            ids = _direct_conclusion_evidence_ids(answer, results)
            if not ids:
                ids = [
                    str(item.get("source_id") or "").strip()
                    for item in (validated_claims or ())
                    if isinstance(item, Mapping)
                    and str(item.get("status") or "").casefold()
                    in {"supported", "verified"}
                    and str(item.get("source_id") or "").strip()
                ]
            if not ids:
                ids = [
                    str(form.get("form_id") or "")
                    for form in (recommended_forms or [])
                    if str(form.get("review_status") or "").casefold()
                    == "approved"
                    and str(form.get("official_level") or "").casefold()
                    == "official"
                    and form.get("source_url")
                    and form.get("legal_basis")
                ]
        elif section == "citations":
            ids = [
                _evidence_id(item) for item in results
                if _evidence_id(item)
                and item.get("document_id")
                and (item.get("law_number") or item.get("document_title"))
                and item.get("article_number")
            ]
            ids.extend(
                str(form.get("form_id") or "")
                for form in (recommended_forms or [])
                if str(form.get("review_status") or "").casefold() == "approved"
                and str(form.get("official_level") or "").casefold() == "official"
                and form.get("source_url")
                and form.get("legal_basis")
            )
        elif section == "forms":
            ids = [
                str(form.get("form_id") or form.get("id") or "")
                for form in (recommended_forms or [])
                if str(form.get("review_status") or "").casefold() == "approved"
                and str(form.get("official_level") or "").casefold() == "official"
                and (form.get("download_url") or form.get("local_file") or form.get("file_path"))
            ]
        else:
            needles = tuple(fold(value) for value in _PATTERNS.get(section, ()))
            ids = [item_id for item_id, blob in blobs if item_id and any(needle in blob for needle in needles)]

        status = "verified" if ids else "missing"
        coverage[section] = {
            "status": status,
            "evidence_ids": list(dict.fromkeys(ids))[:8],
            "reason": (
                "matching_source_content"
                if ids
                else "no_direct_claim_bound_source_content"
                if section == "conclusion"
                else "no_direct_source_content"
            ),
        }
    return coverage


def clarifying_questions_for_gaps(question: str, coverage: dict[str, dict[str, Any]]) -> list[str]:
    text = fold(question)
    questions: list[str] = []
    if any(word in text for word in ("bien ban", "xu phat", "sai phep", "vi pham")) and coverage.get("penalty", {}).get("status") != "verified":
        questions.append("Anh/chị đã nhận quyết định xử phạt hay mới chỉ có biên bản vi phạm?")
    if any(word in text for word in ("xay dung", "giay phep")) and coverage.get("conclusion", {}).get("status") != "verified":
        questions.append("Phần công trình sai giấy phép là thay đổi diện tích, số tầng, công năng hay chỉ bản vẽ chi tiết?")
    if any(word in text for word in ("dat", "xay dung", "quy hoach")) and "quy hoach" not in text:
        questions.append("Công trình hoặc thửa đất có thông tin phù hợp quy hoạch, chỉ giới xây dựng hay không?")
    return questions[:3]


def build_claim_validation(
    *,
    removed_claims: list[dict[str, Any]] | None,
    citations: list[dict[str, Any]] | None,
    legal_as_of: date,
    answer: str | None = None,
    coverage: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for item in removed_claims or []:
        decisions.append({
            "claim_type": item.get("claim_type") or "unknown",
            "status": "rejected",
            "reason": item.get("reason") or "unsupported_by_retrieval",
            "original": item.get("original"),
            "evidence_ids": [],
            "legal_as_of": legal_as_of.isoformat(),
        })
    for citation in citations or []:
        chunk_id = str(citation.get("chunk_id") or "").strip()
        approved_form_catalog = (
            citation.get("verification_source") == "approved_form_catalog"
            and bool(citation.get("source_url"))
            and bool(citation.get("law_number"))
        )
        decisions.append({
            "claim_type": "citation",
            "status": "verified" if approved_form_catalog or (chunk_id and citation.get("doc_id")) else "rejected",
            "reason": (
                "approved_form_catalog_source"
                if approved_form_catalog
                else "retrieved_document_article_mapping"
                if chunk_id and citation.get("doc_id")
                else "missing_document_mapping"
            ),
            "evidence_ids": (
                [str(citation.get("law_number"))]
                if approved_form_catalog
                else [chunk_id]
                if chunk_id
                else []
            ),
            "legal_as_of": legal_as_of.isoformat(),
        })
    seen_claims: set[tuple[str, str]] = set()
    for sentence in _conclusion_fragments(answer):
        normalized = fold(sentence)
        if not normalized or any(marker in normalized for marker in (
            "chua xac minh", "nguon hien co khong neu", "chua du can cu",
        )):
            continue
        source = (coverage or {}).get("conclusion", {})
        evidence_ids = list(source.get("evidence_ids") or [])
        verified = source.get("status") == "verified" and bool(evidence_ids)
        decisions.append({
            "claim_type": "conclusion",
            "status": "verified" if verified else "rejected",
            "reason": "direct_extract_source_match" if verified else "missing_direct_conclusion_evidence",
            "original": sentence.strip()[:500],
            "evidence_ids": evidence_ids,
            "legal_as_of": legal_as_of.isoformat(),
        })
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer or ""):
        if re.match(r"^\s*#{1,6}\s+\S", sentence):
            continue
        normalized = fold(sentence)
        if not normalized or any(marker in normalized for marker in (
            "chua xac minh", "nguon hien co khong neu", "chua du can cu",
            "chua co can cu xac nhan",
        )):
            continue
        for claim_type, values in _PATTERNS.items():
            needles = tuple(fold(value) for value in values)
            if not any(needle in normalized for needle in needles):
                continue
            claim_key = (claim_type, normalized)
            if claim_key in seen_claims:
                continue
            seen_claims.add(claim_key)
            source = (coverage or {}).get(claim_type, {})
            evidence_ids = list(source.get("evidence_ids") or [])
            verified = source.get("status") == "verified" and bool(evidence_ids)
            decisions.append({
                "claim_type": claim_type,
                "status": "verified" if verified else "rejected",
                "reason": "direct_section_evidence" if verified else "missing_direct_section_evidence",
                "original": sentence.strip()[:500],
                "evidence_ids": evidence_ids,
                "legal_as_of": legal_as_of.isoformat(),
            })
    return decisions


def apply_claim_validation(answer: str, decisions: list[dict[str, Any]]) -> str:
    """Remove unsupported claim text while preserving supported answer sections."""
    output = answer
    replacement = "Phần này chưa được xác minh từ nguồn hiện có."
    rejected = [item for item in decisions if item.get("status") == "rejected"]
    if not rejected:
        return output.strip()

    for item in decisions:
        original = str(item.get("original") or "").strip()
        if item.get("status") != "rejected" or not original or original not in output:
            continue
        output = output.replace(original, "", 1)

    for boilerplate in (
        replacement,
        "Kho dữ liệu hiện tại chưa có căn cứ xác nhận thời hạn/lệ phí này.",
        "Kho dữ liệu hiện tại chưa có căn cứ xác nhận thẩm quyền này.",
    ):
        output = output.replace(boilerplate, "")

    lines = output.splitlines()
    kept_lines: list[str] = []
    for index, line in enumerate(lines):
        if not re.match(r"^\s*#{1,6}\s+\S", line):
            kept_lines.append(line)
            continue
        has_content = False
        for candidate in lines[index + 1 :]:
            if re.match(r"^\s*#{1,6}\s+\S", candidate):
                break
            if candidate.strip():
                has_content = True
                break
        if has_content:
            kept_lines.append(line)
    output = "\n".join(kept_lines)
    output = re.sub(r"[ \t]+\n", "\n", output)
    output = re.sub(r"\n{3,}", "\n\n", output).strip()
    output = re.sub(
        r"(?im)^(\s*(?:[-*]\s*)?(?:\*\*)?Bước\s+)\d+",
        lambda match, counter=iter(range(1, 10_000)): (
            f"{match.group(1)}{next(counter)}"
        ),
        output,
    )
    limitation = f"## Nội dung chưa đủ căn cứ\n{replacement}"
    return f"{output}\n\n{limitation}".strip() if output else limitation


def quality_preview(
    *,
    coverage: dict[str, dict[str, Any]],
    grounding_status: str,
    claim_validation: list[dict[str, Any]],
    answer: str | None = None,
) -> tuple[float, list[str]]:
    applicable = [item for item in coverage.values() if item.get("status") != "not_applicable"]
    verified = sum(1 for item in applicable if item.get("status") == "verified")
    coverage_ratio = verified / max(1, len(applicable))
    folded_answer = fold(answer or "")
    visible_rejected = [
        item
        for item in claim_validation
        if item.get("status") == "rejected"
        and (
            not answer
            or not str(item.get("original") or "").strip()
            or fold(str(item.get("original") or "")) in folded_answer
        )
    ]
    rejected = len(visible_rejected)
    grounding_points = 1.0 if grounding_status == "fully_grounded" else 0.6 if grounding_status == "partially_grounded" else 0.0
    score = max(0.0, min(10.0, 7.0 * coverage_ratio + 3.0 * grounding_points - min(3.0, rejected)))
    flags = [f"missing_{name}" for name, item in coverage.items() if item.get("status") == "missing"]
    if rejected:
        flags.append("rejected_unsupported_claims")
    conclusion_verified = coverage.get("conclusion", {}).get("status") == "verified"
    substantive_sections = [
        item
        for name, item in coverage.items()
        if name not in {"conclusion", "citations"}
        and item.get("status") != "not_applicable"
    ]
    # A response can state its direct outcome under "Hồ sơ" or "Thẩm quyền"
    # instead of a redundant conclusion heading. When every requested
    # substantive facet is source-verified, that is an evidence-complete
    # outcome rather than a reason to cap the quality below seven.
    direct_outcome_verified = bool(substantive_sections) and all(
        item.get("status") == "verified" for item in substantive_sections
    )
    conclusion_rejected = any(
        item.get("claim_type") == "conclusion" and item.get("status") == "rejected"
        for item in visible_rejected
    )
    if not conclusion_verified and not direct_outcome_verified:
        score = min(score, 6.9)
        if "missing_conclusion" not in flags:
            flags.append("missing_conclusion")
    return round(score, 2), flags
