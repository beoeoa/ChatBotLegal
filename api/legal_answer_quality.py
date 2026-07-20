"""Deterministic evidence coverage and answer-quality helpers.

This module never invents legal conclusions. It only describes whether the
retrieved evidence contains observable support for answer sections and flags
claims already rejected by the existing safety guard.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any


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


def build_evidence_coverage(
    results: list[dict[str, Any]],
    *,
    required_sections: list[str] | None = None,
    recommended_forms: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Report observable source coverage without making a legal judgment."""
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
            ids = [item_id for item_id, blob in blobs if item_id and blob]
        elif section == "citations":
            ids = [
                _evidence_id(item) for item in results
                if _evidence_id(item)
                and item.get("document_id")
                and (item.get("law_number") or item.get("document_title"))
                and item.get("article_number")
            ]
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
            "reason": "matching_source_content" if ids else "no_direct_source_content",
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
        decisions.append({
            "claim_type": "citation",
            "status": "verified" if chunk_id and citation.get("doc_id") else "rejected",
            "reason": "retrieved_document_article_mapping" if chunk_id and citation.get("doc_id") else "missing_document_mapping",
            "evidence_ids": [chunk_id] if chunk_id else [],
            "legal_as_of": legal_as_of.isoformat(),
        })
    seen_claims: set[tuple[str, str]] = set()
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer or ""):
        normalized = fold(sentence)
        if not normalized or any(marker in normalized for marker in (
            "chua xac minh", "nguon hien co khong neu", "chua du can cu",
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
    for item in decisions:
        original = str(item.get("original") or "").strip()
        if item.get("status") != "rejected" or not original or original not in output:
            continue
        output = output.replace(original, replacement, 1)
    output = re.sub(
        rf"(?:{re.escape(replacement)}\s*){{2,}}",
        replacement + " ",
        output,
    )
    return output.strip()


def quality_preview(
    *,
    coverage: dict[str, dict[str, Any]],
    grounding_status: str,
    claim_validation: list[dict[str, Any]],
) -> tuple[float, list[str]]:
    applicable = [item for item in coverage.values() if item.get("status") != "not_applicable"]
    verified = sum(1 for item in applicable if item.get("status") == "verified")
    coverage_ratio = verified / max(1, len(applicable))
    rejected = sum(1 for item in claim_validation if item.get("status") == "rejected")
    grounding_points = 1.0 if grounding_status == "fully_grounded" else 0.6 if grounding_status == "partially_grounded" else 0.0
    score = max(0.0, min(10.0, 7.0 * coverage_ratio + 3.0 * grounding_points - min(3.0, rejected)))
    flags = [f"missing_{name}" for name, item in coverage.items() if item.get("status") == "missing"]
    if rejected:
        flags.append("rejected_unsupported_claims")
    return round(score, 2), flags
