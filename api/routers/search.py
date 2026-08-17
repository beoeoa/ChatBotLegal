import asyncio
import io
import json
import os
import re
import time
import unicodedata
import urllib.parse
import uuid
import weakref
from dataclasses import replace
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Mapping

import httpx
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from loguru import logger
from sqlalchemy.exc import SQLAlchemyError

from api import ask_idempotency
from api import conversation_service as conv_svc
from api.ask_progress import (
    ProgressCallback,
    ask_progress_enabled,
    cloud_fallback_reason,
    emit_ask_progress,
    local_fallback_enabled,
    safe_public_response_payload,
    stream_ask_progress,
)
from api.ask_role_rollout import enforce_ask_role_rollout
from api.auth import get_request_role, get_request_user_id, get_request_username
from api.legal_answer_completeness import assess_answer_completeness
from api.legal_answer_quality import (
    apply_claim_validation,
    build_claim_validation,
    build_evidence_coverage,
    clarifying_questions_for_gaps,
    effective_legal_date,
    extractive_conclusion_from_evidence,
    prepend_extractive_conclusion_when_supported,
    quality_preview,
    vietnamese_section_names,
)
from api.legal_answer_router import (
    PIPELINE_VERSION as ANSWER_PIPELINE_V2_VERSION,
)
from api.legal_answer_router import (
    is_answer_pipeline_v2_enabled,
    route_legal_answer,
)
from api.legal_answer_trust_log import build_public_trust_projection
from api.legal_citation_provenance import enrich_public_citation
from api.legal_determinism import build_data_release_id
from api.legal_domains import canonical_domain_decision, canonicalize_legal_domain
from api.legal_taxonomy import classify_topic, classify_topic_v1
from api.legal_evidence_relevance import rank_issue_evidence
from api.legal_exact_article import is_single_exact_article_plan
from api.legal_exact_retrieval import normalize_exact_identifier, plan_exact_lookup
from api.legal_form_catalog import FormCatalog, normalize_procedure_id
from api.legal_form_evidence import build_form_evidence_rows
from api.legal_grounding import validate_legal_references
from api.legal_official_procedure_evidence import (
    build_official_procedure_evidence,
)
from api.legal_problem_map import (
    build_hybrid_problem_map,
    build_legal_intent,
    problem_map_to_legal_issues,
)
from api.legal_provider_circuit import (
    StructuredProviderCircuit,
    StructuredProviderCircuitOpen,
)
from api.legal_provider_fallback import (
    NORMAL,
    SOURCE_VIEW_ONLY,
    VERIFIED_SOURCE_CONDENSED,
    answer_fallback_enabled,
    choose_verified_fallback_mode,
    classify_structured_provider_error,
)
from api.legal_provider_privacy import (
    ProviderEgressBlocked,
    ProviderEgressDecision,
    prepare_provider_egress,
    provider_mode,
)
from api.legal_question_policy import (
    answer_contract,
    classify_question,
    missing_answer_sections,
    render_answer_contract,
)
from api.legal_query_understanding import classify_legal_query
from api.legal_retrieval_policy import expanded_retrieval_reason
from api.legal_search_client import get_legal_search_client
from api.legal_section_grounding import (
    CurrentRequestEvidencePacket,
    aggregate_answer_sections,
    build_section_grounding_metric,
    format_public_validity_sync,
    is_section_grounding_enabled,
    issue_requires_expanded_support,
    plan_legal_issues,
    retrieval_domain_slug,
    select_eligible_evidence,
    validate_answer_section,
)
from api.legal_structured_answer import (
    AsyncModelCache,
    build_and_render_extractive_answer,
    build_compact_evidence_context,
    build_fallback_quality_trace,
    build_issue_coverage_matrix,
    build_structured_answer_prompt,
    derive_required_facets_by_issue,
    enforce_explicit_facet_claims,
    ensure_required_facet_issues,
    invoke_blocking_model_with_deadline,
    is_hard_legal_request,
    model_invocation_budget_seconds,
    optimized_profile_enabled,
    parse_structured_answer,
    remaining_generation_budget_seconds,
    render_structured_answer,
    safe_extractive_fallback,
    structured_context_max_chars,
    structured_generation_timeout_seconds,
    structured_model_options,
    structured_retrieval_timeout_seconds,
    structured_total_timeout_seconds,
    supplement_rule_source_diversity,
)
from api.legal_text_cleaning import project_legal_evidence_rows
from api.models import AskRequest, AskResponse, SearchRequest, SearchResponse
from api.observability import telemetry
from api.optimized_profile_rollout import enforce as enforce_optimized_profile_rollout
from api.source_gap_jobs import enqueue_answer_source_gap_notice
from api.upload_security import (
    UploadPolicy,
    UploadSecurityError,
    validate_upload,
)
from api.user_service import list_ask_history, log_ask_history
from api.utils.pii_detector import redact_upload_text
from open_notebook.ai.models import Model, model_manager
from open_notebook.ai.provision import provision_langchain_model
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
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.text_utils import extract_text_content


class _DeterministicPreflight(Exception):
    """Signal that a fully grounded extractive answer can skip the provider."""


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


def _model_provider_identity(model: Any) -> tuple[str, str]:
    provider = str(getattr(model, "provider", None) or "configured").strip()
    model_name = str(
        getattr(model, "name", None)
        or getattr(model, "model", None)
        or getattr(model, "id", None)
        or "configured-model"
    ).strip()
    return provider, model_name


def _prepare_ask_provider_egress(
    question: str,
    *models: Any,
) -> ProviderEgressDecision:
    """Redact once for every model path when any participating provider is cloud."""

    identities = [_model_provider_identity(model) for model in models if model]
    if not identities:
        return prepare_provider_egress(
            question,
            provider="configured",
            model="configured-model",
        )
    # Prefer the final-answer provider label, unless it is local while an
    # earlier strategy/answer model is cloud. Any cloud participant requires
    # the same redacted question at every graph/model boundary.
    chosen = identities[-1]
    for identity in reversed(identities):
        if provider_mode(identity[0]) == "cloud":
            chosen = identity
            break
    return prepare_provider_egress(
        question,
        provider=chosen[0],
        model=chosen[1],
    )


router = APIRouter()
_STRUCTURED_MODEL_CACHE = AsyncModelCache()
_STRUCTURED_PROVIDER_CIRCUIT = StructuredProviderCircuit.from_environment()
_STRUCTURED_MODEL_INVOCATION_SLOTS_BY_LOOP: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, asyncio.Semaphore
] = weakref.WeakKeyDictionary()


def _structured_model_invocation_slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slots = _STRUCTURED_MODEL_INVOCATION_SLOTS_BY_LOOP.get(loop)
    if slots is None:
        try:
            configured = int(
                os.getenv("LEGAL_STRUCTURED_PROVIDER_MAX_CONCURRENCY", "2")
            )
        except (TypeError, ValueError):
            configured = 2
        slots = asyncio.Semaphore(max(1, min(8, configured)))
        _STRUCTURED_MODEL_INVOCATION_SLOTS_BY_LOOP[loop] = slots
    return slots


async def _invoke_structured_model_with_capacity(
    model: Any,
    prompt: str,
    *,
    timeout: float,
) -> Any:
    """Bound provider concurrency and queue only within the request deadline.

    DeepSeek calls that exceed their deadline continue inside their transport
    worker briefly even after the request has fallen back. Two in-flight calls
    are supported. A third request may wait for a slot, but its wait consumes
    the same wall-clock budget; it never receives a fresh timeout and cannot
    push the response beyond the deterministic normal/hard SLA.
    """

    started = time.perf_counter()
    slots = _structured_model_invocation_slots()
    try:
        await asyncio.wait_for(
            slots.acquire(),
            timeout=max(0.05, timeout),
        )
    except asyncio.TimeoutError:
        raise asyncio.TimeoutError
    try:
        remaining = timeout - (time.perf_counter() - started)
        if remaining <= 0:
            raise asyncio.TimeoutError
        return await invoke_blocking_model_with_deadline(
            model, prompt, timeout=remaining
        )
    finally:
        slots.release()
LEGAL_SEARCH_URL = os.getenv(
    "LEGAL_SEARCH_URL", "http://127.0.0.1:8765"
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
            "message": (
                "Dịch vụ AI bên ngoài tạm thời không thể hoàn tất câu trả lời. "
                "Vui lòng thử lại sau."
            ),
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
        answer_status="source_gap",
        fallback_tier="support",
        canonical_domain=canonicalize_legal_domain(policy.get("detected_domain")),
        evidence_count=0,
        coverage_warning="Cần bổ sung thủ tục hoặc văn bản để xác định đúng căn cứ.",
        blocked_reason="approved_current_source_not_found",
        procedure_detail=proc,
        procedure_summary=(
            f"Tham chiếu quy trình: {proc.get('name')}" if proc else None
        ),
        recommended_forms=recommended_forms,
        citations=[],
        question_type=policy["question_type"],
        detected_domain=policy.get("detected_domain"),
        required_sections=policy.get("required_sections") or [],
        forms_unavailable=_forms_unavailable_for_question(
            question,
            recommended_forms,
        ),
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
                facet=issue.intent,
                priority=issue.priority,
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


def _append_request_exact_identifiers(
    issue_query: str,
    *,
    request_exact_plan: Any,
) -> str:
    """Carry one issue's verified identifiers into its own rewritten query."""

    current = plan_exact_lookup(issue_query)
    # A reviewed natural-language alias is an identity binding for the
    # eligibility gate, but the retrieval service also needs a literal
    # law/article token to build an exact packet.  Append the token when the
    # query contains only the alias; avoid duplicating an identifier that the
    # user already wrote explicitly.
    literal_query = normalize_exact_identifier(issue_query).casefold()

    def has_literal_pair(law_number: str, article_number: str) -> bool:
        law_token = normalize_exact_identifier(law_number).casefold()
        article_token = f"dieu{normalize_exact_identifier(article_number).casefold()}"
        return law_token in literal_query and article_token in literal_query

    suffix: list[str] = []
    if request_exact_plan.article_law_pairs:
        # Preserve reviewed law/article pairings when a natural-language
        # question contains more than one approved provision. Appending all
        # laws first and all Articles later would create a cross-product and
        # can bind each Article to the wrong instrument.
        for law_number, article_number in request_exact_plan.article_law_pairs:
            if not has_literal_pair(law_number, article_number):
                suffix.append(f"{law_number} Điều {article_number}")
    else:
        suffix.extend(
            law_number
            for law_number in request_exact_plan.law_numbers
            if law_number not in current.law_numbers
        )
        suffix.extend(
            f"Điều {article_number}"
            for article_number in request_exact_plan.article_numbers
            if article_number not in current.article_numbers
        )
    if request_exact_plan.clause_number and not current.clause_number:
        suffix.append(f"Khoản {request_exact_plan.clause_number}")
    if not suffix:
        return issue_query
    return f"{issue_query.rstrip(' ;')}; {'; '.join(suffix)}"


def _issue_local_exact_plan(
    issue_query: str,
    *,
    request_exact_plan: Any,
    issue_count: int,
) -> Any:
    """Resolve identity locally and never broadcast it to sibling issues."""

    local = plan_exact_lookup(issue_query)
    if local.law_number:
        return local
    # A single issue may inherit an identifier that the facet planner kept in
    # surrounding request wording. Multi-issue requests must resolve every
    # identity from their own span; otherwise one Article contaminates all
    # siblings and the omitted issue can never be retrieved.
    if (
        issue_count > 1
        and not local.law_number
        and len(request_exact_plan.law_numbers or ()) == 1
        and len(local.article_numbers or ()) == 1
    ):
        # A multi-issue request may state one law once and name one Article in
        # each issue span. Bind that single verified law to each local Article
        # instead of leaving both issues without an exact identity.
        law_number = request_exact_plan.law_numbers[0]
        article_number = local.article_numbers[0]
        return replace(
            local,
            law_number=law_number,
            law_numbers=(law_number,),
            article_law_pairs=((law_number, article_number),),
        )
    if issue_count == 1:
        return request_exact_plan
    return local


def _restore_exact_article_context_after_relevance(
    relevant_rows: list[dict[str, Any]],
    projected_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep a complete Article packet when its first child lost relevance.

    Retrieval attaches the assembled Article body to the first child chunk so
    the normal evidence payload stays small.  The relevance gate may exclude
    that first child (for example, because its heading is broad) even though
    the request explicitly names the Article.  In that case the remaining
    children still carry the same complete-packet identity, but no body reaches
    ``build_compact_evidence_context`` and the exact-Article gate fails closed.

    Reattach the already verified assembled body to the first retained child
    only for the same complete packet.  This does not make an ineligible
    document eligible, bypass validity, or broaden non-exact requests; it only
    preserves context for an exact Article whose packet was already verified by
    retrieval.  The full body remains internal evidence metadata.
    """

    if not relevant_rows or not projected_rows:
        return relevant_rows

    packet_context: dict[str, dict[str, Any]] = {}
    for row in projected_rows:
        packet_ref = str(row.get("exact_article_packet_ref") or "").strip()
        if (
            not packet_ref
            or str(row.get("exact_article_packet_status") or "") != "complete"
        ):
            continue
        assembled = str(row.get("exact_article_assembled_content") or "").strip()
        if not assembled:
            continue
        packet_context.setdefault(packet_ref, row)

    if not packet_context:
        return relevant_rows

    retained_by_packet: dict[str, list[dict[str, Any]]] = {}
    for row in relevant_rows:
        packet_ref = str(row.get("exact_article_packet_ref") or "").strip()
        if packet_ref in packet_context:
            retained_by_packet.setdefault(packet_ref, []).append(row)

    for packet_ref, rows in retained_by_packet.items():
        target = min(
            rows,
            key=lambda row: (
                int(row.get("exact_article_order"))
                if str(row.get("exact_article_order") or "").isdigit()
                else 2**31,
                int(row.get("chunk_id"))
                if str(row.get("chunk_id") or "").isdigit()
                else 2**31,
            ),
        )
        source = packet_context[packet_ref]
        assembled = str(source.get("exact_article_assembled_content") or "")
        target.update(
            {
                "parent_context": assembled,
                "parent_context_chars": len(assembled),
                "parent_context_original_chars": int(
                    source.get("parent_context_original_chars")
                    or source.get("exact_article_full_article_character_count")
                    or len(assembled)
                ),
                "parent_context_truncated": bool(
                    source.get("parent_context_truncated")
                ),
                "parent_context_reason": "complete_exact_article",
                "parent_context_primary": True,
                "exact_article_assembled_content": assembled,
                "exact_article_context_restored": True,
                "exact_article_serving_mode": source.get(
                    "exact_article_serving_mode"
                ),
                "exact_article_full_article_character_count": source.get(
                    "exact_article_full_article_character_count"
                ),
            }
        )
    return relevant_rows


async def _run_structured_section_orchestration(
    *,
    ask_request: AskRequest,
    request_id: str,
    question_policy: Mapping[str, Any],
    legal_as_of: str,
    strategy_model_id: str,
    answer_model_id: str,
    final_answer_model_id: str,
) -> tuple[list[Any], dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Run one batch retrieval and exactly one structured model generation."""

    del answer_model_id
    stage_started = time.perf_counter()
    logger.info("Feature005 stage=structured_start request_id={}", request_id)
    original_runtime_domain = (
        ask_request.domain
        or (_detect_question_domain(ask_request.question) or {}).get("slug")
        or ask_request.topic_domain
        or None
    )
    domain_decision = canonical_domain_decision(original_runtime_domain)
    selected_runtime_domain = domain_decision.canonical_domain
    required_sections = [
        str(item) for item in question_policy.get("required_sections") or []
    ]

    async def invoke_problem_map_model(prompt: str) -> str:
        model = await provision_langchain_model(
            prompt,
            strategy_model_id,
            "tools",
            max_tokens=1600,
            timeout=4.0,
            temperature=0.0,
            streaming=False,
            structured={"type": "json_object"},
        )
        message = await model.ainvoke(prompt)
        return clean_thinking_content(extract_text_content(message.content))

    v2_enabled = is_answer_pipeline_v2_enabled(str(ask_request.role or "citizen"))
    answer_route = route_legal_answer(ask_request.question) if v2_enabled else None
    v2_form_resolution: dict[str, Any] | None = None
    preserve_release_confirmed_seed = False
    if (
        answer_route is not None
        and answer_route.answer_route == "procedure_form"
    ):
        if answer_route.procedure_id:
            try:
                v2_form_resolution = _get_canonical_form_catalog().resolve_forms(
                    ask_request.question,
                    role=str(ask_request.role or "citizen"),
                    as_of=date.fromisoformat(str(legal_as_of)[:10]),
                    procedure_ids=[answer_route.procedure_id],
                    limit=12,
                )
            except (OSError, ValueError, TypeError):
                # Forms are optional evidence. Catalog failure is recorded and
                # fails closed; it never blocks retrieval of the governing law.
                v2_form_resolution = {
                    "recommended_forms": [],
                    "rejected_forms": [],
                    "forms_unavailable": True,
                    "data_gap_status": "CATALOG_UNAVAILABLE",
                    "data_gap_reasons": ["catalog_unavailable"],
                }
        try:
            from api.form_router_v3 import resolve_from_configured_release
            feature017_forms = resolve_from_configured_release(
                question=ask_request.question,
                audience=str(ask_request.role or "citizen"),
                legal_as_of=date.fromisoformat(str(legal_as_of)[:10]),
            )
            if feature017_forms is not None:
                if feature017_forms.get("router_mode") == "active":
                    v2_form_resolution = feature017_forms
                    resolved_procedure_id = str(feature017_forms.get("procedure_id") or "").strip() or None
                    feature017_packet = feature017_forms.get("evidence_packet") or {}
                    feature017_identity = (
                        feature017_forms.get("identity_confirmation")
                        or feature017_packet.get("identity_confirmation")
                        or {}
                    )
                    preserve_release_confirmed_seed = bool(
                        feature017_forms.get("status") == "resolved"
                        and resolved_procedure_id
                        and str(feature017_packet.get("release_id") or "").strip()
                        and feature017_identity.get("confirmed") is True
                    )
                    if resolved_procedure_id:
                        answer_route = replace(
                            answer_route,
                            procedure_id=resolved_procedure_id,
                        )
                        if preserve_release_confirmed_seed:
                            # Long official procedure names commonly contain
                            # commas. Generic legal issue splitting can mistake
                            # title fragments (or words such as "Ä‘iá»u chá»‰nh")
                            # for separate intents. Once Feature 017 confirms
                            # one released identity, bind the user's requested
                            # facet and full question back to one issue.
                            folded_question = _ascii_fold(ask_request.question)
                            confirmed_intent = (
                                "form"
                                if any(
                                    marker in folded_question
                                    for marker in ("bieu mau", "mau nao", "tai mau")
                                )
                                else "documents"
                                if any(
                                    marker in folded_question
                                    for marker in ("ho so", "chuan bi")
                                )
                                else "deadline"
                                if any(
                                    marker in folded_question
                                    for marker in ("thoi han", "bao lau")
                                )
                                else "fee"
                                if any(
                                    marker in folded_question
                                    for marker in ("le phi", "chi phi")
                                )
                                else "authority"
                                if any(
                                    marker in folded_question
                                    for marker in ("nop o dau", "co quan nao")
                                )
                                else "condition"
                                if "dieu kien" in folded_question
                                else "procedure"
                            )
                            base_issue = answer_route.issues[0]
                            answer_route = replace(
                                answer_route,
                                issues=(
                                    replace(
                                        base_issue,
                                        text=ask_request.question[:800],
                                        title=(
                                            base_issue.title[:300]
                                            or "Ná»™i dung thá»§ tá»¥c Ä‘Ã£ xÃ¡c nháº­n"
                                        ),
                                        query_text=ask_request.question[:2000],
                                        subject=(
                                            base_issue.subject[:300]
                                            or ask_request.question[:300]
                                        ),
                                        intent=confirmed_intent,
                                    ),
                                ),
                            )
                    if feature017_forms.get("status") == "clarification_required":
                        questions = tuple(
                            str(item) for item in feature017_forms.get("clarifying_questions") or []
                            if str(item).strip()
                        ) or ("Bạn muốn thực hiện thủ tục nào?",)
                        answer_route = replace(answer_route, clarifying_questions=questions)
                elif v2_form_resolution is not None:
                    # Shadow decisions are admin-trace only; they do not alter
                    # the public form set before separately approved activation.
                    v2_form_resolution["feature017_shadow"] = {
                        "status": feature017_forms.get("status"),
                        "procedure_id": feature017_forms.get("procedure_id"),
                        "form_ids": [
                            item.get("form_id")
                            for item in feature017_forms.get("recommended_forms") or []
                        ],
                        "reason": feature017_forms.get("reason"),
                    }
        except (OSError, RuntimeError, ValueError, TypeError, SQLAlchemyError):
            # Feature 017 is optional until its schema and active release are
            # explicitly activated; legacy fail-closed resolution stays intact.
            pass
    planner_enabled = (
        not v2_enabled
        and str(os.getenv("LEGAL_PROBLEM_MAP_LLM_ENABLED", "false"))
        .strip()
        .casefold()
        in {"1", "true", "yes", "on"}
    )
    if answer_route is not None:
        # In V2, requested sections are coverage facets inside the issue. They
        # must not become extra retrieval issues. Explicit numbered/quoted
        # questions have already been separated by the deterministic router.
        deterministic_seed = list(answer_route.issues)
    else:
        deterministic_seed = ensure_required_facet_issues(
            question=ask_request.question,
            issues=plan_legal_issues(ask_request.question, max_issues=8),
            required_sections=required_sections,
            max_issues=8,
        )
    problem_map = await build_hybrid_problem_map(
        ask_request.question,
        role=str(ask_request.role or "citizen"),
        required_sections=required_sections,
        location=("Hải Phòng" if selected_runtime_domain else None),
        invoke_model=invoke_problem_map_model if planner_enabled else None,
        timeout_seconds=4.0,
        seed_issues=deterministic_seed,
        preserve_seed_issues=preserve_release_confirmed_seed,
    )
    planned_issues = problem_map_to_legal_issues(
        problem_map,
        request_id=request_id,
    )
    if answer_route is not None and answer_route.clarifying_questions:
        issue = planned_issues[0]
        section = validate_answer_section(
            request_id=request_id,
            issue_id=issue.issue_id,
            title=issue.title,
            sources=[],
            limitation="Chưa đủ thông tin để xác định đúng thủ tục và biểu mẫu.",
            clarifying_question=answer_route.clarifying_questions[0],
            facet=issue.intent,
            priority=issue.priority,
        )
        aggregate = aggregate_answer_sections([section])
        metric = build_section_grounding_metric(
            request_id=request_id,
            statuses=[section.status],
            stage_timings_ms={
                "retrieval": 0,
                "provisioning": 0,
                "generation": 0,
                "validation": 0,
                "end_to_end": (time.perf_counter() - stage_started) * 1000,
            },
            repair_count=0,
            completed=True,
            error_category="none",
        )
        return [section], aggregate, [], {
            "pipeline_version": ANSWER_PIPELINE_V2_VERSION,
            "answer_route": answer_route.answer_route,
            "route_reason": answer_route.decision_reason,
            "problem_map": {
                "planner_mode": "deterministic_v2",
                "fallback_reason": None,
                "issue_count": 1,
                "query_count": 0,
                "missing_fact_count": 1,
                "branch_count": 0,
                "issues": [],
            },
            "clarifying_questions": list(answer_route.clarifying_questions),
            "issue_plan": [],
            "claim_validation": {
                "issues": [],
                "claim_grounding_ratio": 0.0,
                "coverage_ratio": 0.0,
                "quality_gate": {"pass": False},
                "fallback_reason": "clarification_required",
            },
            "validity_decision": {
                "state": "unknown_or_stale",
                "legal_as_of": legal_as_of,
                "filtered_reason_codes": [],
                "strict_current_answer": True,
            },
            "answer_mode": SOURCE_VIEW_ONLY,
            "provider_error_code": None,
            "metric": metric,
        }
    request_exact_plan = plan_exact_lookup(ask_request.question)
    single_exact_article_request = bool(
        len(planned_issues) == 1
        and is_single_exact_article_plan(request_exact_plan)
    )
    if single_exact_article_request and planned_issues:
        # A named Article is one indivisible retrieval unit. Requested facets
        # remain in the coverage matrix, but splitting them into several search
        # issues would duplicate the same full Article and could exhaust the
        # context budget before generation.
        planned_issues = [
            replace(
                planned_issues[0],
                title=(
                    f"Nội dung Điều {request_exact_plan.article_number} "
                    f"của {request_exact_plan.law_number}"
                ),
                query_text=ask_request.question,
                intent="rule",
            )
        ]
    issues = []
    issue_exact_plans: dict[str, Any] = {}
    for planned_issue in planned_issues:
        issue_exact_plan = _issue_local_exact_plan(
            planned_issue.query_text,
            request_exact_plan=request_exact_plan,
            issue_count=len(planned_issues),
        )
        issue_query = _append_request_exact_identifiers(
            planned_issue.query_text,
            request_exact_plan=issue_exact_plan,
        )
        bound_domain = retrieval_domain_slug(
            planned_issue.domain,
            selected_runtime_domain,
            issue_query,
        )
        bound_issue = replace(
                planned_issue,
                request_id=request_id,
                query_text=issue_query,
                domain=bound_domain or ("unknown" if planned_issue.domain == "administrative" else planned_issue.domain),
                relevance_topics=tuple(set(planned_issue.relevance_topics) | set(ask_request.detected_topics or []))
            )
        issues.append(bound_issue)
        issue_exact_plans[bound_issue.issue_id] = plan_exact_lookup(issue_query)
    exact_article_issue_ids = {
        issue_id
        for issue_id, plan in issue_exact_plans.items()
        if is_single_exact_article_plan(plan)
    }
    exact_article_request = bool(exact_article_issue_ids)
    problem_issue_by_id = {
        issue.issue_id: issue for issue in problem_map.legal_issues
    }
    batch_issues = []
    for issue in issues:
        problem_issue = problem_issue_by_id[issue.issue_id]
        issue_exact_plan = issue_exact_plans[issue.issue_id]
        issue_exact_article = issue.issue_id in exact_article_issue_ids
        batch_issues.append(
            {
                "issue_id": issue.issue_id,
                "query": issue.query_text,
                "queries": [
                    {
                        **query.model_dump(exclude_none=True),
                        "query": _append_request_exact_identifiers(
                            query.query,
                            request_exact_plan=issue_exact_plan,
                        ),
                    }
                    for query in problem_issue.queries[:1]
                ] if not issue_exact_article else [],
                # ``unknown`` is a policy state, not an indexed domain slug.
                # Exact document identifiers are still enforced by retrieval
                # and by the section eligibility gate below.
                "domain": selected_runtime_domain or None,
                "intent": issue.intent,
            }
        )
    required_facets_by_issue = derive_required_facets_by_issue(
        issues=issues,
        required_sections=required_sections,
    )
    hard_question = is_hard_legal_request(
        issues=issues,
        required_facets_by_issue=required_facets_by_issue,
    )

    def bounded_timeout(name: str, default: float, cap: float) -> float:
        try:
            configured = float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            configured = default
        return min(cap, max(0.05, configured))

    total_budget_seconds = structured_total_timeout_seconds(
        hard_question=hard_question,
        role=str(ask_request.role or "citizen"),
    )
    total_deadline = stage_started + total_budget_seconds
    core_timeout = bounded_timeout(
        "LEGAL_STRUCTURED_RETRIEVAL_TIMEOUT_SECONDS",
        structured_retrieval_timeout_seconds(
            "core", hard_question=hard_question, role=str(ask_request.role or "citizen")
        ),
        60.0,
    )
    retrieval_started = time.perf_counter()
    client = get_legal_search_client()
    retrieval_timed_out = False
    try:
        core = await asyncio.wait_for(
            client.search_batch(
                {
                    "request_id": request_id,
                    "as_of": legal_as_of,
                    "as_of_explicit": bool(
                        ask_request.legal_as_of or ask_request.event_date
                    ),
                    "issues": batch_issues,
                    "retrieval_tier": "core",
                    "audience": str(ask_request.role or "citizen"),
                    "include_trace": ask_request.role == "admin",
                    "ranking_strategy": "rrf_v2" if v2_enabled else "legacy_stack",
                    # The last real benchmark did not pass activation Gate C.
                    # V2 therefore keeps BGE off until a separately reviewed
                    # benchmark changes this server-controlled decision.
                    "enable_learned_reranker": False if v2_enabled else True,
                }
            ),
            timeout=min(
                core_timeout,
                max(0.05, total_deadline - time.perf_counter() - 1.0),
            ),
        )
    except asyncio.TimeoutError:
        retrieval_timed_out = True
        core = {"issues": []}
        logger.warning(
            "Feature005 stage=core_retrieval_timeout request_id={} timeout_s={}",
            request_id,
            core_timeout,
        )
    logger.info(
        "Feature005 stage=core_retrieval_done request_id={} elapsed_ms={:.0f} "
        "issue_count={} candidate_counts={} retrieval_service={}",
        request_id,
        (time.perf_counter() - retrieval_started) * 1000,
        len(core.get("issues") or []),
        [
            len(item.get("results") or [])
            for item in core.get("issues") or []
            if isinstance(item, Mapping)
        ],
        getattr(client, "base_url", "in-process-test-adapter"),
    )
    candidates_by_issue: dict[str, list[dict[str, Any]]] = {
        str(item["issue_id"]): [dict(row) for row in item.get("results") or []]
        for item in core.get("issues") or []
    }
    runtime_versions = dict(core.get("versions") or {})
    exact_packets_by_issue: dict[str, list[dict[str, Any]]] = {
        issue.issue_id: [] for issue in issues
    }
    validity_filtered_reasons: dict[str, int] = {}

    def record_validity_sync(payload: Mapping[str, Any]) -> None:
        summaries: list[Mapping[str, Any]] = []
        top_level = payload.get("validity_sync")
        if isinstance(top_level, Mapping):
            summaries.append(top_level)
        else:
            summaries.extend(
                item["validity_sync"]
                for item in payload.get("issues") or []
                if isinstance(item, Mapping)
                and isinstance(item.get("validity_sync"), Mapping)
            )
        for summary in summaries:
            for reason, count in (
                summary.get("filtered_reasons") or {}
            ).items():
                key = str(reason or "unknown")
                validity_filtered_reasons[key] = (
                    validity_filtered_reasons.get(key, 0) + int(count or 0)
                )

    def record_exact_packets(payload: Mapping[str, Any]) -> None:
        for item in payload.get("issues") or []:
            if not isinstance(item, Mapping):
                continue
            issue_id = str(item.get("issue_id") or "")
            if issue_id not in exact_packets_by_issue:
                continue
            known = {
                str(packet.get("packet_ref") or "")
                + ":"
                + str(packet.get("status") or "")
                for packet in exact_packets_by_issue[issue_id]
            }
            for packet in item.get("exact_article_packets") or []:
                if not isinstance(packet, Mapping):
                    continue
                identity = (
                    str(packet.get("packet_ref") or "")
                    + ":"
                    + str(packet.get("status") or "")
                )
                if identity not in known:
                    exact_packets_by_issue[issue_id].append(dict(packet))
                    known.add(identity)

    record_exact_packets(core)
    record_validity_sync(core)
    decision_trace: dict[str, list[dict[str, Any]]] = {}
    content_decision_trace: dict[str, list[dict[str, Any]]] = {}

    def eligible_rows(issue: Any) -> list[dict[str, Any]]:
        projected, _ = project_legal_evidence_rows(
            candidates_by_issue.get(issue.issue_id, [])
        )
        decisions = select_eligible_evidence(
            request_id=request_id,
            issue=issue,
            candidates=projected,
            legal_as_of=legal_as_of,
        )
        decision_trace[issue.issue_id] = [
            {
                "source_id": decision.source_id,
                "status": decision.status,
                "reason": decision.reason,
            }
            for decision in decisions
        ]
        legally_eligible = [
            dict(decision.source_metadata)
            for decision in decisions
            if decision.eligible
        ]
        relevance_context = " ".join(
            value
            for value in (
                issue.title,
                issue.query_text,
                issue.text,
                issue.subject,
                *issue.facts,
            )
            if value
        )
        if issue.issue_id in exact_article_issue_ids:
            # Every chunk of the verified Article is required context. A
            # content-relevance classifier may rank broad questions, but it
            # must not discard a Khoản/Điểm from an explicitly named Article.
            relevant = legally_eligible
            content_decisions = [
                {
                    "source_id": row.get("source_id")
                    or row.get("chunk_id")
                    or row.get("id"),
                    "decision": "keep",
                    "reason": "complete_exact_article_packet",
                    "penalty": 0.0,
                }
                for row in legally_eligible
            ]
        elif v2_enabled:
            # The legal/validity/role hard gate above is authoritative in V2.
            # Relevance is a coverage/ranking signal and cannot silently drop
            # an otherwise eligible source for a second time.
            relevant = legally_eligible
            content_decisions = [
                {
                    "source_id": row.get("source_id")
                    or row.get("chunk_id")
                    or row.get("id"),
                    "decision": "keep",
                    "reason": "coverage_only_v2",
                    "penalty": 0.0,
                }
                for row in legally_eligible
            ]
        else:
            relevant, content_decisions = rank_issue_evidence(
                relevance_context,
                legally_eligible,
                issue_intent=issue.intent,
                relevance_topics=issue.relevance_topics,
            )
        if issue.issue_id in exact_article_issue_ids:
            # The retrieval service attaches the assembled Article body to its
            # first child.  That child may be the one content-relevance labels
            # as broad, while the remaining children are still the same
            # verified exact packet.  Preserve the packet body on a retained
            # child before context construction.
            relevant = _restore_exact_article_context_after_relevance(
                relevant,
                projected,
            )
        content_decision_trace[issue.issue_id] = content_decisions
        candidates_by_issue[issue.issue_id] = [dict(row) for row in projected]
        return [dict(row) for row in relevant]

    selected_by_issue = {issue.issue_id: eligible_rows(issue) for issue in issues}
    missing = [
        issue
        for issue in issues
        if (
            not selected_by_issue[issue.issue_id]
            or issue_requires_expanded_support(
                issue,
                selected_by_issue[issue.issue_id],
            )
        )
    ]
    expanded_used = False
    full_corpus_used = False
    if missing and not retrieval_timed_out:
        expanded_used = True
        missing_ids = {issue.issue_id for issue in missing}
        expanded_timeout = bounded_timeout(
            "LEGAL_STRUCTURED_EXPANDED_RETRIEVAL_TIMEOUT_SECONDS",
            structured_retrieval_timeout_seconds(
                "expanded", hard_question=hard_question, role=str(ask_request.role or "citizen")
            ),
            structured_retrieval_timeout_seconds(
                "expanded", hard_question=hard_question, role=str(ask_request.role or "citizen")
            ),
        )
        try:
            expanded = await asyncio.wait_for(
                client.search_batch(
                    {
                        "request_id": request_id,
                        "as_of": legal_as_of,
                        "as_of_explicit": bool(
                            ask_request.legal_as_of or ask_request.event_date
                        ),
                        "issues": [
                            item
                            for item in batch_issues
                            if item["issue_id"] in missing_ids
                        ],
                        "retrieval_tier": "expanded",
                        "audience": str(ask_request.role or "citizen"),
                        "include_trace": ask_request.role == "admin",
                        "ranking_strategy": "rrf_v2" if v2_enabled else "legacy_stack",
                        "enable_learned_reranker": False if v2_enabled else True,
                    }
                ),
                timeout=min(
                    expanded_timeout,
                    max(0.05, total_deadline - time.perf_counter() - 1.0),
                ),
            )
        except asyncio.TimeoutError:
            if not any(candidates_by_issue.values()):
                retrieval_timed_out = True
            expanded = {"issues": []}
            logger.warning(
                "Feature005 stage=expanded_retrieval_timeout request_id={} timeout_s={}",
                request_id,
                expanded_timeout,
            )
        logger.info(
            "Feature005 stage=expanded_retrieval_done request_id={} elapsed_ms={:.0f}",
            request_id,
            (time.perf_counter() - retrieval_started) * 1000,
        )
        for item in expanded.get("issues") or []:
            issue_id = str(item.get("issue_id") or "")
            candidates_by_issue.setdefault(issue_id, []).extend(
                dict(row) for row in item.get("results") or []
            )
        record_exact_packets(expanded)
        record_validity_sync(expanded)
        selected_by_issue = {issue.issue_id: eligible_rows(issue) for issue in issues}

    # The final retrieval tier broadens only the domain boundary. It still uses
    # the expanded active/reviewed collection and the exact same validity,
    # authority, hierarchy and role eligibility gates below. Exact-article
    # requests never use this route because their identity must remain exact.
    full_corpus_missing = [
        issue
        for issue in issues
        if issue.issue_id not in exact_article_issue_ids
        and (
            not selected_by_issue.get(issue.issue_id)
            or issue_requires_expanded_support(
                issue,
                selected_by_issue.get(issue.issue_id, []),
            )
        )
    ]
    if v2_enabled and full_corpus_missing and not retrieval_timed_out:
        full_corpus_ids = {issue.issue_id for issue in full_corpus_missing}
        remaining_budget = total_deadline - time.perf_counter() - 1.0
        if remaining_budget > 0.05:
            full_corpus_used = True
            full_corpus_issues = [
                {**item, "domain": None}
                for item in batch_issues
                if item["issue_id"] in full_corpus_ids
            ]
            try:
                full_corpus = await asyncio.wait_for(
                    client.search_batch(
                        {
                            "request_id": request_id,
                            "as_of": legal_as_of,
                            "as_of_explicit": bool(
                                ask_request.legal_as_of or ask_request.event_date
                            ),
                            "issues": full_corpus_issues,
                            "retrieval_tier": "expanded",
                            "audience": str(ask_request.role or "citizen"),
                            "include_trace": ask_request.role == "admin",
                            "ranking_strategy": "rrf_v2",
                            "enable_learned_reranker": False,
                        }
                    ),
                    timeout=min(expanded_timeout, max(0.05, remaining_budget)),
                )
            except asyncio.TimeoutError:
                full_corpus = {"issues": []}
                logger.warning(
                    "AnswerV3 stage=full_corpus_retrieval_timeout request_id={}",
                    request_id,
                )
            for item in full_corpus.get("issues") or []:
                issue_id = str(item.get("issue_id") or "")
                candidates_by_issue.setdefault(issue_id, []).extend(
                    dict(row) for row in item.get("results") or []
                )
            record_exact_packets(full_corpus)
            record_validity_sync(full_corpus)
            selected_by_issue = {
                issue.issue_id: eligible_rows(issue) for issue in issues
            }

    exact_article_gate = {
        "required": exact_article_request,
        "ready": True,
        "reason_codes": [],
        "issues": {},
    }
    if exact_article_request:
        for issue in issues:
            if issue.issue_id not in exact_article_issue_ids:
                continue
            packets = exact_packets_by_issue.get(issue.issue_id, [])
            complete = [
                packet for packet in packets if packet.get("status") == "complete"
            ]
            exact_article_gate["issues"][issue.issue_id] = {
                "packet_count": len(packets),
                "complete_packet_count": len(complete),
                "reason_codes": list(
                    dict.fromkeys(
                        str(reason)
                        for packet in packets
                        for reason in packet.get("reason_codes") or []
                    )
                ),
                "missing_chunk_indexes": sorted(
                    {
                        int(index)
                        for packet in packets
                        for index in packet.get("missing_chunk_indexes") or []
                    }
                ),
                "missing_structural_units": list(
                    dict.fromkeys(
                        str(unit)
                        for packet in packets
                        for unit in packet.get("missing_structural_units") or []
                    )
                ),
            }
            if not complete:
                exact_article_gate["ready"] = False
                selected_by_issue[issue.issue_id] = []
        if not exact_article_gate["ready"]:
            exact_article_gate["reason_codes"].append(
                "exact_article_packet_incomplete"
            )

    # Add reviewed official procedure publications only after the corpus has
    # passed approval, validity, hierarchy, diversity and optional expanded
    # retrieval.  These rows are issue-bound and pass the same eligibility and
    # relevance gates; they cannot reorder or make an ineligible corpus source
    # eligible.
    if retrieval_timed_out:
        official_rows, official_procedure_trace = [], {
            "status": "skipped_retrieval_timeout",
            "candidate_count": 0,
        }
    elif len(exact_article_issue_ids) == len(issues):
        official_rows, official_procedure_trace = [], {
            "status": "skipped_exact_article_request",
            "candidate_count": 0,
        }
    else:
        non_exact_issues = [
            issue
            for issue in issues
            if issue.issue_id not in exact_article_issue_ids
        ]
        form_evidence_packet = (
            (v2_form_resolution or {}).get("evidence_packet") or {}
        )
        form_identity_confirmation = (
            (v2_form_resolution or {}).get("identity_confirmation")
            or form_evidence_packet.get("identity_confirmation")
            or {}
        )
        release_confirmed_procedure_id = (
            str((v2_form_resolution or {}).get("procedure_id") or "").strip()
            if (
                (v2_form_resolution or {}).get("status") == "resolved"
                and str(form_evidence_packet.get("release_id") or "").strip()
                and form_identity_confirmation.get("confirmed") is True
            )
            else None
        )
        official_rows, official_procedure_trace = await asyncio.to_thread(
            build_official_procedure_evidence,
            issues=non_exact_issues,
            question=ask_request.question,
            legal_as_of=legal_as_of,
            procedure_id=release_confirmed_procedure_id,
            required_facets_by_issue=required_facets_by_issue,
        )
    official_by_issue: dict[str, list[dict[str, Any]]] = {}
    for row in official_rows:
        official_by_issue.setdefault(str(row.get("issue_id") or ""), []).append(row)

    for issue in issues:
        adapter_candidates, _ = project_legal_evidence_rows(
            official_by_issue.get(issue.issue_id, [])
        )
        adapter_decisions = select_eligible_evidence(
            request_id=request_id,
            issue=issue,
            candidates=adapter_candidates,
            legal_as_of=legal_as_of,
        )
        adapter_eligible = [
            dict(decision.source_metadata)
            for decision in adapter_decisions
            if decision.eligible
        ]
        if v2_enabled:
            adapter_relevant = adapter_eligible
            adapter_content_decisions = [
                {
                    "source_id": row.get("source_id")
                    or row.get("chunk_id")
                    or row.get("id"),
                    "decision": "keep",
                    "reason": "coverage_only_v2",
                    "penalty": 0.0,
                }
                for row in adapter_eligible
            ]
        else:
            adapter_relevant, adapter_content_decisions = rank_issue_evidence(
                " ".join(
                    value
                    for value in (issue.title, issue.query_text, issue.subject)
                    if value
                ),
                adapter_eligible,
                issue_intent=issue.intent,
                relevance_topics=issue.relevance_topics,
            )
        decision_trace.setdefault(issue.issue_id, []).extend(
            {
                "source_id": decision.source_id,
                "status": decision.status,
                "reason": decision.reason,
                "source_adapter": "official_procedure_evidence_v1",
            }
            for decision in adapter_decisions
        )
        content_decision_trace.setdefault(issue.issue_id, []).extend(
            {
                **dict(decision),
                "source_adapter": "official_procedure_evidence_v1",
            }
            for decision in adapter_content_decisions
        )

        existing = list(selected_by_issue.get(issue.issue_id, []))
        # Decree 217/2026/ND-CP replaced the current building-permit procedure
        # from 2026-07-01.  A stale corpus status must not let Decree 175 appear
        # beside the reviewed current source.  Match metadata identity only;
        # historical analysis before the effective date is untouched.
        if any(
            str(row.get("law_number") or "") == "217/2026/NĐ-CP"
            for row in adapter_relevant
        ):
            kept: list[dict[str, Any]] = []
            for row in existing:
                identity = " ".join(
                    str(row.get(field) or "")
                    for field in ("law_number", "document_number", "document_title")
                )
                if "175/2024/NĐ-CP" in identity:
                    content_decision_trace[issue.issue_id].append(
                        {
                            "source_id": row.get("source_id") or row.get("chunk_id"),
                            "decision": "reject",
                            "reason": "superseded_by_217_2026_nd_cp",
                        }
                    )
                    continue
                kept.append(row)
            existing = kept

        merged: list[dict[str, Any]] = []
        seen_source_ids: set[str] = set()
        for row in [*adapter_relevant, *existing]:
            source_id = str(
                row.get("source_id")
                or row.get("chunk_id")
                or row.get("id")
                or ""
            )
            if source_id and source_id in seen_source_ids:
                continue
            if source_id:
                seen_source_ids.add(source_id)
            merged.append(dict(row))
        selected_by_issue[issue.issue_id] = merged

    retrieval_ms = (time.perf_counter() - retrieval_started) * 1000
    if v2_form_resolution is not None and answer_route is not None and issues:
        resolved_procedure_id = str(
            v2_form_resolution.get("procedure_id") or answer_route.procedure_id or ""
        )
        form_evidence = build_form_evidence_rows(
            request_id=request_id,
            issue=issues[0],
            procedure_id=resolved_procedure_id,
            forms=list(v2_form_resolution.get("recommended_forms") or []),
            legal_as_of=legal_as_of,
        )
        selected_by_issue[issues[0].issue_id] = [
            *selected_by_issue.get(issues[0].issue_id, []),
            *form_evidence,
        ]
    evidence_packets: dict[str, CurrentRequestEvidencePacket] = {}
    if v2_enabled:
        # Materialize the issue boundary before prompt construction.  This is
        # the final fail-closed guard against a sibling issue's evidence being
        # broadcast into the current issue by later aggregation code.
        evidence_packets = {
            issue.issue_id: CurrentRequestEvidencePacket(
                request_id=request_id,
                issue_id=issue.issue_id,
                sources=list(selected_by_issue.get(issue.issue_id, [])),
                retrieval_tier=(
                    "full_corpus"
                    if full_corpus_used
                    else "expanded"
                    if expanded_used
                    else "core"
                ),
            )
            for issue in issues
        }
        all_results = [
            dict(row)
            for issue in issues
            for row in evidence_packets[issue.issue_id].eligible_sources
        ]
    else:
        all_results = [
            row
            for issue in issues
            for row in selected_by_issue.get(issue.issue_id, [])
        ]
    context_limit = structured_context_max_chars(
        hard_question=hard_question or exact_article_request,
        role=str(ask_request.role or "citizen"),
    )
    context, evidence_by_id = build_compact_evidence_context(
        issues=issues,
        evidence_rows=all_results,
        max_chars=context_limit,
    )
    if exact_article_request and exact_article_gate["ready"]:
        complete_context_issues = {
            str(item.get("issue_id") or "")
            for item in evidence_by_id.values()
            if item.get("exact_article_context_complete") is True
        }
        missing_context_issues = [
            issue_id
            for issue_id in exact_article_issue_ids
            if issue_id not in complete_context_issues
        ]
        if missing_context_issues:
            exact_article_gate["ready"] = False
            exact_article_gate["reason_codes"].append(
                "exact_article_context_not_fully_loaded"
            )
            exact_article_gate["missing_context_issue_ids"] = (
                missing_context_issues
            )
            for issue_id in missing_context_issues:
                selected_by_issue[issue_id] = []
            all_results = [
                row
                for issue in issues
                for row in selected_by_issue.get(issue.issue_id, [])
            ]
            context, evidence_by_id = build_compact_evidence_context(
                issues=issues,
                evidence_rows=all_results,
                max_chars=context_limit,
            )
    coverage_matrix = build_issue_coverage_matrix(
        issues=issues,
        evidence_by_id=evidence_by_id,
        required_facets_by_issue=required_facets_by_issue,
    )
    required_fact_ids_by_issue = {
        issue.issue_id: list(issue.required_fact_ids)
        for issue in problem_map.legal_issues
        if issue.required_fact_ids
    }
    for issue_id, fact_ids in required_fact_ids_by_issue.items():
        for facet in coverage_matrix.get(issue_id, ()):
            facet["requires_user_fact"] = True
            facet["missing_fact_ids"] = list(fact_ids)
            facet["coverage_status"] = "requires_user_fact"
    generation_ms = 0.0
    provisioning_ms = 0.0
    validation_ms = 0.0
    error_category = "none"
    answer_mode = NORMAL
    provider_error_code: str | None = None
    structured_trace: dict[str, Any] = {
        "accepted_claim_count": 0,
        "rejected_claim_count": 0,
        "issues": [],
    }
    expired_source_blocked = bool(
        exact_article_request and validity_filtered_reasons.get("expired", 0)
    )
    if not evidence_by_id:
        if not answer_fallback_enabled():
            raise HTTPException(
                status_code=502,
                detail="Answer fallback disabled for local diagnostic; no eligible legal evidence was retrieved.",
            )
        error_category = "retrieval_unavailable"
        if expired_source_blocked:
            limitation = (
                f"Văn bản {request_exact_plan.law_number} đã hết hiệu lực – "
                "không dùng để trả lời hiện hành."
            )
            sections = [
                validate_answer_section(
                    request_id=request_id,
                    issue_id=issue.issue_id,
                    title=issue.title,
                    sources=[],
                    limitation=limitation,
                    facet=issue.intent,
                )
                for issue in issues
            ]
            aggregate = aggregate_answer_sections(sections)
        else:
            sections, aggregate = safe_extractive_fallback(
                request_id=request_id,
                issues=issues,
                evidence_by_id=evidence_by_id,
                role=str(ask_request.role or "citizen"),
            )
            next_question = (
                "Bạn muốn thực hiện thủ tục cụ thể nào, và trường hợp của bạn "
                "đang ở bước nộp hồ sơ hay bổ sung hồ sơ?"
                if answer_route is not None
                and answer_route.answer_route == "procedure_form"
                else "Bạn có thể cho biết thủ tục/văn bản, địa bàn và thời điểm của sự việc không?"
            )
            aggregate = {
                **aggregate,
                "answer": (
                    "Tôi chưa xác định được căn cứ hiện hành đủ chắc chắn cho toàn bộ "
                    "câu hỏi sau khi đã mở rộng tra cứu trong kho nguồn chính thức đã duyệt. "
                    "Tôi sẽ không tự suy đoán điều luật, thời hạn, lệ phí hoặc biểu mẫu.\n\n"
                    f"{next_question}"
                ),
            }
        if (
            exact_article_request
            and not exact_article_gate["ready"]
            and not expired_source_blocked
        ):
            missing_indexes = sorted(
                {
                    int(index)
                    for issue_gate in exact_article_gate["issues"].values()
                    for index in issue_gate.get("missing_chunk_indexes") or []
                }
            )
            missing_units = list(
                dict.fromkeys(
                    str(unit)
                    for issue_gate in exact_article_gate["issues"].values()
                    for unit in issue_gate.get("missing_structural_units") or []
                )
            )
            detail_parts = []
            if missing_indexes:
                detail_parts.append(
                    "chunk " + ", ".join(str(index) for index in missing_indexes)
                )
            if missing_units:
                detail_parts.append(
                    "phần " + ", ".join(missing_units[:8])
                )
            detail = (
                " Hệ thống phát hiện thiếu " + "; ".join(detail_parts) + "."
                if detail_parts
                else ""
            )
            aggregate = {
                **aggregate,
                "answer": (
                    f"Chưa thể trả lời Điều {request_exact_plan.article_number} "
                    f"của văn bản {request_exact_plan.law_number} vì kho dữ liệu "
                    "chưa có một gói Điều đầy đủ và liên tục."
                    f"{detail} Hệ thống không dùng một chunk riêng lẻ để suy ra "
                    "toàn bộ Điều luật."
                ),
            }
        structured_trace = build_fallback_quality_trace(
            issues=issues,
            coverage_matrix=coverage_matrix,
            reason=error_category,
        )
        structured_trace["expired_source_blocked"] = expired_source_blocked
        structured_trace["validity_filtered_reasons"] = sorted(
            validity_filtered_reasons
        )
    else:
        # Prepare the validated deterministic fallback before starting the
        # provider clock. If several provider calls time out together, doing
        # this work afterward can hold the GIL and delay another request's
        # asyncio deadline. Precomputation keeps the 24-second deadline real.
        fallback_started = time.perf_counter()
        extractive_fallback = await asyncio.to_thread(
            build_and_render_extractive_answer,
            request_id=request_id,
            issues=issues,
            evidence_by_id=evidence_by_id,
            role=str(ask_request.role or "citizen"),
            coverage_matrix=coverage_matrix,
        )
        validation_ms += (time.perf_counter() - fallback_started) * 1000

        def verified_provider_fallback(
            reason: str,
        ) -> tuple[list[Any], dict[str, Any], dict[str, Any], str]:
            """Render only the strongest deterministic output proven safe."""

            if not answer_fallback_enabled():
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Answer fallback disabled for local diagnostic; "
                        f"provider answer was not accepted ({reason})."
                    ),
                )

            mode = choose_verified_fallback_mode(
                question=ask_request.question,
                evidence_by_id=evidence_by_id,
                quality_trace=extractive_fallback[2],
            )
            if mode == VERIFIED_SOURCE_CONDENSED:
                fallback_sections, fallback_aggregate, fallback_trace = (
                    extractive_fallback
                )
                fallback_trace = dict(fallback_trace)
            else:
                fallback_sections, fallback_aggregate = safe_extractive_fallback(
                    request_id=request_id,
                    issues=issues,
                    evidence_by_id=evidence_by_id,
                    role=str(ask_request.role or "citizen"),
                )
                fallback_trace = build_fallback_quality_trace(
                    issues=issues,
                    coverage_matrix=coverage_matrix,
                    reason=reason,
                )
            fallback_trace["fallback_reason"] = reason
            return (
                fallback_sections,
                fallback_aggregate,
                fallback_trace,
                mode,
            )

        # The optimized profile has a deterministic, already validated answer
        # available before provider provisioning.  When every requested facet
        # is covered, using that answer directly avoids an unnecessary timeout
        # or malformed JSON response while preserving the same grounding and
        # citation gates used by provider output.
        preflight_coverage = float(
            (extractive_fallback[2] or {}).get("coverage_ratio") or 0.0
        )
        preflight_deterministic = False
        preflight_issues = list((extractive_fallback[2] or {}).get("issues") or [])
        preflight_facets_complete = bool(preflight_issues) and all(
            not item.get("missing_facets") and not item.get("unavailable_facets")
            for item in preflight_issues
        )
        preflight_completeness = assess_answer_completeness(
            question=ask_request.question,
            answer=str(extractive_fallback[1].get("answer") or ""),
            sources=all_results,
            orchestration_trace=extractive_fallback[2],
        )
        if (
            optimized_profile_enabled(str(ask_request.role or "citizen"))
            and preflight_coverage >= 1.0
            and preflight_facets_complete
            and preflight_completeness.get("status") == "complete"
            and str(extractive_fallback[1].get("grounding_status") or "")
            == "fully_grounded"
            and bool((extractive_fallback[2].get("quality_gate") or {}).get("pass"))
            and choose_verified_fallback_mode(
                question=ask_request.question,
                evidence_by_id=evidence_by_id,
                quality_trace=extractive_fallback[2],
            )
            == VERIFIED_SOURCE_CONDENSED
        ):
            sections, aggregate, structured_trace = extractive_fallback
            structured_trace = dict(structured_trace)
            preflight_deterministic = True
            answer_mode = NORMAL
            provider_error_code = None

        prompt = build_structured_answer_prompt(
            question=ask_request.question,
            role=str(ask_request.role or "citizen"),
            context=context,
            coverage_matrix=coverage_matrix,
        )
        generation_started = time.perf_counter()
        invocation_started: float | None = None
        try:
            if preflight_deterministic:
                structured_trace["preflight_deterministic"] = True
                structured_trace["preflight_reason"] = "fully_grounded_extractive"
                raise _DeterministicPreflight
            if (
                not ask_request.offline_mode
                and not _STRUCTURED_PROVIDER_CIRCUIT.allow(final_answer_model_id)
            ):
                raise StructuredProviderCircuitOpen(
                    _STRUCTURED_PROVIDER_CIRCUIT.reason(final_answer_model_id)
                    or "provider_failure"
                )
            generation_timeout = structured_generation_timeout_seconds(
                hard_question=hard_question,
                role=str(ask_request.role or "citizen"),
            )
            remaining_total_budget = max(
                0.0, total_deadline - time.perf_counter() - 0.5
            )
            if remaining_total_budget < 1.0:
                error_category = "total_budget_exhausted"
                sections, aggregate, structured_trace = extractive_fallback
                structured_trace["fallback_reason"] = error_category
                logger.warning(
                    "Feature005 stage=total_budget_exhausted request_id={}",
                    request_id,
                )
                raise StructuredProviderCircuitOpen(error_category)
            generation_timeout = min(
                generation_timeout,
                remaining_total_budget,
            )
            model_options = structured_model_options(
                generation_timeout,
                role=str(ask_request.role or "citizen"),
                hard_question=hard_question,
            )
            logger.info(
                "Feature005 stage=generation_start request_id={} timeout_s={} "
                "prompt_chars={} context_chars={} evidence_count={} issue_count={} facet_count={}",
                request_id,
                generation_timeout,
                len(prompt),
                len(context),
                len(evidence_by_id),
                len(issues),
                sum(len(values) for values in required_facets_by_issue.values()),
            )
            provisioning_started = time.perf_counter()
            model = None
            if not ask_request.offline_mode:
                model = await _STRUCTURED_MODEL_CACHE.get(
                    (
                        final_answer_model_id,
                        int(model_options.get("max_tokens") or 0),
                        generation_timeout,
                        "structured" in model_options,
                    ),
                    lambda: provision_langchain_model(
                        prompt,
                        final_answer_model_id,
                        "tools",
                        **model_options,
                    ),
                )
            provisioning_ms = (
                time.perf_counter() - provisioning_started
            ) * 1000
            remaining_timeout = remaining_generation_budget_seconds(
                generation_started, generation_timeout
            )
            logger.info(
                "Feature005 stage=model_provisioned request_id={} elapsed_ms={:.0f} remaining_s={:.1f}",
                request_id,
                provisioning_ms,
                remaining_timeout,
            )
            if remaining_timeout <= 0:
                raise asyncio.TimeoutError
            invocation_timeout = min(
                remaining_timeout,
                model_invocation_budget_seconds(generation_timeout),
            )
            invocation_started = time.perf_counter()
            if ask_request.offline_mode:
                raw = await asyncio.wait_for(
                    _call_ollama(ask_request.offline_model, prompt),
                    timeout=invocation_timeout,
                )
            else:
                message = await _invoke_structured_model_with_capacity(
                    model, prompt, timeout=invocation_timeout
                )
                raw = clean_thinking_content(extract_text_content(message.content))
                _STRUCTURED_PROVIDER_CIRCUIT.record_success(final_answer_model_id)
            generation_ms = (time.perf_counter() - invocation_started) * 1000
            logger.info(
                "Feature005 stage=generation_done request_id={} elapsed_ms={:.0f} raw_preview={}",
                request_id,
                generation_ms,
                raw[:500] if raw else "None",
            )
            validation_started = time.perf_counter()
            output = parse_structured_answer(raw)
            if not v2_enabled:
                # Rollback path keeps the former deterministic claim repair and
                # source supplementation. V2 sends model output directly to the
                # single claim/citation validator below; coverage cannot rewrite
                # or remove a separately valid claim.
                output = enforce_explicit_facet_claims(
                    issues=issues,
                    output=output,
                    evidence_by_id=evidence_by_id,
                    coverage_matrix=coverage_matrix,
                )
                output = supplement_rule_source_diversity(
                    request_id=request_id,
                    issues=issues,
                    output=output,
                    evidence_by_id=evidence_by_id,
                    coverage_matrix=coverage_matrix,
                )
            sections, aggregate, structured_trace = render_structured_answer(
                request_id=request_id,
                issues=issues,
                output=output,
                evidence_by_id=evidence_by_id,
                role=str(ask_request.role or "citizen"),
                coverage_matrix=coverage_matrix,
            )
            gate_pass = bool((structured_trace.get("quality_gate") or {}).get("pass"))
            logger.info("Quality gate check: pass={} quality_gate={} trace={}", gate_pass, structured_trace.get("quality_gate"), structured_trace)
            if not gate_pass:
                error_category = "invalid_output"
                provider_error_code = error_category
                (
                    sections,
                    aggregate,
                    structured_trace,
                    answer_mode,
                ) = verified_provider_fallback(error_category)
            validation_ms += (time.perf_counter() - validation_started) * 1000
        except _DeterministicPreflight:
            error_category = "none"
            provider_error_code = None
            logger.info(
                "Feature005 stage=deterministic_preflight request_id={}",
                request_id,
            )
        except StructuredProviderCircuitOpen as exc:
            if str(exc) == "total_budget_exhausted":
                error_category = "total_budget_exhausted"
            else:
                error_category = "provider_circuit_open"
            provider_error_code = error_category
            (
                sections,
                aggregate,
                structured_trace,
                answer_mode,
            ) = verified_provider_fallback(error_category)
            structured_trace["provider_failure_reason"] = str(exc)
            logger.info(
                "Feature005 stage=provider_circuit_open request_id={} reason={}",
                request_id,
                str(exc),
            )
        except asyncio.TimeoutError:
            generation_ms = (
                (time.perf_counter() - invocation_started) * 1000
                if invocation_started is not None
                else 0.0
            )
            if not ask_request.offline_mode:
                _STRUCTURED_PROVIDER_CIRCUIT.record_failure(
                    final_answer_model_id,
                    "provider_timeout",
                )
            logger.warning(
                "Feature005 stage=generation_timeout request_id={} elapsed_ms={:.0f}",
                request_id,
                generation_ms,
            )
            error_category = "provider_timeout"
            provider_error_code = error_category
            (
                sections,
                aggregate,
                structured_trace,
                answer_mode,
            ) = verified_provider_fallback(error_category)
            structured_trace["fallback_error_class"] = "TimeoutError"
        except Exception as exc:
            generation_ms = (
                (time.perf_counter() - invocation_started) * 1000
                if invocation_started is not None and generation_ms == 0.0
                else generation_ms
            )
            logger.warning("Structured legal answer fallback: {} detail={} raw={}", exc.__class__.__name__, exc, raw if 'raw' in locals() else 'no-raw')
            error_category = classify_structured_provider_error(exc)
            if error_category.startswith("provider_") and not ask_request.offline_mode:
                _STRUCTURED_PROVIDER_CIRCUIT.record_failure(
                    final_answer_model_id,
                    error_category,
                )
            provider_error_code = error_category
            (
                sections,
                aggregate,
                structured_trace,
                answer_mode,
            ) = verified_provider_fallback(error_category)
            structured_trace["fallback_error_class"] = exc.__class__.__name__

    total_ms = (time.perf_counter() - stage_started) * 1000
    metric = build_section_grounding_metric(
        request_id=request_id,
        statuses=[section.status for section in sections],
        stage_timings_ms={
            "retrieval": retrieval_ms,
            "provisioning": provisioning_ms,
            "generation": generation_ms,
            "validation": validation_ms,
            "end_to_end": total_ms,
        },
        repair_count=0,
        completed=True,
        error_category=error_category,
    )
    document_ids = {
        str(row.get("document_id") or row.get("law_number") or "")
        for row in all_results
        if row.get("document_id") or row.get("law_number")
    }
    _, sanitization_summary = project_legal_evidence_rows(all_results)
    missing_coverage_count = sum(
        1
        for facets in coverage_matrix.values()
        for facet in facets
        if not bool(facet.get("evidence_available"))
    )
    rejected_count = sum(
        1
        for decisions in content_decision_trace.values()
        for decision in decisions
        if decision.get("decision") == "reject"
    )
    telemetry.record_legal_orchestration(
        duration_ms=total_ms,
        planner_mode=problem_map.planner_mode,
        issue_count=len(problem_map.legal_issues),
        query_count=sum(len(issue.queries) for issue in problem_map.legal_issues),
        candidate_count=sum(len(rows) for rows in candidates_by_issue.values()),
        rejected_count=rejected_count,
        missing_coverage_count=missing_coverage_count,
        supplemental_round=3 if full_corpus_used else 2 if expanded_used else 1,
        removed_noise_count=int(sanitization_summary.get("removed_count") or 0),
        prompt_chars=len(context),
        outcome="success" if error_category == "none" else "failed",
    )
    normalized_intent = build_legal_intent(
        ask_request.question,
        legal_as_of=legal_as_of,
    )
    if answer_mode == NORMAL and str(aggregate.get("grounding_status") or "") in {
        "ungrounded",
        "insufficient_evidence",
    }:
        # An answer without a grounded aggregate must never be presented as a
        # normal generated answer. The outer form-only catalog path may still
        # explicitly restore NORMAL for an identity-confirmed official form.
        answer_mode = SOURCE_VIEW_ONLY
        structured_trace["answer_mode"] = answer_mode

    return sections, aggregate, all_results, {
        "timing_summary": {
            "retrieval_ms": round(retrieval_ms, 1),
            "provisioning_ms": round(provisioning_ms, 1),
            "generation_ms": round(generation_ms, 1),
            "validation_ms": round(validation_ms, 1),
            "end_to_end_ms": round(total_ms, 1),
        },
        "pipeline_version": (
            ANSWER_PIPELINE_V2_VERSION if answer_route is not None else "legacy"
        ),
        "answer_route": (
            answer_route.answer_route if answer_route is not None else "legacy"
        ),
        "route_reason": (
            answer_route.decision_reason if answer_route is not None else None
        ),
        "retrieval_decision": {
            "ranking_strategy": "rrf_v2" if v2_enabled else "legacy_stack",
            "learned_reranker_enabled": False if v2_enabled else True,
            "learned_reranker_reason": (
                "activation_gate_not_approved" if v2_enabled else "legacy_runtime_policy"
            ),
            "original_domain": domain_decision.original_domain,
            "canonical_domain": domain_decision.canonical_domain,
            "domain_mapping_reason": domain_decision.mapping_reason,
            "fallback_tier": (
                "full_corpus"
                if full_corpus_used
                else "expanded"
                if expanded_used
                else "exact"
                if exact_article_request
                else "domain"
            ),
        },
        "evidence_packets": [
            {
                "issue_id": issue.issue_id,
                "retrieval_tier": evidence_packets[issue.issue_id].retrieval_tier,
                "source_count": len(
                    evidence_packets[issue.issue_id].eligible_sources
                ),
                "coverage": list(coverage_matrix.get(issue.issue_id, [])),
                "source_kinds": sorted(
                    {
                        str(row.get("evidence_kind") or "legal_chunk")
                        for row in evidence_packets[issue.issue_id].eligible_sources
                    }
                ),
            }
            for issue in issues
            if issue.issue_id in evidence_packets
        ],
        "data_release_id": build_data_release_id(runtime_versions),
        "runtime_versions": runtime_versions,
        "form_router": (
            {
                "procedure_id": (
                    (v2_form_resolution or {}).get("procedure_id")
                    or answer_route.procedure_id
                ),
                "status": (
                    "resolved"
                    if v2_form_resolution
                    and v2_form_resolution.get("recommended_forms")
                    else "fail_closed"
                ),
                "recommended_forms": list(
                    (v2_form_resolution or {}).get("recommended_forms") or []
                ),
                "accepted_count": len(
                    (v2_form_resolution or {}).get("recommended_forms") or []
                ),
                "rejected_count": len(
                    (v2_form_resolution or {}).get("rejected_forms") or []
                ),
                "data_gap_status": (v2_form_resolution or {}).get("data_gap_status"),
                "data_gap_reasons": list(
                    (v2_form_resolution or {}).get("data_gap_reasons") or []
                ),
            }
            if answer_route is not None
            and answer_route.answer_route == "procedure_form"
            else None
        ),
        "intent": normalized_intent.model_dump(mode="json"),
        "validity_decision": {
            "state": (
                "expired_or_repealed"
                if expired_source_blocked and not all_results
                else "effective"
                if all_results
                else "unknown_or_stale"
            ),
            "legal_as_of": legal_as_of,
            "filtered_reason_codes": sorted(validity_filtered_reasons),
            "strict_current_answer": True,
        },
        "problem_map": {
            "planner_mode": problem_map.planner_mode,
            "fallback_reason": problem_map.fallback_reason,
            "issue_count": len(problem_map.legal_issues),
            "query_count": sum(
                len(issue.queries) for issue in problem_map.legal_issues
            ),
            "missing_fact_count": len(problem_map.missing_facts),
            "branch_count": len(problem_map.conditional_branches),
            "issues": [
                {
                    "issue_id": issue.issue_id,
                    "title": issue.title,
                    "priority": issue.priority,
                    "intent": issue.intent,
                    "query_count": len(issue.queries),
                }
                for issue in problem_map.legal_issues
            ],
        },
        "clarifying_questions": [
            fact.question for fact in problem_map.missing_facts[:2]
        ],
        "issue_plan": [
            {
                "issue_id": issue.issue_id,
                "title": issue.title,
                "intent": issue.intent,
                "domain": issue.domain,
                "query": issue.query_text,
                "candidate_count": len(candidates_by_issue.get(issue.issue_id, [])),
                "selected_count": len(selected_by_issue.get(issue.issue_id, [])),
                "decisions": decision_trace.get(issue.issue_id, []),
                "content_decisions": content_decision_trace.get(issue.issue_id, []),
            }
            for issue in issues
        ],
        "expanded_retrieval_used": expanded_used,
        "full_corpus_retrieval_used": full_corpus_used,
        "retrieval_timed_out": retrieval_timed_out,
        "total_budget_seconds": total_budget_seconds,
        "supplemental_round": 3 if full_corpus_used else 2 if expanded_used else 1,
        "sanitization_summary": sanitization_summary,
        "official_procedure_evidence": official_procedure_trace,
        "exact_article_gate": exact_article_gate,
        "source_diversity": {
            "document_count": len(document_ids),
            "selected_chunk_count": len(all_results),
        },
        "context_stats": {
            "character_count": len(context),
            "evidence_count": len(evidence_by_id),
            "maximum_characters": context_limit,
        },
        "claim_validation": structured_trace,
        "preflight_deterministic": bool(
            structured_trace.get("preflight_deterministic")
        ),
        "expired_source_blocked": expired_source_blocked,
        "validity_filtered_reasons": sorted(validity_filtered_reasons),
        "answer_mode": answer_mode,
        "provider_error_code": provider_error_code,
        "metric": metric,
    }


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

    # Canonical recommended_forms are attached after deterministic resolution.
    # Never add legacy form records to an LLM context.
    form_context = ""

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
- Khi nguồn đã nêu trực tiếp câu trả lời cho một mục được hỏi, phải nêu lại sự kiện đó trước. Tuyệt đối không nói "chưa xác minh" hoặc "nguồn không nêu" đối với nội dung đã có trong nguồn được cung cấp.
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
            "as_of_explicit": bool(
                ask_request.legal_as_of or ask_request.event_date
            ),
            "domain": domain,
            "include_trace": bool(
                ask_request.show_rag_trace and ask_request.role == "admin"
            ),
            "scope_filter": scope_filter,
            "retrieval_tier": tier,
            "audience": str(ask_request.role or "citizen"),
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
                    "temperature": 0.0,
                    "top_p": 1.0,
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
) -> list[dict[str, Any]]:
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
            
        citation: dict[str, Any] = {
                "chunk_id": chunk_id,  # retained for audit/trace only
                "doc_id": doc_id,
                "law_number": law_number,
                "document_title": document_title,
                "article_number": article_number,
                "article_title": str(item.get("article_title") or ""),
                "clause_number": str(item.get("clause_number") or "").strip(),
                "point_number": str(item.get("point_number") or "").strip(),
                # The retriever returns document_status for article hits. Keep
                # that verified eligibility visible to API consumers instead
                # of dropping it and making an active official citation look
                # unverified at the answer boundary.
                "effective_status": status or None,
                # Source provenance is retained for audit. The default Ask link
                # remains the internal viewer, never this external URL.
                "source_url": raw_source_url,
                "source_metadata": {
                    "issuing_agency": str(item.get("issuing_agency") or "").strip(),
                    "scope": str(item.get("scope") or "").strip(),
                    "effective_date": str(item.get("effective_date") or "").strip(),
                    "source_url": raw_source_url,
                },  # link gốc VBPL làm link phụ
                "validity_sync": format_public_validity_sync(item.get("validity_sync")),
                "authority_level": str(item.get("authority_level") or "").strip(),
                "authority_label": str(item.get("authority_label") or "").strip(),
                "internal_url": internal_url,  # link viewer nội bộ (frontend) làm link chính
                "pdf_url": pdf_url,
                "fallback_search_url": f"https://vbpl.vn/van-ban/tim-kiem?q={urllib.parse.quote_plus(law_number)}" if law_number else "",
                "link_status": "internal_indexed" if doc_id else ("external_recorded" if raw_source_url else "missing"),
                "label": natural_label or "Văn bản pháp luật",
            }
        proof_projection = enrich_public_citation(
            {
                **item,
                **citation,
                "internal_url": internal_url,
            }
        )
        for proof_field in (
            "verification_level",
            "verification_status",
            "verification_reason",
            "viewer_url",
            "proof",
        ):
            if proof_field in proof_projection:
                citation[proof_field] = proof_projection[proof_field]
        citations.append(citation)
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
            "article_status": item.get("effective_article_status") or item.get("article_status"),
            "effective_article_status": item.get("effective_article_status"),
            "request_id": item.get("request_id"),
            "issue_id": item.get("issue_id"),
            "issue_domain": item.get("issue_domain"),
            "relationships": item.get("relationships") or [],
            "score": item.get("score") or item.get("final_score"),
            "source_url": item.get("source_url"),
            "content": item.get("content"),
            "validity_sync": item.get("validity_sync") or {},
            "authority_level": item.get("authority_level"),
            "authority_label": item.get("authority_label"),
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
        for key in (
            "pipeline_version",
            "answer_route",
            "route_reason",
            "data_release_id",
        ):
            value = section_trace.get(key)
            if isinstance(value, (str, int, float, bool)):
                snapshot[key] = value

        retrieval_decision = section_trace.get("retrieval_decision")
        if isinstance(retrieval_decision, dict):
            snapshot["retrieval_decision"] = {
                key: retrieval_decision.get(key)
                for key in (
                    "ranking_strategy",
                    "learned_reranker_enabled",
                    "learned_reranker_reason",
                )
                if isinstance(
                    retrieval_decision.get(key), (str, int, float, bool)
                )
            }

        runtime_versions = section_trace.get("runtime_versions")
        if isinstance(runtime_versions, dict):
            snapshot["runtime_versions"] = {
                key: runtime_versions.get(key)
                for key in (
                    "app_version",
                    "index_collection",
                    "embedding_fingerprint",
                    "validity_snapshot_sha256",
                    "reranker_version",
                )
                if isinstance(runtime_versions.get(key), (str, int, float, bool))
            }

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
            "nha o", "nhà ở", "chuyen nhuong dat", "quy hoach", "khu dat",
            "dat xen ket", "thu hoi dat", "dien tich dat",
            "chung cu", "chung cư", "nha chung cu", "nhà chung cư",
            "ban quan tri", "ban quản trị", "cong nhan ban quan tri",
            "công nhận ban quản trị",
        ],
    },
    {
        "slug": "an_sinh_y_te_giao_duc",
        "name": "An sinh - Y tế - Giáo dục",
        "agency": "Lao động - Thương binh và Xã hội",
        "keywords": [
            "tro cap", "trợ cấp", "bao tro", "bảo trợ", "xa hoi", "xã hội",
            "ngheo", "nghèo", "khuyet tat", "khuyết tật", "y te", "giao duc",
            "truong tieu hoc", "trường tiểu học", "truong mam non", "trường mầm non",
            "truong trung hoc", "trường trung học", "co so giao duc", "cơ sở giáo dục",
            "giai the truong", "giải thể trường", "thanh lap truong", "thành lập trường",
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
    text = text.replace("đ", "d").replace("Đ", "D")
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
        answer_status="clarifying",
        fallback_tier="clarification",
        canonical_domain=canonicalize_legal_domain(suggested_domain),
        evidence_count=0,
        coverage_warning="Cần chọn đúng lĩnh vực trước khi truy xuất căn cứ.",
        blocked_reason="domain_mismatch",
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



def _procedure_response_fields(
    procedure_detail: dict | None,
    question: str | None = None,
    *,
    role: str = "citizen",
) -> tuple[dict | None, list[dict] | None, str | None]:
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
                question or "", raw_forms, limit=12,
                matched_procedure_ids=matched_ids,
                selected_domain=str(proc.get("domain_slug") or "") or None,
                role=role,
            )
            recommended = ranked or None
            primary_id = str(proc.get("id") or "")
            proc["forms"] = [
                form
                for form in ranked
                if str(form.get("procedure_id") or "") == primary_id
            ][:12]
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


def _build_form_provenance_trace(
    *,
    question: str | None,
    procedure_match: dict | None,
    accepted_forms: list[dict] | None,
) -> dict[str, Any]:
    """Build privacy-safe form acceptance/rejection reasons for Admin trace."""

    requested = bool(question and _question_requests_forms(question))
    accepted = [dict(item) for item in (accepted_forms or [])]
    accepted_keys = {
        str(item.get("form_id") or item.get("download_url") or "")
        for item in accepted
    }
    rejected: list[dict[str, Any]] = []
    for item in list((procedure_match or {}).get("recommended_forms") or []):
        form = dict(item)
        key = str(form.get("form_id") or form.get("download_url") or "")
        if key and key in accepted_keys:
            continue
        reasons: list[str] = []
        if form.get("official_level") != "official":
            reasons.append("not_official")
        if form.get("review_status") != "approved":
            reasons.append("not_approved")
        if form.get("has_official_file") is not True:
            reasons.append("missing_official_file")
        if not str(form.get("download_url") or "").strip():
            reasons.append("missing_download_url")
        if not str(form.get("procedure_id") or "").strip():
            reasons.append("missing_procedure_binding")
        rejected.append(
            {
                "form_id": str(form.get("form_id") or "") or None,
                "procedure_id": str(form.get("procedure_id") or "") or None,
                "reasons": reasons or ["not_selected_for_question"],
            }
        )
    accepted_trace = [
        {
            "form_id": str(item.get("form_id") or "") or None,
            "procedure_id": str(item.get("procedure_id") or "") or None,
            "official_level": item.get("official_level"),
            "review_status": item.get("review_status"),
            "has_official_file": item.get("has_official_file") is True,
            "download_url_available": bool(item.get("download_url")),
        }
        for item in accepted
    ]
    return {
        "requested": requested,
        "accepted": accepted_trace,
        "rejected": rejected,
        "catalog_resolution": dict(
            (procedure_match or {}).get("form_resolution_trace") or {}
        ),
        "forms_unavailable": requested and not bool(accepted_trace),
    }

def _normalize_procedure_query(question: str) -> str:
    """Lowercase + strip accents for robust Vietnamese phrase matching."""
    import unicodedata

    value = unicodedata.normalize("NFD", question or "")
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d").replace("Đ", "D")
    value = value.casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value


@lru_cache(maxsize=1)
def _get_canonical_form_catalog() -> FormCatalog:
    """Load the immutable versioned catalog once per API process."""

    return FormCatalog.load_default()


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


def _forms_unavailable_for_question(
    question: str | None,
    recommended_forms: list[dict] | None,
) -> bool:
    """Apply the strict explicit-form detector consistently on every path."""

    return bool(
        _question_requests_forms(str(question or ""))
        and not recommended_forms
    )


def _append_verified_form_answer(
    answer: str,
    *,
    question: str,
    recommended_forms: list[dict] | None,
) -> str:
    """Append only hard-gated form metadata, or one explicit safe gap."""

    if not _question_requests_forms(question):
        return answer
    forms = [
        form
        for form in (recommended_forms or [])
        if str(form.get("review_status") or "").casefold() == "approved"
        and str(form.get("official_level") or "").casefold() == "official"
        and form.get("source_url")
        and form.get("download_url")
        and form.get("procedure_identity_confirmed") is True
    ]
    if not forms:
        notice = (
            "## Biểu mẫu chính thức\n"
            "Hệ thống chưa có biểu mẫu chính thức đã duyệt và đủ điều kiện "
            "phát hành cho thủ tục này."
        )
        return answer if notice in answer else f"{answer}\n\n{notice}".strip()
    lines = ["## Biểu mẫu chính thức đã xác minh"]
    for form in forms:
        name = str(form.get("name") or form.get("display_name") or "").strip()
        code = str(form.get("form_code") or "").strip()
        label = f"{name} ({code})" if code else name
        lines.append(f"- {label}")
        effective_from = str(form.get("effective_from") or "").strip()
        if effective_from:
            lines.append(f"  - Hiệu lực từ: {effective_from}")
        legal_basis = [
            str(value).strip()
            for value in form.get("legal_basis") or []
            if str(value).strip()
        ]
        if legal_basis:
            lines.append("  - Căn cứ: " + "; ".join(legal_basis))
        source_url = str(form.get("source_url") or "").strip()
        if source_url:
            lines.append(f"  - [Nguồn biểu mẫu chính thức]({source_url})")
        download_url = str(form.get("download_url") or "").strip()
        if download_url:
            lines.append(f"  - [Tải biểu mẫu]({download_url})")
    section = "\n".join(lines)
    policy = classify_question(question)
    requested = set(policy.get("required_sections") or [])
    form_lookup_only = requested <= {
        "conclusion",
        "official_forms",
        "legal_basis_links",
    }
    normalized_answer = _normalize_procedure_query(answer)
    if len(answer.strip()) < 260 and any(
        marker in normalized_answer
        for marker in (
            "chua co nguon hien hanh du de xac minh noi dung nay",
            "chua tim thay nguon hien hanh ho tro truc tiep",
            "da tim thay nguon co lien quan nhung chua xac minh du",
        )
    ):
        answer = (
            "Phần biểu mẫu đã được xác minh bên dưới. Các nội dung khác của hồ sơ "
            "chỉ được kết luận khi hệ thống có đủ nguồn hiện hành hỗ trợ trực tiếp."
        )
    if form_lookup_only:
        labels = [
            str(form.get("name") or form.get("display_name") or "").strip()
            for form in forms
        ]
        conclusion = (
            "## Kết luận\n"
            "Theo danh mục biểu mẫu chính thức đã xác minh, thủ tục này sử dụng: "
            + "; ".join(label for label in labels if label)
            + "."
        )
        return f"{conclusion}\n\n{section}".strip()
    return answer if section in answer else f"{answer}\n\n{section}".strip()


def _is_verified_form_lookup_only(
    question_policy: Mapping[str, Any],
    recommended_forms: list[dict] | None,
) -> bool:
    requested = set(question_policy.get("required_sections") or [])
    if not question_policy.get("requests_form") or not requested <= {
        "conclusion",
        "official_forms",
        "legal_basis_links",
    }:
        return False
    forms = list(recommended_forms or [])
    return bool(forms) and all(
        str(form.get("review_status") or "").casefold() == "approved"
        and str(form.get("official_level") or "").casefold() == "official"
        and form.get("source_url")
        and form.get("download_url")
        and form.get("procedure_id")
        and form.get("procedure_identity_confirmed") is True
        for form in forms
    )


def _merge_form_catalog_citations(
    citations: list[dict[str, Any]] | None,
    recommended_forms: list[dict] | None,
) -> list[dict[str, Any]]:
    merged = list(citations or [])
    seen = {
        (
            str(item.get("law_number") or ""),
            str(item.get("source_url") or ""),
        )
        for item in merged
    }
    for form in recommended_forms or []:
        if (
            str(form.get("review_status") or "").casefold() != "approved"
            or str(form.get("official_level") or "").casefold() != "official"
            or form.get("procedure_identity_confirmed") is not True
        ):
            continue
        source_url = str(form.get("source_url") or "").strip()
        if not source_url:
            continue
        bases = [
            str(value).strip()
            for value in form.get("legal_basis") or []
            if str(value).strip()
        ] or [""]
        for basis in bases:
            key = (basis, source_url)
            if key in seen:
                continue
            seen.add(key)
            merged.append({
                "document_title": "Nguồn biểu mẫu chính thức",
                "law_number": basis or None,
                "article_number": None,
                "effective_status": "active",
                "source_url": source_url,
                "verification_source": "approved_form_catalog",
            })
    return merged


def _has_verified_released_form(
    recommended_forms: list[dict] | None,
) -> bool:
    """Return true only for a catalog form safe for public release."""

    return any(
        str(form.get("review_status") or "").casefold() == "approved"
        and str(form.get("official_level") or "").casefold() == "official"
        and bool(form.get("source_url"))
        and bool(form.get("download_url"))
        and form.get("procedure_identity_confirmed") is True
        for form in (recommended_forms or [])
        if isinstance(form, Mapping)
    )


def _reconcile_verified_form_sections(
    sections: list[Any] | None,
    *,
    section_trace: Mapping[str, Any] | None,
    recommended_forms: list[dict] | None,
) -> tuple[list[Any], dict[str, Any]]:
    """Replace a retrieval-only form gap with the verified catalog result.

    Legal retrieval normally has no form-file evidence, while the separately
    governed catalog does. Once a released form is verified, keeping an
    ``insufficient`` form card beside the downloadable form is contradictory.
    Only a form-only issue is removed; mixed fee/form or other legal issues are
    preserved unchanged.
    """

    current = list(sections or [])
    if not current or not _has_verified_released_form(recommended_forms):
        return current, aggregate_answer_sections(current)

    form_only_ids: set[str] = set()
    claim_trace = (
        section_trace.get("claim_validation")
        if isinstance(section_trace, Mapping)
        else None
    )
    if isinstance(claim_trace, Mapping):
        for item in claim_trace.get("issues") or []:
            if not isinstance(item, Mapping):
                continue
            requested = {
                str(value or "").strip()
                for value in item.get("requested_facets") or []
                if str(value or "").strip()
            }
            if requested == {"form"}:
                form_only_ids.add(str(item.get("issue_id") or ""))

    reconciled = [
        section
        for section in current
        if str(getattr(section, "issue_id", "")) not in form_only_ids
        and _normalize_procedure_query(str(getattr(section, "title", "")))
        not in {"bieu mau", "mau bieu", "form"}
    ]
    return reconciled, aggregate_answer_sections(reconciled)


def _structured_source_gap(
    section_trace: Mapping[str, Any] | None,
    *,
    has_verified_form: bool = False,
) -> list[str]:
    """Expose missing structured facets without leaking internal trace data."""

    if not isinstance(section_trace, Mapping):
        return []
    claim_trace = section_trace.get("claim_validation")
    if not isinstance(claim_trace, Mapping):
        return []
    gaps: list[str] = []
    for issue in claim_trace.get("issues") or []:
        if not isinstance(issue, Mapping):
            continue
        for key in ("missing_facets", "unavailable_facets"):
            for raw in issue.get(key) or []:
                facet = str(raw or "").strip()
                if has_verified_form and facet == "form":
                    continue
                if facet and facet not in gaps:
                    gaps.append(facet)
    return gaps


def _rank_forms_for_question(
    question: str,
    forms: list[dict],
    limit: int = 3,
    *,
    matched_procedure_ids: list[str] | None = None,
    selected_domain: str | None = None,
    ward_scope: str | None = None,
    role: str = "citizen",
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
    # A procedure can have more than three mandatory forms. Keep the public
    # response bounded, but never omit a fourth required form for a compact
    # card layout.
    max_forms = max(1, min(int(limit or 3), 12))

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
        audience = str(form.get("audience") or "citizen")
        if role == "citizen" and audience not in {"citizen", "both"}:
            continue
        if role == "officer" and audience not in {"citizen", "officer", "both"}:
            continue
        if role not in {"citizen", "officer", "admin"}:
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
    *,
    role: str = "citizen",
) -> list[dict]:
    """Return only approved official forms with real downloadable files.

    Never invent seed/synthetic download URLs.
    """
    canonical_catalog = _get_canonical_form_catalog()
    canonical_id = normalize_procedure_id(procedure_id)
    if canonical_catalog.get_procedure(canonical_id):
        resolved = canonical_catalog.resolve_forms(
            "",
            role=role,
            procedure_ids=[canonical_id],
            limit=12,
        )
        return list(resolved["recommended_forms"])

    from pathlib import Path as _Path

    from api.routers.ward_procedures import (
        HAI_PHONG_PROCEDURES_SEED,
        OFFICIAL_FORMS_CATALOG_PATH,
        OFFICIAL_FORMS_INDEX_PATH,
        _get_approved_form_ids,
        _load_forms_json,
        _resolve_real_form_file,
        _validate_form_file_integrity,
        normalize_form_display_name,
        normalize_form_record,
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
    catalog = _get_canonical_form_catalog()
    catalog_matches = catalog.resolve_procedures(
        question,
        limit=6,
    )
    raw_matches = list(catalog_matches["matches"])
    strong_matches = [
        item
        for item in raw_matches
        if item.get("match_type") != "form_code"
    ]
    if strong_matches:
        # Descriptive procedure/form wording is direct intent evidence.  A
        # lower-ranked shared code (``01``, ``CT01``...) is only retrieval
        # context and must not create extra procedures or public form cards.
        def matched_tokens(item: Mapping[str, Any]) -> set[str]:
            return max(
                (
                    set(_normalize_procedure_query(phrase).split())
                    for phrase in item.get("matched_phrases") or []
                    if _normalize_procedure_query(phrase)
                ),
                key=len,
                default=set(),
            )

        # Remove a generic procedure whose full matched wording is contained
        # by a more specific, higher-scored procedure in the same domain (for
        # example ``tro cap xa hoi`` inside ``huong tro cap huu tri xa hoi``).
        # Distinct explicit procedures in the same question remain intact.
        strong_matches = [
            item
            for item in strong_matches
            if not any(
                matched_tokens(item)
                and matched_tokens(item) < matched_tokens(other)
                and int(other.get("score") or 0) > int(item.get("score") or 0)
                and str(
                    (catalog.get_procedure(str(other.get("procedure_id"))) or {}).get(
                        "domain"
                    )
                    or ""
                )
                == str(
                    (catalog.get_procedure(str(item.get("procedure_id"))) or {}).get(
                        "domain"
                    )
                    or ""
                )
                for other in strong_matches
                if other is not item
            )
        ]
        raw_matches = strong_matches
    elif catalog_matches.get("ambiguous"):
        # A bare shared form code cannot identify one legal procedure safely.
        raw_matches = []
    else:
        raw_matches = raw_matches[:1]
    ranked_catalog = [
        (
            str(item["procedure_id"]),
            int(item["score"]),
            list(item.get("matched_phrases") or []),
        )
        for item in raw_matches
    ]
    if ranked_catalog:
        return ranked_catalog

    # Compatibility-only virtual intent. It never supplies a form or legal fact.
    normalized_question = _normalize_procedure_query(question)
    if any(
        phrase in normalized_question
        for phrase in ("ho chieu cho con", "ho chieu tre em", "cap ho chieu")
    ):
        return [("cap_ho_chieu_tre_em", 10, ["hộ chiếu trẻ em"])]
    return []

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


def _build_procedure_payload(
    procedure_id: str,
    matched_phrases: list[str] | None = None,
    *,
    role: str = "citizen",
) -> dict | None:
    from api.routers.ward_procedures import HAI_PHONG_PROCEDURES_SEED

    canonical_catalog = _get_canonical_form_catalog()
    canonical_id = normalize_procedure_id(procedure_id)
    canonical = canonical_catalog.get_procedure(canonical_id)
    if canonical:
        form_resolution = canonical_catalog.resolve_forms(
            "",
            role=role,
            procedure_ids=[canonical_id],
            limit=12,
        )
        official_forms = list(form_resolution["recommended_forms"])
        return {
            "id": canonical_id,
            "name": canonical.get("name"),
            "department": canonical.get("receiving_authority"),
            "domain_slug": canonical.get("domain"),
            "steps": [],
            "documents_required": [],
            "duration": None,
            "fee": None,
            "forms": official_forms,
            "matched_phrases": matched_phrases or [],
            "is_reference_only": True,
            "source": "canonical_procedure_catalog",
            "label": "Tham chiếu thủ tục",
            "has_official_forms": bool(official_forms),
            "source_status": canonical.get("source_status"),
            "review_status": canonical.get("review_status"),
            "official_procedure_code": canonical.get("official_procedure_code"),
            "official_procedure_url": canonical.get("official_procedure_url"),
            "form_resolution_trace": {
                "forms_unavailable": form_resolution["forms_unavailable"],
                "data_gap_status": form_resolution.get("data_gap_status"),
                "data_gap_reasons": form_resolution.get("data_gap_reasons", []),
                "rejected_forms": form_resolution["rejected_forms"],
            },
            "disclaimer": (
                "Thông tin thủ tục chỉ được hiển thị khi đã có nguồn chính thức; "
                "biểu mẫu thiếu phê duyệt hoặc thiếu hiệu lực luôn bị loại."
            ),
        }

    # Virtual intents without seed catalog: expose metadata only, never fake forms.
    if procedure_id == "cap_ho_chieu_tre_em":
        official_forms = _collect_official_forms_for_procedure(
            procedure_id,
            None,
            role=role,
        )
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
    official_forms = _collect_official_forms_for_procedure(
        procedure_id,
        seed,
        role=role,
    )
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
    
    # Try PostgreSQL first if configured
    try:
        from api.faq_governance_service import get_faq_governance_service
        faq_service = get_faq_governance_service()
        revisions = faq_service.repository.list_revisions()
        # Keep only released FAQs
        faqs = [r for r in revisions if r.get("public_state") == "released" or r.get("public_state") == "needs_review"]
        # Map fields back to expected keys for the scoring logic
        for f in faqs:
            f["review_status"] = "approved"
            if "canonical_domain" in f:
                f["domain"] = f["canonical_domain"]
    except Exception:
        faqs = []

    if not faqs:
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


def _match_procedure_detail(
    question: str,
    *,
    role: str = "citizen",
) -> dict | None:
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
        payload = _build_procedure_payload(proc_id, phrases, role=role)
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
        limit=12,
        matched_procedure_ids=list(procedure_detail.get("matched_procedure_ids") or []),
        selected_domain=str(procedure_detail.get("domain_slug") or "") or None,
    ) or None


def _apply_m4_temporal_scope(ask_request: AskRequest) -> dict[str, Any]:
    """Apply an exact query date to every downstream answer-validity gate."""

    explicit = bool(ask_request.legal_as_of or ask_request.event_date)
    computed_as_of = effective_legal_date(
        legal_as_of=ask_request.legal_as_of,
        event_date=ask_request.event_date,
    )
    classification = classify_legal_query(
        ask_request.question,
        requested_domain=ask_request.domain,
        as_of=computed_as_of,
        as_of_explicit=explicit,
    )
    if (
        classification["retrieval_allowed"]
        and classification["temporal_scope"] == "historical"
        and not explicit
        and classification["retrieval_as_of"]
    ):
        ask_request.legal_as_of = date.fromisoformat(
            classification["retrieval_as_of"]
        )
    return classification


async def _ask_local(ask_request: AskRequest, user_id: str | None = None, request=None) -> AskResponse:
    try:
        # Keep the feature flag request-local on the offline path as well as
        # the provider path.  The legacy path reads this value after answer
        # verification even when section orchestration is disabled.
        section_grounding = is_section_grounding_enabled()
        m4_classification = _apply_m4_temporal_scope(ask_request)
        detected = _detect_question_domain(ask_request.question)
        question_policy = classify_question(
            ask_request.question,
            detected_domain=(detected or {}).get("slug") if detected else None,
        )
        question_policy["structured_classification"] = m4_classification
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
        procedure_match = _match_procedure_detail(
            ask_request.question,
            role=ask_request.role,
        )
        procedure_detail, recommended_forms, procedure_summary = _procedure_response_fields(
            procedure_match,
            ask_request.question,
            role=ask_request.role,
        )
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
            extractive_fallback = extractive_conclusion_from_evidence(
                retrieval_results,
                required_sections=question_policy.get("required_sections") or [],
            )
            if extractive_fallback:
                answer = extractive_fallback
            else:
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
        answer_out = _append_verified_form_answer(
            answer_out,
            question=ask_request.question,
            recommended_forms=recommended_forms,
        )
        if not section_grounding:
            answer_out = prepend_extractive_conclusion_when_supported(
                answer_out,
                retrieval_results,
                required_sections=question_policy.get("required_sections") or [],
            )
        verified_form_lookup = _is_verified_form_lookup_only(
            question_policy,
            recommended_forms,
        )
        if verified_form_lookup:
            citations = []
            removed_claims = []
            if grounding_status in {"ungrounded", "insufficient_evidence", "unknown"}:
                grounding_status = "partially_grounded"
        citations = _merge_form_catalog_citations(citations, recommended_forms)
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
            rag_trace["form_provenance"] = _build_form_provenance_trace(
                question=ask_request.question,
                procedure_match=procedure_match,
                accepted_forms=recommended_forms,
            )
        legal_as_of = effective_legal_date(
            legal_as_of=ask_request.legal_as_of,
            event_date=ask_request.event_date,
        )
        evidence_coverage = build_evidence_coverage(
            retrieval_results,
            required_sections=question_policy.get("required_sections") or [],
            recommended_forms=recommended_forms,
            answer=answer_out,
        )
        claim_validation = build_claim_validation(
            removed_claims=removed_claims,
            citations=citations,
            legal_as_of=legal_as_of,
            answer=None if verified_form_lookup else answer_out,
            coverage=evidence_coverage,
        )
        if verified_form_lookup:
            claim_validation.append({
                "claim_type": "conclusion",
                "status": "verified",
                "reason": "approved_form_catalog_mapping",
                "original": None,
                "evidence_ids": list(
                    evidence_coverage.get("conclusion", {}).get("evidence_ids") or []
                ),
                "legal_as_of": legal_as_of.isoformat(),
            })
        answer_out = apply_claim_validation(answer_out, claim_validation)
        # A claim guard may remove a model-written conclusion. Restore only a
        # source-verbatim direct outcome and recompute its observable coverage.
        answer_out = prepend_extractive_conclusion_when_supported(
            answer_out,
            retrieval_results,
            required_sections=question_policy.get("required_sections") or [],
        )
        evidence_coverage = build_evidence_coverage(
            retrieval_results,
            required_sections=question_policy.get("required_sections") or [],
            recommended_forms=recommended_forms,
            answer=answer_out,
        )
        answer_score_preview, quality_flags = quality_preview(
            coverage=evidence_coverage,
            grounding_status=grounding_status,
            claim_validation=claim_validation,
            answer=answer_out,
        )
        answer_completeness = assess_answer_completeness(
            question=ask_request.question,
            answer=answer_out,
            sources=retrieval_results,
            orchestration_trace=rag_trace,
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
        trust_fields = build_public_trust_projection(
            trace=rag_trace if isinstance(rag_trace, Mapping) else None,
            citations=citations,
            answer_mode=(
                VERIFIED_SOURCE_CONDENSED if verified_form_lookup else NORMAL
            ),
            provider_label="local",
        )
        return AskResponse(
            answer=answer_out,
            question=ask_request.question,
            rag_trace=(
                rag_trace
                if ask_request.role == "admin" and ask_request.show_rag_trace
                else None
            ),
            grounding_status=grounding_status,
            answer_completeness=answer_completeness,
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
            forms_unavailable=_forms_unavailable_for_question(
                ask_request.question,
                recommended_forms,
            ),
            source_gap=source_gap,
            evidence_coverage=evidence_coverage,
            claim_validation=claim_validation,
            authority_status=evidence_coverage.get("authority", {}).get("status", "not_applicable"),
            legal_as_of=legal_as_of,
            clarifying_questions=clarifying_questions,
            quality_flags=quality_flags,
            answer_score_preview=answer_score_preview,
            **trust_fields,
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
        "validity_sync": item.get("validity_sync") or {},
        "authority_level": item.get("authority_level"),
        "authority_label": item.get("authority_label"),
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
            "as_of": date.today().isoformat(),
            "as_of_explicit": False,
        }
        return await get_legal_search_client().search(payload)

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

        async def privacy_guard_stream() -> StreamingResponse:
            try:
                if not local_fallback_enabled():
                    raise RuntimeError("local_fallback_disabled")
                fallback_request = ask_request.model_copy(
                    update={
                        "offline_mode": True,
                        "offline_model": RECOMMENDED_LOCAL_MODEL,
                    }
                )
                fallback_result = await _ask_local(
                    fallback_request,
                    user_id=user_id,
                    request=request,
                )
            except Exception:
                fallback_result = _insufficient_legal_evidence_response(
                    ask_request.question
                ).model_copy(
                    update={
                        "answer_mode": SOURCE_VIEW_ONLY,
                        "generation_provenance": {
                            "mode": "cloud_blocked",
                            "provider_label": "blocked",
                            "model_label": None,
                            "redaction_applied": False,
                            "reason_code": "pii_redaction_incomplete",
                        },
                    }
                )

            async def guarded_stream() -> AsyncGenerator[str, None]:
                yield f"data: {json.dumps({'type': 'final_answer', 'content': fallback_result.answer})}\n\n"
                yield f"data: {json.dumps({'type': 'complete', 'final_answer': fallback_result.answer})}\n\n"

            return StreamingResponse(guarded_stream(), media_type="text/event-stream")

        try:
            egress_decision = _prepare_ask_provider_egress(
                ask_request.question,
                strategy_model,
                answer_model,
                final_answer_model,
            )
        except ProviderEgressBlocked:
            return await privacy_guard_stream()
        provider_question = egress_decision.text

        section_grounding = is_section_grounding_enabled()
        section_answer_sections: list[Any] | None = None
        section_aggregate: dict[str, Any] | None = None
        section_trace: dict[str, Any] | None = None
        history_question = provider_question
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
        if (
            ask_request.pre_persisted_user_message
            and ctx_msgs
            and ctx_msgs[-1].get("role") == "user"
            and str(ctx_msgs[-1].get("content") or "").strip()
            == ask_request.question.strip()
        ):
            ctx_msgs = ctx_msgs[:-1]
        if ctx_msgs:
            history_str = conv_svc.format_context_for_prompt(ctx_msgs)
            history_question = f"Lịch sử trò chuyện trước đó:\n{history_str}\n---\nCâu hỏi hiện tại:\n{ask_request.question}"
        try:
            history_question = _prepare_ask_provider_egress(
                history_question,
                strategy_model,
                answer_model,
                final_answer_model,
            ).text
        except ProviderEgressBlocked:
            return await privacy_guard_stream()

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
                    current_question=provider_question,
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
        metadata_attachments: list[dict[str, Any]] = []
        if response.answer_mode and response.answer_mode != NORMAL:
            metadata_attachments.append(
                {"kind": "answer_mode", "value": response.answer_mode}
            )
        if response.presentation_version:
            metadata_attachments.append(
                {
                    "kind": "legal_answer_presentation",
                    "value": {
                        "presentation_version": response.presentation_version,
                        "answer_route": response.answer_route,
                        "pipeline_version": response.pipeline_version,
                        "data_release_id": response.data_release_id,
                        "index_fingerprint": response.index_fingerprint,
                        "validity_snapshot": response.validity_snapshot,
                        "verification_label": response.verification_label,
                        "historical_label": response.historical_label,
                        "sections": (
                            response.sections.model_dump(mode="json")
                            if response.sections
                            else None
                        ),
                    },
                }
            )
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
            answer_sections=[
                section.model_dump(mode="json")
                for section in (response.answer_sections or [])
            ] or None,
            forms_unavailable=response.forms_unavailable,
            rag_trace=response.rag_trace if role_val == "admin" else None,
            grounding_status=response.grounding_status,
            answer_status=response.answer_status,
            fallback_tier=response.fallback_tier,
            canonical_domain=response.canonical_domain,
            evidence_count=response.evidence_count,
            coverage_warning=response.coverage_warning,
            blocked_reason=response.blocked_reason,
            attachments=metadata_attachments or None,
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


async def _execute_ask_simple_core(
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
    answer_pipeline_v2 = is_answer_pipeline_v2_enabled(
        str(effective_role or "citizen")
    )
    m4_classification = _apply_m4_temporal_scope(ask_request)
    detected_domain = _detect_question_domain(ask_request.question)
    _topic_classification = classify_topic_v1(ask_request.question)
    detected_topics = _topic_classification.topics
    ask_request.detected_topics = [t.value for t in detected_topics]
    ask_request.topic_confidence = _topic_classification.confidence
    ask_request.topic_domain = _topic_classification.domain.value if _topic_classification.domain else None
    
    question_policy = classify_question(
        ask_request.question,
        detected_domain=(detected_domain or {}).get("slug") if detected_domain else None,
    )
    question_policy["structured_classification"] = m4_classification
    original_selected_domain = ask_request.domain
    selected_canonical_domain = canonicalize_legal_domain(original_selected_domain)
    detected_canonical_domain = canonicalize_legal_domain(
        (detected_domain or {}).get("slug") if detected_domain else None
    )
    soft_domain_mismatch = bool(
        effective_role in ("citizen", "admin")
        and selected_canonical_domain
        and detected_canonical_domain
        and selected_canonical_domain != detected_canonical_domain
    )
    if _should_block_domain_mismatch(effective_role, selected_canonical_domain, detected_domain):
        return _domain_mismatch_response(
            ask_request.question,
            effective_role,
            ask_request.domain,
            detected_domain or {},
        )
    # citizen/admin soft redirect selected domain to detected when clearly mismatched
    if soft_domain_mismatch and detected_domain:
        ask_request.domain = detected_canonical_domain
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

        v2_local = bool(
            ask_request.offline_mode
            and answer_pipeline_v2
        )
        if ask_request.offline_mode and not v2_local:
            await emit_ask_progress(progress, "status", {"stage": "generating"})
            result = await _ask_local(ask_request, user_id=user_id, request=request)
            return await _complete_local_result(result)

        try:
            model_lookup_started = time.perf_counter()
            if v2_local:
                local_id = f"ollama:{ask_request.offline_model}"
                ask_request.strategy_model = local_id
                ask_request.answer_model = local_id
                ask_request.final_answer_model = local_id
                strategy_model = answer_model = final_answer_model = SimpleNamespace(
                    id=local_id,
                    provider="ollama",
                    name=ask_request.offline_model,
                )
            else:
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
            if is_section_grounding_enabled() or answer_pipeline_v2:
                logger.info(
                    "Feature005 stage=model_lookup_done elapsed_ms={:.0f}",
                    (time.perf_counter() - model_lookup_started) * 1000,
                )
        except NotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Model not found: {exc}") from exc

        if not strategy_model or not answer_model or not final_answer_model:
            raise HTTPException(
                status_code=400,
                detail="One or more required models are missing. Register models via Settings or check the model IDs.",
            )

        async def _provider_guard_fallback(reason: str) -> AskResponse:
            if local_fallback_enabled():
                try:
                    local_request = ask_request.model_copy(
                        update={
                            "offline_mode": True,
                            "offline_model": RECOMMENDED_LOCAL_MODEL,
                        }
                    )
                    local_result = await _ask_local(
                        local_request,
                        user_id=user_id,
                        request=request,
                    )
                    return await _complete_local_result(
                        local_result,
                        fallback_reason=reason,
                    )
                except Exception:
                    logger.warning(
                        "Provider privacy guard local fallback unavailable trace_id={}",
                        trace_id,
                    )
            source_only = _insufficient_legal_evidence_response(
                ask_request.question
            ).model_copy(
                update={
                    "answer_mode": SOURCE_VIEW_ONLY,
                    "generation_provenance": {
                        "mode": "cloud_blocked",
                        "provider_label": "blocked",
                        "model_label": None,
                        "redaction_applied": False,
                        "reason_code": reason,
                    },
                    "quality_flags": ["provider_egress_blocked"],
                }
            )
            return await _complete_local_result(
                source_only,
                fallback_reason=reason,
            )

        try:
            egress_decision = _prepare_ask_provider_egress(
                ask_request.question,
                strategy_model,
                answer_model,
                final_answer_model,
            )
        except ProviderEgressBlocked:
            return await _provider_guard_fallback("pii_redaction_incomplete")
        provider_ask_request = ask_request.model_copy(
            update={"question": egress_decision.text}
        )
        provider_redaction_applied = egress_decision.redaction_applied
        # The opt-in section orchestration state belongs to this request only.
        # Keep it initialized even while the flag is off so the legacy path
        # remains a safe, immediate rollback and cannot fail at runtime.
        section_grounding = bool(
            is_section_grounding_enabled() or answer_pipeline_v2
        )
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
        conversation_context_started = time.perf_counter()
        conv_ctx, ctx_msgs = await _build_conversation_context(conv_id, owner_key, real_user_id=user_id, role_context=role_val, is_admin=(role_val=="admin"))
        if section_grounding:
            logger.info(
                "Feature005 stage=conversation_context_done elapsed_ms={:.0f}",
                (time.perf_counter() - conversation_context_started) * 1000,
            )
        if (
            ask_request.pre_persisted_user_message
            and ctx_msgs
            and ctx_msgs[-1].get("role") == "user"
            and str(ctx_msgs[-1].get("content") or "").strip()
            == ask_request.question.strip()
        ):
            ctx_msgs = ctx_msgs[:-1]
        if ctx_msgs:
            history_str = conv_svc.format_context_for_prompt(ctx_msgs)
            history_question = f"Lịch sử trò chuyện trước đó:\n{history_str}\n---\nCâu hỏi hiện tại:\n{ask_request.question}"
        try:
            history_egress = _prepare_ask_provider_egress(
                history_question,
                strategy_model,
                answer_model,
                final_answer_model,
            )
        except ProviderEgressBlocked:
            return await _provider_guard_fallback("pii_redaction_incomplete")
        history_question = history_egress.text
        provider_redaction_applied = bool(
            provider_redaction_applied or history_egress.redaction_applied
        )

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
                ) = await _run_structured_section_orchestration(
                    ask_request=provider_ask_request,
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
                        current_question=provider_ask_request.question,
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
            if section_grounding:
                # The structured feature must fail closed.  Falling through to
                # the legacy graph would make live acceptance measure a
                # different pipeline and could reintroduce ungrounded claims.
                fallback_issues = [
                    replace(issue, request_id=trace_id)
                    for issue in plan_legal_issues(ask_request.question, max_issues=6)
                ]
                section_answer_sections, section_aggregate = safe_extractive_fallback(
                    request_id=trace_id,
                    issues=fallback_issues,
                    evidence_by_id={},
                    role=str(ask_request.role or "citizen"),
                )
                fallback_required_facets = derive_required_facets_by_issue(
                    issues=fallback_issues,
                    required_sections=[
                        str(item)
                        for item in question_policy.get("required_sections") or []
                    ],
                )
                fallback_coverage_matrix = build_issue_coverage_matrix(
                    issues=fallback_issues,
                    evidence_by_id={},
                    required_facets_by_issue=fallback_required_facets,
                )
                section_results = []
                section_trace = {
                    "intent": build_legal_intent(
                        ask_request.question,
                        legal_as_of=str(
                            ask_request.legal_as_of
                            or ask_request.event_date
                            or date.today()
                        ),
                    ).model_dump(mode="json"),
                    "validity_decision": {
                        "state": "unknown_or_stale",
                        "strict_current_answer": True,
                        "reason_codes": ["retrieval_unavailable"],
                    },
                    "issue_plan": [
                        {
                            "issue_id": issue.issue_id,
                            "title": issue.title,
                            "intent": issue.intent,
                            "domain": issue.domain,
                            "query": issue.query_text,
                            "candidate_count": 0,
                            "selected_count": 0,
                            "decisions": [],
                        }
                        for issue in fallback_issues
                    ],
                    "expanded_retrieval_used": False,
                    "source_diversity": {
                        "document_count": 0,
                        "selected_chunk_count": 0,
                    },
                    "error_category": "retrieval_unavailable",
                    "fallback_reason": fallback_reason or "retrieval_unavailable",
                    "claim_validation": build_fallback_quality_trace(
                        issues=fallback_issues,
                        coverage_matrix=fallback_coverage_matrix,
                        reason="retrieval_unavailable",
                    ),
                }
                evidence = []
                final_answer = str(section_aggregate["answer"])
            elif local_fallback_enabled() and fallback_reason:
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

        procedure_match = _match_procedure_detail(
            ask_request.question,
            role=ask_request.role,
        )
        procedure_detail, recommended_forms, procedure_summary = _procedure_response_fields(
            procedure_match,
            ask_request.question,
            role=ask_request.role,
        )
        if section_grounding and isinstance(section_trace, Mapping):
            form_router_trace = section_trace.get("form_router")
            if isinstance(form_router_trace, Mapping) and form_router_trace.get(
                "procedure_id"
            ):
                recommended_forms = list(
                    form_router_trace.get("recommended_forms") or []
                ) or None
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
            section_answer_sections, section_aggregate = (
                _reconcile_verified_form_sections(
                    section_answer_sections,
                    section_trace=section_trace,
                    recommended_forms=recommended_forms,
                )
            )
            grounding_status = str(section_aggregate["grounding_status"])
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
        answer_out = _append_verified_form_answer(
            answer_out,
            question=ask_request.question,
            recommended_forms=recommended_forms,
        )
        if not section_grounding:
            answer_out = prepend_extractive_conclusion_when_supported(
                answer_out,
                retrieval_results,
                required_sections=question_policy.get("required_sections") or [],
            )
        verified_form_lookup = _is_verified_form_lookup_only(
            question_policy,
            recommended_forms,
        )
        if verified_form_lookup:
            citations = []
            removed_claims = []
            if grounding_status in {"ungrounded", "insufficient_evidence", "unknown"}:
                grounding_status = "partially_grounded"
        citations = _merge_form_catalog_citations(citations, recommended_forms)
        source_gap = (
            _structured_source_gap(
                section_trace,
                has_verified_form=_has_verified_released_form(recommended_forms),
            )
            if section_grounding
            else missing_answer_sections(
                answer_out,
                question_policy["question_type"],
                bool(question_policy.get("is_procedural")),
                required_sections=question_policy.get("required_sections") or [],
            )
        )
        if source_gap and str(
            os.getenv("LEGAL_ANSWER_SOURCE_GAP_QUEUE_ENABLED", "false")
        ).strip().casefold() in {"1", "true", "yes", "on"}:
            await asyncio.to_thread(
                enqueue_answer_source_gap_notice,
                question=ask_request.question,
                missing_facets=source_gap,
                domain=str(question_policy.get("detected_domain") or ask_request.domain or "unknown"),
                legal_as_of=str(ask_request.legal_as_of or ask_request.event_date or date.today()),
                actor_role="system",
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
            answer=answer_out,
        )
        claim_validation = build_claim_validation(
            removed_claims=removed_claims,
            # Structured citations are already emitted only from claims that
            # passed request/issue/quote validation. Their public schema
            # intentionally omits internal doc/chunk IDs, so revalidating
            # those display objects here would create false rejections.
            citations=(
                citations
                if verified_form_lookup
                else []
                if section_grounding
                else citations
            ),
            legal_as_of=legal_as_of,
            answer=None if verified_form_lookup else answer_out,
            coverage=evidence_coverage,
        )
        if verified_form_lookup:
            claim_validation.append({
                "claim_type": "conclusion",
                "status": "verified",
                "reason": "approved_form_catalog_mapping",
                "original": None,
                "evidence_ids": list(
                    evidence_coverage.get("conclusion", {}).get("evidence_ids") or []
                ),
                "legal_as_of": legal_as_of.isoformat(),
            })
        if not section_grounding:
            answer_out = apply_claim_validation(answer_out, claim_validation)
        if not section_grounding:
            extractive_fallback = extractive_conclusion_from_evidence(
                retrieval_results,
                required_sections=question_policy.get("required_sections") or [],
            )
            if (
                extractive_fallback
                and evidence_coverage.get("conclusion", {}).get("status") != "verified"
            ):
                # The model omitted a direct answer although active evidence
                # has one. This compatibility repair must never bypass the
                # structured per-issue claim validator.
                answer_out = _append_verified_form_answer(
                    extractive_fallback,
                    question=ask_request.question,
                    recommended_forms=recommended_forms,
                )
            else:
                answer_out = prepend_extractive_conclusion_when_supported(
                    answer_out,
                    retrieval_results,
                    required_sections=question_policy.get("required_sections") or [],
                )
        evidence_coverage = build_evidence_coverage(
            retrieval_results,
            required_sections=question_policy.get("required_sections") or [],
            recommended_forms=recommended_forms,
            answer=answer_out,
        )
        answer_score_preview, quality_flags = quality_preview(
            coverage=evidence_coverage,
            grounding_status=grounding_status,
            claim_validation=claim_validation,
            answer=answer_out,
        )
        if (
            section_grounding
            and isinstance(section_trace, Mapping)
            and section_trace.get("expired_source_blocked")
        ):
            quality_flags = list(
                dict.fromkeys([*quality_flags, "expired_source_blocked"])
            )
        answer_completeness = assess_answer_completeness(
            question=ask_request.question,
            answer=answer_out,
            sources=retrieval_results,
            orchestration_trace=(section_trace if section_grounding else rag_trace),
        )
        # A confirmed procedure/form binding is safe to show independently of
        # provider wording, but it only verifies the form facet.  It must not
        # certify the whole legal answer as fully grounded. It is nevertheless
        # a deterministic, validated answer path rather than a provider
        # fallback, so a form-only request must not be presented as blocked.
        if verified_form_lookup:
            answer_mode = NORMAL
            provider_error_code = None
        else:
            answer_mode = (
                str(section_trace.get("answer_mode") or NORMAL)
                if section_grounding and isinstance(section_trace, Mapping)
                else NORMAL
            )
            provider_error_code = (
                (str(section_trace.get("provider_error_code") or "") or None)
                if section_grounding and isinstance(section_trace, Mapping)
                else None
            )
        provider_fallback_error: dict[str, Any] | None = None
        if answer_mode != NORMAL:
            reason_codes = list(answer_completeness.get("reason_codes") or [])
            reason_codes.append(
                "deterministic_source_view_only"
                if answer_mode == SOURCE_VIEW_ONLY
                else "deterministic_verified_source_condensed"
            )
            answer_completeness = {
                **answer_completeness,
                "status": "incomplete",
                "reason_codes": list(dict.fromkeys(reason_codes)),
            }
            quality_flags = list(dict.fromkeys([
                *quality_flags,
                answer_mode,
            ]))
        clarifying_questions = (
            list(
                dict.fromkeys(
                    [
                        section.clarifying_question
                        for section in (section_answer_sections or [])
                        if section.clarifying_question
                    ]
                    + (
                        list(section_trace.get("clarifying_questions") or [])
                        if isinstance(section_trace, Mapping)
                        else []
                    )
                )
            )[:2]
            if section_grounding
            else clarifying_questions_for_gaps(ask_request.question, evidence_coverage)
        )
        if isinstance(rag_trace, dict):
            rag_trace["question_classification"] = question_policy
            rag_trace["legal_as_of"] = legal_as_of.isoformat()
            rag_trace["evidence_coverage"] = evidence_coverage
            rag_trace["claim_validation"] = claim_validation
            rag_trace["form_provenance"] = _build_form_provenance_trace(
                question=ask_request.question,
                procedure_match=procedure_match,
                accepted_forms=recommended_forms,
            )
            rag_trace["answer_pipeline"] = {
                "retrieved_chunks": len(retrieval_results),
                "llm_sources": len(retrieval_results),
                "citations_created": len(citations or []),
                "grounding_status": grounding_status,
                "answer_completeness_status": answer_completeness["status"],
                "answer_mode": answer_mode,
                "provider_error_code": provider_error_code,
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
            rag_trace.setdefault(
                "forms_accepted",
                rag_trace["form_provenance"]["accepted"],
            )
            rag_trace.setdefault(
                "forms_rejected",
                rag_trace["form_provenance"]["rejected"],
            )
        if soft_domain_mismatch and detected_domain:
            tip = (
                f"\n\n---\nGợi ý: câu hỏi có vẻ thuộc **{detected_domain.get('name')}** "
                f"(cơ quan: **{detected_domain.get('agency')}**). "
                "Bạn nên chuyển sang lĩnh vực này để được hướng dẫn đúng hơn."
            )
            if tip.strip() not in answer_out:
                answer_out = answer_out + tip
        trust_fields = build_public_trust_projection(
            trace=(
                section_trace
                if section_grounding and isinstance(section_trace, Mapping)
                else rag_trace if isinstance(rag_trace, Mapping) else None
            ),
            citations=citations,
            answer_mode=answer_mode,
            provider_label=egress_decision.provider_label,
            provider_mode=egress_decision.mode,
            model_label=egress_decision.model_label,
            redaction_applied=provider_redaction_applied,
        )
        timing_summary = None
        if section_grounding and isinstance(section_trace, Mapping):
            raw_timing = section_trace.get("timing_summary")
            if isinstance(raw_timing, Mapping):
                timing_summary = {
                    key: float(raw_timing[key])
                    for key in (
                        "retrieval_ms",
                        "provisioning_ms",
                        "generation_ms",
                        "validation_ms",
                        "end_to_end_ms",
                    )
                    if isinstance(raw_timing.get(key), (int, float))
                }
        canonical_domain = canonicalize_legal_domain(
            original_selected_domain
            or question_policy.get("detected_domain")
            or (detected_domain or {}).get("slug")
        )
        delivery = _answer_delivery_projection(
            grounding_status=grounding_status,
            answer_completeness=answer_completeness,
            evidence_count=len(retrieval_results),
            clarifying_questions=clarifying_questions,
            provider_error_code=provider_error_code,
            source_gap=source_gap,
            section_trace=(section_trace if isinstance(section_trace, Mapping) else None),
            verified_form_lookup=verified_form_lookup,
        )
        presentation_metadata = _answer_presentation_metadata(
            ask_request.question,
            section_trace if isinstance(section_trace, Mapping) else None,
        )
        response = AskResponse(
            answer=answer_out,
            question=ask_request.question,
            rag_trace=(
                rag_trace
                if ask_request.role == "admin" and ask_request.show_rag_trace
                else None
            ),
            grounding_status=grounding_status,
            canonical_domain=canonical_domain,
            **delivery,
            answer_completeness=answer_completeness,
            answer_mode=answer_mode,
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
            timing_summary=timing_summary,
            trace_id=trace_id,
            error=provider_fallback_error,
            question_type=question_policy["question_type"],
            detected_domain=question_policy.get("detected_domain"),
            required_sections=question_policy.get("required_sections") or [],
            forms_unavailable=_forms_unavailable_for_question(
                ask_request.question,
                recommended_forms,
            ),
            source_gap=source_gap,
            evidence_coverage=evidence_coverage,
            claim_validation=claim_validation,
            authority_status=evidence_coverage.get("authority", {}).get("status", "not_applicable"),
            legal_as_of=legal_as_of,
            clarifying_questions=clarifying_questions,
            quality_flags=quality_flags,
            answer_score_preview=answer_score_preview,
            answer_sections=section_answer_sections if section_grounding else None,
            **presentation_metadata,
            **trust_fields,
        )
        response = AskResponse.model_validate(
            safe_public_response_payload(
                response.model_dump(mode="json"),
                include_admin_trace=(
                    ask_request.role == "admin" and ask_request.show_rag_trace
                ),
            )
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
            detail = dict(detail)
        else:
            detail = {
                "code": "ASK_HTTP_ERROR",
                "message": str(detail),
                "retryable": exc.status_code >= 500,
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
            detail = dict(detail)
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
            },
        ) from e


def _answer_presentation_metadata(
    question: str,
    trace: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Allow-list trace identities needed by the public presentation contract."""

    trace = trace or {}
    raw_route = str(trace.get("answer_route") or "")
    answer_route = (
        raw_route
        if raw_route in {
            "exact_article",
            "procedure_form",
            "general_legal",
            "historical",
        }
        else route_legal_answer(question).answer_route
    )
    runtime_versions = trace.get("runtime_versions")
    runtime_versions = (
        runtime_versions if isinstance(runtime_versions, Mapping) else {}
    )
    return {
        "answer_route": answer_route,
        "pipeline_version": str(
            trace.get("pipeline_version") or ANSWER_PIPELINE_V2_VERSION
        ),
        "data_release_id": (
            str(trace["data_release_id"])
            if trace.get("data_release_id") not in (None, "")
            else None
        ),
        "index_fingerprint": (
            str(runtime_versions["index_fingerprint"])
            if runtime_versions.get("index_fingerprint") not in (None, "")
            else None
        ),
        "validity_snapshot": (
            str(runtime_versions["validity_snapshot_sha256"])
            if runtime_versions.get("validity_snapshot_sha256") not in (None, "")
            else None
        ),
    }


async def _execute_ask_simple(
    ask_request: AskRequest,
    request: Request,
    *,
    progress: ProgressCallback | None = None,
    trace_id_override: str | None = None,
) -> AskResponse:
    from api.unified_chat_service import run_legal_answer_pipeline_v3

    response = await run_legal_answer_pipeline_v3(
        ask_request=ask_request,
        request=request,
        progress=progress,
        trace_id_override=trace_id_override,
        executor=_execute_ask_simple_core,
    )
    await _persist_conversation_answer(
        ask_request,
        request,
        response,
        user_id=get_request_user_id(request),
    )
    return response


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


def _answer_delivery_projection(
    *,
    grounding_status: str,
    answer_completeness: Mapping[str, Any] | None,
    evidence_count: int,
    clarifying_questions: list[str],
    provider_error_code: str | None,
    source_gap: list[str],
    section_trace: Mapping[str, Any] | None,
    verified_form_lookup: bool,
) -> dict[str, Any]:
    retrieval = (
        section_trace.get("retrieval_decision")
        if isinstance(section_trace, Mapping)
        else None
    )
    fallback_tier = str((retrieval or {}).get("fallback_tier") or "")
    if verified_form_lookup:
        fallback_tier = "catalog"
    if clarifying_questions and evidence_count == 0:
        answer_status = "clarifying"
        fallback_tier = "clarification"
    elif evidence_count == 0 and provider_error_code:
        answer_status = "provider_error"
        fallback_tier = "support"
    elif evidence_count == 0:
        answer_status = "source_gap"
        fallback_tier = "support"
    elif fallback_tier == "full_corpus":
        answer_status = "broad_grounded"
    elif (
        grounding_status == "fully_grounded"
        and str((answer_completeness or {}).get("status") or "") == "complete"
    ):
        answer_status = "grounded"
    else:
        answer_status = "partial_grounded"
    if fallback_tier not in {
        "exact", "domain", "expanded", "full_corpus", "catalog",
        "clarification", "support",
    }:
        fallback_tier = "domain" if evidence_count else "support"
    warning = None
    if source_gap:
        warning = (
            "Câu trả lời đã giữ các phần có căn cứ; còn thiếu: "
            + ", ".join(vietnamese_section_names(source_gap))
            + "."
        )
    blocked_reason = None
    if evidence_count == 0:
        blocked_reason = provider_error_code or "approved_current_source_not_found"
    return {
        "answer_status": answer_status,
        "fallback_tier": fallback_tier,
        "evidence_count": evidence_count,
        "coverage_warning": warning,
        "blocked_reason": blocked_reason,
    }


@router.post("/search/ask")
async def ask_knowledge_base(
    ask_request: AskRequest,
    request: Request,
) -> StreamingResponse:
    """Compatibility SSE endpoint with lifecycle and validated final only."""

    enforce_optimized_profile_rollout(request)
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

    enforce_optimized_profile_rollout(request)
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
    enforce_optimized_profile_rollout(request)
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

    file_bytes = await file.read(MAX_VOICE_BYTES + 1)
    voice_policy = UploadPolicy(
        allowed_extensions=frozenset(
            {".webm", ".wav", ".mp3", ".ogg", ".mp4", ".m4a", ".aac", ".flac"}
        ),
        max_bytes=MAX_VOICE_BYTES,
    )
    try:
        validate_upload(filename=file.filename, content=file_bytes, policy=voice_policy)
    except UploadSecurityError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

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
        file_bytes = await file.read(MAX_FILE_BYTES + 1)
        media_policy = UploadPolicy(
            allowed_extensions=frozenset({".txt", ".docx", ".pdf", ".png", ".jpg", ".jpeg"}),
            max_bytes=MAX_FILE_BYTES,
        )
        try:
            validate_upload(filename=file.filename, content=file_bytes, policy=media_policy)
        except UploadSecurityError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc

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
