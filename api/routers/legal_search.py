import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

import httpx
from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api import readiness
from api.admin_config_history import record_config_revision
from api.auth import get_request_role, get_request_user_id
from api.crawlers.legal_document_pipeline import (
    classify_legal_domains,
    fetch_normalized_legal_document,
    normalize_extraction_blocks,
    validate_official_public_url,
)
from api.legal_crawl_service import LEGAL_DOMAINS, LegalCrawlService
from api.legal_domains import canonicalize_legal_domain, legal_domain_values
from api.legal_effectivity_service import (
    get_legal_validity_sync_service,
)
from api.legal_effectivity_service import (
    notify_validity_admin as _notify_validity_admin,
)
from api.legal_lifecycle_service import (
    LifecycleError,
    default_lifecycle_service,
    lifecycle_capabilities,
)
from api.legal_replacement_candidates import project_replacement_candidates
from api.legal_validity_registry import (
    apply_validity_overlay,
    default_registry,
    default_snapshot_cache,
    project_validity_for_row,
)
from api.observability import telemetry
from api.upload_security import (
    DOCUMENT_UPLOAD_POLICY,
    UploadSecurityError,
    validate_upload,
    write_validated_upload,
)
from api.rag_anything_adapter import RagAnythingUnavailable, extract_with_rag_anything
from api.user_service import get_user_profile, list_audit_logs, write_audit_log
from api.utils.pii_detector import redact_upload_text
from open_notebook.database.repository import (
    ensure_record_id,
    repo_create,
    repo_query,
    repo_update,
)

router = APIRouter(prefix="/legal", tags=["legal-search"])
LEGAL_SEARCH_URL = os.getenv(
    "LEGAL_SEARCH_URL", "http://127.0.0.1:8765"
).rstrip("/")

_lifecycle_service = default_lifecycle_service


def _serving_audience(request: Request) -> str:
    """Derive serving access from authenticated backend state, never input."""

    role = str(get_request_role(request) or "citizen").strip().casefold()
    return role if role in {"citizen", "officer", "admin", "system"} else "citizen"


class LifecycleDraftCreateRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    logical_document_id: int | None = Field(default=None, ge=1)
    base_fingerprint: str | None = Field(default=None, max_length=128)
    title: str = Field(default="", max_length=2000)
    law_number: str = Field(default="", max_length=255)
    document_type: str = Field(default="", max_length=255)
    issuing_agency: str = Field(default="", max_length=1000)
    scope: str = Field(default="", max_length=255)
    sector: str = Field(default="", max_length=255)
    issued_date: str | None = Field(default=None, max_length=32)
    effective_date: str | None = Field(default=None, max_length=32)
    expired_date: str | None = Field(default=None, max_length=32)
    source_url: str = Field(default="", max_length=4000)
    source_asset: str | None = Field(default=None, max_length=4000)
    content: str = Field(default="", max_length=2_000_000)


class LifecycleDraftUpdateRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    title: str | None = Field(default=None, max_length=2000)
    law_number: str | None = Field(default=None, max_length=255)
    document_type: str | None = Field(default=None, max_length=255)
    issuing_agency: str | None = Field(default=None, max_length=1000)
    scope: str | None = Field(default=None, max_length=255)
    sector: str | None = Field(default=None, max_length=255)
    issued_date: str | None = Field(default=None, max_length=32)
    effective_date: str | None = Field(default=None, max_length=32)
    expired_date: str | None = Field(default=None, max_length=32)
    source_url: str | None = Field(default=None, max_length=4000)
    source_asset: str | None = Field(default=None, max_length=4000)
    content: str | None = Field(default=None, max_length=2_000_000)


class LifecycleTransitionRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)


class LifecycleReviewRequest(LifecycleTransitionRequest):
    decision: Literal["approved", "rejected", "changes_requested"]


def _lifecycle_actor(request: Request) -> tuple[str, str]:
    actor = str(get_request_user_id(request) or "").strip()
    role = str(get_request_role(request) or "").strip().lower()
    if not actor or role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"code": "lifecycle_forbidden", "message": "Chỉ tài khoản Admin được cấu hình mới truy cập workflow vòng đời."},
        )
    return actor, role


def _lifecycle_revision(if_match: str) -> int:
    value = str(if_match or "").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if not value.isdigit() or int(value) < 1:
        raise HTTPException(
            status_code=400,
            detail={"code": "lifecycle_if_match_invalid", "message": "If-Match phải chứa revision dương hiện tại."},
        )
    return int(value)


async def _lifecycle_result(awaitable: Any) -> Any:
    try:
        return await awaitable
    except LifecycleError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

_MANAGEMENT_LIST_FIELDS = {
    "doc_id",
    "document_title",
    "law_number",
    "document_type",
    "issuing_agency",
    "scope",
    "sector",
    "stored_status",
    "as_of_status",
    "validity_status",
    "validity_sync",
    "serving_status",
    "current_answer_eligible",
    "historical_lookup_allowed",
    "issued_date",
    "effective_date",
    "expired_date",
    "source_url",
    "version",
    "field_id",
    "field_name",
    "domain",
    "domain_name",
    "retrieval_tier",
    "article_count",
    "chunk_count",
    "quality_flags",
}
_MANAGEMENT_DOCUMENT_FIELDS = _MANAGEMENT_LIST_FIELDS | {
    "created_at",
    "source_id",
    "collection_source",
    "gazette_date",
    "signer_title",
    "signer_name",
    "applicability_info",
}


def _management_unavailable(reason_code: str, message: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "reason_code": reason_code,
        "message": message,
    }


async def _management_optional_read(
    awaitable: Any,
    *,
    reason_code: str,
    message: str,
    timeout_seconds: float = 5.0,
) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
    except Exception:
        return _management_unavailable(reason_code, message)


async def _management_required_read(
    path: str,
    *,
    message: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read one required projection without exposing upstream error details."""

    try:
        payload = await _request("GET", path, **({"params": params} if params else {}))
    except HTTPException as exc:
        status_code = 404 if exc.status_code == 404 else 503
        raise HTTPException(status_code=status_code, detail=message) from None
    except Exception:
        raise HTTPException(status_code=503, detail=message) from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=503, detail=message)
    return payload


def _management_safe_list_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    return {key: item.get(key) for key in _MANAGEMENT_LIST_FIELDS if key in item}


def _management_safe_document(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    return {key: item.get(key) for key in _MANAGEMENT_DOCUMENT_FIELDS if key in item}


def _management_validity_projection(
    item: Any,
    *,
    snapshot: dict[str, Any] | None,
    as_of: date,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    projected = dict(item)
    validity = project_validity_for_row(
        projected,
        snapshot=snapshot,
        as_of=as_of,
        mode="protect",
    )
    projected["validity_sync"] = validity
    projected["validity_status"] = validity["status"]
    projected["as_of_status"] = (
        "active"
        if validity.get("warning_code") == "historical_validity"
        and validity.get("current_answer_eligible")
        else validity["status"]
    )
    projected["serving_status"] = validity["serving_action"]
    projected["current_answer_eligible"] = validity["current_answer_eligible"]
    projected["historical_lookup_allowed"] = validity["historical_lookup_allowed"]
    return projected


def _management_document_id(value: str) -> str:
    cleaned = str(value or "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", cleaned):
        raise HTTPException(status_code=400, detail="Mã văn bản không hợp lệ.")
    return cleaned


async def _crawl_source_snapshot(source_id: str) -> dict[str, Any]:
    """Return a safe single-source configuration snapshot for Admin restore history."""
    rows = await LegalCrawlService.list_sources()
    for source in rows:
        if str(source.get("id") or "") == str(source_id):
            return {
                key: source.get(key)
                for key in (
                    "id", "name", "source_type", "sitemap_scope", "base_url", "enabled",
                    "interval_minutes", "lookback_days", "max_documents_per_run",
                    "max_listing_pages_per_run", "filter_keyword", "rate_limit_seconds",
                    "content_fetch_allowed",
                )
            }
    return {}


async def _require_import_plane_ready() -> None:
    """Do not create durable import jobs when their worker cannot consume them."""
    report = await readiness.collect_import_readiness()
    if report.get("status") == "ready":
        return
    raise HTTPException(
        status_code=503,
        detail={
            "code": "import_pipeline_not_ready",
            "message": "Hệ thống nhập kho chưa sẵn sàng; chưa tạo job embedding.",
            "suggestion": "Kiểm tra database, legal retrieval và worker nhập kho rồi thử lại.",
            "components": report.get("components", {}),
        },
    )


class LegalSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    limit: int = Field(default=8, ge=1, le=30)
    candidate_count: int = Field(default=120, ge=20, le=500)
    domain: str | None = None
    include_trace: bool = False


class LegalImportRequest(BaseModel):
    title: str
    law_number: str
    document_type: str
    issuing_agency: str
    scope: str
    sector: str = ""
    field_id: int
    issued_date: str | None = None
    effective_date: str
    expired_date: str | None = None
    source_url: str = ""
    applicability_info: str = ""
    content: str
    confirmed_official_source: bool = False
    domain_slug: str | None = Field(
        default=None,
        pattern="^[a-z0-9_]+$",
        max_length=80,
    )


LEGAL_IMPORT_FIELD_DOMAINS = {
    6: "khieu_nai_to_cao_xu_phat",
    7: "ho_tich_chung_thuc",
    8: "dat_dai_xay_dung",
    9: "cu_tru_an_ninh",
    10: "khieu_nai_to_cao_xu_phat",
}
LEGAL_IMPORT_DOMAIN_SLUGS = frozenset(
    {
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "an_sinh_y_te_giao_duc",
        "hanh_chinh_cong",
        "noi_vu_hanh_chinh",
        "trat_tu_do_thi",
        "cu_tru_an_ninh",
        "khieu_nai_to_cao_xu_phat",
        "xay_dung_do_thi",
    }
)


def _resolved_admin_import_domain(
    *, field_id: int, requested_domain: str | None, classified_domains: list[str]
) -> str | None:
    """Keep the Admin's reviewed field/domain authoritative over text hints."""

    requested = str(requested_domain or "").strip()
    if requested and requested not in LEGAL_IMPORT_DOMAIN_SLUGS:
        raise ValueError("LEGAL_IMPORT_DOMAIN_UNSUPPORTED")
    mapped = LEGAL_IMPORT_FIELD_DOMAINS.get(int(field_id))
    if requested and mapped and requested != mapped:
        raise ValueError("LEGAL_IMPORT_FIELD_DOMAIN_MISMATCH")
    return requested or mapped or (classified_domains[0] if classified_domains else None)


class LegalCrawlPreviewRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4000)


class LegalCrawlSourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=3, max_length=255)
    base_url: str | None = Field(default=None, min_length=8, max_length=4000)
    sitemap_scope: str | None = Field(default=None, pattern="^(central|haiphong|local)$")
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=15, le=10080)
    lookback_days: int | None = Field(default=None, ge=1, le=3650)
    max_documents_per_run: int | None = Field(default=None, ge=1, le=200)
    max_listing_pages_per_run: int | None = Field(default=None, ge=1, le=50)
    rate_limit_seconds: float | None = Field(default=None, ge=0.2, le=30)
    filter_keyword: str | None = Field(default=None, max_length=255)
    content_fetch_allowed: bool | None = None


class LegalCrawlSourceCreateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=255)
    base_url: str = Field(min_length=8, max_length=4000)
    sitemap_scope: str = Field(pattern="^(central|haiphong|local)$")
    interval_minutes: int = Field(default=10080, ge=15, le=10080)
    lookback_days: int = Field(default=30, ge=1, le=3650)
    max_documents_per_run: int = Field(default=30, ge=1, le=200)
    max_listing_pages_per_run: int = Field(default=10, ge=1, le=50)
    rate_limit_seconds: float = Field(default=1.5, ge=0.2, le=30)
    filter_keyword: str | None = Field(default=None, max_length=255)


class LegalCandidateDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|changes_requested)$")
    review_note: str = Field(default="", max_length=2000)


class LegalCandidateMetadataUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=5, max_length=2000)
    law_number: str | None = Field(default=None, max_length=255)
    document_type: str | None = Field(default=None, max_length=255)
    issuing_agency: str | None = Field(default=None, max_length=255)
    scope: str | None = Field(default=None, pattern="^(central|haiphong|local)$")
    sector: str | None = Field(default=None, max_length=255)
    issued_date: str | None = Field(default=None, max_length=20)
    effective_date: str | None = Field(default=None, max_length=20)
    expired_date: str | None = Field(default=None, max_length=20)
    source_url: str | None = Field(default=None, max_length=4000)
    confirmed_official_source: bool | None = None


class LegalValidityRunRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    scope: list[Literal["central", "haiphong", "local"]] = Field(
        default_factory=lambda: ["central", "haiphong", "local"],
        min_length=1,
        max_length=3,
    )
    limit: int = Field(default=100, ge=1, le=100)


class LegalValidityDecisionRequest(BaseModel):
    action: Literal["confirm_mapping", "reject_match", "request_recheck"]
    reason: str = Field(min_length=10, max_length=2000)


class LegalVectorCleanupRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)


class LegalValidityCoverageResponse(BaseModel):
    eligible: int = Field(ge=0)
    observed: int = Field(ge=0)
    fresh: int = Field(ge=0)


class LegalValidityCountsResponse(BaseModel):
    active: int = Field(ge=0)
    blocked: int = Field(ge=0)
    warning: int = Field(ge=0)
    open_events: int = Field(ge=0)


class LegalValiditySourceHealthResponse(BaseModel):
    source_kind: str
    status: Literal["healthy", "degraded"]
    last_success_at: str | None = None
    reason_code: str | None = None


class LegalValidityStatusResponse(BaseModel):
    mode: Literal["observe", "protect", "strict"]
    status: Literal["healthy", "degraded", "stale", "missing", "disabled"]
    generated_at: str | None = None
    last_success_at: str | None = None
    next_run_at: str | None = None
    coverage: LegalValidityCoverageResponse
    counts: LegalValidityCountsResponse
    sources: list[LegalValiditySourceHealthResponse]
    reason_code: str | None = None
    age_seconds: float | None = None


class LegalValidityRunResponse(BaseModel):
    status: str
    checked_at: str | None = None
    last_success_at: str | None = None
    mode: str | None = None
    trigger: str | None = None
    scanned: dict[str, int] = Field(default_factory=dict)
    observations_created: int = 0
    events_created: int = 0
    failures: dict[str, int] = Field(default_factory=dict)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: float | None = None
    next_run_at: str | None = None
    consecutive_failures: int = 0
    recovered: bool = False
    inventory_offset: int = 0
    next_offset: int = 0
    limitation: str | None = None


class LegalValidityEventResponse(BaseModel):
    id: str
    document_id: str | None = None
    document_title: str | None = None
    law_number: str
    issuing_agency: str | None = None
    issued_date: str | None = None
    scope: str | None = None
    event_type: str | None = None
    severity: str
    review_status: str
    serving_action: str | None = None
    source_url: str | None = None
    raw_status: str | None = None
    normalized_status: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    affected_provisions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class LegalValidityEventPageResponse(BaseModel):
    items: list[LegalValidityEventResponse]
    next_cursor: str | None = None


class LegalValidityDecisionResponse(BaseModel):
    event: LegalValidityEventResponse | None
    decision: dict[str, Any]


async def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=900) as client:
            response = await client.request(
                method, f"{LEGAL_SEARCH_URL}{path}", **kwargs
            )
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Dịch vụ tìm kiếm pháp luật chưa sẵn sàng: {exc}",
        ) from exc


DOMAIN_ALIASES: dict[str, set[str]] = {
    "ho_tich_chung_thuc": {"ho_tich_chung_thuc", "ho_tich", "chung_thuc"},
    "dat_dai_xay_dung": {"dat_dai_xay_dung", "dat_dai", "xay_dung"},
    "an_sinh_y_te_giao_duc": {"an_sinh_y_te_giao_duc", "an_sinh", "y_te", "giao_duc"},
    "hanh_chinh_cong": {"hanh_chinh_cong", "hanh_chinh"},
    "cu_tru_an_ninh": {"cu_tru_an_ninh", "cu_tru", "an_ninh"},
    "khieu_nai_to_cao_xu_phat": {
        "khieu_nai_to_cao_xu_phat",
        "khieu_nai",
        "to_cao",
        "xu_phat",
    },
    "trat_tu_do_thi": {"trat_tu_do_thi", "trat_tu"},
}

def _canonical_domain(value: Any) -> str | None:
    domain = str(value or "").strip()
    if not domain:
        return None
    domain = canonicalize_legal_domain(domain) or domain
    if domain in LEGAL_DOMAINS:
        return domain
    for canonical, aliases in DOMAIN_ALIASES.items():
        if domain in aliases:
            return canonical
    return None


def _canonical_domains(values: Any) -> set[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set, frozenset)):
        return set()
    return {
        canonical
        for item in values
        if (canonical := _canonical_domain(item)) is not None
    }


def _candidate_domains(candidate: dict[str, Any]) -> set[str]:
    raw = candidate.get("raw_metadata") or {}
    matched = raw.get("matched_domains")
    domains = _canonical_domains(matched)
    if domains:
        return domains
    return _canonical_domains([candidate.get("domain"), raw.get("domain")])


def _officer_can_view_candidate(
    candidate: dict[str, Any], allowed_domains: list[str]
) -> bool:
    return bool(_candidate_domains(candidate).intersection(_canonical_domains(allowed_domains)))


def _same_user_account(left: Any, right: Any) -> bool:
    def token(value: Any) -> str:
        text = str(value or "").strip()
        return text.split(":", 1)[-1] if ":" in text else text

    return bool(token(left)) and token(left) == token(right)


def _officer_candidate_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    projected = dict(candidate)
    extraction = projected.pop("extraction_result", None) or {}
    preview = str(extraction.get("preview") or "").strip()
    projected.pop("content", None)
    projected.pop("ai_assessment", None)
    projected.pop("review_recommendation", None)
    projected.pop("uploaded_file", None)
    projected.pop("source_asset", None)
    projected["matched_domains"] = sorted(_candidate_domains(candidate))
    if preview:
        projected["content_preview"] = preview[:500]
    return projected


def _require_crawl_admin(request: Request) -> None:
    """Restrict candidate-crawler administration to the final reviewer role."""
    if get_request_role(request) != "admin":
        raise HTTPException(
            status_code=403,
            detail="Chỉ admin được cấu hình, quét, duyệt hoặc import candidate crawler.",
        )


def _domain_allowed(domain: str, allowed_domains: list[str]) -> bool:
    domain_norm = domain.strip()
    if not allowed_domains:
        return False
    if domain_norm in allowed_domains:
        return True
    aliases = set(legal_domain_values(domain_norm)) or DOMAIN_ALIASES.get(domain_norm, {domain_norm})
    return any(
        allowed in aliases or domain_norm in DOMAIN_ALIASES.get(allowed, {allowed})
        for allowed in allowed_domains
    )


def _safe_upload_name(filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(filename).name).strip(".-")
    return safe or "proposal-upload"


def _user_account_record(value: str) -> Any:
    text = str(value).strip()
    if text.startswith("user_account:"):
        return ensure_record_id(text)
    if ":" in text:
        text = text.split(":", 1)[1]
    return ensure_record_id(f"user_account:{text}")


async def _save_proposal_upload(
    file: UploadFile,
    content: bytes,
    *,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    validated = validate_upload(
        filename=file.filename or "",
        content=content,
        policy=DOCUMENT_UPLOAD_POLICY,
        expected_sha256=expected_sha256,
    )
    upload_dir = Path("data") / "uploads" / "officer_proposals"
    safe_name = _safe_upload_name(validated.filename)
    stem = Path(safe_name).stem or "proposal-upload"
    suffix = Path(safe_name).suffix or ".bin"
    target = upload_dir / f"{stem}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}{suffix}"
    write_validated_upload(root=upload_dir, storage_name=target.name, content=content)
    return {
        "filename": validated.filename,
        "content_type": validated.detected_type,
        "size": len(content),
        "path": str(target),
        "sha256": validated.sha256,
        "malware_scan": validated.scan_engine,
    }


def _text_review_metadata(
    text: str,
    *,
    filename: str,
    extractor_used: str,
    language: str = "vie_or_unicode_text",
) -> dict[str, Any]:
    """Attach stable review evidence without altering valid Unicode text."""
    normalized = text.strip()
    return {
        "status": "ok" if normalized else "review_required",
        "extractor_used": extractor_used,
        "filename": filename,
        "pdf_kind": "not_pdf",
        "ocr_status": "not_applicable",
        "ocr_confidence": None,
        "language": language,
        "page_count": 0,
        "preview": normalized[:2000],
        "text_fingerprint": hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None,
    }


def _extract_upload_text(filename: str, content: bytes) -> tuple[str, dict[str, Any]]:
    """Extract review-only candidate text; OCR errors never become text content."""
    lower_name = filename.lower()
    if lower_name.endswith((".txt", ".md")):
        text = content.decode("utf-8", errors="replace")
        return text, _text_review_metadata(text, filename=filename, extractor_used="utf8_text")
    if lower_name.endswith(".docx"):
        text = _extract_docx(content)
        return text, _text_review_metadata(text, filename=filename, extractor_used="python_docx")
    if lower_name.endswith(".pdf"):
        from api.crawlers.pdf_extractor import extract_pdf_bytes_for_review

        result = extract_pdf_bytes_for_review(content, filename=filename)
        return str(result.get("text") or ""), result
    raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file TXT, MD, DOCX hoặc PDF.")


def _redact_extraction_blocks(blocks: Any) -> list[dict[str, Any]]:
    """Prevent raw upload PII from surviving inside layout metadata."""

    safe: list[dict[str, Any]] = []
    for raw in blocks or []:
        if not isinstance(raw, dict):
            continue
        block = dict(raw)
        redaction = redact_upload_text(str(block.get("text") or ""))
        block["text"] = redaction["text"]
        if redaction["masked"]:
            block.update(
                {
                    "text_hash": hashlib.sha256(
                        str(redaction["text"]).encode("utf-8")
                    ).hexdigest(),
                    "char_start": None,
                    "char_end": None,
                    "bounding_box": None,
                    "citation_level": "metadata_only",
                    "verification_status": "redacted",
                    "verification_reason": "PII_REDACTED_LAYOUT_BLOCK",
                }
            )
        safe.append(block)
    return safe


async def _candidate_source_for(scope: str = "officer_proposal") -> Any:
    rows = await repo_query(
        "SELECT * FROM legal_crawl_source WHERE source_type = 'officer_proposal' LIMIT 1;"
    )
    if rows:
        source = rows[0]
        # This record is a review-queue owner, not a crawlable website. Older
        # versions marked it enabled, which made the weekly scheduler request
        # ``local://officer-proposals/robots.txt`` and report a false failure.
        if source.get("enabled") or source.get("last_error") or source.get("last_status") == "failed":
            await repo_update(
                "legal_crawl_source",
                source["id"],
                {
                    "enabled": False,
                    "last_error": None,
                    "last_status": "internal_queue",
                    "updated": datetime.now(timezone.utc),
                },
            )
        return ensure_record_id(rows[0]["id"])
    created = await repo_create(
        "legal_crawl_source",
        {
            "name": "Officer document proposals",
            "source_type": "officer_proposal",
            "sitemap_scope": scope,
            "base_url": "local://officer-proposals",
            "enabled": True,
            "interval_minutes": 1440,
            "lookback_days": 3650,
            "max_documents_per_run": 0,
            "filter_keyword": None,
            "last_checked_at": None,
            "last_success_at": None,
            "last_error": None,
        },
    )
    row = created[0] if isinstance(created, list) else created
    return ensure_record_id(row["id"])


async def _duplicate_candidates(
    law_number: str | None,
    source_url: str | None,
    content_hash: str | None,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    params: dict[str, Any] = {"limit": 5}
    if law_number:
        filters.append("law_number = $law_number")
        params["law_number"] = law_number
    if source_url:
        filters.append("source_url = $source_url")
        params["source_url"] = source_url
    if content_hash:
        filters.append("content_hash = $content_hash")
        params["content_hash"] = content_hash
    if not filters:
        return []
    rows = await repo_query(
        f"SELECT id, title, law_number, status, review_status, source_url, external_id FROM legal_crawl_candidate WHERE {' OR '.join(filters)} LIMIT $limit;",
        params,
    )
    return [
        {
            "id": str(row.get("id")),
            "title": row.get("title"),
            "law_number": row.get("law_number"),
            "status": row.get("status"),
            "source_url": row.get("source_url"),
            "external_id": row.get("external_id"),
            "review_status": row.get("review_status"),
        }
        for row in rows or []
    ]


def _legal_validity_flags(
    *,
    effective_date: str | None,
    expired_date: str | None,
    source_url: str | None,
) -> dict[str, Any]:
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "has_source_url": bool(source_url),
        "has_effective_date": bool(effective_date),
        "has_expired_date": bool(expired_date),
        "appears_not_yet_effective": bool(effective_date and effective_date > today),
        "appears_expired": bool(expired_date and expired_date <= today),
        "requires_admin_legal_check": True,
    }


def _candidate_review_preview(candidate: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = candidate.get("raw_metadata") or {}
    content = candidate.get("content") or ""
    return {
        "title": candidate.get("title"),
        "law_number": candidate.get("law_number"),
        "domain": candidate.get("domain") or raw_metadata.get("domain"),
        "source_type": candidate.get("source_type") or raw_metadata.get("source_type"),
        "submitted_by": str(candidate.get("submitted_by") or raw_metadata.get("submitted_by") or ""),
        "proposal_reason": candidate.get("proposal_reason") or raw_metadata.get("proposal_reason"),
        "metadata": raw_metadata,
        "extraction_result": candidate.get("extraction_result") or {},
        "duplicates": candidate.get("duplicate_candidates") or [],
        "legal_validity_flags": candidate.get("legal_validity_flags") or {},
        "review_recommendation": candidate.get("review_recommendation") or {},
        "ai_assessment": candidate.get("ai_assessment") or raw_metadata.get("ai_assessment") or {},
        "content_preview": (candidate.get("extraction_result") or {}).get("preview") or content[:2000],
        "content_characters": len(content),
        "status": candidate.get("status"),
        "review_status": candidate.get("review_status"),
    }


@router.get("/health")
async def legal_search_health() -> dict[str, Any]:
    return await _request("GET", "/health")


@router.post("/search")
async def legal_search(request: LegalSearchRequest, raw_request: Request) -> dict[str, Any]:
    request_payload = request.model_dump()
    request_payload["audience"] = _serving_audience(raw_request)
    payload = await _request("POST", "/search", json=request_payload)
    return apply_validity_overlay(
        payload,
        snapshot=default_snapshot_cache.load(),
        as_of=date.today(),
    )


@router.get("/import/fields")
async def legal_import_fields() -> dict[str, Any]:
    return await _request("GET", "/import/fields")


@router.get("/domains")
async def legal_domains() -> dict[str, Any]:
    return await _request("GET", "/domains")


@router.get("/documents/lookup")
async def legal_document_lookup(law_number: str, raw_request: Request) -> dict[str, Any]:
    return await _request(
        "GET", "/documents/lookup",
        params={"law_number": law_number, "audience": _serving_audience(raw_request)},
    )


@router.get("/documents")
async def list_legal_documents(
    raw_request: Request,
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    as_of: str | None = Query(default=None, max_length=10),
    issued_from: date | None = None,
    issued_to: date | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    expired_from: date | None = None,
    expired_to: date | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(
        default="effective_date",
        pattern="^(effective_date|issued_date|title|law_number)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> dict[str, Any]:
    """List effective documents from the same corpus used by legal RAG."""
    for date_from, date_to in (
        (issued_from, issued_to),
        (effective_from, effective_to),
        (expired_from, expired_to),
    ):
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_document_date_range",
                    "message": "Ngày bắt đầu không được sau ngày kết thúc.",
                },
            )
    params: dict[str, Any] = {
        "q": q,
        "tier": tier,
        "limit": limit,
        "offset": offset,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "audience": _serving_audience(raw_request),
    }
    if domain:
        params["domain"] = domain
    if as_of:
        params["as_of"] = as_of
    for key, value in (
        ("issued_from", issued_from),
        ("issued_to", issued_to),
        ("effective_from", effective_from),
        ("effective_to", effective_to),
        ("expired_from", expired_from),
        ("expired_to", expired_to),
    ):
        if value is not None:
            params[key] = value.isoformat()
    payload = await _request("GET", "/documents", params=params)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload
    try:
        legal_as_of = date.fromisoformat(as_of) if as_of else date.today()
    except ValueError:
        legal_as_of = date.today()
    overlaid = apply_validity_overlay(
        {"results": payload["items"]},
        snapshot=default_snapshot_cache.load(),
        as_of=legal_as_of,
    )
    return {
        **payload,
        "items": overlaid["results"],
        "validity_sync": overlaid["validity_sync"],
    }


@router.get("/documents/export.xlsx")
async def export_legal_documents_xlsx(
    raw_request: Request,
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    as_of: str | None = Query(default=None, max_length=10),
    issued_from: date | None = None,
    issued_to: date | None = None,
    effective_from: date | None = None,
    effective_to: date | None = None,
    expired_from: date | None = None,
    expired_to: date | None = None,
    sort_by: str = Query(
        default="effective_date",
        pattern="^(effective_date|issued_date|title|law_number)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> Response:
    """Proxy a filtered workbook while deriving serving scope from authentication."""

    for date_from, date_to in (
        (issued_from, issued_to),
        (effective_from, effective_to),
        (expired_from, expired_to),
    ):
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_document_date_range",
                    "message": "Ngày bắt đầu không được sau ngày kết thúc.",
                },
            )

    params: dict[str, Any] = {
        "q": q,
        "tier": tier,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "audience": _serving_audience(raw_request),
    }
    if domain:
        params["domain"] = domain
    if as_of:
        params["as_of"] = as_of
    for key, value in (
        ("issued_from", issued_from),
        ("issued_to", issued_to),
        ("effective_from", effective_from),
        ("effective_to", effective_to),
        ("expired_from", expired_from),
        ("expired_to", expired_to),
    ):
        if value is not None:
            params[key] = value.isoformat()

    try:
        async with httpx.AsyncClient(timeout=900) as client:
            upstream = await client.get(
                f"{LEGAL_SEARCH_URL}/documents/export.xlsx",
                params=params,
            )
        upstream.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", exc.response.text)
        except (ValueError, AttributeError):
            detail = exc.response.text
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=detail,
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Dịch vụ xuất kho văn bản chưa sẵn sàng: {exc}",
        ) from exc

    return Response(
        content=upstream.content,
        media_type=upstream.headers.get(
            "content-type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        headers={
            "Content-Disposition": upstream.headers.get(
                "content-disposition",
                'attachment; filename="danh-sach-van-ban.xlsx"',
            ),
            "X-Record-Count": upstream.headers.get("x-record-count", ""),
        },
    )


@router.get("/lifecycle/capabilities")
async def legal_lifecycle_capabilities(raw_request: Request) -> dict[str, Any]:
    actor, role = _lifecycle_actor(raw_request)
    return lifecycle_capabilities(actor, role)


@router.get("/lifecycle/drafts")
async def legal_lifecycle_drafts(
    raw_request: Request,
    state: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    actor, role = _lifecycle_actor(raw_request)
    return await _lifecycle_result(
        _lifecycle_service.list_drafts(
            actor=actor, role=role, state=state, limit=limit, offset=offset
        )
    )


@router.get("/lifecycle/drafts/{draft_id}")
async def legal_lifecycle_draft(draft_id: str, raw_request: Request) -> dict[str, Any]:
    actor, role = _lifecycle_actor(raw_request)
    return await _lifecycle_result(
        _lifecycle_service.get_draft(draft_id, actor=actor, role=role)
    )


@router.post("/lifecycle/drafts", status_code=201)
async def legal_lifecycle_create_draft(
    payload: LifecycleDraftCreateRequest,
    raw_request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, Any]:
    actor, role = _lifecycle_actor(raw_request)
    result = await _lifecycle_result(
        _lifecycle_service.create_draft(
            actor=actor,
            role=role,
            payload=payload.model_dump(exclude={"reason"}, exclude_none=True),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    )
    response.headers["ETag"] = f'"{result["revision"]}"'
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.put("/lifecycle/drafts/{draft_id}")
async def legal_lifecycle_update_draft(
    draft_id: str,
    payload: LifecycleDraftUpdateRequest,
    raw_request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    if_match: str = Header(alias="If-Match"),
) -> dict[str, Any]:
    actor, role = _lifecycle_actor(raw_request)
    result = await _lifecycle_result(
        _lifecycle_service.update_draft(
            draft_id,
            actor=actor,
            role=role,
            payload=payload.model_dump(exclude={"reason"}, exclude_none=True, exclude_unset=True),
            expected_revision=_lifecycle_revision(if_match),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    )
    response.headers["ETag"] = f'"{result["revision"]}"'
    response.headers["Cache-Control"] = "private, no-store"
    return result


async def _legal_lifecycle_transition(
    *,
    action: str,
    draft_id: str,
    payload: LifecycleTransitionRequest,
    raw_request: Request,
    response: Response,
    idempotency_key: str,
    if_match: str,
) -> dict[str, Any]:
    actor, role = _lifecycle_actor(raw_request)
    method = {
        "validate": _lifecycle_service.validate_draft,
        "submit": _lifecycle_service.submit_draft,
        "activate": _lifecycle_service.activate_draft,
    }[action]
    result = await _lifecycle_result(
        method(
            draft_id,
            actor=actor,
            role=role,
            expected_revision=_lifecycle_revision(if_match),
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    )
    response.headers["ETag"] = f'"{result["revision"]}"'
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.post("/lifecycle/drafts/{draft_id}/validate")
async def legal_lifecycle_validate_draft(
    draft_id: str,
    payload: LifecycleTransitionRequest,
    raw_request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    if_match: str = Header(alias="If-Match"),
) -> dict[str, Any]:
    return await _legal_lifecycle_transition(
        action="validate", draft_id=draft_id, payload=payload,
        raw_request=raw_request, response=response,
        idempotency_key=idempotency_key, if_match=if_match,
    )


@router.post("/lifecycle/drafts/{draft_id}/submit")
async def legal_lifecycle_submit_draft(
    draft_id: str,
    payload: LifecycleTransitionRequest,
    raw_request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    if_match: str = Header(alias="If-Match"),
) -> dict[str, Any]:
    return await _legal_lifecycle_transition(
        action="submit", draft_id=draft_id, payload=payload,
        raw_request=raw_request, response=response,
        idempotency_key=idempotency_key, if_match=if_match,
    )


@router.post("/lifecycle/drafts/{draft_id}/review")
async def legal_lifecycle_review_draft(
    draft_id: str,
    payload: LifecycleReviewRequest,
    raw_request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    if_match: str = Header(alias="If-Match"),
) -> dict[str, Any]:
    actor, role = _lifecycle_actor(raw_request)
    result = await _lifecycle_result(
        _lifecycle_service.review_draft(
            draft_id,
            actor=actor,
            role=role,
            expected_revision=_lifecycle_revision(if_match),
            decision=payload.decision,
            reason=payload.reason,
            idempotency_key=idempotency_key,
        )
    )
    response.headers["ETag"] = f'"{result["revision"]}"'
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.get("/lifecycle/drafts/{draft_id}/activation-preview")
async def legal_lifecycle_activation_preview(
    draft_id: str, raw_request: Request
) -> dict[str, Any]:
    actor, role = _lifecycle_actor(raw_request)
    return await _lifecycle_result(
        _lifecycle_service.activation_preview(draft_id, actor=actor, role=role)
    )


@router.post("/lifecycle/drafts/{draft_id}/activate")
async def legal_lifecycle_activate_draft(
    draft_id: str,
    payload: LifecycleTransitionRequest,
    raw_request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    if_match: str = Header(alias="If-Match"),
) -> dict[str, Any]:
    return await _legal_lifecycle_transition(
        action="activate", draft_id=draft_id, payload=payload,
        raw_request=raw_request, response=response,
        idempotency_key=idempotency_key, if_match=if_match,
    )


@router.get("/management/summary")
async def legal_management_summary(raw_request: Request) -> dict[str, Any]:
    """Compose Admin-only repository health without writing any backing store."""

    _require_crawl_admin(raw_request)
    observed_as_of = date.today().isoformat()
    base = await _management_required_read(
        "/management/summary",
        params={"as_of": observed_as_of},
        message="Không thể đọc tổng quan kho văn bản.",
    )
    operations, validity = await asyncio.gather(
        _management_optional_read(
            LegalCrawlService.summary(),
            reason_code="operations_store_unavailable",
            message="Không thể đọc hàng chờ crawl/import tại thời điểm này.",
        ),
        _management_optional_read(
            get_legal_validity_sync_service().status(),
            reason_code="validity_store_unavailable",
            message="Không thể đọc ảnh chụp hiệu lực tại thời điểm này.",
        ),
    )
    if isinstance(operations, dict) and operations.get("status") != "unavailable":
        operations = {
            "status": "available",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            **operations,
        }
    if isinstance(validity, dict) and validity.get("status") != "unavailable":
        validity = {
            "status": "available",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "service_status": validity.get("status"),
            "mode": validity.get("mode"),
            "coverage": validity.get("coverage") or {},
            "counts": validity.get("counts") or {},
            "sources": validity.get("sources") or [],
            "last_success_at": validity.get("last_success_at"),
        }
    return {
        "observed_at": base.get("observed_at")
        or datetime.now(timezone.utc).isoformat(),
        "as_of": base.get("as_of") or observed_as_of,
        "documents": base.get("documents") or {},
        "structure": base.get("structure") or {},
        "tiers": base.get("tiers") or {},
        "vectors": base.get("vectors")
        or _management_unavailable(
            "vector_store_unavailable", "Không thể đọc trạng thái kho vector."
        ),
        "validity": validity,
        "operations": operations,
        "faq_impacts": _management_unavailable(
            "faq_dependency_not_configured",
            "Chưa có schema phụ thuộc FAQ được phê duyệt.",
        ),
    }


@router.get("/management/documents")
async def legal_management_documents(
    raw_request: Request,
    q: str = Query(default="", max_length=500),
    document_type: str | None = Query(default=None, max_length=255),
    issuing_agency: str | None = Query(default=None, max_length=255),
    scope: str | None = Query(default=None, max_length=120),
    domain: str | None = Query(default=None, max_length=120),
    stored_status: str | None = Query(default=None, max_length=80),
    validity_status: str | None = Query(
        default=None, pattern="^(active|not_yet_effective|expired|unknown)$"
    ),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    data_quality: str | None = Query(
        default=None,
        pattern="^(missing_source|missing_metadata|zero_chunks|unknown_status)$",
    ),
    source_presence: str = Query(default="all", pattern="^(all|present|missing)$"),
    issued_from: str | None = Query(default=None, max_length=10),
    issued_to: str | None = Query(default=None, max_length=10),
    effective_from: str | None = Query(default=None, max_length=10),
    effective_to: str | None = Query(default=None, max_length=10),
    expired_from: str | None = Query(default=None, max_length=10),
    expired_to: str | None = Query(default=None, max_length=10),
    as_of: str | None = Query(default=None, max_length=10),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(
        default="effective_date",
        pattern="^(effective_date|issued_date|expired_date|title|law_number|stored_status|article_count|chunk_count)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
) -> dict[str, Any]:
    """Return Admin inventory metadata only; content/chunks are allowlist-dropped."""

    _require_crawl_admin(raw_request)
    params: dict[str, Any] = {
        "q": q,
        "tier": tier,
        "source_presence": source_presence,
        "limit": limit,
        "offset": offset,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "as_of": as_of or date.today().isoformat(),
    }
    for key, value in (
        ("document_type", document_type),
        ("issuing_agency", issuing_agency),
        ("scope", scope),
        ("domain", domain),
        ("stored_status", stored_status),
        ("validity_status", validity_status),
        ("data_quality", data_quality),
        ("issued_from", issued_from),
        ("issued_to", issued_to),
        ("effective_from", effective_from),
        ("effective_to", effective_to),
        ("expired_from", expired_from),
        ("expired_to", expired_to),
    ):
        if value not in (None, ""):
            params[key] = value
    payload = await _management_required_read(
        "/management/documents",
        params=params,
        message="Không thể đọc danh sách kho văn bản.",
    )
    if not isinstance(payload.get("items"), list):
        raise HTTPException(
            status_code=503,
            detail="Không thể đọc danh sách kho văn bản.",
        )
    try:
        legal_as_of = date.fromisoformat(str(params["as_of"]))
    except ValueError:
        legal_as_of = date.today()
    snapshot = default_snapshot_cache.load()
    return {
        "items": [
            safe
            for item in payload["items"]
            if (
                safe := _management_safe_list_item(
                    _management_validity_projection(
                        item,
                        snapshot=snapshot,
                        as_of=legal_as_of,
                    )
                )
            )
        ],
        "total": int(payload.get("total") or 0),
        "limit": int(payload.get("limit") or limit),
        "offset": int(payload.get("offset") or offset),
        "as_of": payload.get("as_of") or params["as_of"],
        "observed_at": payload.get("observed_at")
        or datetime.now(timezone.utc).isoformat(),
    }


@router.get("/management/documents/{document_id}")
async def legal_management_document(
    document_id: str, raw_request: Request
) -> dict[str, Any]:
    """Return one Admin-only evidence projection with bounded optional sections."""

    _require_crawl_admin(raw_request)
    canonical_id = _management_document_id(document_id)
    base = await _management_required_read(
        f"/management/documents/{canonical_id}",
        message="Không thể đọc chi tiết văn bản.",
    )
    timeline, audit_rows = await asyncio.gather(
        _management_optional_read(
            default_registry.document_timeline(canonical_id),
            reason_code="validity_timeline_unavailable",
            message="Không thể đọc dòng thời gian hiệu lực.",
        ),
        _management_optional_read(
            list_audit_logs(
                limit=100,
                resource_type="legal_document",
                resource_id=canonical_id,
            ),
            reason_code="audit_store_unavailable",
            message="Không thể đọc nhật ký văn bản.",
        ),
    )
    if timeline is None:
        validity: dict[str, Any] = _management_unavailable(
            "validity_timeline_missing",
            "Chưa có dòng thời gian hiệu lực cho văn bản này.",
        )
    elif isinstance(timeline, dict) and timeline.get("status") == "unavailable":
        validity = timeline
    else:
        validity = {
            "status": "available",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            **(timeline if isinstance(timeline, dict) else {}),
        }
        validity["replacement_discovery"] = project_replacement_candidates(
            validity.get("observations") or []
        )
    if isinstance(audit_rows, dict) and audit_rows.get("status") == "unavailable":
        audit = audit_rows
    else:
        safe_audit: list[dict[str, Any]] = []
        for row in audit_rows if isinstance(audit_rows, list) else []:
            if not isinstance(row, dict):
                continue
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            reason = str(details.get("reason") or "").strip()[:2000]
            safe_audit.append(
                {
                    "id": row.get("id"),
                    "actor_user": row.get("actor_user"),
                    "actor_role": row.get("actor_role"),
                    "action": row.get("action"),
                    "entity_type": row.get("entity_type"),
                    "entity_id": row.get("entity_id"),
                    "reason": reason,
                    "created": row.get("created"),
                }
            )
        audit = {
            "status": "available",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "items": safe_audit,
        }
    structure = base.get("structure") if isinstance(base.get("structure"), dict) else {}
    safe_articles = []
    for article in structure.get("articles") or []:
        if not isinstance(article, dict):
            continue
        safe_articles.append(
            {
                key: article.get(key)
                for key in (
                    "article_id",
                    "article_number",
                    "article_title",
                    "status",
                    "effective_from",
                    "effective_to",
                    "chunk_count",
                )
            }
        )
    document_as_of_value = str(base.get("as_of") or date.today().isoformat())
    try:
        document_as_of = date.fromisoformat(document_as_of_value[:10])
    except ValueError:
        document_as_of = date.today()
    projected_document = _management_validity_projection(
        base.get("document"),
        snapshot=default_snapshot_cache.load(),
        as_of=document_as_of,
    )
    return {
        "observed_at": base.get("observed_at")
        or datetime.now(timezone.utc).isoformat(),
        "document": _management_safe_document(projected_document),
        "structure": {
            "article_count": int(structure.get("article_count") or 0),
            "chunk_count": int(structure.get("chunk_count") or 0),
            "articles": safe_articles,
        },
        "relationships": base.get("relationships")
        or _management_unavailable(
            "relationship_store_unavailable", "Không thể đọc quan hệ văn bản."
        ),
        "validity": validity,
        "vectors": base.get("vectors")
        or _management_unavailable(
            "vector_store_unavailable", "Không thể đọc trạng thái kho vector."
        ),
        "faq_impacts": base.get("faq_impacts")
        or _management_unavailable(
            "faq_dependency_not_configured", "Chưa có schema phụ thuộc FAQ."
        ),
        "versions": base.get("versions")
        or _management_unavailable(
            "schema_not_approved", "Lịch sử phiên bản đang chờ phê duyệt schema."
        ),
        "audit": audit,
    }


@router.get("/docs/{doc_id}")
async def get_legal_document(
    doc_id: str,
    response: Response,
    raw_request: Request,
    article: str | None = Query(default=None),
    include_content: bool = Query(default=False),
) -> dict[str, Any]:
    """Return local legal document metadata + content/chunks.

    Used by Ask citations so users can open the in-system source even when the
    original VBPL source_url is broken.
    """
    started = perf_counter()
    params: dict[str, str] | None = None
    if article or include_content:
        params = {}
    if article:
        params["article"] = article
    if include_content:
        params["include_content"] = "true"
    if params is None:
        params = {}
    params["audience"] = _serving_audience(raw_request)
    try:
        payload = await _request("GET", f"/documents/{doc_id}", params=params)
    except HTTPException as exc:
        telemetry.record_operation(
            category="document_view", route="/api/legal/docs/{doc_id}",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=exc.status_code,
        )
        if exc.status_code == 404:
            telemetry.record_issue("citation_dead", category="document_view", status_code=404, error_class="not_found")
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy văn bản pháp lý trong kho nội bộ.",
            ) from exc
        raise
    document = payload.get("document") if isinstance(payload, dict) else payload
    if not document:
        telemetry.record_operation(
            category="document_view", route="/api/legal/docs/{doc_id}",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=404,
        )
        telemetry.record_issue("citation_dead", category="document_view", status_code=404, error_class="empty_document")
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy văn bản pháp lý trong kho nội bộ.",
        )
    # Candidate/unapproved workflow states must not be opened from Ask
    # citations.  Expired/replaced records remain readable as legal history;
    # the canonical validity projection below marks them ineligible for a
    # current-law answer.
    effective_status = str(document.get("effective_status") or document.get("status") or "").strip().lower()
    unpublished_statuses = {
        "pending",
        "draft",
        "staging",
        "submitted",
        "changes_requested",
        "rejected",
        "inactive",
    }
    if effective_status in unpublished_statuses:
        telemetry.record_operation(
            category="document_view", route="/api/legal/docs/{doc_id}",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=404,
        )
        telemetry.record_issue("citation_dead", category="document_view", status_code=404, error_class="inactive_document")
        raise HTTPException(
            status_code=404,
            detail="Văn bản chưa được duyệt hoặc không còn hiệu lực trong kho tra cứu.",
        )
    # Source-asset provenance lives in the review database, separate from the
    # PostgreSQL retrieval index. Expose only the approved copy, never a pending
    # upload path.
    try:
        lookup_document_id = str(document.get("doc_id") or doc_id)
        if lookup_document_id.isdigit():
            lookup_document_id = int(lookup_document_id)
        candidates = await repo_query(
            "SELECT source_asset FROM legal_crawl_candidate WHERE imported_document.document_id = $document_id AND status = 'imported' LIMIT 1;",
            {"document_id": lookup_document_id},
        )
        if candidates and candidates[0].get("source_asset"):
            document["source_asset"] = candidates[0]["source_asset"]
            document["source_file_available"] = True
    except Exception:
        # Viewer remains available with the internally generated PDF if the
        # optional provenance lookup is temporarily unavailable.
        document.setdefault("source_asset", None)
    validity = project_validity_for_row(
        document,
        snapshot=default_snapshot_cache.load(),
        as_of=date.today(),
        mode="protect",
    )
    document["validity_sync"] = validity
    document["validity_status"] = validity["status"]
    document["serving_status"] = validity["serving_action"]
    document["current_answer_eligible"] = validity["current_answer_eligible"]
    document["historical_lookup_allowed"] = validity["historical_lookup_allowed"]
    duration_ms = (perf_counter() - started) * 1000
    response.headers["X-Legal-View-Ms"] = str(round(duration_ms, 1))
    response.headers["Cache-Control"] = "private, max-age=300" if not include_content and not article else "no-store"
    telemetry.record_operation(
        category="document_view", route="/api/legal/docs/{doc_id}", duration_ms=duration_ms,
        metadata={"cached": not include_content and not bool(article)},
    )
    return document


@router.get("/docs/{doc_id}/download.pdf")
async def download_legal_document_pdf(
    doc_id: str,
    raw_request: Request,
    article: str | None = Query(default=None),
) -> Response:
    """Return an authenticated PDF for an approved/indexed legal document.

    Prefer a physical internal PDF when available. Otherwise generate/proxy a PDF
    from the local legal-search service. Never expose candidate/unapproved docs.
    """
    started = perf_counter()
    from pathlib import Path

    from fastapi.responses import FileResponse

    # Gate access through the same detail endpoint: only active indexed documents
    # can be opened/downloaded from Ask citations.
    doc = await get_legal_document(
        doc_id, Response(), raw_request, article=article, include_content=False
    )

    uploads_root = Path("data") / "uploads"
    source_asset = doc.get("source_asset") or {}
    source_asset_path = Path(str(source_asset.get("path") or "")).resolve() if source_asset.get("path") else None
    approved_asset_root = (uploads_root / "legal_sources").resolve()
    pdf_path = source_asset_path if (
        source_asset_path and source_asset_path.is_file() and approved_asset_root in source_asset_path.parents
        and source_asset_path.suffix.lower() == ".pdf"
    ) else uploads_root / "pdfs" / f"{doc_id}.pdf"
    if pdf_path.is_file():
        filename = f"{doc_id}.pdf"
        law_number = str(doc.get("law_number") or "").strip()
        if law_number:
            import re

            safe_law = re.sub(r"[^A-Za-z0-9_.-]+", "-", law_number).strip("-")
            safe_law = re.sub(r"-+", "-", safe_law)
            if safe_law:
                filename = f"{safe_law}.pdf"
        telemetry.record_operation(
            category="pdf_stream_export", route="/api/legal/docs/{doc_id}/download.pdf",
            duration_ms=(perf_counter() - started) * 1000, metadata={"origin": "original-file", "cached": True},
        )
        return FileResponse(
            path=str(pdf_path),
            media_type="application/pdf",
            filename=filename,
            headers={
                "X-Legal-Pdf-Origin": "original-file",
                "X-Legal-Download-Ms": str(round((perf_counter() - started) * 1000, 1)),
                "Cache-Control": "private, max-age=86400",
            },
        )

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.get(
                f"{LEGAL_SEARCH_URL}/documents/{doc_id}/download.pdf",
                params={
                    **({"article": article} if article else {}),
                    "audience": _serving_audience(raw_request),
                },
            )
        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail="Chưa có file PDF nội bộ hoặc không tạo được PDF từ văn bản đã index.",
            )
        response.raise_for_status()
        filename = "legal-document.pdf"
        disposition = response.headers.get("content-disposition") or ""
        if "filename=" in disposition:
            filename = disposition.split("filename=")[-1].strip().strip('"')
        telemetry.record_operation(
            category="pdf_stream_export", route="/api/legal/docs/{doc_id}/download.pdf",
            duration_ms=(perf_counter() - started) * 1000,
            metadata={"origin": "system-extract", "cached": response.headers.get("x-legal-pdf-cache", "unknown")},
        )
        return Response(
            content=response.content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Legal-Pdf-Origin": "system-extract",
                "X-Legal-Download-Ms": str(round((perf_counter() - started) * 1000, 1)),
                "Cache-Control": response.headers.get("cache-control", "private, max-age=86400"),
                "X-Legal-Pdf-Cache": response.headers.get("x-legal-pdf-cache", "unknown"),
                "X-Legal-Export-Ms": response.headers.get("x-legal-export-ms", ""),
            },
        )
    except HTTPException as exc:
        telemetry.record_operation(
            category="pdf_stream_export", route="/api/legal/docs/{doc_id}/download.pdf",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=exc.status_code,
        )
        telemetry.record_issue("pdf_export_failed", category="pdf_stream_export", status_code=exc.status_code, error_class="http_error")
        raise
    except httpx.HTTPError as exc:
        telemetry.record_operation(
            category="pdf_stream_export", route="/api/legal/docs/{doc_id}/download.pdf",
            duration_ms=(perf_counter() - started) * 1000, outcome="error", status_code=503,
        )
        telemetry.record_issue("pdf_export_failed", category="pdf_stream_export", status_code=503, error_class=exc.__class__.__name__)
        raise HTTPException(
            status_code=503,
            detail=f"Dịch vụ tạo PDF văn bản pháp luật chưa sẵn sàng: {exc}",
        ) from exc



def _extract_docx(content: bytes) -> str:
    try:
        from docx import Document
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Máy chủ chưa có thư viện python-docx để đọc file Word.",
        ) from exc
    document = Document(BytesIO(content))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
    table_rows = []
    for table in document.tables:
        for row in table.rows:
            values = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if values:
                table_rows.append(" | ".join(values))
    return "\n".join([part for part in paragraphs + table_rows if part])


def _extract_pdf(content: bytes) -> str:
    try:
        import fitz  # PyMuPDF

        document = fitz.open(stream=content, filetype="pdf")
        text_content = "\n".join(page.get_text("text") for page in document).strip()
        document.close()
        if text_content:
            return text_content
    except Exception:
        pass

    try:
        import pypdf  # type: ignore[import-not-found]
    except Exception as exc:
        raise HTTPException(
            status_code=501,
            detail=(
                "Máy chủ chưa có thư viện đọc PDF phù hợp. "
                "Hiện có thể nạp TXT/DOCX ngay; PDF cần PyMuPDF hoặc pypdf."
            ),
        ) from exc
    reader = pypdf.PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


@router.post("/import/extract")
async def legal_import_extract(
    file: UploadFile = File(...),
    extractor: str = "auto",
) -> dict[str, Any]:
    filename = file.filename or "uploaded"
    lower_name = filename.lower()
    content = await file.read()
    extractor = extractor.strip().lower()
    metadata: dict[str, Any] = {"extractor_used": "basic"}
    if extractor not in {"auto", "basic", "rag_anything"}:
        raise HTTPException(
            status_code=400,
            detail="Extractor phai la auto, basic hoac rag_anything.",
        )

    text_content = ""
    if extractor in {"auto", "rag_anything"} and lower_name.endswith((".pdf", ".docx")):
        try:
            text_content, extra_metadata = extract_with_rag_anything(filename, content)
            metadata.update(extra_metadata)
        except RagAnythingUnavailable as exc:
            if extractor == "rag_anything":
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            metadata["extractor_fallback_reason"] = str(exc)

    if not text_content:
        if lower_name.endswith((".txt", ".md")):
            text_content = content.decode("utf-8", errors="replace")
        elif lower_name.endswith(".docx"):
            text_content = _extract_docx(content)
        elif lower_name.endswith(".pdf"):
            from api.crawlers.pdf_extractor import extract_pdf_bytes_for_review

            pdf_result = extract_pdf_bytes_for_review(content, filename=filename)
            text_content = str(pdf_result.get("text") or "")
            metadata.update(
                {key: value for key, value in pdf_result.items() if key != "text"}
            )
            # Legacy fallback remains guarded for old adapters; the Feature 016
            # review extractor has already performed its explicit OCR fallback.
            if not metadata.get("extraction_blocks") and len(text_content.strip()) < 100:
                try:
                    from api.crawlers.ocr_extractor import run_ocr_on_pdf_bytes
                    ocr_text = run_ocr_on_pdf_bytes(content)
                    if "[LỖI OCR" not in ocr_text and "[LOI OCR" not in ocr_text:
                        text_content = ocr_text
                        metadata["extractor_used"] = "ocr_fallback"
                    else:
                        metadata["extractor_fallback_reason"] = ocr_text
                except Exception as e:
                    metadata["extractor_fallback_reason"] = f"OCR module error: {e}"
        else:
            raise HTTPException(
                status_code=400,
                detail="Chỉ hỗ trợ file TXT, MD, DOCX hoặc PDF.",
            )

    if not metadata.get("extraction_blocks"):
        normalized_blocks = normalize_extraction_blocks(
            [],
            fallback_text=text_content,
            extractor=str(metadata.get("extractor_used") or "basic"),
            extractor_version="feature016-v1",
            source_asset_sha256=hashlib.sha256(content).hexdigest(),
        )
        metadata["extraction_blocks"] = normalized_blocks["blocks"]
        metadata["layout_status"] = normalized_blocks["status"]
        metadata["layout_reason"] = normalized_blocks["reason_code"]

    # Step 12/15: Detect and mask PII (CCCD/phone/email) before returning
    pii = redact_upload_text(text_content or "")
    text_content = pii["text"]
    metadata["extraction_blocks"] = _redact_extraction_blocks(
        metadata.get("extraction_blocks")
    )
    metadata["contains_pii"] = pii["contains_pii"]
    metadata["pii_types"] = pii["pii_types"]
    metadata["pii_counts"] = pii["pii_counts"]
    metadata["pii_masked"] = pii["masked"]
    metadata["retention"] = pii["retention"]

    return {
        "filename": filename,
        "characters": len(text_content),
        "content": text_content,
        **metadata,
    }


def _clean_html_text(html: str) -> tuple[str, str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text_content = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text_content.splitlines() if line.strip()]
    return title, "\n".join(lines)


async def _crawl_with_crawl4ai(url: str) -> dict[str, Any]:
    if "vbpl.vn" in url.lower():
        from open_notebook.utils.vbpl_crawler import crawl_vbpl_url
        res = await crawl_vbpl_url(url)
        return {
            "source_url": url,
            "title": res["title"],
            "characters": len(res["content"]),
            "content": res["content"],
            "crawler": "playwright-custom",
            "requires_manual_review": True,
        }

    from crawl4ai import AsyncWebCrawler

    async with AsyncWebCrawler(verbose=False) as crawler:
        result = await crawler.arun(url=url)
    markdown = str(getattr(result, "markdown", "") or "").strip()
    title = str(getattr(result, "title", "") or "").strip()
    if not markdown:
        raise ValueError("crawl4ai returned empty markdown")
    return {
        "source_url": url,
        "title": title,
        "characters": len(markdown),
        "content": markdown,
        "crawler": "crawl4ai",
        "requires_manual_review": True,
    }


async def _normalized_crawl_preview(url: str) -> dict[str, Any]:
    """Fetch one official page through the deterministic normalized contract."""

    try:
        trusted_url = validate_official_public_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    normalized = await fetch_normalized_legal_document(
        trusted_url,
        scope="central",
        timeout_seconds=30,
    )
    if normalized.get("status") == "rejected" or not normalized.get(
        "clean_markdown"
    ):
        reason = str((normalized.get("extraction") or {}).get("reason") or "")
        raise HTTPException(
            status_code=502,
            detail=(
                "Không lấy được toàn văn từ nguồn chính thức. "
                f"Lý do: {reason or 'nguồn không trả về nội dung hợp lệ'}."
            ),
        )
    return {
        **normalized,
        "content": normalized.get("clean_markdown") or "",
        "crawler": (normalized.get("extraction") or {}).get("method"),
        "requires_manual_review": True,
    }


_VALIDITY_RECORD_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


def _validity_record_id(value: str, table: str) -> str:
    text = str(value or "").strip()
    if text.startswith(f"{table}:"):
        token = text.split(":", 1)[1]
    elif ":" not in text:
        token = text
    else:
        raise HTTPException(status_code=400, detail="Mã bản ghi hiệu lực không hợp lệ.")
    if not _VALIDITY_RECORD_TOKEN.fullmatch(token):
        raise HTTPException(status_code=400, detail="Mã bản ghi hiệu lực không hợp lệ.")
    return f"{table}:{token}"


@router.post("/crawl/preview")
async def legal_crawl_preview(
    request: LegalCrawlPreviewRequest, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    return await _normalized_crawl_preview(request.url)


@router.post("/import/preview")
async def legal_import_preview(request: LegalImportRequest) -> dict[str, Any]:
    return await _request("POST", "/import/preview", json=request.model_dump())


@router.post("/import")
async def legal_import_document(request: LegalImportRequest) -> dict[str, Any]:
    scope_val = "central"
    scope_lower = str(request.scope).lower()
    if "hải phòng" in scope_lower or "haiphong" in scope_lower:
        scope_val = "haiphong"
    elif "địa phương" in scope_lower or "phường" in scope_lower or "xã" in scope_lower:
        scope_val = "local"
        
    source_rows = await repo_query(
        "SELECT id FROM legal_crawl_source WHERE sitemap_scope = $scope LIMIT 1;",
        {"scope": scope_val},
    )
    if not source_rows:
        source_rows = await repo_query("SELECT id FROM legal_crawl_source LIMIT 1;")
    if not source_rows:
        raise HTTPException(
            status_code=503,
            detail="Hàng đợi duyệt văn bản chưa có nguồn crawler mặc định.",
        )

    matched_domains, domain_evidence = classify_legal_domains(
        request.title,
        request.document_type,
        request.issuing_agency,
        request.sector,
        request.content,
    )
    try:
        primary_domain = _resolved_admin_import_domain(
            field_id=request.field_id,
            requested_domain=request.domain_slug,
            classified_domains=matched_domains,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if primary_domain and primary_domain not in matched_domains:
        matched_domains = [primary_domain, *matched_domains]
        domain_evidence = {
            primary_domain: ["admin_reviewed_field_domain"],
            **domain_evidence,
        }
    external_id = request.law_number or f"manual-import:{request.title[:80]}"

    from open_notebook.database.repository import ensure_record_id
    candidate_data = {
        "source": ensure_record_id(source_rows[0]["id"]),
        "title": request.title,
        "law_number": request.law_number,
        "description": request.applicability_info or request.title,
        "detail_url": request.source_url or "",
        "source_url": request.source_url or "",
        "document_type": request.document_type,
        "issuing_agency": request.issuing_agency,
        "scope": scope_val,
        "domain": primary_domain,
        "source_type": "document",
        "status": "pending",
        "review_status": "pending",
        "suggested_action": "manual_import",
        "comparison_status": "new",
        "detected_changes": [],
        # SCHEMAFULL requires external_id; use law_number + hash-stable fallback.
        "external_id": external_id,
        "raw_metadata": {
            "title": request.title,
            "law_number": request.law_number,
            "document_type": request.document_type,
            "issuing_agency": request.issuing_agency,
            "scope": request.scope,
            "sector": request.sector,
            "field_id": request.field_id,
            "domain_slug": primary_domain,
            "issued_date": request.issued_date,
            "effective_date": request.effective_date,
            "expired_date": request.expired_date,
            "source_url": request.source_url,
            "applicability_info": request.applicability_info,
            "confirmed_official_source": request.confirmed_official_source,
            "metadata_only": not bool(request.content.strip()),
            "candidate_origin": "admin_manual_import",
            "domain": primary_domain,
            "matched_domains": matched_domains,
            "domain_evidence": domain_evidence,
        },
        "content": request.content,
    }

    existing_rows = await repo_query(
        "SELECT * FROM legal_crawl_candidate "
        "WHERE external_id = $external_id OR law_number = $law_number "
        "ORDER BY created ASC LIMIT 1;",
        {"external_id": external_id, "law_number": request.law_number},
    )
    if existing_rows:
        existing = existing_rows[0]
        existing_status = str(existing.get("status") or "").strip().lower()
        existing_metadata = existing.get("raw_metadata") or {}
        existing_content = str(existing.get("content") or "").strip()
        incoming_content = request.content.strip()
        should_enrich_pending = (
            existing_status in {"pending", "changes_requested"}
            and bool(incoming_content)
            and (
                bool(existing_metadata.get("metadata_only"))
                or len(incoming_content) > len(existing_content)
            )
        )
        if should_enrich_pending:
            enriched_rows = await repo_update(
                "legal_crawl_candidate",
                existing["id"],
                {
                    **candidate_data,
                    # Keep the crawler/officer identity stable. The law number
                    # is a deduplication key, not a replacement for the source
                    # candidate's unique external identity.
                    "external_id": existing.get("external_id") or external_id,
                    "status": "pending",
                    "review_status": "pending",
                    "review_note": None,
                    "review_reason": None,
                    "requested_changes_note": None,
                    "reviewed_at": None,
                    "reviewed_by": None,
                    "reviewed_role": None,
                    "approved_at": None,
                    "approved_by": None,
                    "import_status": None,
                    "import_job": None,
                },
            )
            enriched = enriched_rows[0] if enriched_rows else {**existing, **candidate_data}
            await LegalCrawlService.persist_automatic_assessment(enriched)
            return {
                "law_number": request.law_number,
                "article_count": 0,
                "chunk_count": 0,
                "message": "Đã bổ sung toàn văn và metadata vào candidate đang chờ duyệt.",
                "candidate_id": str(existing["id"]),
                "deduplicated": True,
                "enriched": True,
            }
        if existing_status in {"rejected", "changes_requested"}:
            reopened_rows = await repo_update(
                "legal_crawl_candidate",
                existing["id"],
                {
                    **candidate_data,
                    "external_id": existing.get("external_id") or external_id,
                    "review_note": None,
                    "review_reason": None,
                    "requested_changes_note": None,
                    "reviewed_at": None,
                    "reviewed_by": None,
                    "reviewed_role": None,
                    "approved_at": None,
                    "approved_by": None,
                    "import_status": None,
                    "import_job": None,
                },
            )
            reopened = reopened_rows[0] if reopened_rows else {**existing, **candidate_data}
            await LegalCrawlService.persist_automatic_assessment(reopened)
            return {
                "law_number": request.law_number,
                "article_count": 0,
                "chunk_count": 0,
                "message": "Đã cập nhật và mở lại candidate cũ trong hàng đợi duyệt.",
                "candidate_id": str(existing["id"]),
                "deduplicated": True,
                "enriched": True,
            }
        return {
            "law_number": request.law_number,
            "article_count": 0,
            "chunk_count": 0,
            "message": "Candidate này đã tồn tại; không tạo thêm bản ghi trùng.",
            "candidate_id": str(existing["id"]),
            "deduplicated": True,
            "enriched": False,
        }

    res = await repo_create("legal_crawl_candidate", candidate_data)
    candidate_id = res[0].get("id") if isinstance(res, list) and len(res) > 0 else (res.get("id") if isinstance(res, dict) else str(res))
    created_candidate = res[0] if isinstance(res, list) and res else res
    if isinstance(created_candidate, dict):
        await LegalCrawlService.persist_automatic_assessment({**candidate_data, **created_candidate})
    
    return {
        "law_number": request.law_number,
        "article_count": 0,
        "chunk_count": 0,
        "message": "Văn bản đã được đưa vào hàng đợi duyệt của admin.",
        "candidate_id": str(candidate_id),
        "deduplicated": False,
        "enriched": False,
    }


@router.post("/proposals")
async def officer_document_proposal(
    raw_request: Request,
    domain: str = Form(...),
    source_type: str = Form(..., pattern="^(document|form|procedure|reference)$"),
    title: str = Form(..., min_length=5, max_length=2000),
    reason: str = Form(..., min_length=10, max_length=2000),
    source_url: str = Form("", max_length=4000),
    law_number: str = Form("", max_length=255),
    document_type: str = Form("Tài liệu đề xuất", max_length=255),
    issuing_agency: str = Form("", max_length=255),
    scope: str = Form("central", max_length=100),
    sector: str = Form("", max_length=255),
    issued_date: str = Form(""),
    effective_date: str = Form(""),
    expired_date: str = Form(""),
    metadata_json: str = Form(""),
    content: str = Form(""),
    file_sha256: str = Form("", max_length=64),
    file: UploadFile | None = File(None),
) -> dict[str, Any]:
    role = get_request_role(raw_request)
    user_id = get_request_user_id(raw_request)
    if role != "officer":
        raise HTTPException(status_code=403, detail="Chi can bo moi duoc de xuat van ban.")
    if not user_id:
        raise HTTPException(status_code=403, detail="Khong xac dinh duoc tai khoan can bo.")

    profile = await get_user_profile(user_id)
    allowed_domains = (profile or {}).get("allowed_domains") or []
    if not _domain_allowed(domain, allowed_domains):
        raise HTTPException(
            status_code=403,
            detail="Can bo khong co quyen de xuat tai lieu ngoai linh vuc duoc phan cong.",
        )

    source_type = source_type.strip().lower()
    proposal_metadata: dict[str, Any] = {}
    if metadata_json.strip():
        try:
            parsed = json.loads(metadata_json)
            if not isinstance(parsed, dict):
                raise ValueError("metadata_json must be an object")
            proposal_metadata = parsed
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"metadata_json khong hop le: {exc}") from exc

    uploaded_file: dict[str, Any] | None = None
    extraction_result: dict[str, Any] = {
        "source": "manual_text" if content.strip() else "metadata_only",
        "characters": len(content or ""),
    }
    extracted_text = content.strip()
    if file:
        file_bytes = await file.read(DOCUMENT_UPLOAD_POLICY.max_bytes + 1)
        try:
            uploaded_file = await _save_proposal_upload(
                file,
                file_bytes,
                expected_sha256=file_sha256 or None,
            )
        except UploadSecurityError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        extracted_text, extraction_result = _extract_upload_text(file.filename or "uploaded", file_bytes)
        pii = redact_upload_text(extracted_text)
        extracted_text = pii["text"]
        extraction_result["extraction_blocks"] = _redact_extraction_blocks(
            extraction_result.get("extraction_blocks")
        )
        extraction_result.update(
            {
                "source": "upload",
                "filename": file.filename,
                "characters": len(extracted_text),
                "contains_pii": pii["contains_pii"],
                "pii_types": pii["pii_types"],
                "pii_counts": pii["pii_counts"],
                "pii_masked": pii["masked"],
                "retention": pii["retention"],
            }
        )

    if not source_url.strip() and not extracted_text:
        raise HTTPException(status_code=400, detail="Can nhap link nguon, noi dung hoac upload file de de xuat.")

    content_hash = extraction_result.get("text_fingerprint") or (
        hashlib.sha256(extracted_text.encode("utf-8")).hexdigest() if extracted_text else None
    )
    duplicates = await _duplicate_candidates(law_number.strip() or None, source_url.strip() or None, content_hash)
    validity_flags = _legal_validity_flags(
        effective_date=effective_date.strip() or None,
        expired_date=expired_date.strip() or None,
        source_url=source_url.strip() or None,
    )
    external_id_seed = "|".join(
        [
            "officer-proposal",
            str(user_id),
            domain,
            source_type,
            law_number.strip(),
            source_url.strip(),
            content_hash or title,
        ]
    )
    external_id = "officer-proposal:" + hashlib.sha256(external_id_seed.encode("utf-8")).hexdigest()[:32]
    exact_duplicate = next(
        (item for item in duplicates if item.get("external_id") == external_id),
        None,
    )
    if exact_duplicate:
        return {
            "candidate_id": str(exact_duplicate.get("id") or ""),
            "status": exact_duplicate.get("status") or "pending",
            "review_status": exact_duplicate.get("review_status") or exact_duplicate.get("status") or "pending",
            "deduplicated": True,
            "message": "Đề xuất này đã có trong hàng đợi; hệ thống không tạo bản ghi trùng.",
            "preview": {
                "title": title.strip(),
                "domain": domain,
                "source_type": source_type,
                "characters": len(extracted_text or ""),
                "duplicates": duplicates,
            },
        }
    source_ref = await _candidate_source_for(scope.strip() or "officer_proposal")

    raw_metadata = {
        **proposal_metadata,
        "ocr_status": extraction_result.get("ocr_status", "not_applicable"),
        "ocr_confidence": extraction_result.get("ocr_confidence"),
        "ocr_reason": extraction_result.get("reason", ""),
        "pdf_kind": extraction_result.get("pdf_kind", "not_pdf"),
        "language": extraction_result.get("language", "unknown"),
        "page_count": extraction_result.get("page_count", 0),
        "text_fingerprint": extraction_result.get("text_fingerprint"),
        "file_fingerprint": extraction_result.get("file_fingerprint"),
        "extraction_preview": extraction_result.get("preview", ""),
        "submitted_by": user_id,
        "domain": domain,
        "source_type": source_type,
        "proposal_reason": reason.strip(),
        "sector": sector.strip(),
        "issued_date": issued_date.strip() or None,
        "effective_date": effective_date.strip() or None,
        "expired_date": expired_date.strip() or None,
        "source_url": source_url.strip(),
        "confirmed_official_source": False,
        "candidate_origin": "officer_document_proposal",
    }
    candidate_data = {
        "source": source_ref,
        "external_id": external_id,
        "detail_url": source_url.strip(),
        "source_url": source_url.strip(),
        "sitemap_url": None,
        "sitemap_lastmod": None,
        "law_number": law_number.strip() or None,
        "title": title.strip(),
        "description": reason.strip(),
        "document_type": document_type.strip() or ("Biểu mẫu" if source_type == "form" else "Tài liệu đề xuất"),
        "issuing_agency": issuing_agency.strip() or None,
        "scope": scope.strip() or None,
        "status": "pending",
        "review_status": "pending",
        "suggested_action": "officer_proposal_review",
        "comparison_status": "duplicate_possible" if duplicates else "new",
        "detected_changes": [],
        "review_note": None,
        "imported_document": None,
        "raw_metadata": raw_metadata,
        "content": extracted_text or None,
        "content_hash": content_hash,
        "submitted_by": _user_account_record(user_id),
        "domain": domain,
        "source_type": source_type,
        "proposal_reason": reason.strip(),
        "uploaded_file": uploaded_file,
        "extraction_result": extraction_result,
        "duplicate_candidates": duplicates,
        "legal_validity_flags": validity_flags,
        "requested_changes_note": None,
    }
    created = await repo_create("legal_crawl_candidate", candidate_data)
    candidate = created[0] if isinstance(created, list) else created
    if isinstance(candidate, dict):
        candidate = await LegalCrawlService.persist_automatic_assessment({**candidate_data, **candidate})
    candidate_id = str(candidate.get("id") if isinstance(candidate, dict) else candidate)

    form_catalog_sync: dict[str, Any] | None = None
    if source_type == "form" and uploaded_file and isinstance(candidate, dict):
        # Forms have their own fail-closed review/attestation pipeline and must
        # never be sent to the legal RAG import worker. Mirror the proposal into
        # that queue so it is visible to Admin immediately after submission.
        try:
            from api.routers.ward_procedures import bridge_officer_form_candidate

            form_catalog_sync = bridge_officer_form_candidate(candidate)
            raw_metadata["form_catalog_candidate_id"] = form_catalog_sync["id"]
            raw_metadata["form_catalog_sync_status"] = "completed"
            await repo_update(
                "legal_crawl_candidate",
                candidate_id,
                {"raw_metadata": raw_metadata, "updated": datetime.now(timezone.utc)},
            )
        except Exception as exc:
            raw_metadata["form_catalog_sync_status"] = "failed"
            raw_metadata["form_catalog_sync_error"] = str(exc)[:1000]
            await repo_update(
                "legal_crawl_candidate",
                candidate_id,
                {"raw_metadata": raw_metadata, "updated": datetime.now(timezone.utc)},
            )

    await write_audit_log(
        action="legal.proposal.create",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=user_id,
        actor_role=role,
        details={
            "domain": domain,
            "source_type": source_type,
            "title": title.strip(),
            "has_upload": uploaded_file is not None,
            "has_source_url": bool(source_url.strip()),
            "duplicate_count": len(duplicates),
            "reason": reason.strip(),
        },
        request=raw_request,
    )
    return {
        "candidate_id": candidate_id,
        "status": "pending",
        "review_status": "pending",
        "message": "De xuat da vao hang doi duyet cua admin. Chua duoc dung de tra loi RAG.",
        "preview": {
            "title": title.strip(),
            "domain": domain,
            "source_type": source_type,
            "characters": len(extracted_text or ""),
            "duplicates": duplicates,
            "legal_validity_flags": validity_flags,
            "extraction_result": extraction_result,
        },
        "form_catalog_sync": form_catalog_sync,
    }


@router.get("/validity/status", response_model=LegalValidityStatusResponse)
async def legal_validity_status(raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    return await get_legal_validity_sync_service().status()


@router.post("/validity/run", response_model=LegalValidityRunResponse)
async def legal_validity_run(
    request: LegalValidityRunRequest, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    scopes = tuple(dict.fromkeys(request.scope))
    actor_user_id = get_request_user_id(raw_request)
    # Audit is intentionally fail-closed and precedes the external fetch.
    await write_audit_log(
        action="admin.legal_validity.run",
        entity_type="legal_validity_sync_run",
        entity_id="manual",
        actor_user_id=actor_user_id,
        actor_role=get_request_role(raw_request),
        details={
            "reason": request.reason.strip(),
            "scope": list(scopes),
            "limit": request.limit,
        },
        request=raw_request,
    )
    report = await get_legal_validity_sync_service().run(
        trigger="admin",
        requested_by=actor_user_id,
        reason=request.reason.strip(),
        scopes=scopes,
        limit=request.limit,
    )
    await _notify_validity_admin(report)
    return report


@router.get("/validity/events", response_model=LegalValidityEventPageResponse)
async def legal_validity_events(
    raw_request: Request,
    review_status: Literal[
        "open", "confirmed", "rejected_match", "recheck_requested"
    ]
    | None = None,
    severity: Literal["low", "medium", "high", "critical"] | None = None,
    scope: Literal["central", "haiphong", "local"] | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=80),
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    try:
        return await default_registry.list_events(
            review_status=review_status,
            severity=severity,
            scope=scope,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Con trỏ danh sách sự kiện không hợp lệ.") from exc


@router.get("/validity/documents/{document_id}")
async def legal_validity_document(
    document_id: str, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    canonical = _validity_record_id(document_id, "legal_document")
    # Runtime inventory commonly exposes a UUID, while older rows may expose a
    # table-qualified ID. Validate both forms but query the identity as stored.
    lookup_id = canonical if ":" in document_id else document_id
    timeline = await default_registry.document_timeline(lookup_id)
    if timeline is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy lịch sử hiệu lực của văn bản.")
    timeline["replacement_discovery"] = project_replacement_candidates(
        timeline.get("observations") or []
    )
    return timeline


@router.get("/validity/documents/{document_id}/vector-cleanup/preview")
async def legal_vector_cleanup_preview(
    document_id: str, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    _validity_record_id(document_id, "legal_document")
    token = os.getenv("LEGAL_VECTOR_CLEANUP_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Chưa cấu hình khóa nội bộ cho thao tác dọn vector.",
        )
    return await _request(
        "GET",
        f"/documents/{document_id}/vector-cleanup/preview",
        headers={"X-Legal-Operations-Token": token},
    )


@router.post("/validity/documents/{document_id}/vector-cleanup")
async def legal_vector_cleanup_execute(
    document_id: str,
    request: LegalVectorCleanupRequest,
    raw_request: Request,
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    _validity_record_id(document_id, "legal_document")
    token = os.getenv("LEGAL_VECTOR_CLEANUP_TOKEN", "").strip()
    if not token:
        raise HTTPException(
            status_code=503,
            detail="Chưa cấu hình khóa nội bộ cho thao tác dọn vector.",
        )
    actor_user_id = get_request_user_id(raw_request)
    await write_audit_log(
        action="admin.legal_validity.vector_cleanup",
        entity_type="legal_document",
        entity_id=document_id,
        actor_user_id=actor_user_id,
        actor_role=get_request_role(raw_request),
        details={"reason": request.reason.strip()},
        request=raw_request,
    )
    return await _request(
        "POST",
        f"/documents/{document_id}/vector-cleanup",
        json={
            "requested_by": actor_user_id,
            "reason": request.reason.strip(),
        },
        headers={"X-Legal-Operations-Token": token},
    )


@router.post(
    "/validity/events/{event_id}/decision",
    response_model=LegalValidityDecisionResponse,
)
async def legal_validity_decision(
    event_id: str,
    request: LegalValidityDecisionRequest,
    raw_request: Request,
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    canonical = _validity_record_id(event_id, "legal_validity_event")
    event = await default_registry.get_event(canonical)
    if event is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự kiện hiệu lực pháp lý.")
    actor_user_id = get_request_user_id(raw_request)
    # Audit precedes mutation so a broken audit facility cannot create an
    # untraceable legal decision.
    await write_audit_log(
        action="admin.legal_validity.decision",
        entity_type="legal_validity_event",
        entity_id=canonical,
        actor_user_id=actor_user_id,
        actor_role=get_request_role(raw_request),
        details={
            "action": request.action,
            "reason": request.reason.strip(),
            "law_number": event.get("law_number"),
        },
        request=raw_request,
    )
    try:
        decision = await default_registry.record_decision(
            event_id=canonical,
            action=request.action,
            reason=request.reason.strip(),
            actor_user_id=actor_user_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy sự kiện hiệu lực pháp lý.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Quyết định hiệu lực không hợp lệ.") from exc
    return {"event": await default_registry.get_event(canonical), "decision": decision}


@router.get("/crawl/summary")
async def legal_crawl_summary(raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    summary = await LegalCrawlService.summary()
    sources = await LegalCrawlService.source_status()
    unread = await LegalCrawlService.get_notifications(unread_only=True)
    checked = [item.get("last_checked_at") for item in sources if item.get("last_checked_at")]
    summary.update({
        # Stable frontend contract. The old service names are retained below
        # for compatibility with scripts and tests.
        "pending_review_count": int(summary.get("pending_document_candidates") or 0),
        "unread_notification_count": len(unread),
        "source_count": len(sources),
        "last_checked_at": max(checked, key=str) if checked else None,
        "schedule_interval_minutes": 7 * 24 * 60,
        "sources": sources,
    })
    return summary


@router.get("/crawl/sources")
async def legal_crawl_sources(raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    await LegalCrawlService.ensure_vbpl_sources()
    return {"sources": await LegalCrawlService.source_status()}


@router.get("/proposals/candidates")
async def officer_visible_candidates(raw_request: Request, status: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Officer-safe candidate view outside the admin-only crawler namespace."""
    role = get_request_role(raw_request)
    if role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="Chỉ cán bộ hoặc admin được xem candidate.")
    candidates = await LegalCrawlService.list_candidates(status=status, limit=min(max(limit, 1), 200))
    if role == "admin":
        return {"candidates": candidates}
    user_id = get_request_user_id(raw_request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Cần đăng nhập bằng tài khoản cán bộ.")
    profile = await get_user_profile(user_id)
    allowed_domains = list((profile or {}).get("allowed_domains") or [])
    visible = []
    for candidate in candidates:
        raw = candidate.get("raw_metadata") or {}
        if raw.get("candidate_origin") != "officer_document_proposal":
            continue
        if not _same_user_account(candidate.get("submitted_by"), user_id):
            continue
        if _officer_can_view_candidate(candidate, allowed_domains):
            visible.append(_officer_candidate_projection(candidate))
    return {"candidates": visible}


@router.patch("/crawl/sources/{source_id}")
async def legal_crawl_update_source(
    source_id: str, request: LegalCrawlSourceUpdateRequest, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    before = await _crawl_source_snapshot(source_id)
    payload = request.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(status_code=400, detail="Không có cấu hình cần cập nhật.")
    try:
        source = await LegalCrawlService.update_source(source_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit_log(
        action="legal.crawl.source.update",
        entity_type="legal_crawl_source",
        entity_id=source_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"fields": sorted(payload.keys())},
        request=raw_request,
    )
    await record_config_revision(
        config_type="crawler_sources",
        before=before,
        after=await _crawl_source_snapshot(source_id),
        actor_user_id=get_request_user_id(raw_request),
        reason="crawler source update",
    )
    return {"source": source}


@router.post("/crawl/sources", status_code=201)
async def legal_crawl_create_source(
    request: LegalCrawlSourceCreateRequest, raw_request: Request
) -> dict[str, Any]:
    """Create a disabled custom official source; only Admin can enable it later."""
    _require_crawl_admin(raw_request)
    payload = request.model_dump()
    try:
        source = await LegalCrawlService.create_source(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit_log(
        action="legal.crawl.source.create",
        entity_type="legal_crawl_source",
        entity_id=str(source.get("id") or ""),
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"name": source.get("name"), "base_url": source.get("base_url"), "fields": sorted(payload.keys())},
        request=raw_request,
    )
    await record_config_revision(
        config_type="crawler_sources",
        before={},
        after=await _crawl_source_snapshot(str(source.get("id") or "")),
        actor_user_id=get_request_user_id(raw_request),
        reason="crawler source create",
    )
    return {"source": source}


@router.delete("/crawl/sources/{source_id}")
async def legal_crawl_delete_source(source_id: str, raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    before = await _crawl_source_snapshot(source_id)
    try:
        result = await LegalCrawlService.delete_source(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await write_audit_log(
        action="legal.crawl.source.delete",
        entity_type="legal_crawl_source",
        entity_id=source_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={
            "name": result.get("name"),
            "configuration_only": True,
            "archived": bool(result.get("archived")),
            "history_preserved": bool(result.get("history_preserved")),
        },
        request=raw_request,
    )
    await record_config_revision(
        config_type="crawler_sources",
        before=before,
        after=await _crawl_source_snapshot(source_id),
        actor_user_id=get_request_user_id(raw_request),
        reason="crawler source delete_or_archive",
    )
    return result


@router.post("/crawl/scan")
async def legal_crawl_scan_now(raw_request: Request, source_id: str | None = None) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    if source_id:
        return await LegalCrawlService.scan_source(source_id)
    sources = await LegalCrawlService.ensure_vbpl_sources()
    runs = []
    for source in sources:
        if source.get("enabled", False) and LegalCrawlService.is_crawlable_source(source):
            runs.append(await LegalCrawlService.scan_source(str(source["id"])))
    created = sum(int((run.get("statistics") or {}).get("created") or 0) for run in runs)
    updated = sum(int((run.get("statistics") or {}).get("updated") or 0) for run in runs)
    await write_audit_log(
        action="legal.crawl.run.manual",
        entity_type="legal_crawl_source",
        entity_id="all_enabled_sources",
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"run_count": len(runs), "created": created, "updated": updated},
        request=raw_request,
    )
    return {"status": "completed", "runs": runs, "run_count": len(runs), "created": created, "updated": updated}


@router.get("/crawl/candidates")
async def legal_crawl_candidates(
    raw_request: Request,
    status: str | None = None,
    domain: str | None = Query(default=None, max_length=100),
    source_type: str | None = Query(default=None, pattern="^(document|form|procedure|reference)$"),
    limit: int = 100,
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    return {
        "candidates": await LegalCrawlService.list_candidates(
            status=status, domain=domain, source_type=source_type, limit=limit
        )
    }


@router.get("/crawl/candidates/{candidate_id}")
async def legal_crawl_candidate(candidate_id: str, raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản chờ duyệt.")
    return {"candidate": candidate, "review_preview": _candidate_review_preview(candidate)}


@router.post("/crawl/candidates/{candidate_id}/review")
async def legal_crawl_review_candidate(
    candidate_id: str,
    request: LegalCandidateDecisionRequest,
    raw_request: Request,
    response: Response,
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    if request.decision == "approved":
        await _require_import_plane_ready()
    try:
        candidate = await LegalCrawlService.review_candidate(
            candidate_id,
            request.decision,
            request.review_note,
            reviewed_by=get_request_user_id(raw_request),
            reviewed_role=get_request_role(raw_request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        telemetry.record_issue(
            "candidate_review_failed",
            category="crawler_review",
            status_code=500,
            error_class=exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "candidate_review_failed",
                "message": "Không duyệt được văn bản ứng viên.",
                "suggestion": "Kiểm tra backend, database và trạng thái candidate. Nếu đang duyệt approve, hãy xem import_job/import_status để biết bước nào lỗi.",
                "trace_id": uuid.uuid4().hex,
            },
        ) from exc
    await write_audit_log(
        action="legal.candidate.review",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={
            "decision": request.decision,
            "review_note": request.review_note,
        },
        request=raw_request,
    )
    if request.decision == "approved" and candidate.get("pipeline_stage") != "blocked":
        response.status_code = 202
    return {
        "candidate": candidate,
        "job_id": str(candidate.get("import_job") or "") or None,
        "pipeline_stage": candidate.get("pipeline_stage"),
        "blockers": list(candidate.get("blockers") or []),
    }


@router.patch("/crawl/candidates/{candidate_id}/metadata")
async def legal_crawl_update_candidate_metadata(
    candidate_id: str,
    request: LegalCandidateMetadataUpdateRequest,
    raw_request: Request,
) -> dict[str, Any]:
    """Admin corrects verified metadata before retrying an approved import."""
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy candidate.")
    changes = request.model_dump(exclude_unset=True)
    for date_field in ("issued_date", "effective_date", "expired_date"):
        if date_field in changes and not str(changes[date_field] or "").strip():
            changes[date_field] = None
    raw_metadata = candidate.get("raw_metadata") or {}
    raw_metadata.update(changes)
    top_level = {
        key: value for key, value in changes.items()
        if key in {"title", "law_number", "document_type", "issuing_agency", "scope", "source_url"}
    }
    if "source_url" in changes:
        top_level["detail_url"] = changes["source_url"]
    validity = _legal_validity_flags(
        effective_date=raw_metadata.get("effective_date"),
        expired_date=raw_metadata.get("expired_date"),
        source_url=raw_metadata.get("source_url") or candidate.get("source_url"),
    )
    recommendation = LegalCrawlService.build_review_recommendation({
        **candidate,
        **top_level,
        "raw_metadata": raw_metadata,
        "legal_validity_flags": validity,
    })
    rows = await repo_update("legal_crawl_candidate", candidate["id"], {
        **top_level,
        "raw_metadata": raw_metadata,
        "legal_validity_flags": validity,
        "review_recommendation": recommendation,
        "updated": datetime.now(timezone.utc),
    })
    updated = rows[0] if rows else {**candidate, **top_level, "raw_metadata": raw_metadata}
    await write_audit_log(
        action="legal.candidate.metadata.update",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"fields": sorted(changes.keys())},
        request=raw_request,
    )
    return {"candidate": updated, "validation_errors": LegalCrawlService.validate_candidate_for_import(updated)}


@router.get("/crawl/import-jobs/{job_id}")
async def legal_import_job_status(job_id: str, raw_request: Request) -> dict[str, Any]:
    """Poll a non-blocking approval → embed job; admin-only audit surface."""
    _require_crawl_admin(raw_request)
    record_id = job_id if ":" in job_id else f"legal_import_job:{job_id}"
    rows = await repo_query(
        "SELECT * FROM legal_import_job WHERE id = $id LIMIT 1;",
        {"id": ensure_record_id(record_id)},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Không tìm thấy import job.")
    return {"job": rows[0]}


@router.post("/crawl/candidates/{candidate_id}/import")
async def legal_crawl_import_approved_candidate(
    candidate_id: str, raw_request: Request
) -> dict[str, Any]:
    """Retry the guarded import only after admin has completed verification."""
    _require_crawl_admin(raw_request)
    await _require_import_plane_ready()
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy candidate.")
    if candidate.get("status") not in {"approved", "import_failed", "changes_requested"}:
        raise HTTPException(status_code=409, detail="Candidate không ở trạng thái có thể thử nhập lại.")
    prepared = await LegalCrawlService.prepare_candidate_for_import(candidate_id)
    validation_errors = list(prepared.get("blockers") or [])
    if validation_errors:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "candidate_preflight_blocked",
                "validation_errors": validation_errors,
                "candidate": prepared,
            },
        )
    try:
        job = await LegalCrawlService.enqueue_import_job(
            candidate_id,
            approval={
                "reviewed_at": datetime.now(timezone.utc),
                "reviewed_by": ensure_record_id(get_request_user_id(raw_request)),
                "reviewed_role": get_request_role(raw_request),
                "approved_by": ensure_record_id(get_request_user_id(raw_request)),
                "approved_at": datetime.now(timezone.utc),
            },
        )
    except (RuntimeError, ValueError) as exc:
        # Duplicate preflight updates the candidate to a human-review state.
        # Treat that state change as a successful classification, not a generic
        # retry error that leaves the Admin looking at stale UI data.
        refreshed_candidate = await LegalCrawlService.get_candidate(candidate_id)
        if (
            refreshed_candidate
            and refreshed_candidate.get("status") == "changes_requested"
            and refreshed_candidate.get("import_status") == "duplicate_conflict"
        ):
            await write_audit_log(
                action="legal.candidate.import.duplicate_conflict",
                entity_type="legal_crawl_candidate",
                entity_id=candidate_id,
                actor_user_id=get_request_user_id(raw_request),
                actor_role=get_request_role(raw_request),
                details={"reason": str(exc)[:500]},
                request=raw_request,
            )
            return {"candidate": refreshed_candidate, "status": "duplicate_conflict"}
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit_log(
        action="legal.candidate.import.enqueue",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"job_id": str(job.get("id")), "status": job.get("status")},
        request=raw_request,
    )
    return {
        "job": job,
        "status": "queued",
        "pipeline_stage": "queued",
        "blockers": [],
    }


@router.post("/crawl/candidates/{candidate_id}/assess")
async def legal_crawl_assess_candidate(
    candidate_id: str, raw_request: Request
) -> dict[str, Any]:
    """Trigger AI assessment for a candidate without changing its status."""
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản chờ duyệt.")
    # Force re-assessment
    try:
        assessed = await LegalCrawlService.assess_candidate(candidate, force=True)
    except Exception as exc:
        telemetry.record_issue(
            "candidate_assess_failed",
            category="crawler_review",
            status_code=500,
            error_class=exc.__class__.__name__,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "candidate_assess_failed",
                "message": "AI đánh giá candidate chưa chạy được.",
                "suggestion": "Kiểm tra API key/model cloud hoặc chuyển sang kiểm tra rule-based; admin vẫn có thể duyệt thủ công nếu metadata và nguồn hợp lệ.",
                "trace_id": uuid.uuid4().hex,
            },
        ) from exc
    await write_audit_log(
        action="legal.candidate.assess",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"forced": True, "has_ai_assessment": bool(assessed.get("ai_assessment"))},
        request=raw_request,
    )
    warning = None
    if not assessed.get("ai_assessment"):
        warning = (
            "AI chưa trả về đánh giá hợp lệ. Hệ thống đã giữ candidate trong hàng duyệt "
            "và cập nhật khuyến nghị kiểm duyệt rule-based nếu có."
        )
    return {"candidate": assessed, "ai_assessment": assessed.get("ai_assessment"), "warning": warning}


@router.post("/crawl/candidates/{candidate_id}/extract", status_code=202)
async def legal_crawl_extract_candidate(
    candidate_id: str, raw_request: Request
) -> dict[str, Any]:
    """Queue local extraction/OCR so the admin page never waits for heavy work.

    The job only processes an already-uploaded local source file.  It never
    deep-fetches a public candidate URL and it never changes approval/import
    status.  Clients poll the returned job endpoint.
    """
    _require_crawl_admin(raw_request)
    try:
        job = await LegalCrawlService.enqueue_processing_job(candidate_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit_log(
        action="legal.candidate.extract.queue",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={"job_id": str(job.get("id")), "job_type": "extract_ocr"},
        request=raw_request,
    )
    return {"job": job, "status": "queued", "message": "Đã xếp hàng trích xuất/OCR. Có thể tiếp tục thao tác và quay lại xem kết quả."}


@router.get("/crawl/extract-jobs/{job_id}")
async def legal_crawl_extract_job_status(job_id: str, raw_request: Request) -> dict[str, Any]:
    """Poll status of a queued candidate extraction/OCR job; admin-only."""
    _require_crawl_admin(raw_request)
    record_id = job_id if ":" in job_id else f"legal_candidate_processing_job:{job_id}"
    rows = await repo_query(
        "SELECT * FROM legal_candidate_processing_job WHERE id = $id LIMIT 1;",
        {"id": ensure_record_id(record_id)},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Không tìm thấy job trích xuất/OCR.")
    return {"job": rows[0]}


@router.get("/crawl/notifications")
async def legal_crawl_notifications(
    raw_request: Request, unread_only: bool = False
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    return {
        "notifications": await LegalCrawlService.get_notifications(
            unread_only=unread_only
        )
    }


@router.post("/crawl/notifications/{notification_id}/read")
async def legal_crawl_mark_notification_read(
    notification_id: str, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    return {
        "notification": await LegalCrawlService.mark_notification_read(notification_id)
    }
