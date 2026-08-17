"""Deterministic request router for the legal answer pipeline V2.

The router classifies and binds request-local identity only. It never answers
law, invents a procedure identifier, or lets an LLM split a request. Retrieval,
validity, authority and citation gates remain downstream responsibilities.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Mapping

from api.legal_exact_retrieval import plan_exact_lookup
from api.legal_form_catalog import FormCatalog
from api.legal_section_grounding import LegalIssue, plan_legal_issues

EXACT_ARTICLE = "exact_article"
PROCEDURE_FORM = "procedure_form"
GENERAL_LEGAL = "general_legal"
HISTORICAL = "historical"
PIPELINE_VERSION = "legal-answer-v2"

AnswerRouteName = Literal[
    "exact_article", "procedure_form", "general_legal", "historical"
]

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FORM_CODE_RE = re.compile(r"\bm[aẫ]u\s+(?:s[oố]\s*)?[0-9]{1,3}[a-z]?\b", re.IGNORECASE)
_HISTORICAL_RE = re.compile(
    r"\b(?:tại|vào|trước|sau)\s+(?:ngày|thời điểm)|"
    r"\b(?:năm|tháng)\s+(?:19|20)\d{2}\b|"
    r"\b(?:19|20)\d{2}\s+(?:có|được|áp dụng)",
    re.IGNORECASE,
)


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFD", str(value or "").casefold())
    return " ".join(
        "".join(character for character in normalized if unicodedata.category(character) != "Mn")
        .replace("đ", "d")
        .split()
    )


@dataclass(frozen=True)
class LegalAnswerRoute:
    pipeline_version: str
    answer_route: AnswerRouteName
    issues: tuple[LegalIssue, ...]
    clarifying_questions: tuple[str, ...] = ()
    procedure_id: str | None = None
    exact_law_number: str | None = None
    exact_article_number: str | None = None
    decision_reason: str = "deterministic_route"


def is_answer_pipeline_v2_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return true only for explicitly enabled rollout roles.

    The default role set is citizen so enabling the main flag cannot
    accidentally activate officer/admin traffic.
    """

    values = os.environ if environ is None else environ
    if str(values.get("LEGAL_ANSWER_PIPELINE_V2_ENABLED", "false")).strip().casefold() not in _TRUE_VALUES:
        return False
    roles = {
        item.strip().casefold()
        for item in str(values.get("LEGAL_ANSWER_PIPELINE_V2_ROLES", "citizen")).split(",")
        if item.strip()
    }
    return str(role or "citizen").strip().casefold() in roles


def _looks_historical(question: str) -> bool:
    folded = _fold(question)
    return bool(
        _HISTORICAL_RE.search(question)
        or "tai thoi diem" in folded
        or "quy dinh cu" in folded
        or "lich su hieu luc" in folded
    )


def _looks_procedural_or_form(question: str) -> bool:
    folded = _fold(question)
    return bool(
        _FORM_CODE_RE.search(question)
        or any(
            marker in folded
            for marker in (
                "bieu mau",
                "to khai",
                "tai mau",
                "thu tuc",
                "ho so",
                "nop o dau",
                "thoi han bao lau",
                "le phi",
            )
        )
    )


def _resolve_unique_procedure(question: str) -> tuple[str | None, bool]:
    """Resolve an approved catalog identity without guessing.

    ``ambiguous`` is true when the catalog reports a tie or when a generic form
    code is the only usable identity. Catalog failures fail closed and leave
    retrieval to the ordinary legal route.
    """

    try:
        resolution = FormCatalog.load_default().resolve_procedures(question, limit=3)
    except (OSError, ValueError, TypeError):
        return None, False
    matches = list(resolution.get("matches") or [])
    if resolution.get("ambiguous"):
        return None, True
    if len(matches) == 1:
        return str(matches[0].get("procedure_id") or "").strip() or None, False
    if _FORM_CODE_RE.search(question):
        return None, True
    return None, False


def _missing_fact_clarification(question: str) -> str | None:
    """Return one deterministic clarification for an explicitly incomplete case."""

    folded = _fold(question)
    if not (
        "he thong co the ket luan ngay khong" in folded
        and "can toi bo sung" in folded
    ):
        return None
    if "thu tuc" in folded and "chua neu" in folded:
        return "Bạn muốn thực hiện thủ tục cụ thể nào và mong nhận kết quả gì?"
    if "quyet dinh" in folded and "ban hanh" in folded:
        return "Vui lòng cho biết cơ quan hoặc người ban hành, số hiệu và ngày của quyết định."
    if "thoi han" in folded or "ngay nhan quyet dinh" in folded:
        return "Vui lòng cho biết ngày nhận quyết định hoặc ngày xảy ra sự kiện cần tính thời hạn."
    if any(marker in folded for marker in ("tuoi", "quan he", "tu cach")):
        return "Vui lòng bổ sung tuổi, quan hệ và tư cách của những người liên quan."
    if any(
        marker in folded
        for marker in ("noi cu tru", "noi co tai san", "noi su kien xay ra")
    ):
        return "Vui lòng cho biết nơi cư trú, nơi có tài sản hoặc nơi sự kiện xảy ra để xác định thẩm quyền."
    if "anh bi cat" in folded or (
        "so hieu" in folded and "ngay ban hanh" in folded
    ):
        return "Vui lòng gửi ảnh đầy đủ hoặc bổ sung số hiệu, ngày và cơ quan ban hành văn bản."
    if "mau thuan" in folded and "ngay" in folded:
        return "Vui lòng xác nhận ngày tháng đúng và cung cấp giấy tờ dùng để đối chiếu."
    if "du kien trong tuong lai" in folded and "khong co van ban chinh thuc" in folded:
        return "Chưa thể kết luận theo quy định dự kiến; vui lòng cung cấp văn bản chính thức nếu đã được ban hành."
    if "chua co ho so" in folded or (
        "chua co" in folded and "chung cu" in folded
    ):
        return "Vui lòng nêu thủ tục, hồ sơ hiện có và dữ kiện cần cơ quan xem xét; hệ thống không thể bảo đảm trước kết quả giải quyết."
    return "Vui lòng bổ sung sự kiện, giấy tờ và kết quả bạn muốn được giải quyết."


def route_legal_answer(question: str) -> LegalAnswerRoute:
    """Classify one request and produce explicit-only, identity-local issues."""

    clean_question = " ".join(str(question or "").split())
    exact = plan_exact_lookup(clean_question)
    historical = _looks_historical(clean_question)
    procedural = _looks_procedural_or_form(clean_question)

    procedure_id: str | None = None
    clarifying: tuple[str, ...] = ()
    if procedural:
        procedure_id, ambiguous = _resolve_unique_procedure(clean_question)
        if ambiguous:
            clarifying = (
                "Bạn cần biểu mẫu cho thủ tục nào? Vui lòng nêu tên thủ tục thay vì chỉ ghi mã mẫu.",
            )

    missing_fact_question = _missing_fact_clarification(clean_question)
    if missing_fact_question:
        clarifying = (missing_fact_question,)

    if historical:
        answer_route: AnswerRouteName = HISTORICAL
        reason = "explicit_historical_time"
    elif exact.law_number and exact.article_number:
        answer_route = EXACT_ARTICLE
        reason = "exact_document_and_article"
    elif procedural:
        answer_route = PROCEDURE_FORM
        reason = "procedure_or_form_markers"
    else:
        answer_route = GENERAL_LEGAL
        reason = "general_legal_default"

    if missing_fact_question:
        reason = "missing_facts_require_clarification"

    issues = tuple(
        plan_legal_issues(clean_question, max_issues=8, explicit_only=True)
    )
    return LegalAnswerRoute(
        pipeline_version=PIPELINE_VERSION,
        answer_route=answer_route,
        issues=issues,
        clarifying_questions=clarifying,
        procedure_id=procedure_id,
        exact_law_number=exact.law_number,
        exact_article_number=exact.article_number,
        decision_reason=reason,
    )
