import asyncio
import urllib.parse
from pathlib import Path
import json
import io
import os
import re
import unicodedata
import time
import uuid
from dataclasses import replace
from typing import AsyncGenerator, Any, Mapping

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from loguru import logger
from api.utils.pii_detector import redact_upload_text
from api.observability import telemetry

from api.auth import get_request_role, get_request_user_id, get_request_username
from api import conversation_service as conv_svc
from api.ask_progress import (
    ProgressCallback,
    ask_progress_enabled,
    cloud_fallback_reason,
    emit_ask_progress,
    local_fallback_enabled,
    stream_ask_progress,
)
from api.ask_role_rollout import enforce_ask_role_rollout
from api.models import AskRequest, AskResponse, SearchRequest, SearchResponse
from api.legal_question_policy import (
    answer_contract,
    classify_question,
    missing_answer_sections,
    render_answer_contract,
)
from api.legal_grounding import validate_legal_references
from api.legal_search_client import get_legal_search_client
from api.legal_retrieval_policy import expanded_retrieval_reason
from api.legal_section_grounding import (
    aggregate_answer_sections,
    build_section_grounding_metric,
    is_section_grounding_enabled,
    plan_legal_issues,
    validate_answer_section,
)
from api.legal_answer_quality import (
    apply_claim_validation,
    build_claim_validation,
    build_evidence_coverage,
    clarifying_questions_for_gaps,
    effective_legal_date,
    quality_preview,
    vietnamese_section_names,
)
from api import ask_idempotency
from api.user_service import list_ask_history, log_ask_history
from open_notebook.ai.models import Model, model_manager
from open_notebook.domain.notebook import text_search, vector_search
from open_notebook.exceptions import (
    ConfigurationError,
    DatabaseOperationError,
    ExternalServiceError,
    InvalidInputError,
    LegalRetrievalUnavailableError,
    NetworkError,
    NotFoundError,
    OpenNotebookError,
    RateLimitError,
)
from open_notebook.graphs.ask import graph as ask_graph


async def _resolve_ask_model_ids(
    strategy_model: str | None,
    answer_model: str | None,
    final_answer_model: str | None,
) -> tuple[str, str, str]:
    """Fill empty ask model IDs from system defaults before Model.get()."""
    strategy = (strategy_model or '').strip()
    answer = (answer_model or '').strip()
    final_answer = (final_answer_model or '').strip()

    if strategy and answer and final_answer:
        return strategy, answer, final_answer

    defaults = await model_manager.get_defaults()
    default_chat = (getattr(defaults, 'default_chat_model', None) or '').strip()
    if not default_chat:
        raise HTTPException(
            status_code=400,
            detail={
                'code': 'MODEL_DEFAULTS_MISSING',
                'message': 'Chưa cấu hình default_chat_model. Vào Settings > Models để gán model chat mặc định.',
                'retryable': False,
            },
        )

    strategy = strategy or default_chat
    answer = answer or default_chat
    final_answer = final_answer or default_chat
    return strategy, answer, final_answer


router = APIRouter()
LEGAL_SEARCH_URL = os.getenv(
    "LEGAL_SEARCH_URL", "http://host.docker.internal:8765"
).rstrip("/")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434").rstrip("/")
RECOMMENDED_LOCAL_MODEL = "qwen2.5:3b"
LOCAL_MAX_SOURCES = 5
LOCAL_SOURCE_CHAR_LIMIT = 1200
LOCAL_NUM_CTX = 4096
LOCAL_NUM_PREDICT = 1024

LEGAL_MIN_TOP_SCORE = 0.12
LEGAL_MIN_QUERY_TOKEN_OVERLAP = 1
VI_STOPWORDS = {
    "cua", "cho", "hoi", "khong", "thu", "tuc", "ve", "la", "co", "can", "duoc",
    "the", "nao", "tai", "noi", "nop", "ho", "so", "ubnd", "phuong", "xa",
}


def _ask_error_response(exc: OpenNotebookError) -> tuple[int, dict]:
    if isinstance(exc, LegalRetrievalUnavailableError):
        return 503, {
            "code": "LEGAL_RETRIEVAL_UNAVAILABLE",
            "message": str(exc),
            "how_to_fix": [
                "Chạy: powershell -ExecutionPolicy Bypass -File scripts/start_legal_search.ps1",
                "Kiểm tra: http://127.0.0.1:8765/health",
                "Sau khi trạng thái là healthy, bấm Hỏi lại.",
            ],
            "retryable": True,
        }
    if isinstance(exc, RateLimitError):
        return 429, {
            "code": "AI_PROVIDER_QUOTA_OR_RATE_LIMIT",
            "message": str(exc),
            "how_to_fix": [
                "Kiểm tra quota hoặc số dư của API key đang dùng.",
                "Chọn model/provider khác còn quota hoặc bật chế độ local.",
            ],
            "retryable": True,
        }
    if isinstance(exc, ConfigurationError):
        return 400, {
            "code": "AI_MODEL_CONFIGURATION_ERROR",
            "message": str(exc),
            "how_to_fix": [
                "Vào Mô hình AI để kiểm tra model mặc định.",
                "Kiểm tra model đã gắn đúng API key và provider.",
            ],
            "retryable": False,
        }
    if isinstance(exc, NetworkError):
        return 503, {
            "code": "AI_PROVIDER_UNAVAILABLE",
            "message": str(exc),
            "how_to_fix": [
                "Kiểm tra mạng và URL của provider.",
                "Dùng nút kiểm tra kết nối API key trong phần Cài đặt.",
                "Nếu cloud lỗi, bật chế độ local và chọn qwen2.5:3b.",
            ],
            "retryable": True,
        }
    if isinstance(exc, ExternalServiceError):
        return 502, {
            "code": "AI_SERVICE_ERROR",
            "message": str(exc),
            "how_to_fix": [
                "Kiểm tra model và API key đang chọn.",
                "Thử provider khác hoặc chế độ local.",
            ],
            "retryable": True,
        }
    return 500, {
        "code": "ASK_FAILED",
        "message": str(exc),
        "how_to_fix": [
            "Kiểm tra trạng thái tại /api/search/health.",
            "Xem log backend để biết dịch vụ nào đang lỗi.",
        ],
        "retryable": True,
    }


def _ascii_fold(value: str) -> str:
    replacements = {
        "à":"a","á":"a","ả":"a","ã":"a","ạ":"a","ă":"a","ắ":"a","ằ":"a","ẳ":"a","ẵ":"a","ặ":"a","â":"a","ấ":"a","ầ":"a","ẩ":"a","ẫ":"a","ậ":"a",
        "è":"e","é":"e","ẻ":"e","ẽ":"e","ẹ":"e","ê":"e","ế":"e","ề":"e","ể":"e","ễ":"e","ệ":"e",
        "ì":"i","í":"i","ỉ":"i","ĩ":"i","ị":"i",
        "ò":"o","ó":"o","ỏ":"o","õ":"o","ọ":"o","ô":"o","ố":"o","ồ":"o","ổ":"o","ỗ":"o","ộ":"o","ơ":"o","ớ":"o","ờ":"o","ở":"o","ỡ":"o","ợ":"o",
        "ù":"u","ú":"u","ủ":"u","ũ":"u","ụ":"u","ư":"u","ứ":"u","ừ":"u","ử":"u","ữ":"u","ự":"u",
        "ỳ":"y","ý":"y","ỷ":"y","ỹ":"y","ỵ":"y","đ":"d",
    }
    return "".join(replacements.get(ch, ch) for ch in value.casefold())


def _meaningful_tokens(value: str) -> set[str]:
    folded = _ascii_fold(value)
    return {token for token in re.findall(r"[a-z0-9]{3,}", folded) if token not in VI_STOPWORDS}




def _answer_admits_no_legal_basis(
    answer: str,
    evidence: list[dict] | None = None,
) -> bool:
    """Detect a *whole-answer* no-basis response, not a local limitation.

    A multi-issue answer may legitimately contain a sentence such as
    ``Nguồn hiện có chưa nêu lệ phí`` next to a cited, grounded section.  The
    old substring-only check treated that local warning as proof that the
    entire answer was unusable and replaced all useful content with the
    generic insufficient-evidence response.  When an evidence packet is
    available, validate the answer first: an explicitly cited, fully grounded
    fragment means the warning is local and must be preserved.  The ordinary
    grounding validator still runs after this check, so this does not permit
    unsupported claims or citations.
    """
    text = _ascii_fold(answer or "")
    markers = (
        "khong duoc neu trong cac van ban",
        "khong co trong cac van ban",
        "khong the cung cap thong tin cu the",
        "chua du can cu",
        "khong tim thay quy dinh",
        "khong co quy dinh",
        "nguon hien co chua",
        "khong nam trong cac van ban phap luat da truy xuat",
        "chua nhan duoc du noi dung de tra loi",
    )
    if not any(m in text for m in markers):
        return False

    # These are whole-answer refusal templates produced by an interrupted or
    # failed generation pass.  A citation appendix appended afterwards does
    # not turn that refusal into a substantive grounded answer.  Check them
    # before inspecting citations so a response like "Tôi chưa nhận được đủ
    # nội dung... Căn cứ: Luật ..." cannot pass as fully grounded.
    whole_answer_markers = (
        "chua nhan duoc du noi dung de tra loi",
        "khong the cung cap thong tin cu the",
    )
    if any(marker in text for marker in whole_answer_markers):
        return True

    if evidence:
        validation = validate_legal_references(answer or "", evidence)
        if (
            validation.status == "fully_grounded"
            and validation.has_explicit_reference
            and validation.matched_source_ids
        ):
            return False

    return True

def _has_sufficient_legal_evidence(question: str, retrieval: dict) -> bool:
    """Controlled recall gate: keep anti-hallucination, reduce false negatives.

    Avoid treating weak lexical collisions (e.g. "lửa" vs PCCC) as enough evidence.
    """
    results = list(retrieval.get("results", []))
    if not results:
        return False

    q = (question or "").strip()
    q_fold = _ascii_fold(q)
    # Explicit out-of-corpus / fictional markers should not pass just because of weak retrieval.
    if any(marker in q_fold for marker in ("gia tuong", "gia dinh tuong", "xyz-", "phap lenh gia")):
        # only pass if an exact law number from the question exists in retrieval
        asked_laws = set(re.findall(r"\b\d{1,4}/\d{4}/[a-z0-9._-]+\b", q_fold))
        if not asked_laws:
            return False
        retrieved_laws = {_ascii_fold(str(item.get("law_number") or "")) for item in results[:8]}
        if not any(law in retrieved_laws or any(law in rl for rl in retrieved_laws) for law in asked_laws):
            return False

    top_score = float(results[0].get("score") or 0)
    query_tokens = _meaningful_tokens(question)
    if not query_tokens:
        return top_score >= 0.35

    def _item_overlap(item: dict) -> int:
        haystack = " ".join(
            str(item.get(field) or "")
            for field in (
                "article_title",
                "content",
                "document_title",
                "domain_name",
                "field_name",
                "law_number",
            )
        )
        return len(query_tokens & _meaningful_tokens(haystack))

    # High-confidence vector score is enough.
    if top_score >= 0.35:
        return True

    # Medium score needs real token support on top hits.
    if top_score >= LEGAL_MIN_TOP_SCORE:
        top_overlap = _item_overlap(results[0])
        if top_overlap >= 2:
            return True
        if top_overlap >= 1 and top_score >= 0.20:
            return True
        law_number = str(results[0].get("law_number") or "").strip()
        if law_number and any(tok in _ascii_fold(law_number) for tok in query_tokens):
            return True

    # Lower score: require multi-token overlap or exact law number match.
    for item in results[:5]:
        overlap = _item_overlap(item)
        if overlap >= max(2, LEGAL_MIN_QUERY_TOKEN_OVERLAP + 1) and top_score >= 0.10:
            return True
        law_number = str(item.get("law_number") or "").strip()
        if law_number and any(tok in _ascii_fold(law_number) for tok in query_tokens):
            return True
    return False




def _sanitize_unsupported_absence_claims(answer: str) -> str:
    replacements = {
        "Không yêu cầu hồ sơ.": "Nguồn hiện có chưa nêu rõ thành phần hồ sơ.",
        "Không yêu cầu hồ sơ": "Nguồn hiện có chưa nêu rõ thành phần hồ sơ",
        "không yêu cầu hồ sơ": "nguồn hiện có chưa nêu rõ thành phần hồ sơ",
        "Không có lệ phí trong các nguồn cung cấp.": "Nguồn hiện có chưa nêu rõ lệ phí.",
        "Không có lệ phí": "Nguồn hiện có chưa nêu rõ lệ phí",
        "không có lệ phí": "nguồn hiện có chưa nêu rõ lệ phí",
        "Không áp dụng thời hạn cụ thể": "Nguồn hiện có chưa nêu rõ thời hạn cụ thể",
        "không áp dụng thời hạn cụ thể": "nguồn hiện có chưa nêu rõ thời hạn cụ thể",
    }
    sanitized = answer
    for old, new in replacements.items():
        sanitized = sanitized.replace(old, new)
    return sanitized



def _ascii_fold_claim_text(value: str) -> str:
    text = unicodedata.normalize("NFD", value or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _retrieval_evidence_text(retrieval_or_results: dict | list | None) -> str:
    """Flatten retrieval hits into one searchable evidence corpus."""
    if not retrieval_or_results:
        return ""
    if isinstance(retrieval_or_results, dict):
        items = list(retrieval_or_results.get("results") or [])
    else:
        items = list(retrieval_or_results or [])
    chunks: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in (
            "content",
            "snippet",
            "text",
            "law_number",
            "document_title",
            "article_number",
            "article_title",
            "source_url",
        ):
            val = item.get(key)
            if val:
                chunks.append(str(val))
    return _ascii_fold_claim_text(" \n ".join(chunks))


def _evidence_supports_claim(claim_type: str, claim_text: str, evidence_norm: str) -> bool:
    """Return True only when retrieval evidence can support the sensitive claim."""
    if not evidence_norm:
        return False
    claim_norm = _ascii_fold_claim_text(claim_text)

    if claim_type in {"thoi_han", "ngay_lam_viec"}:
        # Require both time language and a concrete numeric span or known deadline phrase.
        has_time_lang = any(
            token in evidence_norm
            for token in (
                "thoi han",
                "ngay lam viec",
                "ngay lam",
                "trong ngay",
                "khong qua",
                "giai quyet ngay",
                "giai quyet trong",
            )
        )
        nums = re.findall(r"\b\d{1,3}\b", claim_norm)
        if nums and any(n in evidence_norm for n in nums) and has_time_lang:
            return True
        # Exact common phrases from sources
        for phrase in (
            "trong ngay",
            "ngay trong ngay",
            "03 ngay",
            "3 ngay",
            "05 ngay",
            "5 ngay",
            "07 ngay",
            "7 ngay",
            "10 ngay",
            "15 ngay",
            "20 ngay",
            "30 ngay",
            "60 ngay",
        ):
            if phrase in claim_norm and phrase in evidence_norm:
                return True
        return False

    if claim_type in {"le_phi", "so_tien", "mien_phi"}:
        fee_lang = any(
            token in evidence_norm
            for token in ("le phi", "phi", "mien phi", "khong thu le phi", "dong le phi", "vnd", "dong/")
        )
        if not fee_lang:
            return False
        money = re.findall(r"\b\d{1,3}(?:[.,]\d{3})+\b|\b\d+\s*(?:dong|vnd|vnđ)\b", claim_norm)
        if money:
            # Require same amount-ish digits in evidence
            for m in money:
                digits = re.sub(r"\D", "", m)
                if digits and digits in re.sub(r"\D", "", evidence_norm):
                    return True
            return False
        if "mien phi" in claim_norm:
            return any(x in evidence_norm for x in ("mien phi", "khong thu le phi", "khong phai nop le phi"))
        # Generic fee claim without amount: only keep if source mentions fee explicitly.
        return fee_lang and any(x in evidence_norm for x in ("le phi", "phi le", "muc phi"))

    if claim_type == "tham_quyen":
        authority_lang = any(
            token in evidence_norm
            for token in (
                "tham quyen",
                "uy ban nhan dan",
                "ubnd",
                "cap xa",
                "cap huyen",
                "cap tinh",
                "so tu phap",
                "phong tu phap",
                "co quan dang ky ho tich",
                "bo phan mot cua",
            )
        )
        if not authority_lang:
            return False
        # Keep if answer authority cues also appear in evidence, or evidence has article + authority.
        cues = [
            "ubnd cap xa",
            "ubnd cap huyen",
            "ubnd cap tinh",
            "uy ban nhan dan cap xa",
            "uy ban nhan dan cap huyen",
            "uy ban nhan dan cap tinh",
            "so tu phap",
            "phong tu phap",
            "co quan dang ky ho tich",
            "bo phan mot cua",
            "tham quyen",
        ]
        if any(cue in claim_norm and cue in evidence_norm for cue in cues):
            return True
        # Citation-backed authority: evidence contains both authority language and article/law markers.
        has_article = bool(re.search(r"\bdieu\s+\d+", evidence_norm)) or bool(
            re.search(r"\b\d{1,4}/\d{4}/", evidence_norm)
        )
        return authority_lang and has_article

    return False


def _guard_sensitive_claims(
    answer: str,
    retrieval_or_results: dict | list | None,
) -> tuple[str, list[dict]]:
    """Strip or rewrite unsupported deadline/fee/authority claims.

    Returns (safe_answer, removed_unsupported_claims for rag_trace).
    """
    if not answer:
        return answer, []

    evidence_norm = _retrieval_evidence_text(retrieval_or_results)
    removed: list[dict] = []
    safe = answer

    # Patterns ordered from more specific to broader.
    # Each tuple: (claim_type, regex, replacement)
    safe_time = "Kho dữ liệu hiện tại chưa có căn cứ xác nhận thời hạn/lệ phí này."
    safe_fee = "Kho dữ liệu hiện tại chưa có căn cứ xác nhận thời hạn/lệ phí này."
    safe_auth = "Kho dữ liệu hiện tại chưa có căn cứ xác nhận thẩm quyền này."

    patterns: list[tuple[str, str, str]] = [
        (
            "thoi_han",
            r"(?i)(?:thời hạn|thoi han|giải quyết|giai quyet|trả kết quả|tra ket qua|xử lý|xu ly)[^\n.!?]{0,40}?(?:\d{1,2}\s*[–\-~đếntoi]{1,3}\s*)?\d{1,2}\s*(?:ngày|ngay)(?:\s*làm việc|\s*lam viec)?",
            safe_time,
        ),
        (
            "ngay_lam_viec",
            r"(?i)\b\d{1,2}\s*(?:[-–~]\s*\d{1,2}\s*)?(?:ngày|ngay)\s*(?:làm việc|lam viec)\b",
            safe_time,
        ),
        (
            "thoi_han",
            r"(?i)\b(?:trong vòng|trong vong|không quá|khong qua|trong)\s+\d{1,2}\s*(?:ngày|ngay)(?:\s*(?:làm việc|lam viec))?\b",
            safe_time,
        ),
        (
            "thoi_han",
            r"(?i)\b0?3\s*[–\-]\s*0?5\s*(?:ngày|ngay)(?:\s*(?:làm việc|lam viec))?\b",
            safe_time,
        ),
        (
            "so_tien",
            r"(?i)(?:lệ phí|le phi|phí|phi|mức thu|muc thu)[^\n.!?]{0,40}?\d{1,3}(?:[.,]\d{3})+\s*(?:đồng|dong|vnđ|vnd)?",
            safe_fee,
        ),
        (
            "so_tien",
            r"(?i)\b\d{1,3}(?:[.,]\d{3})+\s*(?:đồng|dong|vnđ|vnd)\b",
            safe_fee,
        ),
        (
            "mien_phi",
            r"(?i)\b(?:miễn phí|mien phi|không thu lệ phí|khong thu le phi|không phải nộp lệ phí|khong phai nop le phi)\b",
            safe_fee,
        ),
        (
            "le_phi",
            r"(?i)(?:lệ phí|le phi)[^\n.!?]{0,60}",
            safe_fee,
        ),
        (
            "tham_quyen",
            r"(?i)(?:thẩm quyền|tham quyen|nộp hồ sơ tại|nop ho so tai|nộp tại|nop tai|cơ quan có thẩm quyền|co quan co tham quyen)[^\n.!?]{0,80}?(?:UBND|Ủy ban|Uy ban|cấp xã|cap xa|cấp huyện|cap huyen|cấp tỉnh|cap tinh|Sở Tư pháp|So Tu phap|Phòng Tư pháp|Phong Tu phap|bộ phận một cửa|bo phan mot cua)[^\n.!?]{0,40}",
            safe_auth,
        ),
        (
            "tham_quyen",
            r"(?i)\b(?:UBND|Ủy ban nhân dân|Uy ban nhan dan)\s+(?:cấp\s+)?(?:xã|xa|huyện|huyen|tỉnh|tinh|phường|phuong)[^\n.!?]{0,40}",
            safe_auth,
        ),
    ]

    # Apply replacements only when unsupported. Process sentence-ish segments to avoid over-deletion.
    # Work on original text with regex finditer, replace from end to start.
    spans: list[tuple[int, int, str, str, str]] = []  # start,end,type,text,replacement
    for claim_type, pattern, replacement in patterns:
        for m in re.finditer(pattern, safe):
            claim_text = m.group(0)
            # Skip if already a safe boilerplate sentence
            if "chưa có căn cứ xác nhận" in claim_text or "chua co can cu xac nhan" in _ascii_fold_claim_text(claim_text):
                continue
            if _evidence_supports_claim(claim_type, claim_text, evidence_norm):
                continue
            spans.append((m.start(), m.end(), claim_type, claim_text, replacement))

    if not spans:
        return safe, []

    # Resolve overlaps: keep earlier longer spans
    spans.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    chosen: list[tuple[int, int, str, str, str]] = []
    occupied: list[tuple[int, int]] = []
    for span in spans:
        s, e = span[0], span[1]
        if any(not (e <= os or s >= oe) for os, oe in occupied):
            continue
        chosen.append(span)
        occupied.append((s, e))
    chosen.sort(key=lambda x: x[0], reverse=True)

    def _expand_to_sentence(text: str, start: int, end: int) -> tuple[int, int]:
        """Expand a short claim span to its containing sentence for cleaner replacement."""
        left_break = max(text.rfind(".", 0, start), text.rfind("!", 0, start), text.rfind("?", 0, start), text.rfind("\n", 0, start))
        s2 = left_break + 1 if left_break >= 0 else 0
        right_candidates = [p for p in (text.find(".", end), text.find("!", end), text.find("?", end), text.find("\n", end)) if p >= 0]
        e2 = (min(right_candidates) + 1) if right_candidates else len(text)
        # Keep expansion modest: only if the claim is a short fragment inside a longer sentence.
        if (end - start) < 40 or (e2 - s2) <= max(80, (end - start) + 20):
            # Trim leading spaces on expanded start
            while s2 < start and text[s2].isspace():
                s2 += 1
            return s2, e2
        return start, end

    expanded: list[tuple[int, int, str, str, str]] = []
    for s, e, claim_type, claim_text, replacement in chosen:
        # Expand short fee/free/deadline fragments so we do not leave broken Vietnamese phrases.
        if claim_type in {"mien_phi", "so_tien", "ngay_lam_viec"} or (e - s) < 28:
            s2, e2 = _expand_to_sentence(safe, s, e)
            # Re-check overlaps after expansion
            if any(not (e2 <= os or s2 >= oe) for os, oe, *_ in expanded):
                s2, e2 = s, e
            expanded.append((s2, e2, claim_type, safe[s2:e2], replacement))
        else:
            expanded.append((s, e, claim_type, claim_text, replacement))

    # Re-resolve overlaps after expansion (prefer earlier/longer)
    expanded.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    final_spans: list[tuple[int, int, str, str, str]] = []
    occupied2: list[tuple[int, int]] = []
    for span in expanded:
        s, e = span[0], span[1]
        if any(not (e <= os or s >= oe) for os, oe in occupied2):
            continue
        final_spans.append(span)
        occupied2.append((s, e))
    final_spans.sort(key=lambda x: x[0], reverse=True)

    for s, e, claim_type, claim_text, replacement in final_spans:
        # Prefer full-sentence replacement when expanded span is sentence-like.
        piece = safe[s:e]
        if piece.strip().endswith((".", "!", "?")) or "\n" in piece:
            repl = replacement if replacement.endswith((".", "!", "?")) else replacement + "."
        else:
            repl = replacement
        safe = safe[:s] + repl + safe[e:]
        removed.append(
            {
                "claim_type": claim_type,
                "reason": "unsupported_by_retrieval",
                "original": claim_text[:300],
                "replacement": repl,
            }
        )

    # Cleanup repeated safe sentences / whitespace / broken punctuation
    safe = re.sub(r"[ \t]{2,}", " ", safe)
    safe = re.sub(r"\n{3,}", "\n\n", safe)
    safe = re.sub(r"\.{2,}", ".", safe)
    safe = re.sub(r"\s+([,.;:!?])", r"\1", safe)
    safe = re.sub(r"([.!?])\s*\1+", r"\1", safe)
    # Deduplicate consecutive identical safe lines
    lines_out: list[str] = []
    for line in safe.splitlines():
        if lines_out and lines_out[-1].strip() == line.strip() and "chưa có căn cứ xác nhận" in line:
            continue
        lines_out.append(line)
    safe = "\n".join(lines_out).strip()
    # reverse removed for chronological order
    removed.reverse()
    return safe, removed


def _merge_claim_guard_into_trace(
    rag_trace: dict | list | None,
    removed_claims: list[dict],
) -> dict:
    """Attach claim-guard results into rag_trace without dropping existing fields."""
    if isinstance(rag_trace, dict):
        trace = dict(rag_trace)
    elif rag_trace is None:
        trace = {}
    else:
        trace = {"raw_trace": rag_trace}
    existing = list(trace.get("removed_unsupported_claims") or [])
    # Normalize entries to include required fields
    normalized = []
    for item in existing + list(removed_claims or []):
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "claim_type": item.get("claim_type") or "unknown",
                "reason": item.get("reason") or "unsupported_by_retrieval",
                "original": item.get("original"),
                "replacement": item.get("replacement"),
            }
        )
    trace["removed_unsupported_claims"] = normalized
    if normalized:
        # convenience rollups
        trace["claim_guard"] = {
            "removed_count": len(normalized),
            "claim_types": sorted({str(x.get("claim_type")) for x in normalized}),
        }
    return trace

def _build_insufficient_answer(
    question: str,
    retrieval: dict | None = None,
    procedure_detail: dict | None = None,
) -> str:
    """Short, non-blocking insufficient message with optional procedure reference."""
    tried = []
    results = list((retrieval or {}).get("results") or [])
    for item in results[:3]:
        law = str(item.get("law_number") or "").strip()
        title = str(item.get("document_title") or "").strip()
        art = str(item.get("article_number") or "").strip()
        label = " - ".join([p for p in [law, title] if p])
        if art:
            label = f"{label}; Điều {art}" if label else f"Điều {art}"
        if label:
            tried.append(label)
    lines = [
        "Chưa đủ căn cứ pháp lý sát trong kho hiện tại để kết luận chắc chắn.",
        "Bạn có thể hỏi cụ thể hơn (số hiệu văn bản, địa bàn, thủ tục) hoặc nạp thêm văn bản chính thức rồi hỏi lại.",
    ]
    if tried:
        lines.append("Đã rà một số nguồn gần nhất: " + "; ".join(tried) + ".")
    else:
        question_preview = (question or "").strip()[:160]
        lines.append(f"Chưa truy xuất được nguồn phù hợp cho câu hỏi: {question_preview}.")
    if procedure_detail:
        proc_name = procedure_detail.get("name") or "thủ tục liên quan"
        dept = procedure_detail.get("department") or "cơ quan phụ trách"
        lines.append(
            f"Thủ tục tham chiếu (không thay căn cứ VBPL): {proc_name} — {dept}."
        )
        lines.append("Phần thủ tục chỉ mang tính hướng dẫn quy trình, chưa phải căn cứ pháp lý đã truy xuất.")
    return "\n".join(lines)


def _insufficient_legal_evidence_response(
    question: str,
    retrieval: dict | None = None,
    procedure_detail: dict | None = None,
) -> AskResponse:
    # Do not present weak/near-miss retrieval as formal legal citations.
    # Near sources are already summarized textually in the insufficient answer.
    # Procedure seed may still be returned, but only as process reference.
    proc = None
    if procedure_detail:
        proc = dict(procedure_detail)
        proc["is_reference_only"] = True
        proc["source"] = proc.get("source") or "procedure_seed"
        proc["label"] = "Tham chiếu quy trình"
        proc["disclaimer"] = (
            "Có tham chiếu quy trình, nhưng chưa có căn cứ VBPL đủ sát từ kho văn bản đã truy xuất."
        )

    answer = _build_insufficient_answer(question, retrieval, proc)
    if proc and "Tham chiếu quy trình" not in answer:
        answer = (
            answer
            + "\n\nTham chiếu quy trình (không thay căn cứ VBPL): "
            + str(proc.get("name") or "thủ tục liên quan")
            + "."
        )

    # Only return forms if the question explicitly requests them
    if not _question_requests_forms(question):
        recommended_forms = None
        if proc is not None:
            proc = dict(proc)
            proc["forms"] = []
            proc["recommended_forms"] = []
    else:
        recommended_forms = _extract_recommended_forms(proc, question)
    policy = classify_question(question)
    return AskResponse(
        question=question,
        answer=answer,
        # This helper has no authenticated role context. Keep diagnostics
        # server-side; the endpoint selectively exposes them to an admin.
        rag_trace=None,
        grounding_status="insufficient_evidence",
        procedure_detail=proc,
        procedure_summary=(
            f"Tham chiếu quy trình: {proc.get('name')}" if proc else None
        ),
        recommended_forms=recommended_forms,
        citations=[],
        question_type=policy["question_type"],
        detected_domain=policy.get("detected_domain"),
        required_sections=policy.get("required_sections") or [],
        forms_unavailable=bool(policy.get("requests_form") and not recommended_forms),
        source_gap=policy.get("required_sections") or [],
    )


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def _filter_by_applicability_tags(question: str, results: list[dict]) -> list[dict]:
    """Filter only on reviewed scope tags, never on a hard-coded law number."""
    folded = _ascii_fold_simple(question)
    requested_scope = (
        "foreign"
        if any(
            phrase in folded
            for phrase in (
                "nuoc ngoai",
                "ngoai nuoc",
                "nguoi nuoc ngoai",
                "quoc tich nuoc ngoai",
            )
        )
        else "domestic"
    )
    filtered: list[dict] = []
    for result in results:
        raw_tags = result.get("applicability_tags") or result.get("scope_tags") or []
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        tags = {_ascii_fold_simple(str(tag)) for tag in raw_tags if tag}
        if tags and {"domestic", "foreign"}.intersection(tags) and requested_scope not in tags:
            continue
        filtered.append(result)
    return filtered


def _select_local_sources(question: str, retrieval: dict) -> list[dict]:
    results = list(retrieval.get("results", []))
    results = _filter_by_applicability_tags(question, results)
    normalized_question = _normalize_text(question)

    def matches(item: dict, keywords: tuple[str, ...]) -> bool:
        haystack = _normalize_text(
            " ".join(
                [
                    str(item.get("article_title") or ""),
                    str(item.get("content") or ""),
                    str(item.get("document_title") or ""),
                ]
            )
        )
        return any(keyword in haystack for keyword in keywords)

    priority_keywords: tuple[str, ...] = ()
    if any(
        phrase in normalized_question
        for phrase in ("nộp ở đâu", "nộp đâu", "ở đâu", "cơ quan nào", "thẩm quyền")
    ):
        priority_keywords = ("thẩm quyền",)
    elif any(
        phrase in normalized_question
        for phrase in ("thời hạn", "bao lâu", "mấy ngày", "giải quyết trong bao lâu")
    ):
        priority_keywords = ("ngày làm việc", "thời hạn", "trong thời hạn")
    elif any(
        phrase in normalized_question
        for phrase in ("lệ phí", "mức phí", "bao nhiêu tiền", "phí bao nhiêu")
    ):
        priority_keywords = ("lệ phí", "phí")

    if priority_keywords:
        priority_items = [item for item in results if matches(item, priority_keywords)]
        remaining = [item for item in results if item not in priority_items]
        results = priority_items + remaining

    return results[:LOCAL_MAX_SOURCES]


def _short_document_title(title: str, law_number: str = "") -> str:
    """Pick a short, human-friendly document title for display citations."""
    raw = (title or "").strip()
    if not raw:
        return ""
    # Prefer the last breadcrumb segment when crawlers store long paths.
    # Do NOT split on "/" because Vietnamese law numbers use it (e.g. 123/2015/NĐ-CP).
    parts = [p.strip() for p in re.split(r"[>|]", raw) if p.strip()]
    candidate = parts[-1] if parts else raw
    # Drop leading "Điều N." leftovers if title accidentally includes article text.
    candidate = re.sub(r"^Điều\s+\d+[a-zA-Z]?[\.\s:-]*", "", candidate, flags=re.IGNORECASE).strip()
    # If title is just the law number, leave empty so caller can format from number alone.
    if law_number and candidate.replace(" ", "").casefold() == law_number.replace(" ", "").casefold():
        return ""
    # Keep titles reasonably short for answer display.
    if len(candidate) > 90:
        candidate = candidate[:87].rstrip() + "..."
    return candidate


def _infer_doc_kind_prefix(law_number: str, title: str = "") -> str:
    """Infer common Vietnamese legal instrument prefix when title is missing."""
    blob = f"{law_number} {title}".casefold()
    if re.search(r"/qh\d*\b", blob) or "luật" in blob or "luat" in _ascii_fold_claim_text(blob):
        return "Luật"
    if re.search(r"/nđ-cp|/nd-cp", blob) or "nghị định" in blob or "nghi dinh" in _ascii_fold_claim_text(blob):
        return "Nghị định"
    if re.search(r"/tt-", blob) or "thông tư" in blob or "thong tu" in _ascii_fold_claim_text(blob):
        return "Thông tư"
    if re.search(r"/qđ-|/qd-", blob) or "quyết định" in blob or "quyet dinh" in _ascii_fold_claim_text(blob):
        return "Quyết định"
    return ""


def _format_natural_legal_citation(
    *,
    law_number: str = "",
    document_title: str = "",
    article_number: str = "",
    clause_number: str = "",
    point_number: str = "",
    law_name: str = "",
) -> str:
    """Human-readable citation without brackets or internal ids.

    Examples:
    - Luật Hộ tịch 60/2014/QH13, Điều 35
    - Nghị định 123/2015/NĐ-CP, Điều 29
    """
    law_number = str(law_number or "").strip()
    title = _short_document_title(str(document_title or law_name or ""), law_number)
    article_number = str(article_number or "").strip()
    clause_number = str(clause_number or "").strip()
    point_number = str(point_number or "").strip()

    head = ""
    if title and law_number:
        # "Luật Hộ tịch 60/2014/QH13"
        if law_number.casefold() in title.casefold():
            head = title
        else:
            head = f"{title} {law_number}".strip()
    elif title:
        head = title
    elif law_number:
        prefix = _infer_doc_kind_prefix(law_number, title)
        head = f"{prefix} {law_number}".strip() if prefix else law_number
    else:
        head = "Văn bản pháp luật"

    tail_parts: list[str] = []
    if article_number:
        # Avoid "Điều Điều 35"
        art = article_number if re.search(r"(?i)^điều\b", article_number) else f"Điều {article_number}"
        tail_parts.append(art)
    if clause_number:
        clause = clause_number if re.search(r"(?i)^khoản\b", clause_number) else f"Khoản {clause_number}"
        tail_parts.append(clause)
    if point_number:
        point = point_number if re.search(r"(?i)^điểm\b", point_number) else f"Điểm {point_number}"
        tail_parts.append(point)

    if tail_parts:
        return f"{head}, {', '.join(tail_parts)}"
    return head


def _legal_label(result: dict) -> str:
    """Build detailed legal citation label with article, clause, point info.

    Returns natural display text (no [legal:id]). Internal chunk ids stay in
    structured citations/rag_trace, not in this display label.
    """
    return _format_natural_legal_citation(
        law_number=str(result.get("law_number") or ""),
        document_title=str(result.get("document_title") or ""),
        law_name=str(result.get("law_name") or ""),
        article_number=str(result.get("article_number") or ""),
        clause_number=str(result.get("clause_number") or ""),
        point_number=str(result.get("point_number") or ""),
    )


def _resolve_ask_session_owner_key(
    request=None, *, user_id=None, role=None
):
    """Return a real account identity for optional persisted Ask context.

    Shared-role legacy authentication has no individual owner. It may ask a
    one-off question, but cannot load or write a persisted conversation.
    """
    if user_id:
        return str(user_id)
    if request is not None:
        rid = get_request_user_id(request)
        if rid:
            return str(rid)
    return None


def _section_retrieval_kwargs(
    *,
    current_question: str,
    request_id: str,
    selected_domain: str | None,
) -> dict[str, str]:
    """Build request-local retrieval provenance only for the opt-in path.

    The full conversation remains in the generation prompt, but it must never
    become retrieval input.  The current question is therefore passed through
    on both paths.  Request/issue provenance is added only when Feature 005 is
    enabled, so disabling the flag still rolls back the section policy without
    reintroducing cross-turn retrieval leakage or changing stored data/corpus.
    """

    from api.legal_section_grounding import (
        classify_issue_domain,
        is_section_grounding_enabled,
    )

    if not current_question.strip():
        return {}
    kwargs = {"retrieval_question": current_question}
    if not is_section_grounding_enabled():
        return kwargs
    issue_domain = classify_issue_domain(current_question)
    kwargs.update({
        "request_id": request_id,
        "issue_id": f"issue-{request_id[:24]}",
        # The deterministic question classifier is used for evidence
        # eligibility; selected_domain remains only a retrieval scope hint.
        "issue_domain": issue_domain or (selected_domain or "unknown"),
    })
    return kwargs


def _build_section_graph_input(
    *,
    issue_id: str,
    issue_query: str,
    issue_domain: str,
    request_id: str,
    role: str | None,
    selected_domain: str | None,
    question_type: str | None,
    required_sections: list[str],
    legal_as_of: str,
) -> dict[str, Any]:
    """Build one bounded Ask-graph input for exactly one current issue.

    This helper intentionally does not accept conversation history.  The
    experimental section path is request- and issue-local by construction;
    the legacy graph input remains unchanged while the feature flag is off.
    """

    safe_domain = issue_domain if issue_domain != "unknown" else selected_domain
    return {
        "question": issue_query,
        "role": role,
        "domain": safe_domain,
        "question_type": question_type,
        "required_sections": required_sections,
        "legal_as_of": legal_as_of,
        "retrieval_question": issue_query,
        "request_id": request_id,
        "issue_id": issue_id,
        "issue_domain": issue_domain,
    }


async def _run_section_orchestration(
    *,
    ask_request: AskRequest,
    request_id: str,
    question_policy: Mapping[str, Any],
    legal_as_of: str,
    strategy_model_id: str,
    answer_model_id: str,
    final_answer_model_id: str,
) -> tuple[list[Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Run the existing graph once per deterministic issue, sequentially.

    Each graph gets its own retrieval query and exact provenance IDs.  A
    generated answer is retained only when the existing legal-reference
    validator confirms it against that issue's packet.  Failure of one issue
    therefore cannot replace another verified issue with a global fallback.
    """

    stage_started = time.perf_counter()
    sections: list[Any] = []
    all_results: list[dict[str, Any]] = []
    issue_traces: list[dict[str, Any]] = []
    generation_ms = 0.0
    retrieval_ms = 0.0

    for planned_issue in plan_legal_issues(ask_request.question):
        issue = replace(planned_issue, request_id=request_id)
        graph_input = _build_section_graph_input(
            issue_id=issue.issue_id,
            issue_query=issue.query_text,
            issue_domain=issue.domain,
            request_id=request_id,
            role=ask_request.role,
            selected_domain=ask_request.domain,
            question_type=question_policy.get("question_type"),
            required_sections=list(question_policy.get("required_sections") or []),
            legal_as_of=legal_as_of,
        )
        issue_started = time.perf_counter()
        candidate_answer = ""
        evidence: Any = []
        async for chunk in ask_graph.astream(
            input=graph_input,  # type: ignore[arg-type]
            config=dict(
                configurable=dict(
                    strategy_model=strategy_model_id,
                    answer_model=answer_model_id,
                    final_answer_model=final_answer_model_id,
                )
            ),
            stream_mode="updates",
        ):
            if "provide_answer" in chunk:
                evidence = chunk["provide_answer"].get("evidence", [])
            if "write_final_answer" in chunk:
                candidate_answer = str(chunk["write_final_answer"].get("final_answer") or "")

        issue_elapsed_ms = (time.perf_counter() - issue_started) * 1000
        retrieval_results = _extract_retrieval_results(evidence)
        all_results.extend(retrieval_results)
        validation = validate_legal_references(candidate_answer, retrieval_results)
        verified_answer = candidate_answer if validation.status == "fully_grounded" else None
        sections.append(
            validate_answer_section(
                request_id=request_id,
                issue_id=issue.issue_id,
                title=issue.title,
                sources=retrieval_results,
                answer=verified_answer,
                limitation=(
                    "Chưa có đủ căn cứ cùng lượt hỏi và cùng nội dung để đưa ra "
                    "kết luận pháp lý cho phần này."
                ),
                clarifying_question=(
                    None
                    if verified_answer
                    else "Bạn có thể nêu rõ thủ tục hoặc tình tiết cụ thể cần xác minh không?"
                ),
            )
        )
        issue_traces.append(
            {
                "issue_id": issue.issue_id,
                "candidate_count": len(retrieval_results),
                "validation_status": validation.status,
                "duration_ms": round(issue_elapsed_ms, 1),
            }
        )
        # Graph telemetry captures individual retrieval/generation timings.
        # Keep a privacy-safe aggregate here without source text or answers.
        generation_ms += issue_elapsed_ms
        retrieval_ms += issue_elapsed_ms

    aggregate = aggregate_answer_sections(sections)
    total_ms = (time.perf_counter() - stage_started) * 1000
    metric = build_section_grounding_metric(
        request_id=request_id,
        statuses=[section.status for section in sections],
        stage_timings_ms={
            "retrieval": retrieval_ms,
            "generation": generation_ms,
            "validation": 0,
            "end_to_end": total_ms,
        },
        repair_count=0,
        completed=True,
        error_category="none",
    )
    return sections, aggregate, all_results, {"issues": issue_traces, "metric": metric}


def _get_ask_session_history(owner_key: str | None, session_id: str | None) -> list[dict]:
    """Legacy JSON fallback: read last 5 messages from ask_sessions JSON."""
    if not session_id or not owner_key:
        return []
    from api.routers.ask_sessions import _load_session
    try:
        session = _load_session(owner_key, session_id)
        if session:
            messages = session.get("messages", [])
            return messages[-5:] if len(messages) > 5 else messages
    except Exception as e:
        logger.warning(f"Error loading session history: {str(e)}")
    return []


async def _build_conversation_context(
    conversation_id: str | None,
    owner_key: str | None,
    real_user_id: str | None = None,
    role_context: str | None = None,
    is_admin: bool = False,
) -> tuple[str | None, list[dict]]:
    """Return (conversation_id_or_none, token-limited context messages)."""
    if not conversation_id or not owner_key:
        return None, []
    ctx = await conv_svc.get_followup_context(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        is_admin=is_admin,
    )
    if not ctx:
        # Fallback: try legacy JSON sessions
        legacy = _get_ask_session_history(owner_key, conversation_id)
        if legacy:
            return conversation_id, legacy
        return None, []
    return conversation_id, ctx


def _build_local_prompt(
    question: str,
    role: str,
    retrieval: dict,
    history: list[dict] | None = None,
    question_policy: dict[str, Any] | None = None,
) -> str:
    """Build offline/local ask prompt with role-locked answer templates.

    Citizen: 3 actionable sections + natural legal citations.
    Officer: 5 professional sections + authority/checklist.
    Admin: ops-oriented guidance.
    """
    selected_results = _select_local_sources(question, retrieval)
    question_policy = question_policy or classify_question(question)
    sources: list[str] = []
    for index, item in enumerate(selected_results, start=1):
        sources.append(
            "\n".join(
                [
                    f"Nguồn {index}: {_legal_label(item)}",
                    f"Văn bản: {item.get('law_number')} - {item.get('document_title')}",
                    f"Điều: {item.get('article_number')} - {item.get('article_title')}",
                    f"Link nguồn: {item.get('source_url') or 'không có'}",
                    f"Lĩnh vực: {item.get('domain_name') or item.get('field_name') or 'không rõ'}",
                    "Nội dung:",
                    str(item.get("content") or "")[:LOCAL_SOURCE_CHAR_LIMIT],
                ]
            )
        )

    form_context = ""
    try:
        from api.routers.ward_procedures import build_official_form_context

        form_context = build_official_form_context(question)
    except Exception as e:
        logger.warning(f"Failed to load matching forms for local context: {str(e)}")

    source_block = (
        "\n\n".join(sources)
        if sources
        else "Không có nguồn phù hợp trong kho hiện tại."
    )
    form_block = f"\n\nBiểu mẫu official liên quan (nếu có):\n{form_context}" if form_context else ""

    history_block = ""
    if history:
        history_lines = []
        for msg in history:
            role_label = "Người dùng" if msg.get("role") == "user" else "Trợ lý"
            content = msg.get("content") or ""
            history_lines.append(f"{role_label}: {content}")
        history_block = "\nLịch sử trò chuyện trước đó:\n" + "\n".join(history_lines) + "\n---\n"

    common_rules = """
Yêu cầu bắt buộc (áp dụng mọi vai trò):
- Chỉ dùng các nguồn được cung cấp bên dưới để trả lời.
- Mỗi kết luận pháp lý quan trọng phải lồng số hiệu và điều/khoản từ chính nguồn được cung cấp.
- CHỈ trích số hiệu/điều/khoản có trong nguồn đã truy xuất. Không bịa điều luật, mức phạt, thời hạn, lệ phí.
- Nếu nguồn không nêu phí/thời hạn/hồ sơ bắt buộc: ghi rõ "Nguồn hiện có không nêu nội dung này" hoặc "Chưa đủ căn cứ trong kho hiện tại".
- Không thêm giấy tờ, cơ quan hoặc nơi lấy biểu mẫu từ kiến thức nền.
- Nếu thiếu biểu mẫu chính thức: nói thẳng "Hệ thống chưa có biểu mẫu chính thức đã duyệt".
- KHÔNG dùng mã nội bộ [legal:...], legal:123, chunk id, hay ngoặc vuông citation kỹ thuật.
- Không mở đầu xã giao dài ("Chào anh, tôi hiểu..."). Đi thẳng vào kết luận.
- Không dùng văn bản hết hiệu lực/chưa có hiệu lực.
- Viết tiếng Việt, ngắn gọn, mạch lạc.
""".strip()

    shared_contract = answer_contract(
        role,
        str(question_policy.get("question_type") or "unknown"),
        list(question_policy.get("required_sections") or []),
    )
    contract_block = f"""
Loại câu hỏi đã phân loại: {question_policy.get('question_type')}
Các mục cần kiểm tra: {', '.join(question_policy.get('required_sections') or [])}
Nếu nguồn không có một mục, ghi đúng câu: "Chưa xác minh được nội dung này từ nguồn hiện có." Không được tự điền bằng kiến thức nền.

{render_answer_contract(shared_contract)}
""".strip()

    if role == "officer":
        role_block = "Vai trò: Cán bộ/chuyên viên pháp lý phường-xã.\nNgười đọc là cán bộ."
    elif role == "admin":
        role_block = "Vai trò: Quản trị hệ thống.\nNgười đọc là quản trị hệ thống."
    else:
        role_block = "Vai trò: Người dân.\nNgười đọc là người dân."

    return f"""
Bạn là Trợ lý Pháp luật Phường Xã Hải Phòng.

{common_rules}

{contract_block}

{role_block}

{history_block}Câu hỏi:
{question}

Nguồn pháp luật đã truy xuất:
{source_block}{form_block}

Hãy trả lời hoàn chỉnh bằng tiếng Việt theo đúng định dạng vai trò ở trên.
""".strip()



def _detect_scope_from_question(question: str) -> str | None:
    """Auto-detect which scope to prioritize based on question keywords.
    
    Returns: "central", "haiphong", "local", or None (no preference)
    """
    import re
    
    # Normalize question
    text = question.lower()
    
    # De-accent Vietnamese for matching
    replacements = {
        'hải phòng': 'hai phong',
        'hải phong': 'hai phong',
        'tp hải phòng': 'tp hai phong',
        'thành phố hải phòng': 'thanh pho hai phong',
        'phường': 'phuong',
        'xã': 'xa',
        'quận': 'quan',
        'huyện': 'huyen',
        'ủy ban': 'uy ban',
        'trung ương': 'trung uong',
    }
    
    for accented, plain in replacements.items():
        text = text.replace(accented, plain)
    
    # Check for local indicators
    local_patterns = [
        r'\bphuong\s+\w+',
        r'\bxa\s+\w+',
        r'\bubnd\s+phuong',
        r'\bubnd\s+xa',
        r'cap\s+(xa|phuong)',
        r'\bo\s+(phuong|xa)\s+nao',
        r'làm ở\s+(phuong|xa)',
    ]
    
    # Check for Hai Phong specific
    haiphong_patterns = [
        r'\btp\s+hai\s+phong\b',
        r'\bhai\s+phong\b',
        r'\bthanh\s+pho\s+hai\s+phong\b',
        r'ubnd\s+tp\s*hai',
        r'\bo\s+hai\s+phong\b',
    ]
    
    # Check for central (default, no explicit keywords needed)
    central_patterns = [
        r'\bluat\s+\d+/\d{4}/qh',
        r'\bnghi\s+dinh\s+\d+/\d{4}/n\s*d-cp',
        r'\bthong\s+tu\s+\d+/\d{4}',
        r'\btrung\s+uong\b',
    ]
    
    # Priority: local > haiphong > central
    for pattern in local_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return "local"
    
    for pattern in haiphong_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return "haiphong"
    
    for pattern in central_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return "central"
    
    # No specific scope detected - let retrieval decide
    return None


async def _call_legal_retrieval(ask_request: AskRequest) -> dict:
    """Retrieve from the fast legal scope, then expand only on evidence gaps."""
    scope_filter = None
    selected_domain = (ask_request.domain or "").strip() or None
    if selected_domain is None:
        detected_domain = _detect_question_domain(ask_request.question)
        if detected_domain:
            selected_domain = str(detected_domain.get("slug") or "").strip() or None

    async def _search(domain: str | None, tier: str, candidate_count: int) -> dict:
        payload = {
            "query": ask_request.question,
            "limit": 8,
            "candidate_count": candidate_count,
            "as_of": effective_legal_date(
                legal_as_of=ask_request.legal_as_of,
                event_date=ask_request.event_date,
            ).isoformat(),
            "domain": domain,
            "include_trace": bool(
                ask_request.show_rag_trace and ask_request.role == "admin"
            ),
            "scope_filter": scope_filter,
            "retrieval_tier": tier,
        }
        return await get_legal_search_client().search(payload)

    result = await _search(selected_domain, "core", 180)
    used_domain = selected_domain
    core_results = list(result.get("results") or [])
    fallback_reason = expanded_retrieval_reason(
        ask_request.question, core_results
    ) or ""

    if fallback_reason:
        result = await _search(selected_domain, "expanded", 240)
        trace = result.get("trace") if isinstance(result.get("trace"), dict) else {}
        if not isinstance(result.get("trace"), dict):
            result["trace"] = {}
            trace = result["trace"]
        trace["retrieval_fallback"] = {
            "from_tier": "core",
            "to_tier": "expanded",
            "reason": fallback_reason,
        }

    # Keep allowed_domains only as an officer authorization boundary. It is not
    # a geographic retrieval filter and does not affect citizen/admin search.
    if getattr(ask_request, "allowed_domains", None) is not None:
        allowed = ask_request.allowed_domains
        if "results" in result:
            filtered_results = []
            for res in result["results"]:
                res_domain = (
                    res.get("domain_slug")
                    or res.get("domain")
                    or (res.get("metadata") or {}).get("domain")
                )
                # Keep rows with unknown domain; only drop clear mismatches.
                if res_domain and res_domain not in allowed:
                    continue
                filtered_results.append(res)
            result["results"] = filtered_results

    if isinstance(result.get("trace"), dict):
        result["trace"]["retrieval_domain_used"] = used_domain
        result["trace"]["requested_domain"] = selected_domain
        result["trace"]["retrieval_tier"] = (
            "expanded" if fallback_reason else "core"
        )
    return result


async def _call_ollama(model: str, prompt: str) -> str:
    async with httpx.AsyncClient(timeout=900) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.05,
                    "top_p": 0.9,
                    "num_ctx": LOCAL_NUM_CTX,
                    "num_predict": LOCAL_NUM_PREDICT,
                },
            },
        )
    response.raise_for_status()
    data = response.json()
    return str(data.get("response") or "").strip()




def _build_vbpl_search_url(law_number: str | None = None, document_title: str | None = None) -> str:
    """Build a reliable VBPL search fallback URL from law number/title."""
    query = " ".join(
        part.strip()
        for part in (str(law_number or "").strip(), str(document_title or "").strip())
        if part and str(part).strip()
    ).strip() or "văn bản pháp luật"
    from urllib.parse import quote
    return f"https://www.google.com/search?q={quote(query + ' site:vbpl.vn')}"


def _extract_doc_id(item: dict) -> str:
    """Best-effort document id for internal fallback links."""
    for key in ("doc_id", "document_id", "parent_id", "law_id", "id", "chunk_id"):
        raw = str(item.get(key) or "").strip()
        if not raw:
            continue
        raw = raw.replace("legal:", "").strip()
        # Prefer parent/document ids over pure chunk numeric ids when available.
        if key in {"doc_id", "document_id", "parent_id", "law_id"} and raw:
            return raw
        if key in {"id", "chunk_id"} and raw:
            # keep as last resort
            candidate = raw
    # second pass for last resort
    for key in ("parent_id", "document_id", "doc_id", "id", "chunk_id"):
        raw = str(item.get(key) or "").replace("legal:", "").strip()
        if raw:
            return raw
    return ""


def _resolve_citation_source_url(item: dict) -> str:
    """Prefer internal legal-doc endpoint so Ask can open text even if VBPL URL is broken."""
    source_url = str(item.get("source_url") or "").strip()
    # already an internal API path
    if source_url.startswith("/api/legal/docs/"):
        return source_url
    doc_id = _extract_doc_id(item)
    if doc_id:
        return f"/api/legal/docs/{doc_id}"
    # Keep non-VBPL external URLs only when no internal doc is available.
    if source_url.startswith("http://") or source_url.startswith("https://"):
        if "vbpl.vn" in source_url.lower():
            return ""
        return source_url
    if source_url.startswith("/api/"):
        return source_url
    return ""


def _build_citations_from_retrieval(
    retrieval_or_results: dict | list | None,
    answer_text: str | None = None,
) -> list[dict[str, str]]:
    """Normalize retrieval hits into citation objects with links when available.

    Each citation aims to include:
    - law_number, document_title, article_number
    - source_url (external http kept; else internal /api/legal/docs/{doc_id} when possible)
    - doc_id
    - fallback_search_url (VBPL search by law_number/title)
    """
    if not retrieval_or_results:
        return []
    if isinstance(retrieval_or_results, dict):
        raw = list(retrieval_or_results.get("results") or [])
    else:
        raw = list(retrieval_or_results)
    # Retrieval ordering is not always legal-answer ordering. Rank active,
    # directly relevant chunks first and do not cite weak metadata-only hits.
    query_text = ""
    if isinstance(retrieval_or_results, dict):
        query_text = str(retrieval_or_results.get("query") or retrieval_or_results.get("question") or "")
    query_tokens = _meaningful_tokens(query_text)

    def citation_score(item: dict) -> tuple[float, float, int, int]:
        status = str(item.get("effective_status") or item.get("document_status") or item.get("status") or "").strip().casefold()
        active = 1 if not status or status == "active" else 0
        article = str(item.get("article_number") or "").strip()
        doc_id = _extract_doc_id(item)
        haystack = " ".join(str(item.get(field) or "") for field in (
            "document_title", "law_number", "article_title", "content", "field_name", "domain_name",
        ))
        overlap = len(query_tokens & _meaningful_tokens(haystack)) if query_tokens else 0
        retrieval_score = float(item.get("score") or item.get("final_score") or 0.0)
        # Ordered tuple prevents a high vector score from selecting inactive docs.
        return (active, retrieval_score, overlap, int(bool(article and doc_id)))

    candidates = [item for item in raw if isinstance(item, dict)]
    # If the answer names a law/article, prefer only the retrieved chunks that
    # contain that same reference. This prevents a nearby high-score result
    # from being displayed as the source of a different legal claim.
    mentioned_laws = {
        value.casefold()
        for value in re.findall(r"\b\d{1,4}/\d{4}/[A-Za-zÀ-ỸĐđ0-9_.-]+\b", answer_text or "")
    }
    mentioned_articles = {
        value.casefold()
        for value in re.findall(r"(?i)\b(?:điều|dieu)\s+(\d+[a-z]?)", answer_text or "")
    }
    matched_candidates = []
    for item in candidates:
        law = str(item.get("law_number") or "").strip().casefold()
        article = str(item.get("article_number") or "").strip().casefold()
        if (not mentioned_laws or law in mentioned_laws) and (not mentioned_articles or article in mentioned_articles):
            matched_candidates.append(item)
    if mentioned_laws or mentioned_articles:
        # An explicit legal reference must never fall back to a nearby result.
        # Returning no citation is safer than linking a different document.
        candidates = matched_candidates
    candidates.sort(key=citation_score, reverse=True)
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        if len(citations) >= 3:
            break
        status = str(item.get("effective_status") or item.get("document_status") or item.get("status") or "").strip().casefold()
        if status and status != "active":
            continue
        chunk_id = str(item.get("chunk_id") or item.get("id") or "").replace("legal:", "").strip()
        law_number = str(item.get("law_number") or "").strip()
        document_title = str(item.get("document_title") or "").strip()
        article_number = str(item.get("article_number") or "").strip()
        # Dedup key prefers law+article, then chunk id
        dedup_key = "|".join(
            part for part in (law_number.lower(), article_number.lower(), chunk_id) if part
        )
        if not dedup_key or dedup_key in seen:
            continue
        seen.add(dedup_key)

        natural_label = _format_natural_legal_citation(
            law_number=law_number,
            document_title=document_title,
            article_number=article_number,
            clause_number=str(item.get("clause_number") or ""),
            point_number=str(item.get("point_number") or ""),
            law_name=str(item.get("law_name") or ""),
        )
        doc_id = _extract_doc_id(item)
        # Keep original VBPL URL in source_url (do not overwrite with api endpoint)
        raw_source_url = str(item.get("source_url") or "").strip()
        if not raw_source_url.startswith(("http://", "https://", "/api/")):
            raw_source_url = ""
        
        # Build clean frontend internal URL (canonical route)
        internal_url = f"/legal-documents/{doc_id}" if doc_id else ""
        if internal_url and article_number:
            clause_number = str(item.get("clause_number") or "").strip()
            point_number = str(item.get("point_number") or "").strip()
            params = f"article={article_number}"
            if clause_number:
                params += f"&clause={clause_number}"
            if point_number:
                params += f"&point={point_number}"
            internal_url = f"{internal_url}?{params}"
            
        pdf_url = f"/api/legal/docs/{doc_id}/download.pdf" if doc_id else ""
        if article_number and pdf_url:
            pdf_url = f"{pdf_url}?article={article_number}"
            
        citations.append(
            {
                "chunk_id": chunk_id,  # retained for audit/trace only
                "doc_id": doc_id,
                "law_number": law_number,
                "document_title": document_title,
                "article_number": article_number,
                "article_title": str(item.get("article_title") or ""),
                "clause_number": str(item.get("clause_number") or "").strip(),
                "point_number": str(item.get("point_number") or "").strip(),
                # Source provenance is retained for audit. The default Ask link
                # remains the internal viewer, never this external URL.
                "source_url": raw_source_url,
                "source_metadata": {
                    "issuing_agency": str(item.get("issuing_agency") or "").strip(),
                    "scope": str(item.get("scope") or "").strip(),
                    "effective_date": str(item.get("effective_date") or "").strip(),
                    "source_url": raw_source_url,
                },  # link gốc VBPL làm link phụ
                "internal_url": internal_url,  # link viewer nội bộ (frontend) làm link chính
                "pdf_url": pdf_url,
                "fallback_search_url": f"https://vbpl.vn/van-ban/tim-kiem?q={urllib.parse.quote_plus(law_number)}" if law_number else "",
                "link_status": "internal_indexed" if doc_id else ("external_recorded" if raw_source_url else "missing"),
                "label": natural_label or "Văn bản pháp luật",
            }
        )
    return citations[:3]


def _format_sources_appendix(citations: list[dict[str, str]]) -> str:
    """Compact natural source lines (1-3) when model answer lacks natural citations.

    No internal ids, no long technical card. Example:
    Căn cứ:
    - Luật Hộ tịch 60/2014/QH13, Điều 35
    """
    if not citations:
        return ""
    lines: list[str] = []
    for c in citations[:3]:
        natural = _format_natural_legal_citation(
            law_number=str(c.get("law_number") or ""),
            document_title=str(c.get("document_title") or ""),
            article_number=str(c.get("article_number") or ""),
            clause_number=str(c.get("clause_number") or ""),
            point_number=str(c.get("point_number") or ""),
            law_name=str(c.get("label") or ""),
        )
        if natural and natural not in lines and natural != "Văn bản pháp luật":
            lines.append(natural)
    if not lines:
        return ""
    body = "\n".join(f"- {line}" for line in lines)
    return "\n\nCăn cứ:\n" + body



def _sanitize_answer_citation_display(answer: str, retrieval_or_results: dict | list | None = None) -> str:
    """Normalize answer citations for user display.

    - Strip [legal:...] / legal:123 internal markers
    - Convert bracket citations like [123/2015/NĐ-CP - Điều 29] to natural text
    - Prefer natural forms: "Nghị định 123/2015/NĐ-CP, Điều 29"
    - Keep structured citations/rag_trace elsewhere for audit
    """
    if not answer:
        return answer

    text = answer

    # Map common chunk ids from retrieval to natural labels for replacement.
    id_to_label: dict[str, str] = {}
    if retrieval_or_results:
        items = (
            list(retrieval_or_results.get("results") or [])
            if isinstance(retrieval_or_results, dict)
            else list(retrieval_or_results or [])
        )
        for item in items:
            if not isinstance(item, dict):
                continue
            chunk_id = str(item.get("chunk_id") or item.get("id") or "").replace("legal:", "").strip()
            label = _format_natural_legal_citation(
                law_number=str(item.get("law_number") or ""),
                document_title=str(item.get("document_title") or ""),
                article_number=str(item.get("article_number") or ""),
                clause_number=str(item.get("clause_number") or ""),
                point_number=str(item.get("point_number") or ""),
                law_name=str(item.get("law_name") or ""),
            )
            if chunk_id and label:
                id_to_label[chunk_id] = label

    def _replace_legal_bracket(match: re.Match) -> str:
        body = (match.group(1) or "").strip()
        # patterns: "481400", "481400 - 60/2014/QH13 - Điều 35", "60/2014/QH13 - Điều 35"
        if re.fullmatch(r"\d+", body):
            return id_to_label.get(body, "")
        m_id = re.match(r"^(?P<id>\d+)\s*[-–—:]\s*(?P<rest>.+)$", body)
        if m_id:
            mapped = id_to_label.get(m_id.group("id"))
            if mapped:
                return mapped
            body = m_id.group("rest").strip()
        # body may already be law/article text
        law = ""
        art = ""
        m_law = re.search(r"(\d{1,4}/\d{4}/[A-Za-zÀ-ỹĐđ0-9\-]+)", body)
        if m_law:
            law = m_law.group(1)
        m_art = re.search(r"(?i)(?:điều|dieu)\s*(\d+[a-zA-Z]?)", body)
        if m_art:
            art = m_art.group(1)
        title = ""
        # If body starts with a name before law number
        if law and law in body:
            title = body.split(law)[0].strip(" -–—:")
        natural = _format_natural_legal_citation(
            law_number=law,
            document_title=title,
            article_number=art,
        )
        return natural if natural and natural != "Văn bản pháp luật" else (body if not re.fullmatch(r"\d+", body) else "")

    # [legal:...] and [legal: ... - ...]
    text = re.sub(r"\[\s*legal\s*:\s*([^\]]+)\]", _replace_legal_bracket, text, flags=re.IGNORECASE)
    # bare legal:123
    def _replace_bare_legal(match: re.Match) -> str:
        cid = match.group(1)
        return id_to_label.get(cid, "")

    text = re.sub(r"\blegal\s*:\s*(\d+)\b", _replace_bare_legal, text, flags=re.IGNORECASE)

    # Bracket citations without legal prefix: [123/2015/NĐ-CP - Điều 29] or [60/2014/QH13, Điều 35]
    def _replace_bracket_law(match: re.Match) -> str:
        body = (match.group(1) or "").strip()
        if not body:
            return ""
        # Skip markdown links / urls / source/note refs
        if "http://" in body.casefold() or "https://" in body.casefold():
            return match.group(0)
        if re.match(r"(?i)^(source|note|source_insight)\s*:", body):
            return match.group(0)
        if re.search(r"\d{1,4}/\d{4}/", body) or re.search(r"(?i)điều\s*\d+", body):
            return _replace_legal_bracket(match)
        return match.group(0)

    text = re.sub(r"\[([^\]]{3,120})\]", _replace_bracket_law, text)

    # Normalize residual "law - Điều N" / "law (Điều N)" into natural comma form.
    text = re.sub(
        r"(\d{1,4}/\d{4}/[A-Za-zÀ-ỹĐđ0-9\-]+)\s*[-–—]\s*(?:Đi[eè]u|Điều)\s*(\d+[a-zA-Z]?)",
        lambda m: _format_natural_legal_citation(law_number=m.group(1), article_number=m.group(2)),
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(\d{1,4}/\d{4}/[A-Za-zÀ-ỹĐđ0-9\-]+)\s*\(\s*(?:Đi[eè]u|Điều)\s*(\d+[a-zA-Z]?)\s*\)",
        lambda m: _format_natural_legal_citation(law_number=m.group(1), article_number=m.group(2)),
        text,
        flags=re.IGNORECASE,
    )

    # Drop leftover long source appendix headers if present.
    text = re.sub(r"(?im)^##\s*Căn cứ\s*/\s*Nguồn[^\n]*\n?", "", text)
    text = re.sub(r"(?im)^###\s*Liên kết nguồn\s*\n?", "", text)

    # No internal retrieval syntax is a valid public-answer format. Keep this
    # backend guard even though the frontend has defense-in-depth cleanup.
    text = re.sub(r"#ref-source-[\w-]+", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(?:chunk[_ -]?id|trace[_ -]?id|packet[_ -]?id|source[_ -]?record[_ -]?id)\s*[:=]\s*[\w.-]+",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Cleanup whitespace after removals
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" ?\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" ?([,.;:])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    return text.strip()


def _ensure_answer_has_source_links(answer: str, retrieval: dict | list | None) -> tuple[str, list[dict[str, str]]]:
    """Normalize answer citations for display and return structured citations for audit.

    Rules:
    1) Strip [legal:id] / legal:123 from user-facing answer.
    2) Build citations[] with law_number/title/article/source_url/doc_id/fallback_search_url.
    3) Keep external source_url; otherwise internal doc fallback when possible.
    4) If answer lacks natural legal cues but retrieval is available, append short
       "Căn cứ:" lines (1-3), never a long technical card.
    5) Caller must not use this for insufficient_evidence paths (those keep citations=[]).
    """
    citations = _build_citations_from_retrieval(retrieval, answer_text=answer)
    out = _sanitize_answer_citation_display(answer or "", retrieval)

    has_natural_cue = bool(
        re.search(r"\d{1,4}/\d{4}/", out)
        or re.search(r"(?i)\b(?:luật|nghị định|thông tư|quyết định|bộ luật)\b", out)
        or re.search(r"(?i)\bđiều\s+\d+", out)
        or re.search(r"(?im)^\s*căn\s*cứ\b", out)
    )
    if citations and not has_natural_cue:
        appendix = _format_sources_appendix(citations)
        if appendix and appendix.strip() not in out:
            out = f"{out.rstrip()}{appendix}"

    # Final sanitize to guarantee no residual [legal:...] markers.
    out = _sanitize_answer_citation_display(out, retrieval)
    out = re.sub(r"\[\s*legal\s*:[^\]]*\]", "", out, flags=re.IGNORECASE)
    out = re.sub(r"\blegal\s*:\s*\d+\b", "", out, flags=re.IGNORECASE)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out, citations


def _verify_citations(answer: str, retrieval: dict) -> tuple[bool, list[str]]:
    """Verify that all citations in answer exist in retrieval results.
    
    Returns:
        (is_valid, list_of_invalid_citations)
    """
    # Extract all [legal:...] citations from answer
    citation_pattern = r'\[legal:([^\]]+)\]'
    citations = re.findall(citation_pattern, answer)
    
    if not citations:
        return False, []
    
    # Get valid chunk_ids from retrieval
    valid_ids = set()
    for result in retrieval.get("results", []):
        chunk_id = result.get("chunk_id")
        if chunk_id:
            valid_ids.add(str(chunk_id))
    
    # Check each citation
    invalid = []
    for citation in citations:
        # citation format: "chunk_id - ..." or just "chunk_id"
        chunk_id = citation.split(" - ")[0] if " - " in citation else citation
        if chunk_id not in valid_ids:
            invalid.append(citation)
    
    return len(invalid) == 0, invalid


def _extract_retrieval_results(retrieval_or_evidence: dict | list) -> list[dict]:
    results = []

    def normalize_item(item: dict, chunk_id: object | None = None) -> dict:
        """Keep the metadata required to build a verifiable internal link."""
        document_id = item.get("document_id") or item.get("doc_id")
        return {
            "chunk_id": str(chunk_id if chunk_id is not None else item.get("chunk_id") or ""),
            "document_id": document_id,
            "doc_id": document_id,
            "law_number": item.get("law_number"),
            "law_name": item.get("law_name"),
            "document_title": item.get("document_title"),
            "article_number": item.get("article_number"),
            "article_title": item.get("article_title"),
            "clause_number": item.get("clause_number"),
            "point_number": item.get("point_number"),
            "effective_status": item.get("effective_status") or item.get("document_status") or item.get("status"),
            "effective_date": item.get("effective_date"),
            "issuing_agency": item.get("issuing_agency"),
            "scope": item.get("scope"),
            "official": item.get("official"),
            "official_level": item.get("official_level"),
            "domain": item.get("domain") or item.get("domain_slug") or item.get("issue_domain"),
            "document_status": item.get("document_status"),
            "article_status": item.get("article_status"),
            "request_id": item.get("request_id"),
            "issue_id": item.get("issue_id"),
            "issue_domain": item.get("issue_domain"),
            "relationships": item.get("relationships") or [],
            "score": item.get("score") or item.get("final_score"),
            "source_url": item.get("source_url"),
            "content": item.get("content"),
        }

    if isinstance(retrieval_or_evidence, dict):
        raw_list = retrieval_or_evidence.get("results") or []
        for item in raw_list:
            if isinstance(item, dict):
                results.append(normalize_item(item))
    elif isinstance(retrieval_or_evidence, list):
        for item in retrieval_or_evidence:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id") or ""
            chunk_id = item.get("chunk_id") or (item_id.split(":", 1)[-1] if ":" in item_id else item_id)
            results.append(normalize_item(item, chunk_id))
    return results




async def _safe_log_ask_history(**kwargs) -> bool:
    """Best-effort ask-history logging; audit failures must not break answers."""
    try:
        from api.user_service import log_ask_history
        # Audit persistence is deliberately off the critical answer path.  A
        # stalled database connection must not turn an already validated legal
        # answer into a browser timeout.  Keep the bound short and configurable
        # for local diagnostics; no question/answer data is logged on timeout.
        audit_timeout = max(
            0.5,
            float(os.getenv("LEGAL_AUDIT_WRITE_TIMEOUT_SECONDS", "5")),
        )
        await asyncio.wait_for(
            log_ask_history(**kwargs),
            timeout=audit_timeout,
        )
        return True
    except asyncio.TimeoutError:
        logger.warning("Ask history audit write timed out; answer delivery continues")
        telemetry.record_issue(
            "ask_audit_write_timeout",
            category="ask.persistence",
            error_class="TimeoutError",
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to write ask history audit record: {}",
            exc.__class__.__name__,
        )
        return False


def _audit_trace_snapshot(trace: dict | list | None) -> dict:
    """Return a bounded, privacy-safe trace summary for ask audit storage.

    Retrieval traces can contain source text, raw query fragments and nested
    candidate payloads.  They are useful for an admin response only when
    explicitly requested, but must not be serialized into the normal answer
    path or make audit persistence unbounded.  Keep only aggregate counters,
    statuses and timings.
    """
    if not isinstance(trace, dict):
        return {}

    snapshot: dict[str, Any] = {}
    latency = trace.get("latency_by_stage")
    if isinstance(latency, dict):
        snapshot["latency_by_stage"] = {
            str(key): float(value)
            for key, value in latency.items()
            if isinstance(value, (int, float))
        }

    pipeline = trace.get("answer_pipeline")
    if isinstance(pipeline, dict):
        snapshot["answer_pipeline"] = {
            key: pipeline.get(key)
            for key in (
                "retrieved_chunks",
                "llm_sources",
                "citations_created",
                "grounding_status",
                "final_answer_chars",
            )
            if pipeline.get(key) is not None
        }

    for key in ("retrieval_tier", "retrieval_domain_used", "requested_domain"):
        value = trace.get(key)
        if isinstance(value, (str, int, float, bool)):
            snapshot[key] = value

    fallback = trace.get("retrieval_fallback")
    if isinstance(fallback, dict):
        snapshot["retrieval_fallback"] = {
            key: fallback.get(key)
            for key in ("from_tier", "to_tier", "reason")
            if isinstance(fallback.get(key), (str, int, float, bool))
        }

    section_trace = trace.get("section_orchestration")
    if isinstance(section_trace, dict):
        issues = section_trace.get("issues")
        statuses = []
        if isinstance(issues, list):
            statuses = [
                str(item.get("validation_status"))
                for item in issues
                if isinstance(item, dict) and item.get("validation_status")
            ]
        snapshot["section_orchestration"] = {
            "issue_count": len(issues) if isinstance(issues, list) else 0,
            "validation_statuses": statuses[:32],
        }
        metric = section_trace.get("metric")
        if isinstance(metric, dict):
            snapshot["section_orchestration"]["metric"] = {
                key: metric.get(key)
                for key in (
                    "completed",
                    "error_category",
                    "repair_count",
                )
                if metric.get(key) is not None
            }

    return snapshot


def _verify_and_ground_answer(
    question: str, 
    answer: str, 
    retrieval_results: list[dict]
) -> tuple[str, str]:
    """
    Xác minh mọi trích dẫn [legal:id] trong câu trả lời có hợp lệ và nằm trong retrieval_results hay không.
    Trả về tuple: (final_answer, grounding_status).
    """
    safe_answer = _build_insufficient_answer(question, {"results": retrieval_results})
    if _answer_admits_no_legal_basis(answer, retrieval_results):
        return safe_answer, "insufficient_evidence"
    validation = validate_legal_references(answer, retrieval_results)
    if validation.status in {"ungrounded", "insufficient_evidence"}:
        logger.warning(
            "Legal grounding rejected answer; status={}, invalid_ids={}, unsupported_count={}",
            validation.status,
            len(validation.invalid_internal_ids),
            len(validation.unsupported_references),
        )
        return safe_answer, validation.status
    enriched, _ = _ensure_answer_has_source_links(answer, retrieval_results)
    return enriched, validation.status



# Domain catalog for ward/commune pilot routing
WARD_DOMAIN_CATALOG: list[dict[str, Any]] = [
    {
        "slug": "ho_tich_chung_thuc",
        "name": "Hộ tịch - chứng thực",
        "agency": "Tư pháp - Hộ tịch",
        "keywords": [
            "khai sinh", "ket hon", "kết hôn", "doc than", "độc thân", "khai tu", "khai tử",
            "hon nhan", "hôn nhân", "ho tich", "hộ tịch", "chung thuc", "chứng thực",
            "giay khai sinh", "giấy khai sinh", "tinh trang hon nhan",
        ],
    },
    {
        "slug": "dat_dai_xay_dung",
        "name": "Đất đai - Xây dựng",
        "agency": "Địa chính - Xây dựng - Đô thị - Môi trường",
        "keywords": [
            "xay dung", "xây dựng", "giay phep xay dung", "giấy phép xây dựng",
            "so do", "sổ đỏ", "sang ten", "sang tên", "dat dai", "đất đai",
            "nha o", "nhà ở", "chuyen nhuong dat", "quy hoach",
        ],
    },
    {
        "slug": "an_sinh_y_te_giao_duc",
        "name": "An sinh - Y tế - Giáo dục",
        "agency": "Lao động - Thương binh và Xã hội",
        "keywords": [
            "tro cap", "trợ cấp", "bao tro", "bảo trợ", "xa hoi", "xã hội",
            "ngheo", "nghèo", "khuyet tat", "khuyết tật", "y te", "giao duc",
        ],
    },
    {
        "slug": "hanh_chinh_cong",
        "name": "Hành chính công",
        "agency": "Tài chính - Kế hoạch / Bộ phận Một cửa",
        "keywords": [
            "ho kinh doanh", "hộ kinh doanh", "dang ky kinh doanh", "đăng ký kinh doanh",
            "thu tuc hanh chinh", "thủ tục hành chính", "mot cua", "một cửa",
        ],
    },
    {
        "slug": "trat_tu_do_thi",
        "name": "Trật tự đô thị",
        "agency": "Địa chính - Xây dựng - Đô thị - Môi trường",
        "keywords": [
            "do xe", "đỗ xe", "via he", "vỉa hè", "trat tu", "trật tự",
            "phat vi pham", "phạt vi phạm", "long duong", "lòng đường",
        ],
    },
    {
        "slug": "cu_tru_an_ninh",
        "name": "Cư trú - An ninh",
        "agency": "Công an cấp xã/phường",
        "keywords": [
            "cu tru", "cư trú", "thuong tru", "thường trú", "tam tru", "tạm trú",
            "luu tru", "lưu trú", "can cuoc", "căn cước", "an ninh",
        ],
    },
    {
        "slug": "khieu_nai_to_cao_xu_phat",
        "name": "Khiếu nại - Tố cáo - Xử phạt",
        "agency": "UBND/cơ quan có thẩm quyền xử lý vụ việc",
        "keywords": [
            "khieu nai", "khiếu nại", "to cao", "tố cáo", "xu phat", "xử phạt",
            "phat hanh chinh", "phạt hành chính", "giai trinh", "giải trình",
        ],
    },
]


def _ascii_fold_simple(value: str) -> str:
    text = unicodedata.normalize("NFD", value or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.casefold()


def _detect_question_domain(question: str) -> dict[str, Any] | None:
    """Detect likely ward domain from question keywords. Returns None if ambiguous."""
    q = _ascii_fold_simple(question)
    if not q.strip():
        return None
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in WARD_DOMAIN_CATALOG:
        score = 0
        for kw in item["keywords"]:
            if _ascii_fold_simple(kw) in q:
                score += 1
        if score:
            scored.append((score, item))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top = scored[0]
    # Ambiguous if second-best is close
    if len(scored) > 1 and scored[1][0] == top_score:
        return None
    if top_score < 1:
        return None
    return {
        "slug": top["slug"],
        "name": top["name"],
        "agency": top["agency"],
        "confidence": top_score,
    }


def _domain_catalog_item(slug: str | None) -> dict[str, Any] | None:
    if not slug:
        return None
    for item in WARD_DOMAIN_CATALOG:
        if item["slug"] == slug:
            return item
    return None


def _domain_mismatch_response(
    question: str,
    role: str,
    selected_domain: str | None,
    detected: dict[str, Any],
) -> AskResponse:
    selected = _domain_catalog_item(selected_domain)
    selected_name = selected["name"] if selected else (selected_domain or "đang chọn")
    selected_agency = selected["agency"] if selected else "cơ quan đang chọn"
    suggested_domain = detected["slug"]
    suggested_agency = detected["agency"]
    suggested_name = detected["name"]

    if role == "officer":
        answer = (
            f"Bạn đang hỏi sai lĩnh vực. Câu hỏi thuộc **{suggested_name}** "
            f"(cơ quan: **{suggested_agency}**), không thuộc **{selected_name}** "
            f"(cơ quan: **{selected_agency}**) đang chọn.\n\n"
            "Vui lòng chọn cơ quan/lĩnh vực phụ trách hợp lý rồi hỏi lại.\n"
            f"Gợi ý: chuyển sang **{suggested_name}**."
        )
        status = "domain_mismatch"
        mismatch = True
    else:
        # citizen/admin: soft guidance, no hard block of content generation path
        answer = (
            f"Câu hỏi của bạn có vẻ thuộc **{suggested_name}** "
            f"(cơ quan: **{suggested_agency}**), trong khi đang chọn **{selected_name}**.\n\n"
            "Gợi ý: hãy chuyển sang lĩnh vực phù hợp để được hướng dẫn đúng hơn."
        )
        status = "domain_mismatch"
        mismatch = True

    return AskResponse(
        question=question,
        answer=answer,
        grounding_status=status,
        domain_mismatch=mismatch,
        selected_domain=selected_domain,
        suggested_domain=suggested_domain,
        suggested_agency=suggested_agency,
        rag_trace={
            "selected_domain": selected_domain,
            "detected_domain": {
                "slug": suggested_domain,
                "name": suggested_name,
                "count": detected.get("confidence", 1),
            },
            "domain_mismatch": True,
            "question_classification": classify_question(question, suggested_domain),
        },
        question_type=classify_question(question, suggested_domain)["question_type"],
        detected_domain=suggested_domain,
        required_sections=classify_question(question, suggested_domain)["required_sections"],
    )


def _should_block_domain_mismatch(role: str, selected_domain: str | None, detected: dict[str, Any] | None) -> bool:
    if not selected_domain or not detected:
        return False
    if selected_domain == detected.get("slug"):
        return False
    # only hard-block officer when mismatch is clear
    return role == "officer"




def _as_reference_procedure(procedure_detail: dict | None) -> dict | None:
    """Ensure procedure seed is always labeled as process reference, not legal proof."""
    if not procedure_detail:
        return None
    proc = dict(procedure_detail)
    proc["is_reference_only"] = True
    proc.setdefault("source", "procedure_seed")
    proc.setdefault("label", "Tham chiếu quy trình")
    proc.setdefault(
        "disclaimer",
        "Đây là tham chiếu quy trình/thủ tục hành chính, không thay cho căn cứ văn bản pháp luật đã truy xuất.",
    )
    return proc



def _procedure_response_fields(procedure_detail: dict | None, question: str | None = None) -> tuple[dict | None, list[dict] | None, str | None]:
    """Return a reference procedure plus only explicitly requested valid forms."""
    proc = _as_reference_procedure(procedure_detail)
    form_intent = bool(question and _question_requests_forms(question))
    recommended: list[dict] | None = None

    if proc is not None:
        proc = dict(proc)
        # Seed metadata may match an intent, but it is not a retrieved and
        # effectivity-checked legal source. Never expose those legal claims as
        # Ask answer data. Verified form files are handled separately below.
        proc["steps"] = []
        proc["documents_required"] = []
        proc["duration"] = None
        proc["fee"] = None
        # Preserve matching metadata for audit/debug, but never expose a seed form.
        for key in (
            "related_procedures", "matched_procedure_ids", "multi_procedure",
            "missing_form_intents", "matched_phrases", "match_score",
        ):
            if procedure_detail and key in procedure_detail:
                proc[key] = procedure_detail.get(key)

        if form_intent:
            raw_forms = list((procedure_detail or {}).get("recommended_forms") or [])
            matched_ids = list((procedure_detail or {}).get("matched_procedure_ids") or [])
            ranked = _rank_forms_for_question(
                question or "", raw_forms, limit=3,
                matched_procedure_ids=matched_ids,
                selected_domain=str(proc.get("domain_slug") or "") or None,
            )
            recommended = ranked or None
            primary_id = str(proc.get("id") or "")
            proc["forms"] = [form for form in ranked if str(form.get("procedure_id") or "") == primary_id][:2]
            proc["recommended_forms"] = list(ranked)
            # An explicit request may match a workflow but still have no approved
            # file. Make that absence explicit without inventing a template.
            proc["forms_unavailable"] = not bool(ranked)
        else:
            proc["forms"] = []
            proc["recommended_forms"] = []

    summary = f"Tham chiếu quy trình: {proc.get('name')}" if proc else None
    if proc and proc.get("multi_procedure") and proc.get("related_procedures"):
        related_names = [str(item.get("name") or item.get("id")) for item in proc["related_procedures"][:3]]
        if related_names:
            summary = f"{summary}; li?n quan: " + "; ".join(related_names)
    return proc, recommended, summary


def _queue_form_discovery_if_needed(question: str | None, recommended_forms: list[dict] | None) -> None:
    """Start best-effort discovery without delaying the legal answer."""
    if not question or not _question_requests_forms(question) or recommended_forms:
        return
    try:
        from api.form_discovery_service import queue_missing_form_discovery

        queue_missing_form_discovery(limit=50)
    except Exception:
        # Missing-form discovery is optional and must never affect answering.
        logger.debug("Unable to queue missing-form discovery", exc_info=True)

def _normalize_procedure_query(question: str) -> str:
    """Lowercase + strip accents for robust Vietnamese phrase matching."""
    import unicodedata

    value = unicodedata.normalize("NFD", question or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d").replace("Đ", "D")
    value = value.casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _question_requests_forms(question: str) -> bool:
    """Return True only when the user explicitly asks to obtain/fill a form.

    A legal question can mention a complaint, declaration or application without
    asking for a template. Such mentions must not surface unrelated downloads.
    Required workflow forms are handled only when a concrete matched procedure
    has an approved official file; the UI still receives no synthetic fallback.
    """
    q = _normalize_procedure_query(question)
    if not q:
        return False

    explicit_phrases = (
        "bieu mau", "to khai", "mau don", "mau to khai", "file mau",
        "file bieu mau", "tai ve", "tai mau", "tai bieu mau", "tai to khai",
        "tai don", "cho toi mau", "cho toi bieu mau", "cho toi to khai",
        "can mau don", "can bieu mau", "can to khai", "xin mau",
        "gui mau", "cung cap mau", "form template", "download form",
    )
    if any(phrase in q for phrase in explicit_phrases):
        return True

    # "??n" alone can be a noun in the facts. Treat it as a request only when
    # coupled with a request/download/fill verb, not merely "khi?u n?i".
    return bool(
        re.search(r"\b(don|mau)\b", q)
        and re.search(r"\b(tai|xin|can|gui|cung cap|dien|viet|nop)\b", q)
    )

def _rank_forms_for_question(
    question: str,
    forms: list[dict],
    limit: int = 3,
    *,
    matched_procedure_ids: list[str] | None = None,
    selected_domain: str | None = None,
    ward_scope: str | None = None,
) -> list[dict]:
    """Rank exact, downloadable official forms for the active question.

    Ranking is deterministic: exact procedure intent, form-type/name overlap,
    selected domain, compatible ward scope, then availability.  A candidate
    without an official approved file is never allowed into this function's
    output.  One result per procedure is preferred before a second form from
    the same procedure, avoiding a noisy list of near-duplicates.
    """
    if not _question_requests_forms(question):
        return []

    q = _normalize_procedure_query(question)
    q_tokens = {token for token in re.findall(r"[a-z0-9]{3,}", q)}
    matched = set(matched_procedure_ids or [proc_id for proc_id, _score, _phrases in _detect_matched_procedure_ids(question)])
    detected_domain = selected_domain or (_detect_question_domain(question) or {}).get("slug")
    max_forms = max(1, min(int(limit or 3), 3))

    intent_map = {
        "dang_ky_khai_sinh": ("khai sinh", "giay khai sinh"),
        "xac_nhan_doc_than": ("tinh trang hon nhan", "doc than", "xac nhan"),
        "dang_ky_ket_hon": ("ket hon",),
        "khieu_nai": ("khieu nai",),
        "cap_giay_phep_xay_dung": ("giay phep xay dung", "xay dung"),
        "sang_ten_so_do": ("sang ten", "so do", "bien dong dat dai"),
        "dang_ky_khai_tu": ("khai tu",),
        "tro_cap_xa_hoi": ("tro cap", "bao tro xa hoi"),
        "dang_ky_ho_kinh_doanh": ("ho kinh doanh",),
    }

    scored: list[tuple[float, str, dict]] = []
    seen: set[str] = set()
    for form in forms:
        if not (
            form.get("official_level") == "official"
            and form.get("review_status") == "approved"
            and form.get("has_official_file") is True
            and str(form.get("download_url") or "").strip()
        ):
            continue
        procedure_id = str(form.get("procedure_id") or "").strip()
        # A form with no bound procedure must not be guessed from a domain.
        if not procedure_id or (matched and procedure_id not in matched):
            continue
        form_id = str(form.get("form_id") or form.get("download_url") or form.get("name") or "").strip()
        if not form_id or form_id in seen:
            continue
        seen.add(form_id)

        name = _normalize_procedure_query(" ".join(str(form.get(key) or "") for key in ("name", "form_title", "display_name", "procedure_name")))
        name_tokens = {token for token in re.findall(r"[a-z0-9]{3,}", name)}
        score = 100.0  # validated, downloadable official file
        if procedure_id in matched:
            score += 80.0
        for phrase in intent_map.get(procedure_id, ()):
            if phrase in q:
                score += 30.0
        score += min(30.0, len(q_tokens & name_tokens) * 8.0)
        form_domain = str(form.get("domain") or form.get("domain_slug") or "").strip()
        if detected_domain and form_domain and form_domain == detected_domain:
            score += 15.0
        form_scope = str(form.get("ward_scope") or "").casefold()
        if ward_scope and form_scope and ward_scope.casefold() in form_scope:
            score += 10.0
        # Stable tie-break by the concrete procedure, then form identity.
        scored.append((score, procedure_id, form))

    scored.sort(key=lambda item: (-item[0], item[1], str(item[2].get("form_id") or item[2].get("name") or "")))
    selected: list[dict] = []
    covered_procedures: set[str] = set()
    # First pass: one exact form per matched procedure.
    for _score, procedure_id, form in scored:
        if procedure_id in covered_procedures:
            continue
        selected.append(form)
        covered_procedures.add(procedure_id)
        if len(selected) >= max_forms:
            return selected
    # Second pass: only if still below cap, retain the strongest remaining forms.
    for _score, _procedure_id, form in scored:
        if form in selected:
            continue
        selected.append(form)
        if len(selected) >= max_forms:
            break
    return selected



def _form_record_matches_specific_procedure(record: dict, procedure_id: str) -> bool:
    """Strictly match catalog/index records to a concrete procedure.

    Records without procedure_id are often broad domain packages. Do not attach
    them to an Ask response unless the title/file text contains procedure/form
    keywords for the specific procedure.
    """
    import unicodedata

    def norm(value: object) -> str:
        text_value = str(value or "").casefold()
        text_value = unicodedata.normalize("NFD", text_value)
        text_value = "".join(ch for ch in text_value if unicodedata.category(ch) != "Mn")
        return text_value.replace("đ", "d").replace("Ä'", "d").replace("Ð", "d")

    blob = norm(" ".join(
        str(record.get(key) or "")
        for key in (
            "form_title",
            "detected_form_name",
            "file_name",
            "title",
            "name",
            "source_package_path",
            "local_path",
            "priority_path",
            "procedure_name",
        )
    ))
    if not blob:
        return False

    keyword_map: dict[str, list[str]] = {
        "dang_ky_khai_sinh": ["khai sinh", "dang ky khai sinh", "to khai dang ky khai sinh"],
        "xac_nhan_doc_than": ["xac nhan tinh trang hon nhan", "tinh trang hon nhan", "doc than"],
        "dang_ky_ket_hon": ["dang ky ket hon", "ket hon"],
        "dang_ky_khai_tu": ["dang ky khai tu", "khai tu"],
        "sang_ten_so_do": ["dang ky bien dong", "bien dong dat dai", "so do", "sang ten", "dat dai"],
        "cap_giay_phep_xay_dung": ["cap giay phep xay dung", "giay phep xay dung", "xay dung"],
        "khieu_nai": ["don khieu nai", "khieu nai"],
        "tro_cap_xa_hoi": ["tro cap", "bao tro xa hoi", "xa hoi"],
        "dang_ky_ho_kinh_doanh": ["ho kinh doanh", "dang ky ho kinh doanh", "kinh doanh"],
    }
    phrases = keyword_map.get(procedure_id, [])
    return bool(phrases and any(phrase in blob for phrase in phrases))

def _collect_official_forms_for_procedure(
    procedure_id: str,
    procedure_meta: dict | None = None,
) -> list[dict]:
    """Return only approved official forms with real downloadable files.

    Never invent seed/synthetic download URLs.
    """
    from pathlib import Path as _Path
    from api.routers.ward_procedures import (
        OFFICIAL_FORMS_CATALOG_PATH,
        OFFICIAL_FORMS_INDEX_PATH,
        _get_approved_form_ids,
        _load_forms_json,
        _resolve_real_form_file,
        _validate_form_file_integrity,
        normalize_form_display_name,
        normalize_form_record,
        HAI_PHONG_PROCEDURES_SEED,
    )

    forms: list[dict] = []
    seen: set[str] = set()

    # 1) Admin-approved official catalog/index forms for this procedure_id.
    try:
        approved_ids = _get_approved_form_ids()
        catalog = _load_forms_json(OFFICIAL_FORMS_CATALOG_PATH, {"forms": []})
        index = _load_forms_json(OFFICIAL_FORMS_INDEX_PATH, {"forms": []})
        by_id: dict[str, dict] = {}
        for record in (catalog.get("forms") or []) + (index.get("forms") or []):
            rid = str(record.get("id") or "")
            if not rid or rid not in approved_ids:
                continue
            if record.get("review_status") not in (None, "approved") and record.get("is_approved") is not True:
                continue
            # Require an actual path under priority/official candidates.
            path = str(
                record.get("source_package_path")
                or record.get("local_path")
                or record.get("priority_path")
                or ""
            ).replace("\\", "/")
            if not path:
                continue
            resolved = (_Path(__file__).resolve().parents[2] / path).resolve()
            if not resolved.is_file():
                continue
            file_ok, _file_reason = _validate_form_file_integrity(resolved)
            if not file_ok:
                continue
            proc_id = str(record.get("procedure_id") or "")
            if proc_id and proc_id != procedure_id:
                continue
            # Never attach broad domain/procedure packages by metadata alone.
            # Even when procedure_id is present, require title/file metadata to
            # mention the concrete form/procedure (e.g. "tờ khai đăng ký khai sinh").
            if not _form_record_matches_specific_procedure(record, procedure_id):
                continue
            by_id[rid] = record
        for rid, record in by_id.items():
            record = normalize_form_record(record)
            title = normalize_form_display_name(record, fallback=f"Biểu mẫu {rid}")
            path = str(
                record.get("source_package_path")
                or record.get("local_path")
                or record.get("priority_path")
                or ""
            )
            ext = _Path(path).suffix.lstrip(".") if path else "pdf"
            key = f"official:{rid}"
            url_key = f"url:/api/procedures/forms-catalog/official/{rid}/download"
            path_key = f"path:{str(path).replace(chr(92), '/')}" if path else ""
            if key in seen or url_key in seen or (path_key and path_key in seen):
                continue
            seen.add(key)
            seen.add(url_key)
            if path_key:
                seen.add(path_key)
            forms.append(
                {
                    "name": title,
                    "display_name": title,
                    "form_title": title,
                    "file_type": ext or "pdf",
                    "download_url": f"/api/procedures/forms-catalog/official/{rid}/download",
                    "official_level": "official",
                    "review_status": "approved",
                    "has_official_file": True,
                    "needs_official_file": False,
                    "form_id": rid,
                    "procedure_id": procedure_id,
                    "procedure_name": (procedure_meta or {}).get("name"),
                }
            )
    except Exception as form_exc:
        logger.warning(f"Failed to collect approved official forms for {procedure_id}: {form_exc}")

    # 2) Real uploaded files bound to seed form slots only if physically present.
    seed = procedure_meta or next(
        (item for item in HAI_PHONG_PROCEDURES_SEED if item.get("id") == procedure_id),
        None,
    )
    if seed:
        # Prefer catalog/index official forms. Only add seed-backed real files
        # that are not already represented by an official download_url/form_id.
        existing_urls = {
            str(item.get("download_url") or "")
            for item in forms
            if item.get("download_url")
        }
        for idx, f in enumerate(seed.get("forms") or []):
            real_form_path = _resolve_real_form_file(procedure_id, idx, f if isinstance(f, dict) else None)
            if not real_form_path:
                continue
            download_url = (f or {}).get("download_url") or f"/api/procedures/{procedure_id}/forms/{idx}"
            key = f"seed:{procedure_id}:{idx}:{real_form_path}"
            url_key = f"url:{download_url}"
            path_key = f"path:{str(real_form_path).replace(chr(92), '/')}"
            if key in seen or url_key in seen or path_key in seen or download_url in existing_urls:
                continue
            seen.add(key)
            seen.add(url_key)
            seen.add(path_key)
            existing_urls.add(download_url)
            forms.append(
                {
                    "name": normalize_form_display_name(f if isinstance(f, dict) else None, fallback=f"Biểu mẫu {procedure_id}"),
                    "display_name": normalize_form_display_name(f if isinstance(f, dict) else None, fallback=f"Biểu mẫu {procedure_id}"),
                    "form_title": normalize_form_display_name(f if isinstance(f, dict) else None, fallback=f"Biểu mẫu {procedure_id}"),
                    "file_type": (f or {}).get("file_type") or "docx",
                    "download_url": download_url,
                    "official_level": "official",
                    "review_status": "approved",
                    "has_official_file": True,
                    "needs_official_file": False,
                    "form_id": None,
                    "procedure_id": procedure_id,
                    "procedure_name": seed.get("name"),
                }
            )

    # Strict: only downloadable official/approved forms.
    return [
        item
        for item in forms
        if item.get("has_official_file")
        and item.get("download_url")
        and item.get("review_status") == "approved"
        and item.get("official_level") == "official"
    ]


def _detect_matched_procedure_ids(question: str) -> list[tuple[str, int, list[str]]]:
    """Return ranked multi-procedure matches: (procedure_id, score, matched_phrases).

    Supports complex multi-intent questions without merging distinct procedures.
    """
    q = _normalize_procedure_query(question)
    q_raw = (question or "").casefold()

    # phrase detectors: each procedure accumulates independent score
    phrase_rules: dict[str, list[tuple[list[str], int]]] = {
        "dang_ky_khai_sinh": [
            (["sinh o nuoc ngoai", "sinh o nuoc ngoai", "sinh tại nước ngoài", "sinh tai nuoc ngoai"], 50),
            (["giay khai sinh nuoc ngoai", "giấy khai sinh nước ngoài", "khai sinh nuoc ngoai"], 45),
            (["dang ky khai sinh tai viet nam", "đăng ký khai sinh tại việt nam", "khai sinh ve viet nam", "khai sinh về việt nam"], 40),
            (["dang ky khai sinh", "đăng ký khai sinh", "lam khai sinh", "làm khai sinh", "khai sinh"], 25),
            (["giay khai sinh", "giấy khai sinh"], 20),
        ],
        "xac_nhan_doc_than": [
            (["xac nhan tinh trang hon nhan", "xác nhận tình trạng hôn nhân", "tinh trang hon nhan", "tình trạng hôn nhân"], 50),
            (["giay xac nhan doc than", "giấy xác nhận độc thân", "xac nhan doc than", "xác nhận độc thân", "doc than", "độc thân"], 45),
            (["giay xac nhan tinh trang hon nhan", "giấy xác nhận tình trạng hôn nhân"], 50),
        ],
        "dang_ky_ket_hon": [
            (["dang ky ket hon", "đăng ký kết hôn", "ket hon", "kết hôn", "lay vo", "lay chong"], 20),
        ],
        "sang_ten_so_do": [
            (["sang ten so do", "sang ten so do", "chuyen nhuong dat", "so do", "dat dai"], 20),
        ],
        "cap_giay_phep_xay_dung": [
            (["cap phep xay dung", "giay phep xay dung", "xay dung khong phep", "xay dung"], 20),
        ],
        "dang_ky_khai_tu": [
            (["khai tu", "chung tu", "khai tử"], 20),
        ],
        "tro_cap_xa_hoi": [
            (["tro cap", "bao tro xa hoi", "tro cap xa hoi"], 15),
        ],
        "dang_ky_ho_kinh_doanh": [
            (["ho kinh doanh", "kinh doanh ca the"], 15),
        ],
        "khieu_nai": [
            (["don khieu nai", "??n khi?u n?i", "khieu nai quyet dinh", "khi?u n?i quy?t ??nh"], 45),
            (["khieu nai", "khi?u n?i"], 25),
        ],
        # Detect passport intent but it is NOT a seeded procedure with forms.
        # Used only for related_intents metadata; never invent forms.
        "cap_ho_chieu_tre_em": [
            (["ho chieu cho con", "hộ chiếu cho con", "ho chieu tre em", "hộ chiếu trẻ em", "cap ho chieu cho con", "lam ho chieu cho con"], 40),
            (["ho chieu", "hộ chiếu"], 10),
        ],
    }

    scored: list[tuple[str, int, list[str]]] = []
    for proc_id, rules in phrase_rules.items():
        score = 0
        matched: list[str] = []
        for phrases, weight in rules:
            for phrase in phrases:
                pn = _normalize_procedure_query(phrase)
                if pn and (pn in q or phrase.casefold() in q_raw):
                    score += weight
                    matched.append(phrase)
                    break
        if score > 0:
            scored.append((proc_id, score, matched))

    # Prefer more specific birth-abroad / marriage phrases over generic ones.
    scored.sort(key=lambda item: (item[1], 1 if item[0] != "cap_ho_chieu_tre_em" else 0), reverse=True)
    return scored


def _build_procedure_payload(procedure_id: str, matched_phrases: list[str] | None = None) -> dict | None:
    from api.routers.ward_procedures import HAI_PHONG_PROCEDURES_SEED

    # Virtual intents without seed catalog: expose metadata only, never fake forms.
    if procedure_id == "cap_ho_chieu_tre_em":
        official_forms = _collect_official_forms_for_procedure(procedure_id, None)
        return {
            "id": procedure_id,
            "name": "Cấp hộ chiếu cho con/trẻ em",
            "department": "Xuất nhập cảnh / Cơ quan có thẩm quyền",
            "domain_slug": "cu_tru_an_ninh",
            "steps": [],
            "documents_required": [],
            "duration": None,
            "fee": None,
            "forms": official_forms,  # empty unless admin approved a real form
            "matched_phrases": matched_phrases or [],
            "is_reference_only": True,
            "source": "intent_detect",
            "label": "Tham chiếu quy trình",
            "has_official_forms": bool(official_forms),
            "disclaimer": (
                "Đã nhận diện nhu cầu liên quan hộ chiếu trẻ em, nhưng chỉ hiển thị biểu mẫu "
                "khi có file official đã duyệt trong kho."
            ),
        }

    seed = next((item for item in HAI_PHONG_PROCEDURES_SEED if item.get("id") == procedure_id), None)
    if not seed:
        return None
    official_forms = _collect_official_forms_for_procedure(procedure_id, seed)
    return {
        "id": seed["id"],
        "name": seed["name"],
        "department": seed["department"],
        "domain_slug": seed["domain_slug"],
        "steps": seed.get("steps") or [],
        "documents_required": seed.get("documents_required") or [],
        "duration": seed.get("duration"),
        "fee": seed.get("fee"),
        "forms": official_forms,
        "matched_phrases": matched_phrases or [],
        "is_reference_only": True,
        "source": "procedure_seed",
        "label": "Tham chiếu quy trình",
        "has_official_forms": bool(official_forms),
        "disclaimer": (
            "Đây là tham chiếu quy trình/thủ tục hành chính từ danh mục seed. "
            "Không phải căn cứ văn bản pháp luật đã truy xuất."
        ),
    }



def _match_faqs_for_question(
    question: str,
    domain: str | None = None,
    ward_scope: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """Match approved FAQs for Ask UI from local seed/store. Fail soft if missing."""
    import json
    from api.data_paths import notebook_data_dir

    data_root = notebook_data_dir()
    store_path = data_root / "faq_store.json"
    seed_path = data_root / "faq" / "faq_seed_haiphong_lechan.json"

    faqs: list[dict] = []
    try:
        if store_path.exists():
            data = json.loads(store_path.read_text(encoding="utf-8"))
            faqs = list(data.get("faqs") or [])
        elif seed_path.exists():
            faqs = list(json.loads(seed_path.read_text(encoding="utf-8")))
    except Exception:
        return []

    faqs = [f for f in faqs if (f.get("review_status") or "approved") == "approved"]
    if domain:
        faqs = [f for f in faqs if f.get("domain") == domain]
    if ward_scope:
        faqs = [
            f
            for f in faqs
            if not f.get("ward_scope")
            or ward_scope.lower() in str(f.get("ward_scope") or "").lower()
        ]

    q = (question or "").lower()
    tokens = [t for t in q.replace("?", " ").split() if len(t) > 2]
    phrases = [
        "khai sinh",
        "sang tên",
        "sang ten",
        "sổ đỏ",
        "so do",
        "đỗ xe",
        "do xe",
        "kết hôn",
        "ket hon",
        "xây dựng",
        "xay dung",
        "hộ kinh doanh",
        "ho kinh doanh",
        "khiếu nại",
        "khieu nai",
        "chứng tử",
        "chung tu",
        "tạm trú",
        "tam tru",
        "thừa kế",
        "thua ke",
    ]
    scored: list[tuple[int, dict]] = []
    for f in faqs:
        blob = f"{f.get('question','')} {f.get('answer','')}".lower()
        score = sum(1 for t in tokens if t in blob)
        for phrase in phrases:
            if phrase in q and phrase in blob:
                score += 3
        if score > 0:
            scored.append((score, f))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in scored[:limit]]


def _match_procedure_detail(question: str) -> dict | None:
    """Match one primary procedure + multi-procedure recommended forms.

    Rules:
    - Detect multiple independent procedures/intents from complex questions.
    - Primary procedure_detail is the highest-scoring seeded procedure.
    - recommended_forms aggregates official/approved forms across matched procedures.
    - Never merge two procedures into one fake combined procedure.
    - Never return seed/synthetic download_url without a real official file.
    - Passport intent may be detected, but no form is shown unless official file exists.
    """
    ranked = _detect_matched_procedure_ids(question)
    if not ranked:
        return None

    built: list[dict] = []
    for proc_id, score, phrases in ranked:
        payload = _build_procedure_payload(proc_id, phrases)
        if not payload:
            continue
        payload["match_score"] = score
        built.append(payload)

    if not built:
        return None

    # Primary = highest score among seeded procedures; fall back to first built.
    seeded = [item for item in built if item.get("source") != "intent_detect" or item.get("id") != "cap_ho_chieu_tre_em"]
    # Prefer real seed procedures as primary over virtual passport intent.
    primary_candidates = [item for item in built if item.get("id") != "cap_ho_chieu_tre_em"]
    primary = primary_candidates[0] if primary_candidates else built[0]

    # Aggregate recommended forms from ALL matched procedures (dedupe by form_id/download_url).
    recommended_forms: list[dict] = []
    seen_form_keys: set[str] = set()
    for item in built:
        for form in item.get("forms") or []:
            if not (
                form.get("has_official_file")
                and form.get("download_url")
                and form.get("review_status") == "approved"
                and form.get("official_level") == "official"
            ):
                continue
            fid = str(form.get("form_id") or "").strip()
            url = str(form.get("download_url") or "").strip()
            name = str(form.get("name") or "").strip().casefold()
            keys = [k for k in (f"id:{fid}" if fid else "", f"url:{url}" if url else "", f"name:{name}" if name else "") if k]
            if any(k in seen_form_keys for k in keys):
                continue
            for k in keys:
                seen_form_keys.add(k)
            recommended_forms.append(form)

    related_procedures = []
    for item in built:
        if item.get("id") == primary.get("id"):
            continue
        related_procedures.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "department": item.get("department"),
                "match_score": item.get("match_score"),
                "matched_phrases": item.get("matched_phrases") or [],
                "has_official_forms": bool(item.get("forms")),
                "forms_count": len(item.get("forms") or []),
            }
        )

    # Primary forms: only official forms for the primary procedure.
    primary_forms = [
        form
        for form in (primary.get("forms") or [])
        if form.get("has_official_file")
        and form.get("download_url")
        and form.get("review_status") == "approved"
    ]

    result = dict(primary)
    # Form discovery is separate from recommendation. It must not cause forms
    # to appear for an ordinary legal question; response assembly gates it by
    # explicit form intent and then ranks the exact matches.
    result["forms"] = primary_forms
    result["recommended_forms"] = recommended_forms
    result["related_procedures"] = related_procedures
    result["matched_procedure_ids"] = [item.get("id") for item in built]
    result["multi_procedure"] = len(built) > 1
    result["is_reference_only"] = True
    result.setdefault("source", "procedure_seed")
    result.setdefault("label", "Tham chiếu quy trình")
    # Helpful note when passport intent detected without forms.
    passport = next((item for item in built if item.get("id") == "cap_ho_chieu_tre_em"), None)
    if passport and not (passport.get("forms") or []):
        result["missing_form_intents"] = [
            {
                "id": "cap_ho_chieu_tre_em",
                "name": passport.get("name"),
                "reason": "Chưa có biểu mẫu official đã duyệt cho hộ chiếu trẻ em trong kho.",
            }
        ]
    return result


def _extract_recommended_forms(procedure_detail: dict | None, question: str | None = None) -> list[dict] | None:
    """Compatibility helper: return only explicit, ranked official form requests."""
    if not procedure_detail or not question or not _question_requests_forms(question):
        return None
    return _rank_forms_for_question(
        question,
        list(procedure_detail.get("recommended_forms") or procedure_detail.get("forms") or []),
        limit=3,
        matched_procedure_ids=list(procedure_detail.get("matched_procedure_ids") or []),
        selected_domain=str(procedure_detail.get("domain_slug") or "") or None,
    ) or None
async def _ask_local(ask_request: AskRequest, user_id: str | None = None, request=None) -> AskResponse:
    try:
        detected = _detect_question_domain(ask_request.question)
        question_policy = classify_question(
            ask_request.question,
            detected_domain=(detected or {}).get("slug") if detected else None,
        )
        original_selected_domain = ask_request.domain
        soft_mismatch = bool(
            ask_request.role in ("citizen", "admin")
            and original_selected_domain
            and detected
            and original_selected_domain != detected.get("slug")
        )
        if _should_block_domain_mismatch(ask_request.role, ask_request.domain, detected):
            return _domain_mismatch_response(
                ask_request.question,
                ask_request.role,
                ask_request.domain,
                detected or {},
            )
        # Citizen/admin soft-route: retrieve by detected domain for better answer quality
        if soft_mismatch and detected:
            ask_request.domain = detected.get("slug")

        retrieval = await _call_legal_retrieval(ask_request)
        procedure_match = _match_procedure_detail(ask_request.question)
        procedure_detail, recommended_forms, procedure_summary = _procedure_response_fields(procedure_match, ask_request.question)
        _queue_form_discovery_if_needed(ask_request.question, recommended_forms)
        matched_faqs = _match_faqs_for_question(
            ask_request.question,
            domain=getattr(ask_request, "domain", None),
            ward_scope=getattr(ask_request, "ward_scope", None) or "Le Chan",
        )
        if matched_faqs and procedure_detail:
            procedure_detail = {
                "id": procedure_detail.get("id"),
                "name": procedure_detail.get("name"),
                "department": procedure_detail.get("department"),
                "reference_only": True,
                "summary": procedure_summary or procedure_detail.get("name"),
            }
        matched_faqs = _match_faqs_for_question(
            ask_request.question,
            domain=getattr(ask_request, "domain", None),
            ward_scope=getattr(ask_request, "ward_scope", None) or "Le Chan",
        )
        # Prefer FAQ UX: keep procedure only as short reference when FAQ exists.
        if matched_faqs and procedure_detail:
            procedure_detail = {
                "id": procedure_detail.get("id"),
                "name": procedure_detail.get("name"),
                "department": procedure_detail.get("department"),
                "reference_only": True,
                "summary": procedure_summary or procedure_detail.get("name"),
            }
        if not _has_sufficient_legal_evidence(ask_request.question, retrieval):
            resp = _insufficient_legal_evidence_response(
                ask_request.question,
                retrieval,
                procedure_detail,
            )
            resp.selected_domain = original_selected_domain
            resp.suggested_domain = (detected or {}).get("slug") if detected else None
            resp.suggested_agency = (detected or {}).get("agency") if detected else None
            resp.domain_mismatch = soft_mismatch
            resp.detected_domain = (detected or {}).get("slug") if detected else None
            resp.question_type = question_policy["question_type"]
            resp.required_sections = question_policy.get("required_sections") or []
            resp.forms_unavailable = bool(question_policy.get("requests_form") and not recommended_forms)
            return resp

        history = _get_ask_session_history(_resolve_ask_session_owner_key(request, user_id=user_id, role=getattr(ask_request, "role", None)), ask_request.session_id)
        prompt = _build_local_prompt(
            ask_request.question,
            ask_request.role,
            retrieval,
            history,
            question_policy,
        )
        answer = await _call_ollama(ask_request.offline_model, prompt)
        if not answer:
            raise HTTPException(status_code=502, detail="Ollama không trả về nội dung.")
        answer = _sanitize_unsupported_absence_claims(answer)

        retrieval_results = _extract_retrieval_results(retrieval)
        # If the model itself admits no matching regulation, prefer a short insufficient answer.
        if _answer_admits_no_legal_basis(answer, retrieval_results):
            resp = _insufficient_legal_evidence_response(
                ask_request.question,
                retrieval,
                procedure_detail,
            )
            resp.selected_domain = original_selected_domain
            resp.suggested_domain = (detected or {}).get("slug") if detected else None
            resp.suggested_agency = (detected or {}).get("agency") if detected else None
            resp.domain_mismatch = soft_mismatch
            resp.detected_domain = (detected or {}).get("slug") if detected else None
            resp.question_type = question_policy["question_type"]
            resp.required_sections = question_policy.get("required_sections") or []
            resp.forms_unavailable = bool(question_policy.get("requests_form") and not recommended_forms)
            return resp

        # Gọi verify_and_ground_answer
        final_answer, grounding_status = _verify_and_ground_answer(
            ask_request.question, answer, retrieval_results
        )

        if grounding_status in {"ungrounded", "insufficient_evidence"}:
            answer_out = final_answer
            citations = []
            removed_claims = []
        else:
            answer_out, citations = _ensure_answer_has_source_links(final_answer, retrieval)
            answer_out, removed_claims = _guard_sensitive_claims(answer_out, retrieval)
        answer_out = _sanitize_answer_citation_display(answer_out, retrieval)
        source_gap = missing_answer_sections(
            answer_out,
            question_policy["question_type"],
            bool(question_policy.get("is_procedural")),
            required_sections=question_policy.get("required_sections") or [],
        )
        if source_gap:
            answer_out += "\n\nThông tin chưa xác minh được từ nguồn hiện có: " + ", ".join(vietnamese_section_names(source_gap)) + "."
        rag_trace = _merge_claim_guard_into_trace(retrieval.get("trace") if isinstance(retrieval, dict) else None, removed_claims)
        if isinstance(rag_trace, dict):
            rag_trace["question_classification"] = question_policy
        legal_as_of = effective_legal_date(
            legal_as_of=ask_request.legal_as_of,
            event_date=ask_request.event_date,
        )
        evidence_coverage = build_evidence_coverage(
            retrieval_results,
            required_sections=question_policy.get("required_sections") or [],
            recommended_forms=recommended_forms,
        )
        claim_validation = build_claim_validation(
            removed_claims=removed_claims,
            citations=citations,
            legal_as_of=legal_as_of,
            answer=answer_out,
            coverage=evidence_coverage,
        )
        answer_out = apply_claim_validation(answer_out, claim_validation)
        answer_score_preview, quality_flags = quality_preview(
            coverage=evidence_coverage,
            grounding_status=grounding_status,
            claim_validation=claim_validation,
        )
        clarifying_questions = clarifying_questions_for_gaps(
            ask_request.question,
            evidence_coverage,
        )
        if isinstance(rag_trace, dict):
            rag_trace["evidence_coverage"] = evidence_coverage
            rag_trace["claim_validation"] = claim_validation
            rag_trace["legal_as_of"] = legal_as_of.isoformat()
        if soft_mismatch and detected:
            tip = (
                f"\n\n---\nGợi ý: câu hỏi có vẻ thuộc **{detected.get('name')}** "
                f"(cơ quan: **{detected.get('agency')}**). "
                "Bạn nên chuyển sang lĩnh vực này để được hướng dẫn đúng hơn."
            )
            if tip.strip() not in answer_out:
                answer_out = answer_out + tip

        if soft_mismatch and detected:
            tip = (
                f"\n\n---\nGợi ý: câu hỏi có vẻ thuộc **{detected.get('name')}** "
                f"(cơ quan: **{detected.get('agency')}**). "
                "Bạn nên chuyển sang lĩnh vực này để được hướng dẫn đúng hơn."
            )
            if tip.strip() not in answer_out:
                answer_out = answer_out + tip
        return AskResponse(
            answer=answer_out,
            question=ask_request.question,
            rag_trace=(
                rag_trace
                if ask_request.role == "admin" and ask_request.show_rag_trace
                else None
            ),
            grounding_status=grounding_status,
            procedure_detail=procedure_detail,
            recommended_forms=recommended_forms,
            faqs=matched_faqs if 'matched_faqs' in locals() else None,
            procedure_summary=procedure_summary,
            citations=citations,
            domain_mismatch=soft_mismatch,
            selected_domain=original_selected_domain,
            suggested_domain=(detected or {}).get("slug") if detected else None,
            suggested_agency=(detected or {}).get("agency") if detected else None,
            question_type=question_policy["question_type"],
            detected_domain=question_policy.get("detected_domain"),
            required_sections=question_policy.get("required_sections") or [],
            forms_unavailable=bool(question_policy.get("requests_form") and not recommended_forms),
            source_gap=source_gap,
            evidence_coverage=evidence_coverage,
            claim_validation=claim_validation,
            authority_status=evidence_coverage.get("authority", {}).get("status", "not_applicable"),
            legal_as_of=legal_as_of,
            clarifying_questions=clarifying_questions,
            quality_flags=quality_flags,
            answer_score_preview=answer_score_preview,
        )
    except httpx.ConnectError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Chưa kết nối được Ollama hoặc legal retrieval local. "
                "Hãy bật Ollama và legal_search_server trước khi hỏi offline."
            ),
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=exc.response.text,
        ) from exc


def _normalize_legal_search_item(item: dict, index: int = 0) -> dict[str, Any]:
    """Map legal retrieval hit into a Search tab result with clickable source links."""
    chunk_id = item.get("chunk_id") or item.get("id") or index
    law_number = str(item.get("law_number") or "").strip()
    document_title = str(item.get("document_title") or "").strip()
    article_number = str(item.get("article_number") or "").strip()
    article_title = str(item.get("article_title") or "").strip()
    content = str(item.get("content") or "").strip()
    source_url = str(item.get("source_url") or "").strip()
    domain_name = str(item.get("domain_name") or item.get("domain") or "").strip()
    domain_slug = str(item.get("domain_slug") or "").strip()
    score = float(
        item.get("score")
        or item.get("rerank_score")
        or item.get("vector_score")
        or 0.0
    )

    title_parts = [p for p in [law_number, document_title] if p]
    title = " - ".join(title_parts) if title_parts else (article_title or f"Văn bản pháp lý {chunk_id}")
    if article_number:
        title = f"{title} (Điều {article_number})"

    snippet = content
    if len(snippet) > 360:
        snippet = snippet[:357].rstrip() + "..."
    matches = [snippet] if snippet else []
    if article_title and article_title not in matches:
        matches.insert(0, article_title)

    return {
        "id": f"legal:{chunk_id}",
        "parent_id": f"legal:{chunk_id}",
        "type": "legal",
        "source_type": "legal",
        "title": title,
        "law_number": law_number,
        "document_title": document_title,
        "article_number": article_number,
        "article_title": article_title,
        "snippet": snippet,
        "content": content,
        "source_url": source_url,
        "domain": domain_name or domain_slug,
        "domain_slug": domain_slug,
        "domain_name": domain_name,
        "score": score,
        "relevance": score,
        "final_score": score,
        "matches": matches,
        "chunk_id": str(chunk_id),
        "document_id": item.get("document_id"),
        "scope": item.get("scope"),
        "document_status": item.get("document_status"),
        "created": str(item.get("effective_date") or item.get("issued_date") or ""),
        "updated": str(item.get("expired_date") or ""),
    }


def _normalize_notebook_search_item(item: dict) -> dict[str, Any]:
    """Keep notebook/source/note results secondary and clearly labeled."""
    result = dict(item or {})
    parent_id = str(result.get("parent_id") or result.get("id") or "")
    result.setdefault("parent_id", parent_id or "notebook:unknown")
    result.setdefault("id", result.get("id") or parent_id or "notebook:unknown")
    result.setdefault("type", "notebook")
    result.setdefault("source_type", "notebook")
    if not result.get("title"):
        result["title"] = result.get("name") or parent_id or "Kết quả notebook"
    score = float(
        result.get("final_score")
        or result.get("relevance")
        or result.get("similarity")
        or result.get("score")
        or 0.0
    )
    result["score"] = score
    result["final_score"] = score
    result.setdefault("matches", result.get("matches") or [])
    result.setdefault("source_url", "")
    result.setdefault("law_number", "")
    result.setdefault("article_number", "")
    result.setdefault("snippet", (result.get("matches") or [""])[0] if result.get("matches") else "")
    result.setdefault("domain", "")
    return result


def _filter_results_by_allowed_domains(
    results: list[dict[str, Any]],
    allowed_domains: list[str],
) -> list[dict[str, Any]]:
    """Keep unknown-domain rows, but drop rows that clearly belong elsewhere."""
    if not allowed_domains:
        return results

    filtered: list[dict[str, Any]] = []
    for res in results:
        metadata = res.get("metadata") or {}
        res_domain = str(
            res.get("domain_slug")
            or res.get("domain")
            or metadata.get("domain_slug")
            or metadata.get("domain")
            or ""
        ).strip()
        if res_domain and not _is_domain_allowed(res_domain, allowed_domains):
            continue
        filtered.append(res)
    return filtered


async def _search_legal_documents(query: str, limit: int = 12, domain: str | None = None) -> list[dict[str, Any]]:
    """Primary legal search against the dedicated legal retrieval service."""
    selected_domain = (domain or "").strip() or None
    # Search the complete reviewed corpus. Do not narrow by Hai Phong/ward
    # scope; those values are metadata and ranking context only.
    scope_filter = None

    async def _search(domain_value: str | None) -> dict:
        payload = {
            "query": query,
            "limit": max(1, min(int(limit or 12), 30)),
            "candidate_count": max(80, min(int(limit or 12) * 20, 220)),
            "domain": domain_value,
            "include_trace": False,
            "scope_filter": scope_filter,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(f"{LEGAL_SEARCH_URL}/search", json=payload)
        response.raise_for_status()
        return response.json()

    try:
        data = await _search(selected_domain)
        raw = list(data.get("results") or [])
        # Domain metadata is incomplete for some corpora; avoid false-empty legal search.
        if selected_domain and not raw:
            data = await _search(None)
            raw = list(data.get("results") or [])
    except Exception as exc:
        logger.warning("Legal search unavailable for /api/search: {}", exc)
        return []

    return [_normalize_legal_search_item(item, idx) for idx, item in enumerate(raw)]


@router.post("/search", response_model=SearchResponse)
async def search_knowledge_base(search_request: SearchRequest, request: Request):
    """Search legal corpus first; notebook sources/notes are secondary/optional."""
    user_role = get_request_role(request)
    user_id = get_request_user_id(request)
    try:
        query = (search_request.query or "").strip()
        if len(query) < 2:
            return SearchResponse(
                results=[],
                total_count=0,
                search_type="legal",
            )

        # 1) Legal-first retrieval
        legal_results = await _search_legal_documents(
            query=query,
            limit=min(search_request.limit or 12, 30),
            domain=None,
        )

        # Officer domain filter only drops clear mismatches; unknown domain rows stay.
        if user_role == "officer" and user_id:
            from api.user_service import get_user_profile
            profile = await get_user_profile(user_id)
            if profile:
                allowed_domains = profile.get("allowed_domains") or []
                if allowed_domains:
                    legal_results = _filter_results_by_allowed_domains(
                        legal_results,
                        allowed_domains,
                    )

        # 2) Notebook sources/notes remain secondary/optional
        notebook_results: list[dict[str, Any]] = []
        want_notebook = bool(search_request.search_sources or search_request.search_notes)
        if want_notebook:
            try:
                if search_request.type == "vector":
                    if await model_manager.get_embedding_model():
                        raw_notebook = await vector_search(
                            keyword=query,
                            results=min(search_request.limit or 20, 30),
                            source=search_request.search_sources,
                            note=search_request.search_notes,
                            minimum_score=search_request.minimum_score,
                        )
                    else:
                        raw_notebook = []
                else:
                    raw_notebook = await text_search(
                        keyword=query,
                        results=min(search_request.limit or 20, 30),
                        source=search_request.search_sources,
                        note=search_request.search_notes,
                    )
                notebook_results = [
                    _normalize_notebook_search_item(item)
                    for item in (raw_notebook or [])
                ]
                if user_role == "officer" and user_id:
                    from api.user_service import get_user_profile
                    profile = await get_user_profile(user_id)
                    if profile:
                        allowed_domains = profile.get("allowed_domains") or []
                        notebook_results = _filter_results_by_allowed_domains(
                            notebook_results,
                            allowed_domains,
                        )
            except Exception as exc:
                logger.warning("Secondary notebook search failed: {}", exc)
                notebook_results = []

        # Legal docs first, notebook second. Empty only when legal is empty and notebook empty.
        results = list(legal_results) + list(notebook_results)
        search_type = "legal"
        if legal_results and notebook_results:
            search_type = f"legal+{search_request.type}"
        elif notebook_results and not legal_results:
            search_type = search_request.type
        elif not legal_results and not notebook_results:
            search_type = "legal"

        return SearchResponse(
            results=results,
            total_count=len(results),
            search_type=search_type,
        )

    except HTTPException:
        raise
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DatabaseOperationError as e:
        logger.error(f"Database error during search: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error during search: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


async def stream_ask_response(
    question: str,
    role: str,
    strategy_model: Model,
    answer_model: Model,
    final_answer_model: Model,
    domain: str | None = None,
    legal_as_of: str | None = None,
    *,
    retrieval_question: str | None = None,
    request_id: str | None = None,
    issue_id: str | None = None,
    issue_domain: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream Ask events and record only sanitized stream-finalize timing."""
    started = time.perf_counter()
    outcome = "success"
    final_answer = None
    try:
        import asyncio
        queue = asyncio.Queue()

        async def _run_graph():
            try:
                graph_input = dict(
                    question=question,
                    role=role,
                    domain=domain,
                    legal_as_of=legal_as_of,
                )
                # Keep conversation history in the generation prompt while
                # always binding retrieval to the current question.  The
                # section flag controls provenance/eligibility only; when it
                # is off, the retrieval query must not fall back to history.
                if retrieval_question:
                    graph_input["retrieval_question"] = retrieval_question
                    if request_id and issue_id:
                        graph_input.update(
                            request_id=request_id,
                            issue_id=issue_id,
                            issue_domain=issue_domain,
                        )
                async for chunk in ask_graph.astream(
                    input=graph_input,  # type: ignore[arg-type]
                    config=dict(
                        configurable=dict(
                            strategy_model=strategy_model.id,
                            answer_model=answer_model.id,
                            final_answer_model=final_answer_model.id,
                        )
                    ),
                    stream_mode="updates",
                ):
                    await queue.put(("chunk", chunk))
                await queue.put(("done", None))
            except Exception as e:
                await queue.put(("error", e))

        async def _run_heartbeats():
            while True:
                await asyncio.sleep(15)
                await queue.put(("ping", None))

        graph_task = asyncio.create_task(_run_graph())
        heartbeat_task = asyncio.create_task(_run_heartbeats())

        try:
            while True:
                msg_type, val = await queue.get()
                if msg_type == "chunk":
                    chunk = val
                    # Strategy and per-source model answers are drafts. Keep
                    # them server-side and expose only the graph's validated
                    # final value for callers of this deprecated helper.
                    if "write_final_answer" in chunk:
                        final_answer = chunk["write_final_answer"]["final_answer"]
                elif msg_type == "ping":
                    yield ": keep-alive\n\n"
                elif msg_type == "done":
                    break
                elif msg_type == "error":
                    raise val
        finally:
            heartbeat_task.cancel()
            graph_task.cancel()
            await asyncio.gather(graph_task, heartbeat_task, return_exceptions=True)

        if final_answer:
            yield f"data: {json.dumps({'type': 'final_answer', 'content': final_answer})}\n\n"
        yield f"data: {json.dumps({'type': 'complete', 'final_answer': final_answer})}\n\n"
    except GeneratorExit:
        outcome = "cancelled"
        raise
    except Exception as exc:
        outcome = "error"
        from open_notebook.utils.error_classifier import classify_error

        _, user_message = classify_error(exc)
        logger.error(f"Error in ask streaming: {str(exc)}")
        yield f"data: {json.dumps({'type': 'error', 'message': user_message})}\n\n"
    finally:
        telemetry.record_operation(
            category="ask_stream_finalize",
            route="/api/search/ask",
            duration_ms=(time.perf_counter() - started) * 1000,
            outcome=outcome,
        )
        if outcome == "error":
            telemetry.record_issue("unanswered_question", category="ask_stream_finalize", error_class="stream_error")
        elif not final_answer and outcome == "success":
            telemetry.record_issue("unanswered_question", category="ask_stream_finalize", error_class="empty_answer")


def map_department_to_domain(department: str | None) -> str | None:
    if not department:
        return None
    dept_lower = department.lower().strip()
    if "hộ tịch" in dept_lower or "tư pháp" in dept_lower or "ho_tich" in dept_lower:
        return "ho_tich_chung_thuc"
    if "địa chính" in dept_lower or "xây dựng" in dept_lower or "đô thị" in dept_lower or "dat_dai" in dept_lower or "xay_dung" in dept_lower:
        return "dat_dai_xay_dung"
    if "văn hóa" in dept_lower or "xã hội" in dept_lower or "an_sinh" in dept_lower:
        return "an_sinh_y_te_giao_duc"
    if "văn phòng" in dept_lower or "thống kê" in dept_lower or "một cửa" in dept_lower or "hanh_chinh" in dept_lower:
        return "hanh_chinh_cong"
    if "trật tự" in dept_lower or "an ninh" in dept_lower or "công an" in dept_lower or "cu_tru" in dept_lower:
        return "cu_tru_an_ninh"
    if "khiếu nại" in dept_lower or "tố cáo" in dept_lower or "xử phạt" in dept_lower:
        return "khieu_nai_to_cao_xu_phat"
    return None


def _is_domain_allowed(domain_slug: str | None, allowed_domains: list[str]) -> bool:
    if not domain_slug:
        return True
    slug_parts = set(domain_slug.split("_"))
    for allowed in allowed_domains:
        allowed_parts = set(allowed.split("_"))
        if slug_parts.intersection(allowed_parts):
            return True
        if allowed == domain_slug:
            return True
    return False


async def _deprecated_ask_knowledge_base_stream(
    ask_request: AskRequest,
    request: Request,
):
    """Unrouted pre-progress implementation retained for rollback only."""
    user_role = get_request_role(request)
    user_id = get_request_user_id(request)
    detected_domain = _detect_question_domain(ask_request.question)
    # The authenticated role is authoritative. A payload must never upgrade a
    # citizen/officer into an admin solely to request diagnostic trace data.
    ask_request.role = user_role or ask_request.role  # type: ignore[assignment]
    if user_role == "officer" and user_id:
        from api.user_service import get_user_profile
        profile = await get_user_profile(user_id)
        if profile:
            allowed_domains = profile.get("allowed_domains") or []
            if ask_request.domain:
                if not _is_domain_allowed(ask_request.domain, allowed_domains):
                    raise HTTPException(
                        status_code=403,
                        detail="Tài khoản cán bộ không có quyền truy cập lĩnh vực này."
                    )
            
            detected = _detect_question_domain(ask_request.question)
            if detected:
                detected_slug = detected.get("slug")
                if detected_slug and not _is_domain_allowed(detected_slug, allowed_domains):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Câu hỏi thuộc lĩnh vực {detected.get('name')} (cơ quan {detected.get('agency')}). Tài khoản của bạn không được phân quyền phụ trách lĩnh vực này. Hãy chuyển tiếp cho cán bộ phù hợp."
                    )
            
            ask_request.allowed_domains = allowed_domains

    started_at = time.perf_counter()
    trace_id = uuid.uuid4().hex
    try:
        if ask_request.offline_mode:
            result = await _ask_local(ask_request, user_id=user_id, request=request)
            retrieval_results = _extract_retrieval_results(result.rag_trace or {})
            await _safe_log_ask_history(
                owner_user_id=get_request_user_id(request),
                owner_role=get_request_role(request),
                question=ask_request.question,
                answer=result.answer,
                domain=ask_request.domain,
                strategy_model=ask_request.strategy_model,
                answer_model=ask_request.answer_model,
                final_answer_model=ask_request.final_answer_model,
                offline_mode=True,
                offline_model=ask_request.offline_model,
                rag_trace=result.rag_trace,
                sources=retrieval_results,
                grounding_status=result.grounding_status,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )

            async def local_stream() -> AsyncGenerator[str, None]:
                yield (
                    f"data: {json.dumps({'type': 'final_answer', 'content': result.answer})}\n\n"
                )
                if result.rag_trace:
                    yield (
                        f"data: {json.dumps({'type': 'rag_trace', 'trace': result.rag_trace})}\n\n"
                    )
                yield (
                    f"data: {json.dumps({'type': 'complete', 'final_answer': result.answer})}\n\n"
                )

            return StreamingResponse(local_stream(), media_type="text/event-stream")

        strategy_id, answer_id, final_id = await _resolve_ask_model_ids(
            ask_request.strategy_model,
            ask_request.answer_model,
            ask_request.final_answer_model,
        )
        ask_request.strategy_model = strategy_id
        ask_request.answer_model = answer_id
        ask_request.final_answer_model = final_id

        strategy_model = await Model.get(strategy_id)
        answer_model = await Model.get(answer_id)
        final_answer_model = await Model.get(final_id)

        if not strategy_model:
            raise HTTPException(
                status_code=400,
                detail=f"Strategy model {strategy_id} not found",
            )
        if not answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Answer model {answer_id} not found",
            )
        if not final_answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Final answer model {final_id} not found",
            )

        section_grounding = is_section_grounding_enabled()
        section_answer_sections: list[Any] | None = None
        section_aggregate: dict[str, Any] | None = None
        section_trace: dict[str, Any] | None = None
        history_question = ask_request.question
        # Use a deterministic domain hint for retrieval when the UI is in
        # auto-select mode.  Keep the user-selected field unchanged in the
        # response; this is only a scope hint and never bypasses officer ACLs.
        retrieval_domain = (
            ask_request.domain
            or (detected_domain or {}).get("slug")
            or None
        )
        conv_id = getattr(ask_request, "conversation_id", None) or ask_request.session_id
        owner_key = _resolve_ask_session_owner_key(request, user_id=user_id, role=getattr(ask_request, "role", None))
        role_val = getattr(ask_request, "role", None) or get_request_role(request)
        conv_ctx, ctx_msgs = await _build_conversation_context(conv_id, owner_key, real_user_id=user_id, role_context=role_val, is_admin=(role_val=="admin"))
        if ctx_msgs:
            history_str = conv_svc.format_context_for_prompt(ctx_msgs)
            history_question = f"Lịch sử trò chuyện trước đó:\n{history_str}\n---\nCâu hỏi hiện tại:\n{ask_request.question}"

        return StreamingResponse(
            stream_ask_response(
                history_question,
                ask_request.role,
                strategy_model,
                answer_model,
                final_answer_model,
                retrieval_domain,
                effective_legal_date(
                    legal_as_of=ask_request.legal_as_of,
                    event_date=ask_request.event_date,
                ).isoformat(),
                **_section_retrieval_kwargs(
                    current_question=ask_request.question,
                    request_id=uuid.uuid4().hex,
                    selected_domain=retrieval_domain,
                ),
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in ask endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ask operation failed: {str(e)}")


async def _persist_conversation_answer(
    ask_request: AskRequest,
    request: Request,
    response: AskResponse,
    *,
    user_id: str | None,
) -> None:
    """Best-effort persistence of an assistant snapshot after Ask completes."""
    conversation_id = getattr(ask_request, "conversation_id", None)
    if not conversation_id:
        return
    role_val = getattr(ask_request, "role", None) or get_request_role(request)
    owner_key = _resolve_ask_session_owner_key(request, user_id=user_id, role=role_val)
    if not owner_key:
        return
    try:
        persisted = await conv_svc.add_message(
            conversation_id,
            owner_key=owner_key,
            role="assistant",
            content=response.answer,
            real_user_id=user_id,
            role_context=role_val,
            status="complete",
            citations=response.citations,
            recommended_forms=response.recommended_forms,
            faq_refs=[item.get("id") for item in (response.faqs or []) if isinstance(item, dict) and item.get("id")] or None,
            faqs=response.faqs,
            procedure_detail=response.procedure_detail,
            rag_trace=response.rag_trace if role_val == "admin" else None,
            grounding_status=response.grounding_status,
        )
        if not persisted:
            logger.warning(
                "Ask answer was not persisted because conversation {} was not found for user {}",
                conversation_id,
                user_id,
            )
    except Exception as exc:
        # Answer must still reach the user when persistence has a transient failure.
        logger.warning(f"Failed to persist Ask answer to conversation {conversation_id}: {exc}")


async def _persist_conversation_error(
    ask_request: AskRequest,
    request: Request,
    error_text: str,
    *,
    user_id: str | None,
) -> None:
    """Best-effort persistence for a failed Ask turn.

    The user message is written before Ask starts. Persisting a matching
    assistant error keeps the conversation structurally complete after reload.
    """
    conversation_id = getattr(ask_request, "conversation_id", None)
    if not conversation_id:
        return
    role_val = getattr(ask_request, "role", None) or get_request_role(request)
    owner_key = _resolve_ask_session_owner_key(request, user_id=user_id, role=role_val)
    if not owner_key:
        return
    try:
        await conv_svc.add_message(
            conversation_id,
            owner_key=owner_key,
            role="assistant",
            content=error_text.strip() or "Không thể trả lời câu hỏi. Vui lòng thử lại.",
            real_user_id=user_id,
            role_context=role_val,
            status="error",
        )
    except Exception as exc:
        logger.warning(
            "Failed to persist Ask error to conversation {}: {}",
            conversation_id,
            exc,
        )


async def _execute_ask_simple(
    ask_request: AskRequest,
    request: Request,
    *,
    progress: ProgressCallback | None = None,
    trace_id_override: str | None = None,
) -> AskResponse:
    """Ask the knowledge base a question and return a simple response (non-streaming)."""
    operation_started = time.perf_counter()
    user_role = get_request_role(request)
    user_id = get_request_user_id(request)

    # Prefer request role, fall back to payload role
    effective_role = user_role or ask_request.role
    ask_request.role = effective_role  # type: ignore[assignment]
    detected_domain = _detect_question_domain(ask_request.question)
    question_policy = classify_question(
        ask_request.question,
        detected_domain=(detected_domain or {}).get("slug") if detected_domain else None,
    )
    original_selected_domain = ask_request.domain
    soft_domain_mismatch = bool(
        effective_role in ("citizen", "admin")
        and original_selected_domain
        and detected_domain
        and original_selected_domain != detected_domain.get("slug")
    )
    if _should_block_domain_mismatch(effective_role, ask_request.domain, detected_domain):
        return _domain_mismatch_response(
            ask_request.question,
            effective_role,
            ask_request.domain,
            detected_domain or {},
        )
    # citizen/admin soft redirect selected domain to detected when clearly mismatched
    if soft_domain_mismatch and detected_domain:
        ask_request.domain = detected_domain.get("slug")
    if user_role == "officer" and user_id:
        from api.user_service import get_user_profile
        profile = await get_user_profile(user_id)
        if profile:
            allowed_domains = profile.get("allowed_domains") or []
            if ask_request.domain:
                if not _is_domain_allowed(ask_request.domain, allowed_domains):
                    raise HTTPException(
                        status_code=403,
                        detail="Tài khoản cán bộ không có quyền truy cập lĩnh vực này."
                    )
            
            detected = _detect_question_domain(ask_request.question)
            if detected:
                detected_slug = detected.get("slug")
                if detected_slug and not _is_domain_allowed(detected_slug, allowed_domains):
                    raise HTTPException(
                        status_code=403,
                        detail=f"Câu hỏi thuộc lĩnh vực {detected.get('name')} (cơ quan {detected.get('agency')}). Tài khoản của bạn không được phân quyền phụ trách lĩnh vực này. Hãy chuyển tiếp cho cán bộ phù hợp."
                    )
            
            ask_request.allowed_domains = allowed_domains

    started_at = time.perf_counter()
    trace_id = trace_id_override or uuid.uuid4().hex
    idempotency_cache_key = ask_idempotency.scoped_key(
        user_id=user_id,
        role=effective_role,
        key=ask_request.idempotency_key,
    )
    idempotency_owned = False
    try:
        idempotency_started = time.perf_counter()
        idempotency_mode, idempotency_value = await ask_idempotency.begin(
            idempotency_cache_key
        )
        telemetry.record_ask_stage(
            "queue",
            duration_ms=(time.perf_counter() - idempotency_started) * 1000,
            cached=idempotency_mode in {"cached", "wait"},
        )
        if idempotency_mode == "cached":
            return idempotency_value
        if idempotency_mode == "wait":
            return await idempotency_value
        idempotency_owned = idempotency_mode == "owner"

        async def _complete_local_result(
            result: AskResponse,
            *,
            fallback_reason: str | None = None,
        ) -> AskResponse:
            """Finish a validated local result through the normal audit path."""

            await emit_ask_progress(progress, "status", {"stage": "validating"})
            await emit_ask_progress(
                progress,
                "sources",
                {"citations": result.citations or []},
            )
            await emit_ask_progress(progress, "status", {"stage": "persisting"})

            updates: dict[str, Any] = {
                "conversation_id": ask_request.conversation_id,
                "latency_ms": int((time.perf_counter() - started_at) * 1000),
                "trace_id": trace_id,
            }
            if fallback_reason:
                updates["error"] = {
                    "code": "LOCAL_MODEL_FALLBACK",
                    "message": (
                        "Nhà cung cấp AI tạm thời gián đoạn; câu trả lời đã được "
                        "tạo bằng mô hình local và vẫn qua kiểm tra nguồn."
                    ),
                    "retryable": False,
                    "reason": fallback_reason,
                }
                updates["quality_flags"] = list(
                    dict.fromkeys([*(result.quality_flags or []), "local_model_fallback"])
                )
            completed_result = result.model_copy(update=updates)
            retrieval_results = [
                dict(item)
                for item in (completed_result.citations or [])
                if isinstance(item, dict)
            ] or _extract_retrieval_results(completed_result.rag_trace or {})
            await _safe_log_ask_history(
                owner_user_id=get_request_user_id(request),
                owner_role=get_request_role(request),
                question=ask_request.question,
                answer=completed_result.answer,
                domain=ask_request.domain,
                strategy_model=ask_request.strategy_model,
                answer_model=ask_request.answer_model,
                final_answer_model=ask_request.final_answer_model,
                offline_mode=True,
                offline_model=(
                    RECOMMENDED_LOCAL_MODEL if fallback_reason else ask_request.offline_model
                ),
                rag_trace=_audit_trace_snapshot(completed_result.rag_trace),
                sources=retrieval_results,
                grounding_status=completed_result.grounding_status,
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
            await _persist_conversation_answer(
                ask_request,
                request,
                completed_result,
                user_id=user_id,
            )
            telemetry.record_ask_stage(
                "total",
                duration_ms=(time.perf_counter() - operation_started) * 1000,
            )
            if idempotency_owned:
                await ask_idempotency.complete(
                    idempotency_cache_key,
                    completed_result,
                )
            return completed_result

        if ask_request.offline_mode:
            await emit_ask_progress(progress, "status", {"stage": "generating"})
            result = await _ask_local(ask_request, user_id=user_id, request=request)
            return await _complete_local_result(result)

        try:
            strategy_id, answer_id, final_id = await _resolve_ask_model_ids(
                ask_request.strategy_model,
                ask_request.answer_model,
                ask_request.final_answer_model,
            )
            ask_request.strategy_model = strategy_id
            ask_request.answer_model = answer_id
            ask_request.final_answer_model = final_id

            strategy_model = await Model.get(strategy_id)
            answer_model = await Model.get(answer_id)
            final_answer_model = await Model.get(final_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Model not found: {exc}") from exc

        if not strategy_model or not answer_model or not final_answer_model:
            raise HTTPException(
                status_code=400,
                detail="One or more required models are missing. Register models via Settings or check the model IDs.",
            )
        # The opt-in section orchestration state belongs to this request only.
        # Keep it initialized even while the flag is off so the legacy path
        # remains a safe, immediate rollback and cannot fail at runtime.
        section_grounding = is_section_grounding_enabled()
        section_answer_sections: list[Any] | None = None
        section_aggregate: dict[str, Any] | None = None
        section_trace: dict[str, Any] | None = None
        history_question = ask_request.question
        # Use a deterministic domain hint for retrieval when the UI is in
        # auto-select mode. Keep the user-selected field unchanged in the
        # response; this is only a scope hint and never bypasses officer ACLs.
        retrieval_domain = (
            ask_request.domain
            or (detected_domain or {}).get("slug")
            or None
        )
        conv_id = getattr(ask_request, "conversation_id", None) or ask_request.session_id
        owner_key = _resolve_ask_session_owner_key(request, user_id=user_id, role=getattr(ask_request, "role", None))
        role_val = getattr(ask_request, "role", None) or get_request_role(request)
        conv_ctx, ctx_msgs = await _build_conversation_context(conv_id, owner_key, real_user_id=user_id, role_context=role_val, is_admin=(role_val=="admin"))
        if ctx_msgs:
            history_str = conv_svc.format_context_for_prompt(ctx_msgs)
            history_question = f"Lịch sử trò chuyện trước đó:\n{history_str}\n---\nCâu hỏi hiện tại:\n{ask_request.question}"

        final_answer = None
        evidence = []
        llm_started = time.perf_counter()
        await emit_ask_progress(progress, "status", {"stage": "generating"})
        try:
            legal_as_of_iso = effective_legal_date(
                legal_as_of=ask_request.legal_as_of,
                event_date=ask_request.event_date,
            ).isoformat()
            if section_grounding:
                (
                    section_answer_sections,
                    section_aggregate,
                    section_results,
                    section_trace,
                ) = await _run_section_orchestration(
                    ask_request=ask_request,
                    request_id=trace_id,
                    question_policy=question_policy,
                    legal_as_of=legal_as_of_iso,
                    strategy_model_id=strategy_model.id,
                    answer_model_id=answer_model.id,
                    final_answer_model_id=final_answer_model.id,
                )
                evidence = section_results
                final_answer = str(section_aggregate["answer"])
            else:
                graph_input = dict(
                    question=history_question,
                    role=ask_request.role,
                    domain=retrieval_domain,
                    question_type=question_policy["question_type"],
                    required_sections=question_policy.get("required_sections") or [],
                    legal_as_of=legal_as_of_iso,
                )
                graph_input.update(
                    _section_retrieval_kwargs(
                        current_question=ask_request.question,
                        request_id=trace_id,
                        selected_domain=retrieval_domain,
                    )
                )
                async for chunk in ask_graph.astream(
                    input=graph_input,  # type: ignore[arg-type]
                    config=dict(
                        configurable=dict(
                            strategy_model=strategy_model.id,
                            answer_model=answer_model.id,
                            final_answer_model=final_answer_model.id,
                        )
                    ),
                    stream_mode="updates",
                ):
                    if "provide_answer" in chunk:
                        evidence = chunk["provide_answer"].get("evidence", [])
                    if "write_final_answer" in chunk:
                        final_answer = chunk["write_final_answer"]["final_answer"]
        except (
            RateLimitError,
            NetworkError,
            ExternalServiceError,
            httpx.HTTPError,
        ) as cloud_exc:
            fallback_reason = cloud_fallback_reason(cloud_exc)
            if local_fallback_enabled() and fallback_reason:
                # Provider fallback changes only the generator. The local path
                # still enforces retrieval sufficiency and citation validation.
                fallback_request = ask_request.model_copy(
                    update={
                        "offline_mode": True,
                        "offline_model": RECOMMENDED_LOCAL_MODEL,
                    }
                )
                local_result = await _ask_local(
                    fallback_request,
                    user_id=user_id,
                    request=request,
                )
                return await _complete_local_result(
                    local_result,
                    fallback_reason=fallback_reason,
                )
            raise

        if not final_answer:
            raise HTTPException(status_code=500, detail="No answer generated")

        # Xác thực câu trả lời online
        retrieval_trace = evidence.get("trace") if isinstance(evidence, dict) else None
        if retrieval_trace is None and isinstance(evidence, list):
            retrieval_trace = next(
                (
                    item.get("_retrieval_trace")
                    for item in evidence
                    if isinstance(item, dict) and isinstance(item.get("_retrieval_trace"), dict)
                ),
                None,
            )
        retrieval_results = _extract_retrieval_results(evidence)
        post_validation_started = time.perf_counter()
        await emit_ask_progress(progress, "status", {"stage": "validating"})
        if section_grounding and section_aggregate is not None:
            grounding_status = str(section_aggregate["grounding_status"])
            retrieval_trace = {"section_orchestration": section_trace or {}}
        else:
            final_answer, grounding_status = _verify_and_ground_answer(
                ask_request.question, final_answer, retrieval_results
            )

        procedure_match = _match_procedure_detail(ask_request.question)
        procedure_detail, recommended_forms, procedure_summary = _procedure_response_fields(procedure_match, ask_request.question)
        _queue_form_discovery_if_needed(ask_request.question, recommended_forms)
        matched_faqs = _match_faqs_for_question(
            ask_request.question,
            domain=getattr(ask_request, "domain", None),
            ward_scope=getattr(ask_request, "ward_scope", None) or "Le Chan",
        )
        if matched_faqs and procedure_detail:
            procedure_detail = {
                "id": procedure_detail.get("id"),
                "name": procedure_detail.get("name"),
                "department": procedure_detail.get("department"),
                "reference_only": True,
                "summary": procedure_summary or procedure_detail.get("name"),
            }
        if section_grounding and section_aggregate is not None:
            answer_out = str(section_aggregate["answer"])
            citations = list(section_aggregate["citations"])
            removed_claims: list[dict[str, Any]] = []
        elif grounding_status in {"ungrounded", "insufficient_evidence"}:
            answer_out = final_answer
            citations = []
            removed_claims = []
        else:
            answer_out, citations = _ensure_answer_has_source_links(final_answer, retrieval_results)
            answer_out, removed_claims = _guard_sensitive_claims(answer_out, retrieval_results)
        answer_out = _sanitize_answer_citation_display(answer_out, retrieval_results)
        source_gap = [] if section_grounding else missing_answer_sections(
            answer_out,
            question_policy["question_type"],
            bool(question_policy.get("is_procedural")),
            required_sections=question_policy.get("required_sections") or [],
        )
        if source_gap and not section_grounding:
            answer_out += "\n\nThông tin chưa xác minh được từ nguồn hiện có: " + ", ".join(vietnamese_section_names(source_gap)) + "."
        rag_trace = _merge_claim_guard_into_trace(retrieval_trace, removed_claims)
        legal_as_of = effective_legal_date(
            legal_as_of=ask_request.legal_as_of,
            event_date=ask_request.event_date,
        )
        evidence_coverage = build_evidence_coverage(
            retrieval_results,
            required_sections=question_policy.get("required_sections") or [],
            recommended_forms=recommended_forms,
        )
        claim_validation = build_claim_validation(
            removed_claims=removed_claims,
            citations=citations,
            legal_as_of=legal_as_of,
            answer=answer_out,
            coverage=evidence_coverage,
        )
        if not section_grounding:
            answer_out = apply_claim_validation(answer_out, claim_validation)
        answer_score_preview, quality_flags = quality_preview(
            coverage=evidence_coverage,
            grounding_status=grounding_status,
            claim_validation=claim_validation,
        )
        clarifying_questions = (
            [
                section.clarifying_question
                for section in (section_answer_sections or [])
                if section.clarifying_question
            ][:1]
            if section_grounding
            else clarifying_questions_for_gaps(ask_request.question, evidence_coverage)
        )
        if isinstance(rag_trace, dict):
            rag_trace["question_classification"] = question_policy
            rag_trace["legal_as_of"] = legal_as_of.isoformat()
            rag_trace["evidence_coverage"] = evidence_coverage
            rag_trace["claim_validation"] = claim_validation
            rag_trace["answer_pipeline"] = {
                "retrieved_chunks": len(retrieval_results),
                "llm_sources": len(retrieval_results),
                "citations_created": len(citations or []),
                "grounding_status": grounding_status,
                "final_answer_chars": len(answer_out),
            }
            existing_timing = dict(rag_trace.get("latency_by_stage") or {})
            existing_timing["llm_and_answer_ms"] = round(
                (time.perf_counter() - llm_started) * 1000, 1
            )
            existing_timing["post_validation_ms"] = round(
                (time.perf_counter() - post_validation_started) * 1000, 1
            )
            rag_trace["latency_by_stage"] = existing_timing
            rag_trace.setdefault("filtered_by_effective_date", rag_trace.get("filtered_candidates", []))
            rag_trace.setdefault("filtered_by_location", [])
            rag_trace.setdefault("filtered_by_legal_hierarchy", [])
            rag_trace.setdefault("filtered_by_relationship", [])
            rag_trace.setdefault("forms_accepted", recommended_forms or [])
            rag_trace.setdefault("forms_rejected", [])
        if soft_domain_mismatch and detected_domain:
            tip = (
                f"\n\n---\nGợi ý: câu hỏi có vẻ thuộc **{detected_domain.get('name')}** "
                f"(cơ quan: **{detected_domain.get('agency')}**). "
                "Bạn nên chuyển sang lĩnh vực này để được hướng dẫn đúng hơn."
            )
            if tip.strip() not in answer_out:
                answer_out = answer_out + tip
        response = AskResponse(
            answer=answer_out,
            question=ask_request.question,
            rag_trace=(
                rag_trace
                if ask_request.role == "admin" and ask_request.show_rag_trace
                else None
            ),
            grounding_status=grounding_status,
            procedure_detail=procedure_detail,
            recommended_forms=recommended_forms,
            faqs=matched_faqs if 'matched_faqs' in locals() else None,
            procedure_summary=procedure_summary,
            citations=citations,
            domain_mismatch=soft_domain_mismatch,
            selected_domain=original_selected_domain,
            suggested_domain=(detected_domain or {}).get("slug") if detected_domain else None,
            suggested_agency=(detected_domain or {}).get("agency") if detected_domain else None,
            conversation_id=ask_request.conversation_id,
            latency_ms=int((time.perf_counter() - started_at) * 1000),
            trace_id=trace_id,
            question_type=question_policy["question_type"],
            detected_domain=question_policy.get("detected_domain"),
            required_sections=question_policy.get("required_sections") or [],
            forms_unavailable=bool(question_policy.get("requests_form") and not recommended_forms),
            source_gap=source_gap,
            evidence_coverage=evidence_coverage,
            claim_validation=claim_validation,
            authority_status=evidence_coverage.get("authority", {}).get("status", "not_applicable"),
            legal_as_of=legal_as_of,
            clarifying_questions=clarifying_questions,
            quality_flags=quality_flags,
            answer_score_preview=answer_score_preview,
            answer_sections=section_answer_sections if section_grounding else None,
        )
        await emit_ask_progress(
            progress,
            "sources",
            {"citations": response.citations or []},
        )
        await emit_ask_progress(progress, "status", {"stage": "persisting"})
        await _safe_log_ask_history(
            owner_user_id=get_request_user_id(request),
            owner_role=get_request_role(request),
            question=ask_request.question,
            answer=response.answer,
            domain=ask_request.domain,
            strategy_model=ask_request.strategy_model,
            answer_model=ask_request.answer_model,
            final_answer_model=ask_request.final_answer_model,
            offline_mode=False,
            offline_model=None,
            rag_trace=_audit_trace_snapshot(rag_trace),
            sources=retrieval_results,
            grounding_status=response.grounding_status,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
        await _persist_conversation_answer(ask_request, request, response, user_id=user_id)
        telemetry.record_ask_stage(
            "total",
            duration_ms=(time.perf_counter() - operation_started) * 1000,
        )
        if idempotency_owned:
            await ask_idempotency.complete(idempotency_cache_key, response)
        return response

    except asyncio.CancelledError as exc:
        if idempotency_owned:
            await ask_idempotency.fail(idempotency_cache_key, exc)
        telemetry.record_ask_stage(
            "total",
            duration_ms=(time.perf_counter() - operation_started) * 1000,
            outcome="cancelled",
        )
        # Client cancellation is not a failed legal answer and must not create
        # an assistant error message in conversation history.
        raise
    except HTTPException as exc:
        if idempotency_owned:
            await ask_idempotency.fail(idempotency_cache_key, exc)
        telemetry.record_ask_stage(
            "total",
            duration_ms=(time.perf_counter() - operation_started) * 1000,
            outcome="error",
        )
        detail = exc.detail
        if isinstance(detail, dict):
            detail = {**detail, "trace_id": trace_id}
        else:
            detail = {
                "code": "ASK_HTTP_ERROR",
                "message": str(detail),
                "retryable": exc.status_code >= 500,
                "trace_id": trace_id,
            }
        await _persist_conversation_error(
            ask_request,
            request,
            str(detail.get("message") or "Không thể trả lời câu hỏi."),
            user_id=user_id,
        )
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    except OpenNotebookError as exc:
        if idempotency_owned:
            await ask_idempotency.fail(idempotency_cache_key, exc)
        status_code, detail = _ask_error_response(exc)
        if isinstance(detail, dict):
            detail = {**detail, "trace_id": trace_id}
        logger.error(
            "Ask simple failed [{}]: {}",
            detail["code"],
            detail["message"],
        )
        telemetry.record_ask_stage(
            "total",
            duration_ms=(time.perf_counter() - operation_started) * 1000,
            outcome="error",
        )
        telemetry.record_issue("unanswered_question", category="ask", error_class=exc.__class__.__name__)
        await _persist_conversation_error(
            ask_request,
            request,
            str(detail.get("message") or "Dịch vụ hỏi đáp pháp luật đang gặp lỗi."),
            user_id=user_id,
        )
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except Exception as e:
        if idempotency_owned:
            await ask_idempotency.fail(idempotency_cache_key, e)
        telemetry.record_ask_stage(
            "total",
            duration_ms=(time.perf_counter() - operation_started) * 1000,
            outcome="error",
        )
        telemetry.record_issue("unanswered_question", category="ask", error_class=e.__class__.__name__)
        logger.exception("Unexpected error in ask simple endpoint")
        await _persist_conversation_error(
            ask_request,
            request,
            "Hệ thống chưa thể hoàn tất câu trả lời. Vui lòng thử lại.",
            user_id=user_id,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "ASK_FAILED",
                "message": "Hệ thống chưa thể hoàn tất câu trả lời. Vui lòng thử lại.",
                "how_to_fix": [
                    "Kiểm tra trạng thái tại /api/search/health.",
                    "Xem log backend để xác định lỗi chưa được phân loại.",
                ],
                "retryable": True,
                "trace_id": trace_id,
            },
        ) from e


def _build_validated_ask_stream(
    ask_request: AskRequest,
    request: Request,
    *,
    compatibility_data_envelope: bool,
) -> StreamingResponse:
    """Wrap the canonical Ask pipeline without exposing model drafts."""

    trace_id = uuid.uuid4().hex
    if not ask_request.idempotency_key:
        ask_request.idempotency_key = f"progress-{uuid.uuid4().hex}"
    effective_role = get_request_role(request) or ask_request.role

    async def execute(progress_callback: ProgressCallback) -> AskResponse:
        return await _execute_ask_simple(
            ask_request,
            request,
            progress=progress_callback,
            trace_id_override=trace_id,
        )

    return StreamingResponse(
        stream_ask_progress(
            execute,
            trace_id=trace_id,
            conversation_id=ask_request.conversation_id,
            idempotency_key=ask_request.idempotency_key,
            effective_role=effective_role,
            compatibility_data_envelope=compatibility_data_envelope,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Ask-Contract": "validated-final-v1",
        },
    )


@router.post("/search/ask")
async def ask_knowledge_base(
    ask_request: AskRequest,
    request: Request,
) -> StreamingResponse:
    """Compatibility SSE endpoint with lifecycle and validated final only."""

    return _build_validated_ask_stream(
        ask_request,
        request,
        compatibility_data_envelope=True,
    )


@router.post("/search/ask/simple", response_model=AskResponse)
async def ask_knowledge_base_simple(
    ask_request: AskRequest,
    request: Request,
) -> AskResponse:
    """Backward-compatible non-streaming Ask endpoint."""

    return await _execute_ask_simple(ask_request, request)


@router.post("/search/ask/progress")
async def ask_knowledge_base_progress(
    ask_request: AskRequest,
    request: Request,
) -> StreamingResponse:
    """Emit safe lifecycle progress and one validated final AskResponse."""

    if not ask_progress_enabled():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ASK_PROGRESS_DISABLED",
                "message": "Tính năng theo dõi tiến độ hỏi đáp chưa được bật.",
                "retryable": False,
            },
        )

    enforce_ask_role_rollout(request)
    return _build_validated_ask_stream(
        ask_request,
        request,
        compatibility_data_envelope=False,
    )


@router.get("/search/health")
async def search_health() -> dict:
    components: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            response = await client.get(f"{LEGAL_SEARCH_URL}/health")
            response.raise_for_status()
            data = response.json()
            components["legal_retrieval"] = {
                "healthy": True,
                "url": LEGAL_SEARCH_URL,
                "indexed_records": data.get("indexed_records"),
                "collection": data.get("collection"),
            }
        except Exception as exc:
            components["legal_retrieval"] = {
                "healthy": False,
                "url": LEGAL_SEARCH_URL,
                "error": type(exc).__name__,
                "how_to_fix": (
                    "powershell -ExecutionPolicy Bypass "
                    "-File scripts/start_legal_search.ps1"
                ),
            }

        try:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
            installed = [
                item.get("name")
                for item in response.json().get("models", [])
                if item.get("name")
            ]
            components["ollama"] = {
                "healthy": True,
                "url": OLLAMA_URL,
                "recommended_model": RECOMMENDED_LOCAL_MODEL,
                "recommended_installed": RECOMMENDED_LOCAL_MODEL in installed,
            }
        except Exception as exc:
            components["ollama"] = {
                "healthy": False,
                "url": OLLAMA_URL,
                "error": type(exc).__name__,
                "how_to_fix": "Mở Ollama và chạy: ollama serve",
            }

    healthy = bool(components["legal_retrieval"]["healthy"])
    return {
        "status": "healthy" if healthy else "degraded",
        "can_ask_cloud": healthy,
        "can_ask_local": healthy and bool(components["ollama"]["healthy"]),
        "components": components,
    }


@router.get("/search/local-models")
async def local_models() -> dict:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        data = response.json()
        models = [
            {
                "name": item.get("name"),
                "size": item.get("size"),
                "modified_at": item.get("modified_at"),
            }
            for item in data.get("models", [])
        ]
        installed_names = {item["name"] for item in models if item.get("name")}
        return {
            "available": True,
            "recommended": RECOMMENDED_LOCAL_MODEL,
            "models": models,
            "recommended_installed": RECOMMENDED_LOCAL_MODEL in installed_names,
        }
    except Exception as exc:
        return {
            "available": False,
            "recommended": RECOMMENDED_LOCAL_MODEL,
            "models": [],
            "recommended_installed": False,
            "error": str(exc),
        }








# ---------------------------------------------------------------------------
# Voice input transcription endpoint -- Stage 1 of voice ask pipeline
# ---------------------------------------------------------------------------
VOICE_AUDIO_MIMES = {
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "audio/mp4",
    "audio/m4a",
    "audio/aac",
    "audio/flac",
}
MAX_VOICE_BYTES = 15 * 1024 * 1024  # 15 MB


@router.post("/media/transcribe-voice")
async def transcribe_voice_upload(
    file: UploadFile = File(...),
) -> dict:
    """
    Voice input stage for search/ask.

    This endpoint only transcribes audio to text. The returned text is inserted
    into the question box by the frontend and then goes through the existing ask
    pipeline. Video is intentionally rejected.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Không có file âm thanh được gửi lên.")

    mime_type = (file.content_type or "application/octet-stream").lower().split(";", 1)[0].strip()
    if mime_type.startswith("video/"):
        raise HTTPException(
            status_code=415,
            detail="Không hỗ trợ video. Voice input chỉ nhận âm thanh để chuyển thành văn bản.",
        )
    if mime_type not in VOICE_AUDIO_MIMES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Định dạng âm thanh '{mime_type}' chưa được hỗ trợ. "
                "Chấp nhận audio/webm, wav, mp3, ogg, m4a/aac/flac. Không hỗ trợ video."
            ),
        )

    try:
        stt_model = await model_manager.get_speech_to_text()
    except Exception as exc:
        logger.error(f"Failed to load speech-to-text model: {exc}")
        raise HTTPException(
            status_code=503,
            detail=(
                "Speech-to-text model chưa sẵn sàng hoặc cấu hình sai. Vào Cài đặt → "
                "API Keys/Mô hình AI để kiểm tra model STT mặc định và khóa API."
            ),
        ) from exc

    if stt_model is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Chưa cấu hình speech-to-text model. Vào Cài đặt → API Keys/Mô hình AI "
                "và chọn model STT mặc định trước khi dùng nhập giọng nói."
            ),
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_VOICE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File âm thanh quá lớn ({len(file_bytes) // 1024} KB). "
                f"Giới hạn tối đa là {MAX_VOICE_BYTES // 1024 // 1024} MB."
            ),
        )

    audio_buffer = io.BytesIO(file_bytes)
    audio_buffer.name = file.filename
    try:
        transcription = await stt_model.atranscribe(
            audio_buffer,
            language="vi",
            prompt="Đây là câu hỏi pháp luật/hành chính công bằng tiếng Việt.",
        )
    except Exception as exc:
        logger.error(f"Voice transcription failed for '{file.filename}': {exc}")
        raise HTTPException(
            status_code=502,
            detail="Không thể chuyển giọng nói thành văn bản. Hãy kiểm tra cấu hình STT hoặc thử ghi âm lại rõ hơn.",
        ) from exc

    transcript = (getattr(transcription, "text", "") or "").strip()
    if not transcript:
        raise HTTPException(
            status_code=422,
            detail="STT không nhận diện được nội dung giọng nói. Vui lòng ghi âm rõ hơn hoặc nhập bằng văn bản.",
        )

    return {
        "transcript": transcript,
        "filename": file.filename,
        "mime_type": mime_type,
        "char_count": len(transcript),
        "language": getattr(transcription, "language", None),
        "model": getattr(transcription, "model", None),
        "provider": getattr(transcription, "provider", None),
        "max_file_mb": MAX_VOICE_BYTES // 1024 // 1024,
    }

# ---------------------------------------------------------------------------
# Multimodal extract-text endpoint -- Stage 1 of the 2-stage pipeline
# ---------------------------------------------------------------------------
@router.post("/media/extract-text")
async def extract_text_from_upload(
    file: UploadFile = File(...),
) -> dict:
    """
    Stage 1: Receive a citizen upload and return extracted plain text/context.

    Supported: txt, docx, pdf, png, jpg, jpeg.
    Not supported: audio/video.

    The caller appends the returned text to the question and then calls
    /api/search/ask or /api/search/ask/simple as usual (Stage 2).
    """
    import mimetypes

    from api.multimodal_preprocess import (
        MAX_FILE_BYTES,
        SUPPORTED_EXTENSIONS,
        classify_upload,
        extract_text_from_file,
    )

    if not file.filename:
        raise HTTPException(status_code=400, detail="Không có file được gửi lên.")

    mime_type = file.content_type or ""
    if not mime_type or mime_type == "application/octet-stream":
        guessed_mime, _ = mimetypes.guess_type(file.filename)
        mime_type = guessed_mime or mime_type or "application/octet-stream"

    source_type = classify_upload(file.filename, mime_type)
    if source_type == "unknown":
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise HTTPException(
            status_code=415,
            detail=(
                f"Định dạng '{mime_type}' của file '{file.filename}' chưa được hỗ trợ. "
                f"Chấp nhận: {supported}. Không hỗ trợ audio/video."
            ),
        )

    try:
        file_bytes = await file.read()
        if len(file_bytes) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"File '{file.filename}' quá lớn ({len(file_bytes) // 1024} KB). "
                    f"Giới hạn tối đa là {MAX_FILE_BYTES // 1024 // 1024} MB."
                ),
            )

        extracted_text, source_type = await extract_text_from_file(
            file_bytes=file_bytes,
            filename=file.filename,
            mime_type=mime_type,
        )
        pii = redact_upload_text(extracted_text or "")
        extracted_text = pii["text"]

        return {
            "extracted_text": extracted_text,
            "source_type": source_type,
            "filename": file.filename,
            "char_count": len(extracted_text),
            "mime_type": mime_type,
            "max_file_mb": MAX_FILE_BYTES // 1024 // 1024,
            "contains_pii": pii["contains_pii"],
            "pii_types": pii["pii_types"],
            "pii_counts": pii["pii_counts"],
            "pii_masked": pii["masked"],
            "retention": pii["retention"],
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"Multimodal extract-text error for '{file.filename}': {exc}")
        raise HTTPException(
            status_code=500,
            detail=(
                f"Không thể trích xuất văn bản từ file '{file.filename}'. "
                "Nếu đây là ảnh hoặc PDF scan, hãy kiểm tra GOOGLE_API_KEY/GEMINI_API_KEY; "
                "nếu là file văn bản, hãy kiểm tra file có bị hỏng hay không."
            ),
        ) from exc


@router.get("/search/ask-history")
async def get_ask_history(
    limit: int = 100,
    role: str | None = None,
    domain: str | None = None,
    department: str | None = None,
    grounding_status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    request: Request = None,
):
    """Admin/officer endpoint to view and filter ask history.

    PII upload metadata is restricted: officers only receive redacted counts/types.
    Citizens cannot list other users' ask history.
    """
    requester_role = get_request_role(request) if request else None
    if requester_role not in ("admin", "officer"):
        raise HTTPException(status_code=403, detail="Only admin and officers can view ask history")
    try:
        results = await list_ask_history(
            limit=limit,
            role=role,
            domain=domain,
            department=department,
            grounding_status=grounding_status,
            date_from=date_from,
            date_to=date_to,
        )
        if requester_role != "admin":
            for item in results:
                meta = item.get("file_upload_metadata") or {}
                if isinstance(meta, dict) and meta:
                    item["file_upload_metadata"] = {
                        "contains_pii": bool(meta.get("contains_pii")),
                        "pii_types": meta.get("pii_types") or meta.get("types") or [],
                        "pii_counts": meta.get("pii_counts") or meta.get("counts") or {},
                        "pii_masked": bool(meta.get("pii_masked") or meta.get("masked")),
                        "retention": meta.get("retention") or {
                            "retention_hours": 24,
                            "delete_after_use": True,
                            "scope": "temporary_upload_context",
                        },
                    }
                # Officers do not need full rag_trace payloads for routine support.
                if "rag_trace" in item:
                    item["rag_trace"] = None
        return {"total": len(results), "results": results}
    except Exception as exc:
        logger.error(f"Failed to list ask history: {exc}")
        raise HTTPException(status_code=500, detail="Cannot list ask history, check server logs for details") from exc
