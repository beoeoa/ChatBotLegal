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
from loguru import logger
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
from api.legal_candidate_duplicates import (
    CandidateChanged,
    DuplicateCheckUnavailable,
    candidate_duplicate_rows,
    identity_metadata,
    public_duplicate_match,
    resolve_candidate_duplicate,
    update_candidate_if_current,
)
from api.legal_crawl_service import LEGAL_DOMAINS, LegalCrawlService
from api.legal_document_identity import candidate_external_id, compare_legal_documents
from api.legal_document_serving_state import document_id as canonical_document_id
from api.legal_domains import canonicalize_legal_domain
from api.legal_effectivity_service import (
    get_legal_validity_sync_service,
)
from api.legal_lifecycle_service import (
    LifecycleError,
    default_lifecycle_service,
    lifecycle_capabilities,
)
from api.legal_replacement_candidates import project_replacement_candidates
from api.legal_replacement_workflow import (
    create_replacement_workflow,
    find_retryable_replacement_workflow,
    get_replacement_workflow,
    get_replacement_workflow_by_idempotency_key,
    list_replacement_workflows_for_document,
    update_replacement_workflow,
)
from api.legal_validity_models import LegalValidityObservation, vietnam_legal_date
from api.legal_validity_registry import (
    apply_validity_overlay,
    default_registry,
    default_snapshot_cache,
    project_validity_for_row,
)
from api.observability import telemetry
from api.organization_service import (
    copy_document_unit_assignments,
    delete_document_unit_assignments,
    domains_for_unit,
    find_unit,
    replace_document_unit_assignments,
)
from api.system_settings import (
    active_organization_units,
    active_settings,
    normalize_legal_domains,
)
from api.rag_anything_adapter import RagAnythingUnavailable, extract_with_rag_anything
from api.upload_security import (
    DOCUMENT_UPLOAD_POLICY,
    UploadSecurityError,
    validate_upload,
    write_validated_upload,
)
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
    "LEGAL_SEARCH_URL",
    os.getenv("LEGAL_RETRIEVAL_V2_URL", "http://127.0.0.1:8766"),
).rstrip("/")
LEGAL_MANAGEMENT_URL = os.getenv(
    "LEGAL_MANAGEMENT_URL", "http://127.0.0.1:8765"
).rstrip("/")

_lifecycle_service = default_lifecycle_service


async def _document_unit_import_fields(document_id: str | int) -> dict[str, Any]:
    rows = await repo_query(
        "SELECT * FROM document_organization_unit WHERE document_id = $document_id;",
        {"document_id": str(document_id)},
    )
    assignment_rows = await repo_query(
        "SELECT * FROM document_organization_assignment WHERE document_id = $document_id LIMIT 1;",
        {"document_id": str(document_id)},
    )
    primary = (assignment_rows[0].get("primary_organization_unit_id") if assignment_rows else None) or next(
        (
            str(item.get("organization_unit_id") or "")
            for item in rows
            if item.get("relationship") == "reviewing"
        ),
        None,
    )
    unit_ids = list(
        dict.fromkeys(
            str(item.get("organization_unit_id") or "")
            for item in rows
            if item.get("organization_unit_id")
        )
    )
    return {
        "primary_organization_unit_id": primary,
        "organization_unit_ids": unit_ids,
        "organization_assignment_status": (
            str(assignment_rows[0].get("confirmation_status") or "confirmed").strip()
            if assignment_rows
            else "needs_confirmation"
        ),
        "organization_assignment_state": (
            str(assignment_rows[0].get("assignment_state") or "").strip()
            if assignment_rows
            else ("assigned" if primary else "unassigned")
        ),
    }


def _positive_document_ids(rows: list[Any]) -> list[int]:
    result: set[int] = set()
    for row in rows:
        value = row.get("document_id") if isinstance(row, dict) else row
        try:
            document_id = int(str(value or "").strip())
        except (TypeError, ValueError):
            continue
        if document_id > 0:
            result.add(document_id)
    return sorted(result)


def _organization_assignment_fingerprint(snapshot: dict[str, Any]) -> str:
    canonical = {
        "assignment_state": str(snapshot.get("organization_assignment_state") or "unassigned"),
        "primary_organization_unit_id": snapshot.get("primary_organization_unit_id"),
        "organization_unit_ids": sorted(
            str(item) for item in snapshot.get("organization_unit_ids") or [] if str(item)
        ),
        "confirmation_status": str(
            snapshot.get("organization_assignment_status") or "confirmed"
        ),
    }
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


async def _management_unit_scope(
    organization_unit_id: str | None,
    responsibility: str = "all",
) -> tuple[list[int] | None, list[int] | None]:
    """Resolve SurrealDB unit relations before querying the SQL inventory."""

    unit_id = str(organization_unit_id or "").strip()
    if not unit_id:
        return None, None
    if unit_id == "__shared__":
        rows = await repo_query(
            "SELECT document_id FROM document_organization_assignment "
            "WHERE assignment_state = 'shared' GROUP BY document_id;"
        )
        return _positive_document_ids(rows), None
    if unit_id == "__unassigned__":
        assignment_rows = await repo_query(
            "SELECT document_id, assignment_state FROM document_organization_assignment;"
        )
        relation_rows = await repo_query(
            "SELECT document_id FROM document_organization_unit GROUP BY document_id;"
        )
        authoritative_ids = set(_positive_document_ids(assignment_rows))
        assigned_ids = set(
            _positive_document_ids(
                [
                    row
                    for row in assignment_rows
                    if isinstance(row, dict)
                    and str(row.get("assignment_state") or "") in {"assigned", "shared"}
                ]
            )
        )
        # The assignment row is authoritative.  Relation rows are only a
        # compatibility fallback for legacy documents that do not yet have an
        # assignment-state record; stale relations must not hide an explicitly
        # unassigned document from this filter.
        legacy_relation_ids = set(_positive_document_ids(relation_rows)) - authoritative_ids
        return None, sorted(assigned_ids | legacy_relation_ids)
    units = await active_organization_units()
    if find_unit(units, unit_id=unit_id) is None:
        raise HTTPException(status_code=400, detail="Phòng ban được chọn không tồn tại hoặc đã ngừng hoạt động.")
    rows = await repo_query(
        "SELECT document_id FROM document_organization_unit "
        "WHERE organization_unit_id = $organization_unit_id "
        "AND ($relationship = 'all' OR relationship = $relationship) GROUP BY document_id;",
        {"organization_unit_id": unit_id, "relationship": {"primary": "reviewing", "support": "reference"}.get(responsibility, "all")},
    )
    return _positive_document_ids(rows), None


async def _management_unit_projection(
    document_ids: list[str | int],
) -> dict[str, dict[str, Any]]:
    normalized_ids = list(
        dict.fromkeys(str(item).strip() for item in document_ids if str(item).strip())
    )
    if not normalized_ids:
        return {}
    rows = await repo_query(
        "SELECT document_id, organization_unit_id, relationship, confirmation_status "
        "FROM document_organization_unit WHERE document_id IN $document_ids;",
        {"document_ids": normalized_ids},
    )
    assignment_rows = await repo_query(
        "SELECT document_id, assignment_state, primary_organization_unit_id, confirmation_status "
        "FROM document_organization_assignment WHERE document_id IN $document_ids;",
        {"document_ids": normalized_ids},
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        document_id = str(row.get("document_id") or "").strip()
        if document_id:
            grouped.setdefault(document_id, []).append(row)
    projection: dict[str, dict[str, Any]] = {}
    for document_id, assignments in grouped.items():
        unit_ids = list(
            dict.fromkeys(
                str(item.get("organization_unit_id") or "").strip()
                for item in assignments
                if str(item.get("organization_unit_id") or "").strip()
            )
        )
        primary = next(
            (
                str(item.get("organization_unit_id") or "").strip()
                for item in assignments
                if item.get("relationship") == "reviewing"
            ),
            None,
        )
        projection[document_id] = {
            "primary_organization_unit_id": primary,
            "organization_unit_ids": unit_ids,
            "organization_assignment_status": (
                "needs_confirmation"
                if any(item.get("confirmation_status") != "confirmed" for item in assignments)
                else "confirmed"
            ),
            "organization_assignment_state": "assigned",
        }
    for assignment in assignment_rows:
        if not isinstance(assignment, dict):
            continue
        document_id = str(assignment.get("document_id") or "").strip()
        if not document_id:
            continue
        current = projection.setdefault(
            document_id,
            {
                "primary_organization_unit_id": None,
                "organization_unit_ids": [],
                "organization_assignment_status": "confirmed",
            },
        )
        current["organization_assignment_state"] = str(
            assignment.get("assignment_state") or "unassigned"
        )
        if assignment.get("primary_organization_unit_id"):
            current["primary_organization_unit_id"] = str(
                assignment["primary_organization_unit_id"]
            )
        confirmation_status = str(
            assignment.get("confirmation_status") or "confirmed"
        )
        if confirmation_status != "confirmed":
            current["organization_assignment_status"] = confirmation_status
    return projection


def _serving_audience(request: Request) -> str:
    """Derive serving access from authenticated backend state, never input."""

    role = str(get_request_role(request) or "citizen").strip().casefold()
    return role if role in {"citizen", "officer", "admin", "system"} else "citizen"


class LifecycleDraftCreateRequest(BaseModel):
    field_id: int | None = Field(default=None, ge=1)
    domain_slug: str | None = Field(default=None, max_length=80)
    primary_organization_unit_id: str | None = Field(default=None, max_length=120)
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
    field_id: int | None = Field(default=None, ge=1)
    domain_slug: str | None = Field(default=None, max_length=80)
    primary_organization_unit_id: str | None = Field(default=None, max_length=120)
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
    "serving_state",
    "current_answer_eligible",
    "historical_lookup_allowed",
    "search_included",
    "search_reason",
    "state_revision",
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
    "metadata_revision",
    "metadata_editable",
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
    timeout_seconds: float = 0.4,
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
    timeout_seconds: float | None = None,
    base_url: str = LEGAL_MANAGEMENT_URL,
) -> dict[str, Any]:
    """Read one required projection without exposing upstream error details."""

    try:
        payload = await _request(
            "GET",
            path,
            base_url=base_url,
            timeout_seconds=timeout_seconds or float(
                os.getenv("LEGAL_MANAGEMENT_METADATA_TIMEOUT_SECONDS", "15")
            ),
            **({"params": params} if params else {}),
        )
    except HTTPException as exc:
        status_code = 404 if exc.status_code == 404 else 503
        raise HTTPException(status_code=status_code, detail=message) from None
    except Exception:
        raise HTTPException(status_code=503, detail=message) from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=503, detail=message)
    return payload


async def _management_required_query(
    path: str,
    *,
    payload: dict[str, Any],
    message: str,
    timeout_seconds: float | None = None,
    base_url: str = LEGAL_MANAGEMENT_URL,
) -> dict[str, Any]:
    """Run a required content-free POST against the management service."""

    try:
        result = await _request(
            "POST",
            path,
            base_url=base_url,
            timeout_seconds=timeout_seconds
            or float(os.getenv("LEGAL_MANAGEMENT_METADATA_TIMEOUT_SECONDS", "15")),
            json=payload,
        )
    except HTTPException as exc:
        status_code = 404 if exc.status_code == 404 else 503
        raise HTTPException(status_code=status_code, detail=message) from None
    except Exception:
        raise HTTPException(status_code=503, detail=message) from None
    if not isinstance(result, dict):
        raise HTTPException(status_code=503, detail=message)
    return result


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
    # Official validity may further restrict SQL permission, never restore a
    # blocked/archived document from a stale snapshot. Older records without
    # this projection retain the previous compatibility behavior.
    if "current_answer_eligible" in item:
        projected["current_answer_eligible"] = bool(item["current_answer_eligible"]) and bool(validity["current_answer_eligible"])
    if "historical_lookup_allowed" in item:
        projected["historical_lookup_allowed"] = bool(item["historical_lookup_allowed"]) and bool(validity["historical_lookup_allowed"])
    if item.get("serving_state") in {"excluded", "quarantined", "historical_only", "not_yet_effective"}:
        projected["serving_status"] = item["serving_state"]
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
    as_of: date = Field(default_factory=vietnam_legal_date)
    as_of_explicit: bool | None = None
    temporal_scope: Literal["current", "historical"] | None = None
    document_id: int | None = Field(default=None, ge=1)


class LegalImportRequest(BaseModel):
    uploaded_pdf_sha256: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')
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
        pattern="^[a-z0-9][a-z0-9_-]*$",
        max_length=80,
    )
    domain_codes: list[str] = Field(default_factory=list, max_length=20)
    primary_organization_unit_id: str | None = Field(default=None, max_length=120)
    organization_unit_ids: list[str] = Field(default_factory=list, max_length=50)
    organization_assignment_state: Literal["assigned", "shared", "unassigned"] | None = None


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
    *, field_id: int, requested_domain: str | None, classified_domains: list[str], allowed_domains: set[str] | None = None
) -> str | None:
    """Keep the Admin's reviewed field/domain authoritative over text hints."""

    requested = str(requested_domain or "").strip()
    supported = allowed_domains or set(LEGAL_IMPORT_DOMAIN_SLUGS)
    if requested and requested not in supported:
        raise ValueError("LEGAL_IMPORT_DOMAIN_UNSUPPORTED")
    mapped = LEGAL_IMPORT_FIELD_DOMAINS.get(int(field_id))
    if requested and mapped and requested != mapped:
        raise ValueError("LEGAL_IMPORT_FIELD_DOMAIN_MISMATCH")
    if requested:
        return requested
    if mapped in supported:
        return mapped
    return next((domain for domain in classified_domains if domain in supported), None)


def _normalized_import_assignment(
    *,
    assignment_state: str | None,
    primary_organization_unit_id: str | None,
    organization_unit_ids: list[str],
) -> tuple[str, str | None, list[str]]:
    """Normalize the reviewed department choice before it enters the queue.

    Older clients sent only ``primary_organization_unit_id``. Treat that as
    an explicit assignment instead of allowing the candidate review step to
    infer a different owner later. Shared/unassigned records must remain free
    of department identifiers so the serving projection has one unambiguous
    authority.
    """

    primary = str(primary_organization_unit_id or "").strip() or None
    state = str(assignment_state or ("assigned" if primary else "unassigned")).strip()
    unit_ids = list(
        dict.fromkeys(
            str(item).strip()
            for item in [primary, *organization_unit_ids]
            if str(item or "").strip()
        )
    )
    if state == "assigned":
        if not primary:
            raise ValueError("Đã chọn phân công nhưng chưa có phòng ban tiếp nhận.")
        return state, primary, unit_ids
    if unit_ids:
        raise ValueError("Văn bản dùng chung hoặc chưa phân công không được gắn phòng ban.")
    return state, None, []


class LegalCrawlPreviewRequest(BaseModel):
    url: str = Field(min_length=8, max_length=4000)


class LegalReplacementWorkflowRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=8, max_length=4000)
    reason: str = Field(min_length=10, max_length=2000)
    uploaded_content: str | None = Field(default=None, max_length=8_000_000)
    uploaded_filename: str | None = Field(default=None, max_length=255)


class LegalManagementReplacementRequest(BaseModel):
    """Admin replacement started directly from the legal document inventory."""

    source_url: str = Field(default="", max_length=4000)
    reason: str = Field(min_length=10, max_length=2000)
    uploaded_content: str | None = Field(default=None, min_length=20, max_length=8_000_000)
    uploaded_filename: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=2000)
    law_number: str | None = Field(default=None, max_length=255)
    document_type: str | None = Field(default=None, max_length=255)
    issuing_agency: str | None = Field(default=None, max_length=500)
    issued_date: date | None = None
    effective_date: date | None = None
    expired_date: date | None = None
    scope: str | None = Field(default=None, max_length=100)
    sector: str | None = Field(default=None, max_length=500)
    applicability_info: str | None = Field(default=None, max_length=4000)


class LegalManagementSearchStateRequest(BaseModel):
    action: Literal["exclude", "restore", "historical", "quarantine"]
    reason: str = Field(min_length=10, max_length=2000)
    expected_revision: str = Field(pattern="^[0-9a-f]{64}$")


class LegalManagementHardDeleteRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)
    expected_revision: str = Field(pattern="^[0-9a-f]{64}$")
    confirmation_text: str = Field(min_length=1, max_length=255)


class LegalManagementOrganizationAssignmentRequest(BaseModel):
    assignment_state: Literal["assigned", "shared", "unassigned"]
    primary_organization_unit_id: str | None = Field(default=None, max_length=120)
    organization_unit_ids: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=10, max_length=2000)
    expected_fingerprint: str | None = Field(
        default=None, pattern="^[0-9a-f]{64}$"
    )


class LegalManagementMetadataUpdateRequest(BaseModel):
    issued_date: date | None = None
    effective_date: date | None = None
    expired_date: date | None = None
    source_url: str | None = Field(default=None, max_length=4000)
    gazette_date: date | None = None
    signer_title: str | None = Field(default=None, max_length=500)
    signer_name: str | None = Field(default=None, max_length=500)
    applicability_info: str | None = Field(default=None, max_length=4000)
    version: str | None = Field(default=None, max_length=255)
    reason: str = Field(min_length=10, max_length=2000)
    expected_revision: str = Field(pattern="^[0-9a-f]{64}$")
    confirm_validity: bool = False


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
    compatibility_mode: Literal["standard", "high"] | None = None
    website_type: Literal["mixed_official", "legal_documents", "procedures", "forms", "reference"] | None = None
    link_selector: str | None = Field(default=None, max_length=500)
    next_page_selector: str | None = Field(default=None, max_length=500)
    include_patterns: list[str] | None = Field(default=None, max_length=20)
    exclude_patterns: list[str] | None = Field(default=None, max_length=20)
    domains: list[str] | None = Field(default=None, max_length=20)
    default_organization_unit_id: str | None = Field(default=None, max_length=120)
    unassigned_policy: Literal["unassigned", "shared"] | None = None


class LegalCrawlSourceCreateRequest(BaseModel):
    name: str = Field(min_length=3, max_length=255)
    base_url: str = Field(min_length=8, max_length=4000)
    sitemap_scope: str = Field(pattern="^(central|haiphong|local)$")
    interval_minutes: int = Field(default=10080, ge=15, le=10080)
    lookback_days: int = Field(default=30, ge=1, le=3650)
    max_documents_per_run: int = Field(default=30, ge=1, le=200)
    max_listing_pages_per_run: int = Field(default=10, ge=1, le=50)
    rate_limit_seconds: float = Field(default=1.5, ge=0.2, le=30)
    compatibility_mode: Literal["standard", "high"] = "standard"
    filter_keyword: str | None = Field(default=None, max_length=255)
    website_type: Literal["mixed_official", "legal_documents", "procedures", "forms", "reference"] = "mixed_official"
    link_selector: str | None = Field(default=None, max_length=500)
    next_page_selector: str | None = Field(default=None, max_length=500)
    include_patterns: list[str] = Field(default_factory=list, max_length=20)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=20)
    domains: list[str] = Field(default_factory=lambda: sorted(LEGAL_DOMAINS), max_length=20)
    default_organization_unit_id: str | None = Field(default=None, max_length=120)
    unassigned_policy: Literal["unassigned", "shared"] = "unassigned"


class LegalCandidateDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected|changes_requested)$")
    review_note: str = Field(default="", max_length=2000)


class LegalCandidateDuplicateResolutionRequest(BaseModel):
    action: Literal["archive_duplicate", "review_replacement"]
    target_type: Literal["document", "candidate"]
    target_id: str = Field(min_length=1, max_length=200)
    expected_revision: str = Field(pattern="^[a-f0-9]{64}$")
    target_revision: str = Field(pattern="^[a-f0-9]{64}$")
    reason: str = Field(min_length=10, max_length=2000)
    confirmed_same_document: bool = False


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
    assignment_state: Literal["assigned", "shared", "unassigned"] | None = None
    primary_organization_unit_id: str | None = Field(default=None, max_length=120)
    assignment_reason: str | None = Field(default=None, min_length=10, max_length=2000)


class LegalValidityDecisionRequest(BaseModel):
    action: Literal[
        "confirm_mapping",
        "reject_match",
        "request_recheck",
        "mark_historical",
        "quarantine",
    ]
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
    failure_phase: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    release_id: str | None = None
    manifest_sha256: str | None = None


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
    operation: dict[str, Any] | None = None


async def _request(
    method: str,
    path: str,
    *,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    timeout = timeout_seconds or float(os.getenv("LEGAL_UPSTREAM_TIMEOUT_SECONDS", "900"))
    from api.legal_upstream import get_legal_upstream_client

    normalized_method = method.upper()
    attempts = 2 if normalized_method == "GET" else 1
    url = f"{base_url or LEGAL_SEARCH_URL}{path}"
    for attempt in range(attempts):
        try:
            client = get_legal_upstream_client()
            if client is not None:
                response = await client.request(
                    normalized_method, url, timeout=timeout, **kwargs
                )
            else:
                async with httpx.AsyncClient(timeout=timeout) as temporary_client:
                    response = await temporary_client.request(
                        normalized_method, url, **kwargs
                    )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            try:
                payload = exc.response.json()
            except (ValueError, TypeError):
                payload = None
            detail = payload.get("detail") if isinstance(payload, dict) else payload
            if status_code >= 500:
                # Replacement is an Admin-only workflow whose structured
                # compensation receipt is required for safe retry and cleanup.
                # Preserve only its bounded business fields; arbitrary upstream
                # text, traces and infrastructure errors remain hidden.
                if (
                    isinstance(detail, dict)
                    and detail.get("code") == "replacement_activation_failed"
                ):
                    safe_keys = {
                        "code", "stage", "new_document_id", "compensation",
                        "verification", "message",
                    }
                    raise HTTPException(
                        status_code=status_code,
                        detail={key: detail[key] for key in safe_keys if key in detail},
                    ) from exc
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Dịch vụ dữ liệu pháp luật đang tạm gián đoạn. "
                        "Vui lòng thử lại sau ít phút."
                    ),
                ) from exc
            if not detail:
                detail = "Không thể hoàn tất thao tác với dữ liệu pháp luật."
            raise HTTPException(status_code=status_code, detail=detail) from exc
        except httpx.TransportError as exc:
            # A pooled keep-alive connection can become stale while the local
            # management service is idle or restarted. Retrying a read is safe;
            # writes are deliberately never replayed here.
            if attempt + 1 < attempts:
                continue
            raise HTTPException(
                status_code=503,
                detail=(
                    "Dịch vụ dữ liệu pháp luật đang tạm gián đoạn. "
                    "Vui lòng thử lại sau ít phút."
                ),
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Dịch vụ dữ liệu pháp luật đang tạm gián đoạn. "
                    "Vui lòng thử lại sau ít phút."
                ),
            ) from exc

    raise HTTPException(
        status_code=503,
        detail="Dịch vụ dữ liệu pháp luật đang tạm gián đoạn. Vui lòng thử lại sau ít phút.",
    )


def _replacement_replay(existing: dict[str, Any] | None, fingerprint: str) -> dict[str, Any] | None:
    """A durable reservation, including one won by another API worker."""
    if not existing:
        return None
    previous = str(existing.get("request_fingerprint") or "")
    if previous and previous != fingerprint:
        raise HTTPException(status_code=409, detail="Idempotency-Key đã được dùng cho nội dung thay thế khác.")
    if existing.get("status") == "activated" and isinstance(existing.get("result"), dict):
        return {**existing["result"], "workflow_id": existing.get("workflow_id"), "idempotent_replay": True}
    if existing.get("status") == "failed":
        raise HTTPException(status_code=409, detail={
            "code": "replacement_previous_attempt_failed", "workflow_id": existing.get("workflow_id"),
            "message": "Lần thay thế trước thất bại. Kiểm tra trạng thái hoàn tác trước khi tạo yêu cầu mới.",
        })
    if (
        existing.get("status") in {"processing", "pending_retry"}
        and (existing.get("steps") or {}).get("activation_journal") == "prepared"
    ):
        # The PostgreSQL activation journal serializes this exact version key
        # and returns its durable receipt after an API crash. Continuing here
        # is therefore a recovery attempt, not a second import.
        return None
    raise HTTPException(status_code=409, detail={
        "code": "replacement_in_progress", "workflow_id": existing.get("workflow_id"),
        "message": "Yêu cầu thay thế này đã được tiếp nhận; không tạo thêm lần nhập trùng.",
    })


def _verified_replacement_result(result: dict[str, Any]) -> dict[str, Any]:
    """Require proof from the shared management saga, without a second probe."""
    smoke = result.get("retrieval_smoke") or {}
    if not (
        result.get("status") == "replaced"
        and result.get("activation_status") == "active"
        and int(result.get("chunk_count") or 0) > 0
        and result.get("vector_collection")
        and (result.get("activation_receipt") or {}).get("passed") is True
        and smoke.get("passed") is True
        and smoke.get("exact_match") is True
        and (smoke.get("vector_membership") or {}).get(
            "current_retrieval_ready"
        ) is True
        and str(smoke.get("document_id") or "") == str(result.get("document_id") or "")
        and (result.get("old_document") or {}).get("search_included") is False
        and ((result.get("old_document") or {}).get("verification") or {}).get("passed") is True
    ):
        raise HTTPException(status_code=502, detail={
            "code": "replacement_confirmation_incomplete",
            "message": "Chưa xác nhận đủ trạng thái thay thế và kiểm tra tìm kiếm; cần kiểm tra workflow trước khi thử lại.",
        })
    return dict(smoke)


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
    uploaded = projected.get("uploaded_file") if isinstance(projected.get("uploaded_file"), dict) else {}
    projected.pop("content", None)
    projected.pop("ai_assessment", None)
    projected.pop("review_recommendation", None)
    projected.pop("uploaded_file", None)
    projected.pop("source_asset", None)
    projected["matched_domains"] = sorted(_candidate_domains(candidate))
    if preview:
        projected["content_preview"] = preview[:500]
    raw = projected.get("raw_metadata") if isinstance(projected.get("raw_metadata"), dict) else {}
    projected["proposal_revision"] = int(
        projected.get("proposal_revision")
        or raw.get("proposal_revision")
        or 1
    )
    projected["proposal_thread_id"] = str(
        projected.get("proposal_thread_id")
        or raw.get("proposal_thread_id")
        or projected.get("id")
    )
    if uploaded:
        # Never expose the local/object-storage path to an officer client.
        projected["uploaded_file"] = {
            key: value
            for key, value in uploaded.items()
            if key in {"filename", "content_type", "size", "sha256", "malware_scan"}
        }
    return projected


def _officer_candidate_edit_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return only the submitting officer's bounded editable proposal fields."""
    raw = candidate.get("raw_metadata") if isinstance(candidate.get("raw_metadata"), dict) else {}
    uploaded = candidate.get("uploaded_file") if isinstance(candidate.get("uploaded_file"), dict) else {}
    fields = {
        "id": str(candidate.get("id") or ""),
        "title": candidate.get("title") or "",
        "law_number": candidate.get("law_number") or "",
        "domain": candidate.get("domain") or raw.get("domain") or "",
        "source_type": candidate.get("source_type") or raw.get("source_type") or "document",
        "document_type": candidate.get("document_type") or raw.get("document_type") or "",
        "issuing_agency": candidate.get("issuing_agency") or raw.get("issuing_agency") or "",
        "scope": candidate.get("scope") or raw.get("scope") or "central",
        "sector": raw.get("sector") or "",
        "source_url": candidate.get("source_url") or raw.get("source_url") or candidate.get("detail_url") or "",
        "issued_date": candidate.get("issued_date") or raw.get("issued_date") or "",
        "effective_date": candidate.get("effective_date") or raw.get("effective_date") or "",
        "expired_date": candidate.get("expired_date") or raw.get("expired_date") or "",
        "proposal_reason": candidate.get("proposal_reason") or raw.get("proposal_reason") or "",
        # Bound the editable text to keep the detail response safe for a large queue.
        "content": str(candidate.get("content") or "")[:100_000],
        "content_truncated": len(str(candidate.get("content") or "")) > 100_000,
        "status": candidate.get("status") or "",
        "review_status": candidate.get("review_status") or candidate.get("status") or "",
        "requested_changes_note": candidate.get("requested_changes_note") or candidate.get("review_note") or "",
        "proposal_revision": int(candidate.get("proposal_revision") or raw.get("proposal_revision") or 1),
        "proposal_thread_id": str(candidate.get("proposal_thread_id") or raw.get("proposal_thread_id") or candidate.get("id") or ""),
        "updated": candidate.get("updated") or candidate.get("updated_at"),
    }
    if uploaded:
        fields["uploaded_file"] = {
            key: value
            for key, value in uploaded.items()
            if key in {"filename", "content_type", "size", "sha256", "malware_scan"}
        }
    return fields


def _require_crawl_admin(request: Request) -> None:
    """Restrict candidate-crawler administration to the final reviewer role."""
    if get_request_role(request) != "admin":
        raise HTTPException(
            status_code=403,
            detail="Chỉ admin được cấu hình, quét, duyệt hoặc import candidate crawler.",
        )


def _domain_allowed(domain: str, allowed_domains: list[str]) -> bool:
    target = canonicalize_legal_domain(domain)
    if not target:
        return False
    return any(canonicalize_legal_domain(item) == target for item in allowed_domains)


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


def _cached_upload_extraction(
    filename: str,
    content: bytes,
) -> tuple[str, dict[str, Any]] | None:
    """Reuse a canonical SHA-bound artifact before invoking OCR again."""

    from api.document_contracts import ExtractionArtifactStore
    from api.multimodal_preprocess import EXTRACTION_PIPELINE_VERSION

    digest = hashlib.sha256(content).hexdigest()
    artifact = ExtractionArtifactStore().get_valid(
        digest,
        required_version=EXTRACTION_PIPELINE_VERSION,
    )
    if artifact is None:
        return None
    pages = list(artifact.pages)
    blocks = list(artifact.blocks)
    total_pages = len(pages)
    processed_pages = sum(1 for page in pages if str(page.get("text") or "").strip())
    metadata = {
        "status": "ok" if artifact.status == "complete" else "review_required",
        "filename": filename,
        "file_fingerprint": digest,
        "text_fingerprint": hashlib.sha256(artifact.text.encode("utf-8")).hexdigest(),
        "preview": artifact.text[:2000],
        "extractor_used": artifact.extractor,
        "extractor_version": artifact.version,
        "ocr_status": "cached",
        "ocr_confidence": artifact.confidence,
        "complete": artifact.status == "complete",
        "total_pages": total_pages,
        "page_count": total_pages,
        "processed_pages": processed_pages or total_pages,
        "coverage_percent": 100 if artifact.status == "complete" else None,
        "extraction_blocks": blocks,
        "table_count": len(artifact.tables),
        "table_extracted_count": len(artifact.tables),
        "warnings": list(artifact.warnings),
        "cache_hit": True,
        "reason": "Đã dùng kết quả trích xuất hợp lệ theo SHA-256; không chạy lại OCR.",
    }
    return artifact.text, metadata


def _store_upload_extraction_artifact(
    content: bytes,
    extraction: dict[str, Any],
) -> None:
    """Persist only useful canonical results; a deferred preview is not an artifact."""

    if not str(extraction.get("text") or "").strip() or extraction.get("deferred_ocr"):
        return
    from api.document_contracts import ExtractionArtifact, ExtractionArtifactStore
    from api.multimodal_preprocess import EXTRACTION_PIPELINE_VERSION

    value = dict(extraction)
    value["extractor_version"] = EXTRACTION_PIPELINE_VERSION
    artifact = ExtractionArtifact.from_result(hashlib.sha256(content).hexdigest(), value)
    ExtractionArtifactStore().put(artifact)


def _extract_upload_text(
    filename: str,
    content: bytes,
    *,
    defer_ocr: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Extract review-only candidate text; OCR errors never become text content."""
    lower_name = filename.lower()
    from api.local_image_ocr import IMAGE_EXTENSIONS, extract_image
    if Path(filename).suffix.lower() in IMAGE_EXTENSIONS:
        cached = _cached_upload_extraction(filename, content)
        if cached is not None:
            return cached
        if defer_ocr:
            return "", {
                "status": "processing",
                "filename": filename,
                "file_fingerprint": hashlib.sha256(content).hexdigest(),
                "pdf_kind": "not_pdf",
                "ocr_status": "queued",
                "ocr_confidence": None,
                "complete": False,
                "total_pages": 1,
                "page_count": 1,
                "processed_pages": 0,
                "coverage_percent": 0,
                "pages_without_text": [1],
                "ocr_requested_pages": [1],
                "deferred_ocr": True,
                "reason": "Ảnh sẽ được OCR trong tác vụ nền sau khi gửi duyệt.",
            }
        try:
            result = extract_image(content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result = dict(result)
        text = str(result.get("text") or "")
        result["text"] = text
        _store_upload_extraction_artifact(content, result)
        return text, {key: value for key, value in result.items() if key != 'text'}
    if lower_name.endswith((".txt", ".md", ".json")):
        text = content.decode("utf-8", errors="replace")
        return text, _text_review_metadata(text, filename=filename, extractor_used="utf8_text")
    if lower_name.endswith(".docx"):
        text = _extract_docx(content)
        return text, _text_review_metadata(text, filename=filename, extractor_used="python_docx")
    if lower_name.endswith(".pdf"):
        from api.crawlers.pdf_extractor import extract_pdf_bytes_for_review

        cached = _cached_upload_extraction(filename, content)
        if cached is not None:
            return cached
        result = extract_pdf_bytes_for_review(
            content,
            filename=filename,
            perform_ocr=not defer_ocr,
        )
        _store_upload_extraction_artifact(content, result)
        return str(result.get("text") or ""), result
    raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file TXT, MD, DOCX hoặc PDF.")


async def _extract_legal_upload_for_review(
    filename: str,
    content: bytes,
    mime_type: str,
    *,
    defer_ocr: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Use one upload contract for preview and the eventual review submission.

    Legacy Office and spreadsheet formats must pass through the canonical
    native/LibreOffice pipeline. PDF and image OCR may be deferred so request
    latency stays bounded and the existing candidate worker owns heavy work.
    """

    suffix = Path(filename).suffix.lower()
    if suffix not in {".doc", ".xls", ".xlsx"}:
        return await asyncio.to_thread(
            _extract_upload_text,
            filename,
            content,
            defer_ocr=defer_ocr,
        )

    cached = _cached_upload_extraction(filename, content)
    if cached is not None:
        return cached

    from api.multimodal_preprocess import extract_artifact_from_file

    try:
        artifact_result, _ = await extract_artifact_from_file(
            content,
            filename,
            mime_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = dict(artifact_result)
    text = str(result.get("text") or "")
    result.setdefault("extractor_used", result.get("extractor") or "document-pipeline")
    result.setdefault("page_count", len(result.get("pages") or []))
    result.setdefault("processed_pages", result.get("page_count") or 0)
    result.setdefault("preview", text[:2000])
    result.setdefault(
        "text_fingerprint",
        hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
    )
    result.setdefault("file_fingerprint", hashlib.sha256(content).hexdigest())
    _store_upload_extraction_artifact(content, result)
    return text, result


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
    candidate: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    candidate = candidate or {"law_number": law_number, "source_url": source_url}
    rows = await candidate_duplicate_rows([candidate], query=repo_query)
    return [
        {
            **match,
            "external_id": row.get("external_id"),
            "review_status": row.get("review_status"),
        }
        for row in rows or []
        if (match := public_duplicate_match(candidate, row, "candidate"))
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
    request_payload = request.model_dump(mode="json")
    request_payload["as_of_explicit"] = (
        request.as_of_explicit
        if request.as_of_explicit is not None
        else "as_of" in request.model_fields_set
    )
    audience = _serving_audience(raw_request)
    request_payload["audience"] = audience
    if audience == "officer":
        profile = await get_user_profile(str(get_request_user_id(raw_request) or ""))
        nested = (
            profile.get("profile", {})
            if isinstance((profile or {}).get("profile"), dict)
            else {}
        )
        request_payload["organization_unit_id"] = (
            (profile or {}).get("organization_unit_id")
            or nested.get("organization_unit_id")
        )
    payload = await _request("POST", "/search", json=request_payload)
    return apply_validity_overlay(
        payload,
        snapshot=default_snapshot_cache.load(),
        as_of=request.as_of,
    )


@router.get("/import/fields")
async def legal_import_fields() -> dict[str, Any]:
    from api.commune_catalog import scoped_import_fields
    payload = await _request("GET", "/import/fields", base_url=LEGAL_MANAGEMENT_URL)
    return {**payload, "fields": scoped_import_fields(payload.get("fields", []))}


@router.get("/commune-catalog")
async def public_commune_catalog() -> dict[str, Any]:
    from api.commune_catalog import commune_catalog
    from api.system_settings import (
        active_organization_units,
        active_settings,
        normalize_legal_domains,
    )
    settings = await active_settings()
    units = await active_organization_units(settings)
    domains = normalize_legal_domains(getattr(settings, "legal_domains", None))
    active_domain_names = {
        item.code: item.name for item in domains if item.is_active
    }
    # This endpoint supplies current filter choices. Retired domains remain in
    # stored records/history but must not return as a new public selection.
    groups = [
        group
        for group in commune_catalog()
        if group["field_code"] in active_domain_names
    ]
    public_groups = [
        {
            **group,
            "name": active_domain_names.get(group["field_code"], group["name"]),
            "organization_unit_ids": [
                unit.id
                for unit in units
                if unit.is_active and unit.code == group["code"]
            ],
        }
        for group in groups
    ]
    departments = []
    for unit in units:
        if not unit.is_active:
            continue
        fields = [
            {
                "code": group["field_code"],
                "name": active_domain_names.get(group["field_code"], group["name"]),
                "domains": list(
                    dict.fromkeys([group["field_code"], *group["domains"]])
                ),
            }
            for group in groups
            if group["field_code"] in unit.domain_codes
            or (
                group["code"] == unit.code
                and set(group["domains"]).intersection(unit.domain_codes)
            )
        ]
        represented = {field["code"] for field in fields}
        fields.extend(
            {
                "code": code,
                "name": active_domain_names[code],
                "domains": [code],
            }
            for code in unit.domain_codes
            if code in active_domain_names and code not in represented
        )
        departments.append(
            {"id": unit.id, "name": unit.name, "code": unit.code, "fields": fields}
        )
    return {
        "groups": public_groups,
        "departments": departments,
    }


@router.get("/domains")
async def legal_domains() -> dict[str, Any]:
    return await _request("GET", "/domains", base_url=LEGAL_MANAGEMENT_URL)


@router.get("/documents/lookup")
async def legal_document_lookup(law_number: str, raw_request: Request) -> dict[str, Any]:
    return await _request(
        "GET", "/documents/lookup", base_url=LEGAL_MANAGEMENT_URL,
        params={"law_number": law_number, "audience": _serving_audience(raw_request)},
    )


@router.get("/documents")
async def list_legal_documents(
    raw_request: Request,
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    validity_status: str | None = Query(
        default=None,
        pattern="^(active|expiring_30|not_yet_effective|expired|unknown)$",
    ),
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
    """List the canonical legal inventory with its shared validity projection."""
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
        "include_expired_history": True,
    }
    if domain:
        params["domain"] = domain
    if validity_status:
        params["validity_status"] = validity_status
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
    payload = await _management_required_read(
        "/management/documents",
        params=params,
        message="Không thể đọc kho văn bản pháp luật.",
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return payload
    try:
        legal_as_of = date.fromisoformat(as_of) if as_of else vietnam_legal_date()
    except ValueError:
        legal_as_of = vietnam_legal_date()
    snapshot = default_snapshot_cache.load()
    items = [
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
    ]
    return {
        **payload,
        "items": items,
        "inventory_source": "canonical_management_projection",
    }


@router.get("/documents/export.xlsx")
async def export_legal_documents_xlsx(
    raw_request: Request,
    q: str = Query(default="", max_length=500),
    domain: str | None = Query(default=None, max_length=120),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    validity_status: str | None = Query(
        default=None,
        pattern="^(active|expiring_30|not_yet_effective|expired|unknown)$",
    ),
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
    }
    if domain:
        params["domain"] = domain
    if validity_status:
        params["validity_status"] = validity_status
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
                f"{LEGAL_MANAGEMENT_URL}/management/documents/export.xlsx",
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
    if payload.decision == "approved" and lifecycle_capabilities(actor, role)["activation_enabled"]:
        try:
            result = await _lifecycle_service.activate_draft(
                draft_id, actor=actor, role=role, expected_revision=result["revision"],
                reason=payload.reason, idempotency_key="activate:" + hashlib.sha256(idempotency_key.encode()).hexdigest(),
            )
        except LifecycleError as exc:
            result = {**result, "activation_state": "pending_retry", "activation_error": exc.message}
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
    observed_as_of = vietnam_legal_date().isoformat()
    base, operations, validity = await asyncio.gather(
        _management_required_read(
            "/management/summary",
            params={"as_of": observed_as_of},
            message="Không thể đọc tổng quan kho văn bản.",
            timeout_seconds=float(
                os.getenv("LEGAL_MANAGEMENT_SUMMARY_TIMEOUT_SECONDS", "5.0")
            ),
        ),
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
        "serving_release": base.get("serving_release") or {},
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
    organization_unit_id: str | None = Query(default=None, max_length=120),
    organization_responsibility: str = Query(default="all", pattern="^(all|primary|support)$"),
    include_organization_units: bool = Query(default=False),
    stored_status: str | None = Query(default=None, max_length=80),
    validity_status: str | None = Query(
        default=None, pattern="^(active|not_yet_effective|expired|unknown)$"
    ),
    tier: str = Query(default="all", pattern="^(all|core|expanded)$"),
    data_quality: str | None = Query(
        default=None,
        pattern="^(missing_source|missing_metadata|zero_chunks|unknown_status|unclassified)$",
    ),
    source_presence: str = Query(default="all", pattern="^(all|present|missing)$"),
    issued_from: str | None = Query(default=None, max_length=10),
    issued_to: str | None = Query(default=None, max_length=10),
    effective_from: str | None = Query(default=None, max_length=10),
    effective_to: str | None = Query(default=None, max_length=10),
    expired_from: str | None = Query(default=None, max_length=10),
    expired_to: str | None = Query(default=None, max_length=10),
    include_expired_history: bool = Query(default=False),
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
    unit_filter = (
        organization_unit_id.strip()
        if isinstance(organization_unit_id, str) and organization_unit_id.strip()
        else None
    )
    include_unit_projection = include_organization_units is True
    params: dict[str, Any] = {
        "q": q,
        "tier": tier,
        "source_presence": source_presence,
        "limit": limit,
        "offset": offset,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "include_expired_history": include_expired_history,
        "as_of": as_of or vietnam_legal_date().isoformat(),
    }
    for key, value in (
        ("document_type", document_type),
        ("issuing_agency", issuing_agency),
        ("scope", scope),
        ("domain", canonicalize_legal_domain(domain) if domain else domain),
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
    if unit_filter:
        try:
            include_document_ids, exclude_document_ids = await asyncio.wait_for(
                _management_unit_scope(
                    unit_filter,
                    organization_responsibility
                    if isinstance(organization_responsibility, str)
                    else "all",
                ),
                timeout=3.0,
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Không thể áp dụng bộ lọc phòng ban lúc này.",
            ) from None
        payload = await _management_required_query(
            "/management/documents/query",
            payload={
                **params,
                "include_document_ids": include_document_ids,
                "exclude_document_ids": exclude_document_ids,
            },
            message="Không thể đọc danh sách kho văn bản.",
        )
    else:
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
        legal_as_of = vietnam_legal_date()
    snapshot = default_snapshot_cache.load()
    items = [
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
        ]
    unit_projection: dict[str, dict[str, Any]] = {}
    unit_projection_available = not include_unit_projection
    if include_unit_projection:
        try:
            unit_projection = await asyncio.wait_for(
                _management_unit_projection([item.get("doc_id") for item in items]),
                timeout=0.4,
            )
            unit_projection_available = True
        except Exception:
            unit_projection = {}
            unit_projection_available = False
    for item in items:
        item.update(unit_projection.get(str(item.get("doc_id") or ""), {}))
    return {
        "items": items,
        "total": int(payload.get("total") or 0),
        "limit": int(payload.get("limit") or limit),
        "offset": int(payload.get("offset") or offset),
        "as_of": payload.get("as_of") or params["as_of"],
        "observed_at": payload.get("observed_at")
        or datetime.now(timezone.utc).isoformat(),
        "serving_release": payload.get("serving_release") or {},
        "organization_units_available": unit_projection_available,
    }


@router.get("/management/documents/{document_id}")
async def legal_management_document(
    document_id: str, raw_request: Request
) -> dict[str, Any]:
    """Return one Admin-only evidence projection with bounded optional sections."""

    _require_crawl_admin(raw_request)
    canonical_id = _management_document_id(document_id)
    # Metadata and lifecycle operations belong to the management service.
    # Its detail adapter obtains a content-free vector receipt from live V2;
    # V2 itself intentionally does not expose the full management document API.
    base = await _management_required_read(
        f"/management/documents/{canonical_id}",
        message="Không thể đọc chi tiết văn bản.",
        base_url=LEGAL_MANAGEMENT_URL,
        # Detail includes a bounded live-vector receipt; the 1.5 s list budget
        # can expire before that receipt completes on the local deployment.
        timeout_seconds=5.0,
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
            "Chưa có bản ghi xác minh hiệu lực cho văn bản này. Các ngày trong metadata không tự được coi là bằng chứng đã xác minh.",
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
    document_as_of_value = str(
        base.get("as_of") or vietnam_legal_date().isoformat()
    )
    try:
        document_as_of = date.fromisoformat(document_as_of_value[:10])
    except ValueError:
        document_as_of = vietnam_legal_date()
    projected_document = _management_validity_projection(
        base.get("document"),
        snapshot=default_snapshot_cache.load(),
        as_of=document_as_of,
    )
    safe_document = _management_safe_document(projected_document)
    organization_units_available = True
    try:
        unit_projection = await asyncio.wait_for(
            _management_unit_projection([canonical_id]), timeout=0.4
        )
        safe_document.update(unit_projection.get(canonical_id, {}))
        safe_document["organization_assignment_fingerprint"] = (
            _organization_assignment_fingerprint(safe_document)
        )
    except Exception:
        organization_units_available = False
    base_versions = (
        base.get("versions") if isinstance(base.get("versions"), dict) else {}
    )
    replacement_versions: list[dict[str, Any]] = []
    try:
        for workflow in list_replacement_workflows_for_document(canonical_id):
            result = (
                workflow.get("result")
                if isinstance(workflow.get("result"), dict)
                else {}
            )
            old_document = (
                result.get("old_document")
                if isinstance(result.get("old_document"), dict)
                else {}
            )
            new_document = (
                result.get("new_document")
                if isinstance(result.get("new_document"), dict)
                else {}
            )
            old_id = str(
                old_document.get("document_id")
                or workflow.get("old_document_id")
                or ""
            ).split(":")[-1]
            new_id = str(
                result.get("document_id") or new_document.get("document_id") or ""
            ).split(":")[-1]
            replacement_versions.append(
                {
                    "workflow_id": workflow.get("workflow_id"),
                    "status": workflow.get("status"),
                    "relation": "replaced_by" if canonical_id == old_id else "replaces",
                    "old_document_id": old_id or None,
                    "old_law_number": old_document.get("law_number"),
                    "old_title": old_document.get("title"),
                    "new_document_id": new_id or None,
                    "new_law_number": result.get("law_number"),
                    "source_url": workflow.get("source_url"),
                    "reason": str(workflow.get("reason") or "")[:2000],
                    "created_at": workflow.get("created_at"),
                    "updated_at": workflow.get("updated_at"),
                }
            )
    except Exception:
        replacement_versions = []
    if replacement_versions:
        versions = {
            "status": "available",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "legacy_version": base_versions.get("legacy_version"),
            "items": [
                *(base_versions.get("items") or []),
                *replacement_versions,
            ],
            "projection_source": "replacement_workflow_audit",
        }
    else:
        versions = base_versions or _management_unavailable(
            "schema_not_approved",
            "Lịch sử phiên bản chưa được cấu hình cho kho dữ liệu này.",
        )
    return {
        "observed_at": base.get("observed_at")
        or datetime.now(timezone.utc).isoformat(),
        "document": safe_document,
        "organization_units_available": organization_units_available,
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
            "faq_dependency_not_configured", "Hệ thống chưa cấu hình bảng liên kết FAQ với văn bản pháp luật; đây không phải lỗi riêng của văn bản này."
        ),
        "versions": versions,
        "audit": audit,
    }


@router.post("/management/documents/{document_id}/metadata")
async def legal_management_document_metadata_update(
    document_id: str,
    request: LegalManagementMetadataUpdateRequest,
    raw_request: Request,
) -> dict[str, Any]:
    """Correct mutable metadata and optionally record an explicit validity review."""

    _require_crawl_admin(raw_request)
    canonical_id = _management_document_id(document_id)
    current = await _management_required_read(
        f"/management/documents/{canonical_id}",
        message="Không thể đọc metadata hiện tại của văn bản.",
        base_url=LEGAL_MANAGEMENT_URL,
        timeout_seconds=5.0,
    )
    before = _management_safe_document(current.get("document"))
    if not before:
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản.")
    if before.get("metadata_editable") is False:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "immutable_release_document_requires_replacement",
                "message": "Văn bản thuộc bộ dữ liệu đã phát hành; hãy dùng luồng Thay thế văn bản để bảo toàn bản phát hành.",
            },
        )
    if request.expected_revision != str(before.get("metadata_revision") or ""):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "document_metadata_changed",
                "message": "Thông tin văn bản vừa thay đổi. Vui lòng tải lại trước khi lưu.",
            },
        )

    source_url = str(request.source_url or "").strip() or None
    if source_url:
        try:
            source_url = validate_official_public_url(source_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.effective_date and request.expired_date and request.expired_date < request.effective_date:
        raise HTTPException(
            status_code=422,
            detail="Ngày hết hiệu lực không được trước ngày có hiệu lực.",
        )
    if request.confirm_validity and (not source_url or not request.effective_date):
        raise HTTPException(
            status_code=422,
            detail="Muốn xác nhận hiệu lực, cần có URL nguồn chính thức và ngày có hiệu lực.",
        )

    upstream_payload = request.model_dump(
        mode="json", exclude={"reason", "confirm_validity"}
    )
    upstream_payload["source_url"] = source_url
    result = await _management_required_query(
        f"/management/documents/{canonical_id}/metadata",
        payload=upstream_payload,
        message="Không thể cập nhật đồng bộ metadata và kho tra cứu.",
        timeout_seconds=30.0,
    )
    after = _management_safe_document(result.get("document"))
    actor_user_id = get_request_user_id(raw_request)
    await write_audit_log(
        action="legal.document.metadata.update",
        entity_type="legal_document",
        entity_id=canonical_id,
        actor_user_id=actor_user_id,
        actor_role=get_request_role(raw_request),
        details={
            "reason": request.reason.strip(),
            "before": {key: before.get(key) for key in (
                "issued_date", "effective_date", "expired_date", "source_url",
                "gazette_date", "signer_title", "signer_name", "version",
                "applicability_info",
            )},
            "after": {key: after.get(key) for key in (
                "issued_date", "effective_date", "expired_date", "source_url",
                "gazette_date", "signer_title", "signer_name", "version",
                "applicability_info",
            )},
            "validity_confirmed": request.confirm_validity,
        },
        request=raw_request,
    )

    validity_receipt: dict[str, Any] | None = None
    if request.confirm_validity:
        today = vietnam_legal_date()
        normalized_status = (
            "expired"
            if request.expired_date and request.expired_date <= today
            else "not_yet_effective"
            if request.effective_date and request.effective_date > today
            else "active"
        )
        now = datetime.now(timezone.utc)
        try:
            observation = LegalValidityObservation.from_dict(
                {
                    "document_id": canonical_id,
                    "law_number": before.get("law_number"),
                    "issuing_agency": before.get("issuing_agency"),
                    "issued_date": request.issued_date,
                    "source_url": source_url,
                    "source_kind": "admin_confirmed_stored_metadata",
                    "raw_status": "Quản trị viên xác nhận metadata hiệu lực từ nguồn chính thức.",
                    "normalized_status": normalized_status,
                    "effective_from": request.effective_date,
                    "effective_to": request.expired_date,
                    "affected_provisions": [],
                    "identity_status": "exact",
                    "evidence_status": "sufficient",
                    "observed_at": now.isoformat(),
                    "verified_by": actor_user_id,
                    "verified_at": now.isoformat(),
                }
            )
            recorded = await default_registry.record_observation(
                observation,
                scope=before.get("scope"),
                document_title=before.get("document_title"),
            )
            snapshot = await default_registry.refresh_snapshot_projection(
                additional_document_ids=[canonical_id]
            )
            if not snapshot or canonical_id not in (snapshot.get("document_ids") or {}):
                raise RuntimeError("validity_snapshot_projection_missing")
            validity_receipt = {
                "status": normalized_status,
                "verified_at": now.isoformat(),
                "observation_id": str((recorded.get("observation") or {}).get("id") or ""),
            }
        except Exception as exc:
            logger.warning(
                "Metadata updated but validity projection failed for document %s: %s",
                canonical_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "document_metadata_updated_validity_pending",
                    "message": "Metadata đã lưu nhưng trạng thái hiệu lực chưa đồng bộ. Hãy tải lại và xác nhận hiệu lực lần nữa.",
                },
            ) from exc

    return {
        "document_id": canonical_id,
        "metadata_revision": result.get("metadata_revision"),
        "projection_updated": bool(result.get("projection_updated")),
        "validity": validity_receipt,
        "message": (
            "Đã cập nhật metadata và xác nhận hiệu lực từ nguồn chính thức."
            if validity_receipt
            else "Đã cập nhật metadata. Trạng thái hiệu lực vẫn chưa được xác nhận."
        ),
    }


@router.post("/management/documents/{document_id}/organization-assignment")
async def legal_management_document_organization_assignment(
    document_id: str,
    request: LegalManagementOrganizationAssignmentRequest,
    raw_request: Request,
) -> dict[str, Any]:
    """Update the authoritative assignment, then its rebuildable SQL projection."""

    _require_crawl_admin(raw_request)
    canonical_id = _management_document_id(document_id)
    # Validate existence before writing the cross-store authority. A removed
    # document must not acquire an orphan assignment when SQL returns 404.
    await _management_required_read(
        f"/management/documents/{canonical_id}/organization-assignment",
        message="Không thể xác nhận văn bản trước khi phân công phòng ban.",
        base_url=LEGAL_MANAGEMENT_URL,
        timeout_seconds=5.0,
    )
    current_projection = await _management_unit_projection([canonical_id])
    before = current_projection.get(
        canonical_id,
        {
            "organization_assignment_state": "unassigned",
            "primary_organization_unit_id": None,
            "organization_unit_ids": [],
            "organization_assignment_status": "confirmed",
        },
    )
    before_fingerprint = _organization_assignment_fingerprint(before)
    if (
        request.expected_fingerprint
        and request.expected_fingerprint != before_fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail="Thông tin phòng ban đã thay đổi. Vui lòng tải lại trước khi lưu.",
        )

    primary = str(request.primary_organization_unit_id or "").strip() or None
    unit_ids = list(
        dict.fromkeys(
            str(item).strip()
            for item in [primary, *request.organization_unit_ids]
            if str(item or "").strip()
        )
    )
    if request.assignment_state == "assigned" and not primary:
        raise HTTPException(
            status_code=422,
            detail="Vui lòng chọn phòng ban chủ trì.",
        )
    if request.assignment_state != "assigned" and unit_ids:
        raise HTTPException(
            status_code=422,
            detail="Tài liệu dùng chung hoặc chưa phân công không được gắn phòng ban.",
        )
    active_units = await active_organization_units()
    missing_units = [
        unit_id
        for unit_id in unit_ids
        if (unit := find_unit(active_units, unit_id=unit_id)) is None or not unit.is_active
    ]
    if missing_units:
        raise HTTPException(
            status_code=422,
            detail="Một phòng ban được chọn không tồn tại hoặc đã ngừng hoạt động.",
        )

    await replace_document_unit_assignments(
        document_id=canonical_id,
        primary_organization_unit_id=primary,
        organization_unit_ids=unit_ids,
        assignment_source="admin",
        confirmation_status="projection_pending",
        assignment_state=request.assignment_state,
    )
    projection_payload = {
        "assignment_state": request.assignment_state,
        "primary_organization_unit_id": primary,
        "organization_unit_ids": unit_ids,
        "assignment_source": "admin",
        "confirmation_status": "confirmed",
    }
    try:
        projection_result = await _management_required_query(
            f"/management/documents/{canonical_id}/organization-assignment",
            payload=projection_payload,
            message="Đã lưu phòng ban nhưng chưa thể đồng bộ sang kho tìm kiếm. Vui lòng thử đồng bộ lại.",
            timeout_seconds=3.0,
        )
    except HTTPException:
        await write_audit_log(
            action="legal.document.organization_assignment.sync_pending",
            entity_type="legal_document",
            entity_id=canonical_id,
            actor_user_id=get_request_user_id(raw_request),
            actor_role=get_request_role(raw_request),
            details={
                "reason": request.reason,
                "before": before,
                "requested": projection_payload,
            },
            request=raw_request,
        )
        raise

    await replace_document_unit_assignments(
        document_id=canonical_id,
        primary_organization_unit_id=primary,
        organization_unit_ids=unit_ids,
        assignment_source="admin",
        confirmation_status="confirmed",
        assignment_state=request.assignment_state,
    )
    after = {
        "organization_assignment_state": request.assignment_state,
        "primary_organization_unit_id": primary,
        "organization_unit_ids": unit_ids,
        "organization_assignment_status": "confirmed",
    }
    after_fingerprint = _organization_assignment_fingerprint(after)
    await write_audit_log(
        action="legal.document.organization_assignment.update",
        entity_type="legal_document",
        entity_id=canonical_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={
            "reason": request.reason,
            "before": before,
            "after": after,
            "projection_updated": bool(projection_result.get("projection_updated")),
        },
        request=raw_request,
    )
    return {
        "document_id": canonical_id,
        "assignment": after,
        "fingerprint": after_fingerprint,
        "projection_updated": True,
    }


@router.post("/management/documents/{document_id}/replacement")
async def legal_management_document_replacement(
    document_id: str,
    request: LegalManagementReplacementRequest,
    raw_request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Replace one inventory document through the real chunk/embed pipeline.

    A URL is normalized from an approved official source. A file-only request
    is allowed with explicit Admin confirmation. Deterministic extraction may
    suggest identity metadata, but only fields reviewed and submitted by the
    Admin can override the old document identity.
    """

    _require_crawl_admin(raw_request)
    canonical_id = _management_document_id(document_id)
    source_url = request.source_url.strip()
    uploaded_content = str(request.uploaded_content or "")
    uploaded_hash = (
        hashlib.sha256(uploaded_content.encode("utf-8")).hexdigest()
        if uploaded_content
        else None
    )
    if not source_url and not uploaded_content.strip():
        raise HTTPException(
            status_code=422,
            detail="Cần nhập URL nguồn chính thức hoặc tải lên tệp đã trích xuất nội dung.",
        )

    base = await _management_required_read(
        f"/management/documents/{canonical_id}",
        message="Không thể đọc metadata văn bản cũ để thay thế.",
    )
    old_document = base.get("document") if isinstance(base, dict) else None
    if not isinstance(old_document, dict):
        raise HTTPException(status_code=503, detail="Metadata văn bản cũ không hợp lệ.")

    if source_url:
        preview = await _normalized_crawl_preview(source_url)
        metadata_source = preview
    else:
        # File-only replacement may not safely infer legal identity. Keep the
        # reviewed old identity unless the Admin submits explicit overrides.
        metadata_source = {
            "title": old_document.get("document_title"),
            "law_number": old_document.get("law_number"),
            "document_type": old_document.get("document_type"),
            "issuing_agency": old_document.get("issuing_agency"),
            "issued_date": old_document.get("issued_date"),
            "effective_date": old_document.get("effective_date"),
            "expired_date": old_document.get("expired_date"),
            "primary_domain": old_document.get("domain"),
            "source": "admin_confirmed_file",
        }

    submitted_metadata = request.model_dump(mode="json")

    def replacement_value(key: str, old_key: str | None = None) -> Any:
        if key in request.model_fields_set:
            reviewed_value = submitted_metadata.get(key)
            if reviewed_value not in (None, ""):
                return reviewed_value
        value = metadata_source.get(key)
        if value not in (None, ""):
            return value
        if source_url:
            # A new official URL may identify another instrument. Never
            # borrow the old identity or expiration to complete its metadata.
            return None
        return old_document.get(old_key or key)

    required = {
        "title": replacement_value("title", "document_title"),
        "law_number": replacement_value("law_number"),
        "document_type": replacement_value("document_type"),
        "issuing_agency": replacement_value("issuing_agency"),
        "issued_date": replacement_value("issued_date"),
        "effective_date": replacement_value("effective_date"),
    }
    content = uploaded_content if len(uploaded_content.strip()) >= 100 else str(
        metadata_source.get("clean_markdown") or metadata_source.get("content") or ""
    )
    missing = [key for key, value in required.items() if not str(value or "").strip()]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Văn bản thay thế thiếu metadata đã xác minh: " + ", ".join(missing),
        )
    if len(content.strip()) < 20:
        raise HTTPException(
            status_code=422,
            detail="Không có đủ nội dung để tách đoạn và embedding; hãy dùng URL chính thức hoặc tệp có thể đọc được.",
        )

    domain = canonicalize_legal_domain(
        metadata_source.get("primary_domain") or old_document.get("domain")
    )
    field_id = old_document.get("field_id")
    try:
        field_id = int(field_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Văn bản cũ thiếu lĩnh vực đã xác minh; không thể thay thế an toàn.",
        )
    old_scope = str(
        replacement_value("scope") or old_document.get("scope") or "central"
    ).strip()
    source_content_hash = str(metadata_source.get("content_hash") or "").strip() or None
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "old_document_id": canonical_id,
                "source_url": source_url.rstrip("/"),
                "source_content_hash": source_content_hash,
                "uploaded_content_sha256": uploaded_hash,
                "reviewed_metadata": {
                    key: submitted_metadata.get(key)
                    for key in (
                        "title", "law_number", "document_type", "issuing_agency",
                        "issued_date", "effective_date", "expired_date", "scope",
                        "sector", "applicability_info",
                    )
                    if key in request.model_fields_set
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    retry_workflow = None
    if not (idempotency_key or "").strip():
        retry_workflow = find_retryable_replacement_workflow(
            old_document_id=canonical_id,
            source_url=source_url,
            source_content_hash=source_content_hash,
            uploaded_content_sha256=uploaded_hash,
        )
        if retry_workflow and retry_workflow.get("request_fingerprint"):
            request_fingerprint = str(retry_workflow["request_fingerprint"])
    idem = (
        (idempotency_key or "").strip()
        or str((retry_workflow or {}).get("idempotency_key") or "").strip()
        or f"management-replacement-{canonical_id}-{request_fingerprint}"
    )
    existing = retry_workflow or get_replacement_workflow_by_idempotency_key(idem)
    replay = _replacement_replay(existing, request_fingerprint)
    if replay:
        return replay

    # Assignment validation is a preflight: a rejected request must not leave a
    # processing workflow behind, nor appear as a requested replacement.
    inherited_unit_fields = await _document_unit_import_fields(canonical_id)
    assignment_snapshot = dict(inherited_unit_fields)
    settings = await active_settings()
    if getattr(settings, "organization_routing_mode", "legacy") in {"hybrid", "unit_primary"} and (
        inherited_unit_fields.get("organization_assignment_state") not in {"assigned", "shared"}
        or inherited_unit_fields.get("organization_assignment_status") != "confirmed"
    ):
        raise HTTPException(status_code=409, detail="Cần xác nhận và đồng bộ phân công phòng ban bản cũ trước khi thay thế.")

    workflow = create_replacement_workflow(
        old_document_id=f"legal_document:{canonical_id}",
        source_url=source_url,
        preview={
            **metadata_source,
            "content": "",
            "clean_markdown": "",
            "uploaded_content_sha256": uploaded_hash,
            "uploaded_filename": request.uploaded_filename,
            "replacement_entrypoint": "legal_management_inventory",
        },
        reason=request.reason.strip(),
        requested_by=get_request_user_id(raw_request) or "admin",
        idempotency_key=idem,
        request_fingerprint=request_fingerprint,
    )
    if workflow.get("idempotent_replay"):
        replay = _replacement_replay(workflow, request_fingerprint)
        if replay:
            return replay
    workflow_id = str(workflow.get("workflow_id") or "")
    update_replacement_workflow(
        workflow_id,
        status="processing",
        steps={
            "official_url_identity": "passed" if source_url else "admin_confirmed_file",
            "official_relation": "admin_confirmed",
            "crawl": "passed" if source_url else "not_applicable",
            "normalize": "passed",
            "chunk": "pending_import",
            "activation_journal": "prepared",
        },
    )
    actor_user_id = get_request_user_id(raw_request)
    await write_audit_log(
        action="admin.legal_management.document_replacement_requested",
        entity_type="legal_document",
        entity_id=canonical_id,
        actor_user_id=actor_user_id,
        actor_role=get_request_role(raw_request),
        details={
            "source_url": source_url,
            "uploaded_filename": request.uploaded_filename,
            "uploaded_content_sha256": uploaded_hash,
            "reason": request.reason.strip(),
            "workflow_id": workflow_id,
            "organization_assignment_before": assignment_snapshot,
        },
        request=raw_request,
    )

    async def record_replacement_failure(exc: Exception) -> None:
        detail = exc.detail if isinstance(exc, HTTPException) else None
        safe_detail = detail if isinstance(detail, dict) else {}
        recoverable_activation = (
            isinstance(exc, HTTPException)
            and exc.status_code == 502
            and safe_detail.get("code") == "replacement_activation_failed"
        )
        try:
            update_replacement_workflow(
                workflow_id,
                status=(
                    "pending_retry"
                    if (
                        isinstance(exc, HTTPException)
                        and (exc.status_code == 503 or recoverable_activation)
                    )
                    else "failed"
                ),
                error=(
                    str(detail)[:1000]
                    if isinstance(exc, HTTPException)
                    else exc.__class__.__name__
                ),
            )
        except Exception:
            pass
        try:
            await write_audit_log(
                action="admin.legal_management.document_replacement_failed",
                entity_type="legal_document",
                entity_id=canonical_id,
                actor_user_id=actor_user_id,
                actor_role=get_request_role(raw_request),
                details={
                    "workflow_id": workflow_id,
                    "http_status": (
                        exc.status_code if isinstance(exc, HTTPException) else 502
                    ),
                    "error_code": safe_detail.get("code"),
                    "failure_stage": safe_detail.get("stage"),
                    "reason": request.reason.strip(),
                },
                request=raw_request,
            )
        except Exception:
            # Failure audit is best-effort here because the original failure
            # and workflow state must remain visible to the caller.
            pass

    inherited_confirmation_status = str(
        inherited_unit_fields.pop("organization_assignment_status", "confirmed")
    ).strip()
    inherited_confirmation_status = (
        "confirmed"
        if inherited_confirmation_status == "confirmed"
        else "needs_confirmation"
    )
    try:
        document_payload = {
                **required,
                "scope": old_scope,
                "sector": str(
                    replacement_value("sector") or old_document.get("sector") or ""
                ),
                "field_id": field_id,
                "expired_date": replacement_value("expired_date"),
                "source_url": source_url,
                "uploaded_pdf_sha256": uploaded_hash,
                "applicability_info": replacement_value("applicability_info") or (
                    f"Thay thế bản ghi {old_document.get('law_number') or canonical_id}. "
                    + (f"Toàn văn được trích xuất từ {request.uploaded_filename}." if request.uploaded_filename else "")
                ).strip(),
                "confirmed_official_source": True,
                "domain_slug": domain,
                **inherited_unit_fields,
                "replacement_of_document_id": int(canonical_id),
                # A replacement supersedes the old instrument for current
                # answers but must preserve it for explicit historical lookup.
                "replacement_action": "historical",
                "structure": "auto",
                "content": content,
        }
        base_revision = await _management_required_read(
            f"/lifecycle/documents/{canonical_id}/revision",
            message="Không thể khóa phiên bản văn bản cũ trước khi thay thế.",
            timeout_seconds=15.0,
        )
        import_result = await _request(
            "POST",
            "/lifecycle/activate",
            base_url=LEGAL_MANAGEMENT_URL,
            json={
                "version_key": request_fingerprint,
                "base_fingerprint": base_revision.get("fingerprint"),
                "document": document_payload,
            },
        )
        retrieval_smoke = _verified_replacement_result(import_result)
        unit_assignment_sync: dict[str, Any]
        try:
            inherited = await copy_document_unit_assignments(
                old_document_id=canonical_id,
                new_document_id=import_result.get("document_id"),
                confirmation_status=inherited_confirmation_status,
            )
            await _management_required_query(
                f"/management/documents/{import_result.get('document_id')}/organization-assignment",
                payload={
                    "assignment_state": inherited_unit_fields.get(
                        "organization_assignment_state", "unassigned"
                    ),
                    "primary_organization_unit_id": inherited_unit_fields.get(
                        "primary_organization_unit_id"
                    ),
                    "organization_unit_ids": inherited_unit_fields.get(
                        "organization_unit_ids", []
                    ),
                    "assignment_source": "inherited_replacement",
                    "confirmation_status": inherited_confirmation_status,
                },
                message="Văn bản mới đã được tạo nhưng phân công phòng ban đang chờ đồng bộ.",
                timeout_seconds=3.0,
            )
            unit_assignment_sync = {
                "status": inherited_confirmation_status,
                "count": len(inherited),
            }
        except Exception as exc:
            # SQL document/chunks/assignment activate together, but the
            # independent authority projection can still fail. Never report
            # that distributed partial success as a completed replacement.
            compensation = await LegalCrawlService._exclude_failed_activation(
                import_result.get("document_id"),
                reason="Tạm ngừng bản thay thế do phân công phòng ban chưa đồng bộ; cần đối soát trước khi phục hồi.",
            )
            raise HTTPException(status_code=503, detail={
                "code": "replacement_assignment_sync_failed",
                "stage": "organization_assignment",
                "message": "Bản thay thế chưa đồng bộ phòng ban. Hãy kiểm tra workflow và hai văn bản trước khi phục hồi hoặc thử lại.",
                "document_id": import_result.get("document_id"),
                "old_document_id": canonical_id,
                "compensation": compensation,
            }) from exc
        result = {
            **import_result,
            "workflow_id": workflow_id,
            "retrieval_smoke": retrieval_smoke,
            "chatbot_ready": bool(retrieval_smoke.get("passed")),
            "unit_assignment_sync": unit_assignment_sync,
            "message": "Đã crawl/trích xuất, chunk, embedding và đưa văn bản thay thế vào tra cứu hiện hành; bản cũ chỉ còn trong tra cứu lịch sử và audit.",
        }
    except HTTPException as exc:
        await record_replacement_failure(exc)
        raise
    except Exception as exc:
        await record_replacement_failure(exc)
        raise HTTPException(
            status_code=502,
            detail="Không xác nhận được toàn bộ kết quả thay thế; kiểm tra workflow và trạng thái hai văn bản trước khi thử lại.",
        ) from exc

    audit_sync: dict[str, Any] = {"status": "recorded"}
    try:
        await write_audit_log(
            action="admin.legal_management.document_replacement_activated",
            entity_type="legal_document",
            entity_id=canonical_id,
            actor_user_id=actor_user_id,
            actor_role=get_request_role(raw_request),
            details={
                "workflow_id": workflow_id,
                "new_document_id": import_result.get("document_id"),
                "chunk_count": import_result.get("chunk_count"),
                "retrieval_smoke": retrieval_smoke,
                "source_url": source_url,
                "organization_assignment_before": assignment_snapshot,
                "organization_assignment_after": {**assignment_snapshot, "document_id": import_result.get("document_id")},
                "unit_assignment_sync": unit_assignment_sync,
            },
            request=raw_request,
        )
    except Exception as exc:
        audit_sync = {"status": "pending", "error_class": type(exc).__name__}

    workflow_sync: dict[str, Any] = {"status": "recorded"}
    try:
        update_replacement_workflow(
            workflow_id,
            status="activated",
            steps={
                "chunk": "passed",
                "embedding": "passed",
                "retrieval_smoke": "passed",
                "publish": "replacement_active",
            },
            hard_gates={
                "official_identity_verified": bool(source_url),
                "admin_confirmed_file_identity": not bool(source_url),
                "replacement_relation_recorded": True,
                "import_active": True,
                "chunks_indexed": True,
                "vector_collection_available": True,
                "exact_retrieval_smoke": bool(retrieval_smoke.get("exact_match")),
                "semantic_retrieval_smoke": bool(
                    retrieval_smoke.get("semantic_match")
                ),
            },
            result={
                **result,
                "audit_sync": audit_sync,
                "workflow_sync": {"status": "recorded"},
            },
        )
    except Exception as exc:
        workflow_sync = {
            "status": "pending",
            "error_class": type(exc).__name__,
        }
    result["audit_sync"] = audit_sync
    result["workflow_sync"] = workflow_sync
    if audit_sync["status"] != "recorded" or workflow_sync["status"] != "recorded":
        result["message"] = (
            "Văn bản thay thế đã được kích hoạt và bản cũ đã ngừng phục vụ; "
            "một biên nhận audit/workflow đang chờ đồng bộ. Hãy tải lại chi tiết trước khi thao tác tiếp."
        )
    return result


@router.post("/management/documents/{document_id}/search-state")
async def legal_management_document_search_state(
    document_id: str,
    request: LegalManagementSearchStateRequest,
    raw_request: Request,
) -> dict[str, Any]:
    """Exclude or restore one document from chatbot retrieval with audit."""

    _require_crawl_admin(raw_request)
    canonical_id = _management_document_id(document_id)
    actor_user_id = get_request_user_id(raw_request)
    assignment_snapshot = await _document_unit_import_fields(canonical_id)
    await write_audit_log(
        action=f"admin.legal_management.document_search_{request.action}",
        entity_type="legal_document",
        entity_id=canonical_id,
        actor_user_id=actor_user_id,
        actor_role=get_request_role(raw_request),
        details={"action": request.action, "reason": request.reason.strip(), "organization_assignment": assignment_snapshot},
        request=raw_request,
    )
    result = await _request(
        "POST",
        f"/documents/{canonical_id}/search-state",
        base_url=LEGAL_MANAGEMENT_URL,
        json={
            "action": request.action,
            "requested_by": actor_user_id or "admin",
            "reason": request.reason.strip(),
            "expected_revision": request.expected_revision,
            "verify_vectors": True,
        },
    )
    try:
        verification = (
            result.get("operation_verification")
            if isinstance(result.get("operation_verification"), dict)
            else {}
        )
        await write_audit_log(
            action=f"admin.legal_management.document_search_{request.action}_applied",
            entity_type="legal_document",
            entity_id=canonical_id,
            actor_user_id=actor_user_id,
            actor_role=get_request_role(raw_request),
            details={
                "action": request.action,
                "reason": request.reason.strip(),
                "state_revision": result.get("state_revision"),
                "state_authority_verified": verification.get(
                    "state_authority_verified"
                ),
                "vector_ready": verification.get("vector_ready"),
                "verification_status": verification.get("status"),
                "organization_assignment": assignment_snapshot,
            },
            request=raw_request,
        )
    except Exception as exc:
        # The authoritative state transition has already committed. Returning
        # an HTTP error here makes the UI claim failure and invites a repeated
        # click with a stale revision. Keep the successful state receipt and
        # expose the secondary audit as pending instead.
        result["audit_sync"] = {
            "status": "pending",
            "error_class": type(exc).__name__,
        }
        result["message"] = (
            str(result.get("message") or "Đã cập nhật trạng thái tra cứu.")
            + " Biên nhận nhật ký hoàn tất đang chờ đồng bộ; không cần thao tác lại."
        )
    else:
        result["audit_sync"] = {"status": "recorded"}
    return result


@router.get("/management/documents/{document_id}/hard-delete/preview")
async def legal_management_document_hard_delete_preview(
    document_id: str,
    raw_request: Request,
) -> dict[str, Any]:
    """Return a content-free, fail-closed permanent-deletion preview."""

    _require_crawl_admin(raw_request)
    canonical_id = _management_document_id(document_id)
    return await _request(
        "GET",
        f"/documents/{canonical_id}/hard-delete/preview",
        base_url=LEGAL_MANAGEMENT_URL,
    )


@router.post("/management/documents/{document_id}/hard-delete")
async def legal_management_document_hard_delete(
    document_id: str,
    request: LegalManagementHardDeleteRequest,
    raw_request: Request,
) -> dict[str, Any]:
    """Delete one incremental document, preserve audit, and tombstone imports."""

    _require_crawl_admin(raw_request)
    canonical_id = _management_document_id(document_id)
    actor_user_id = get_request_user_id(raw_request)
    actor_role = get_request_role(raw_request)
    assignment_snapshot = await _document_unit_import_fields(canonical_id)
    await write_audit_log(
        action="admin.legal_management.document_hard_delete_requested",
        entity_type="legal_document",
        entity_id=canonical_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        details={"reason": request.reason.strip(), "organization_assignment_before": assignment_snapshot},
        request=raw_request,
    )
    result = await _request(
        "POST",
        f"/documents/{canonical_id}/hard-delete",
        base_url=LEGAL_MANAGEMENT_URL,
        json={
            "requested_by": actor_user_id or "admin",
            "reason": request.reason.strip(),
            "expected_revision": request.expected_revision,
            "confirmation_text": request.confirmation_text.strip(),
        },
    )

    try:
        await delete_document_unit_assignments(canonical_id)
        unit_assignment_sync: dict[str, Any] = {"status": "deleted"}
    except Exception as exc:
        unit_assignment_sync = {
            "status": "pending",
            "error_class": type(exc).__name__,
        }

    candidate_sync: dict[str, Any] = {"status": "not_found", "count": 0}
    try:
        candidates = await repo_query(
            "SELECT * FROM legal_crawl_candidate "
            "WHERE imported_document.document_id = $document_id OR document_id = $document_id;",
            {"document_id": int(canonical_id)},
        )
        for candidate in candidates:
            imported = dict(candidate.get("imported_document") or {})
            imported.update(
                {
                    "document_id": None,
                    "activation_status": "deleted",
                    "status": "deleted",
                    "deleted_document_id": int(canonical_id),
                    "deleted_at": datetime.now(timezone.utc),
                }
            )
            await repo_update(
                "legal_crawl_candidate",
                candidate["id"],
                {
                    "status": "deleted",
                    "review_status": "deleted",
                    "import_status": "deleted",
                    "pipeline_stage": "deleted",
                    "chatbot_ready": False,
                    "document_id": None,
                    "imported_document": imported,
                    "review_note": "Văn bản đã được Admin xóa vĩnh viễn; giữ biên nhận nguồn và audit.",
                },
            )
        candidate_sync = {"status": "recorded", "count": len(candidates)}
    except Exception as exc:
        candidate_sync = {"status": "pending", "error_class": type(exc).__name__}

    try:
        await write_audit_log(
            action="admin.legal_management.document_hard_delete_applied",
            entity_type="legal_document",
            entity_id=canonical_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            details={
                "reason": request.reason.strip(),
                "law_number": result.get("law_number"),
                "deleted_counts": result.get("deleted_counts"),
                "vectors_deleted": result.get("vectors_deleted"),
                "candidate_sync": candidate_sync,
                "unit_assignment_sync": unit_assignment_sync,
                "organization_assignment_before": assignment_snapshot,
                "organization_assignment_after": None,
            },
            request=raw_request,
        )
    except Exception as exc:
        return {
            **result,
            "unit_assignment_sync": unit_assignment_sync,
            "audit_sync": {"status": "pending", "error_class": type(exc).__name__},
            "candidate_sync": candidate_sync,
            "message": "Dữ liệu đã được xóa nhưng biên nhận audit đang chờ đồng bộ.",
        }
    return {
        **result,
        "audit_sync": {"status": "recorded"},
        "candidate_sync": candidate_sync,
        "unit_assignment_sync": unit_assignment_sync,
        "message": "Đã xóa văn bản nhập bổ sung, chunk và vector; audit vẫn được giữ.",
    }


@router.get("/docs/{doc_id}")
async def get_legal_document(
    doc_id: str,
    response: Response,
    raw_request: Request,
    article: str | None = Query(default=None),
    include_content: bool = Query(default=False),
    law_number: str | None = Query(default=None, max_length=160),
) -> dict[str, Any]:
    """Return local legal document metadata + content/chunks.

    Used by Ask citations so users can open the in-system source even when the
    original VBPL source_url is broken.
    """
    if doc_id.startswith("dvc:"):
        from api.legal_official_procedure_evidence import official_procedure_document
        publication = official_procedure_document(doc_id[4:])
        if not publication:
            raise HTTPException(404, "Chưa có bản công bố thủ tục đã xác minh trong kho.")
        response.headers["Cache-Control"] = "no-store"
        return publication
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
    real_doc_id = doc_id
    if law_number:
        resolved = await _request(
            "GET", "/documents/lookup", base_url=LEGAL_MANAGEMENT_URL,
            params={"law_number": law_number, "audience": _serving_audience(raw_request)},
        )
        matched = resolved.get("document") or {}
        if not matched.get("id"):
            raise HTTPException(404, "Không tìm thấy văn bản theo số hiệu trong kho nội bộ.")
        real_doc_id = str(matched["id"])
    elif not doc_id.isdigit():
        resolved = await _request(
            "GET", "/documents/lookup", base_url=LEGAL_MANAGEMENT_URL,
            params={"law_number": doc_id.removeprefix("legal:"), "audience": _serving_audience(raw_request)},
        )
        matched = resolved.get("document") or {}
        if not matched.get("id"):
            raise HTTPException(404, "Không tìm thấy số hiệu trong kho đang phục vụ.")
        real_doc_id = str(matched["id"])
    try:
        payload = await _request(
            "GET",
            f"/documents/{real_doc_id}",
            base_url=LEGAL_MANAGEMENT_URL,
            params=params,
        )
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
        candidates = await asyncio.wait_for(repo_query(
            "SELECT source_asset FROM legal_crawl_candidate WHERE imported_document.document_id = $document_id AND status = 'imported' LIMIT 1;",
            {"document_id": lookup_document_id},
        ), timeout=1.5)
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
        as_of=vietnam_legal_date(),
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
        doc_id, Response(), raw_request, article=article, include_content=False, law_number=None
    )
    # Aliases resolve in the detail gate; export the verified physical row.
    doc_id = str(doc.get("doc_id") or doc.get("id") or doc_id)

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
                f"{LEGAL_MANAGEMENT_URL}/documents/{doc_id}/download.pdf",
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
    raw_request: Request,
    file: UploadFile = File(...),
    extractor: str = "auto",
) -> dict[str, Any]:
    filename = file.filename or "uploaded"
    lower_name = filename.lower()
    content = await file.read(DOCUMENT_UPLOAD_POLICY.max_bytes + 1)
    try:
        validated = validate_upload(filename=filename, content=content, policy=DOCUMENT_UPLOAD_POLICY)
    except UploadSecurityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    extractor = extractor.strip().lower()
    metadata: dict[str, Any] = {"extractor_used": "basic"}
    if extractor not in {"auto", "basic", "rag_anything"}:
        raise HTTPException(
            status_code=400,
            detail="Extractor phai la auto, basic hoac rag_anything.",
        )

    text_content = ""
    handled = False
    from api.local_image_ocr import IMAGE_EXTENSIONS
    if Path(filename).suffix.lower() in IMAGE_EXTENSIONS:
        text_content, image_metadata = await asyncio.to_thread(
            _extract_upload_text,
            filename,
            content,
            defer_ocr=True,
        )
        metadata.update({key: value for key, value in image_metadata.items() if key != 'text'})
        handled = True
    # RAG-Anything is opt-in for a deliberately complex document. The default
    # path must remain native-first and must never start a heavy parser inside
    # an ordinary upload request.
    if extractor == "rag_anything" and lower_name.endswith((".pdf", ".docx")):
        try:
            text_content, extra_metadata = extract_with_rag_anything(filename, content)
            metadata.update(extra_metadata)
            handled = bool(text_content)
        except RagAnythingUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not handled and not text_content:
        if lower_name.endswith((".txt", ".md", ".json")):
            text_content = content.decode("utf-8", errors="replace")
        elif lower_name.endswith(".docx"):
            text_content = _extract_docx(content)
        elif lower_name.endswith(".pdf"):
            text_content, pdf_result = await asyncio.to_thread(
                _extract_upload_text,
                filename,
                content,
                defer_ocr=True,
            )
            metadata.update(
                {key: value for key, value in pdf_result.items() if key != "text"}
            )
        elif lower_name.endswith((".doc", ".xls", ".xlsx")):
            from api.multimodal_preprocess import extract_artifact_from_file

            artifact_result, _ = await extract_artifact_from_file(
                content,
                filename,
                validated.detected_type,
            )
            text_content = str(artifact_result.get("text") or "")
            metadata.update({
                key: value for key, value in artifact_result.items() if key != "text"
            })
            metadata["extractor_used"] = artifact_result.get("extractor")
        else:
            raise HTTPException(
                status_code=400,
                detail="Chỉ hỗ trợ TXT, MD, DOC, DOCX, PDF, XLS, XLSX hoặc ảnh.",
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

    # Every accepted upload is a canonical owner-scoped DocumentAsset, even
    # when its SHA already has a valid ExtractionArtifact. Keeping ``file_id``
    # on cache hits lets the review UI render the original PDF instead of
    # falling back to an unreliable browser blob/plugin surface.
    from api.chat_upload_store import store_upload, update_upload_metadata
    from api.document_contracts import DocumentAsset

    owner_id = str(
        get_request_user_id(raw_request)
        or f"local:{get_request_role(raw_request) or 'admin'}"
    )
    asset = DocumentAsset.from_bytes(
        content,
        filename=filename,
        mime_type=validated.detected_type,
        owner_id=owner_id,
    )
    file_id = await asyncio.to_thread(
        store_upload,
        owner_id,
        filename,
        validated.detected_type,
        content,
        asset=asset,
    )
    metadata["file_id"] = file_id

    # A fast native preview is useful only when the same action also starts the
    # missing OCR work. Reuse the extraction worker used by chat/notebook, then
    # let the UI poll this job. The SHA-bound store prevents duplicate OCR.
    if metadata.get("deferred_ocr"):
        from api.chat_extraction_worker import submit_job
        from api.multimodal_preprocess import classify_upload

        job = submit_job(
            owner_id,
            file_id=file_id,
            sha256=asset.sha256,
            source_type=classify_upload(filename, validated.detected_type),
        )
        job_id = str(job.get("job_id") or "")
        await asyncio.to_thread(
            update_upload_metadata,
            owner_id,
            file_id,
            {
                "extraction_job_id": job_id,
                "extraction_status": str(job.get("status") or "processing"),
            },
        )
        metadata.update(
            {
                "extraction_job_id": job_id,
                "extraction_status": str(job.get("status") or "processing"),
                "reason": "Đã đọc lớp chữ; hệ thống đang OCR các trang ảnh trong nền.",
            }
        )

    return {
        "filename": filename,
        "characters": len(text_content),
        "content": text_content,
        **metadata,
        "file_fingerprint": hashlib.sha256(content).hexdigest(),
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


async def _normalized_crawl_preview(url: str) -> dict[str, Any]:
    """Fetch one official page through the deterministic normalized contract."""

    try:
        trusted_url = validate_official_public_url(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        normalized = await fetch_normalized_legal_document(
            trusted_url, scope="central", timeout_seconds=30,
        )
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=422, detail={
            "code": "legal_source_content_unavailable",
            "message": "Không kết nối được trang nguồn. Hãy tải tệp gốc hoặc dán nội dung; liên kết vẫn được giữ để đối chiếu.",
        }) from exc
    if normalized.get("status") == "rejected" or not normalized.get(
        "clean_markdown"
    ):
        raise HTTPException(
            status_code=422,
            detail={"code": "legal_source_content_unavailable", "message":
                "Trang nguồn chưa cho lấy toàn văn hoặc chỉ trả trang tải dữ liệu. "
                "Bạn có thể tải tệp gốc hoặc dán nội dung và giữ liên kết này để người duyệt đối chiếu."},
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
    return await _request(
        "POST",
        "/import/preview?for_review=true",
        base_url=LEGAL_MANAGEMENT_URL,
        json=request.model_dump(),
    )


@router.post("/import")
async def legal_import_document(request: LegalImportRequest) -> dict[str, Any]:
    # A client-provided fingerprint alone is not evidence of a stored original.
    return await _queue_manual_import(request.model_copy(update={'uploaded_pdf_sha256': None}))


@router.post("/import/with-file")
async def legal_import_document_with_file(raw_request: Request, metadata_json: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    try:
        request = LegalImportRequest.model_validate_json(metadata_json)
    except ValueError as exc:
        raise HTTPException(422, 'Thông tin văn bản chưa hợp lệ.') from exc
    content = await file.read(DOCUMENT_UPLOAD_POLICY.max_bytes + 1)
    try:
        uploaded = await _save_proposal_upload(file, content)
    except UploadSecurityError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    _, extraction = await _extract_legal_upload_for_review(
        file.filename or "uploaded",
        content,
        str(uploaded.get("content_type") or file.content_type or ""),
        defer_ocr=True,
    )
    result = await _queue_manual_import(
        request.model_copy(update={'uploaded_pdf_sha256': uploaded['sha256']}),
        uploaded_file=uploaded,
        extraction_result=extraction,
    )
    needs_background_extraction = bool(
        extraction.get("deferred_ocr")
        or extraction.get("ocr_status") in {"queued", "pending"}
    )
    can_queue = not result.get("deduplicated") or bool(result.get("enriched"))
    if needs_background_extraction and can_queue and result.get("candidate_id"):
        try:
            job = await LegalCrawlService.enqueue_processing_job(
                str(result["candidate_id"])
            )
            result.update({
                "processing_status": str(job.get("status") or "queued"),
                "processing_job_id": str(job.get("id") or ""),
                "message": (
                    "Văn bản đã vào danh sách chờ duyệt; các trang ảnh đang được OCR nền."
                ),
            })
        except (RuntimeError, ValueError) as exc:
            result.update({
                "processing_status": "queue_failed",
                "processing_error": str(exc)[:500],
            })
    return result


async def _queue_manual_import(request: LegalImportRequest, *, uploaded_file: dict[str, Any] | None = None, extraction_result: dict[str, Any] | None = None) -> dict[str, Any]:
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
    # Department and field are one managed relation.  The UI uses the same
    # settings projection, but enforce it here as well so old clients cannot
    # submit a stale or cross-department combination.
    settings = await active_settings()
    # ``ContentSettings`` is persisted through a flexible JSON field.  Older
    # records therefore expose ``legal_domains`` as plain dictionaries while
    # newer callers may already have ``LegalDomainConfig`` instances.  Always
    # normalize at the boundary; accessing ``.code`` directly made the manual
    # import endpoint fail with HTTP 500 for otherwise valid payloads.
    configured_domains = {
        domain.code.strip()
        for domain in normalize_legal_domains(
            getattr(settings, "legal_domains", None)
        )
        if domain.is_active
    }
    units = await active_organization_units(settings)
    try:
        assignment_state, primary_unit_id, assigned_unit_ids = (
            _normalized_import_assignment(
                assignment_state=request.organization_assignment_state,
                primary_organization_unit_id=request.primary_organization_unit_id,
                organization_unit_ids=request.organization_unit_ids,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if primary_unit_id:
        unit = next((item for item in units if item.id == primary_unit_id and item.is_active), None)
        if unit is None:
            raise HTTPException(status_code=422, detail='Phòng ban không tồn tại hoặc đã ngừng hoạt động.')
        requested_domain = str(request.domain_slug or '').strip()
        if requested_domain and requested_domain not in set(domains_for_unit(unit)):
            raise HTTPException(status_code=422, detail='Lĩnh vực không thuộc phòng ban tiếp nhận.')
    active_unit_ids = {item.id for item in units if item.is_active}
    if any(unit_id not in active_unit_ids for unit_id in assigned_unit_ids):
        raise HTTPException(
            status_code=422,
            detail='Danh sách phân công chứa phòng ban không tồn tại hoặc đã ngừng hoạt động.',
        )
    try:
        primary_domain = _resolved_admin_import_domain(
            field_id=request.field_id,
            requested_domain=request.domain_slug,
            classified_domains=matched_domains,
            allowed_domains=configured_domains or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if primary_domain and primary_domain not in matched_domains:
        matched_domains = [primary_domain, *matched_domains]
        domain_evidence = {
            primary_domain: ["admin_reviewed_field_domain"],
            **domain_evidence,
        }
    external_id = candidate_external_id(request.model_dump(), origin="admin_manual_import")

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
        "assignment_state": assignment_state,
        "primary_organization_unit_id": primary_unit_id,
        "proposed_organization_unit_ids": assigned_unit_ids,
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
            "uploaded_pdf_sha256": request.uploaded_pdf_sha256,
            "metadata_only": not bool(request.content.strip()),
            "candidate_origin": "admin_manual_import",
            "domain": primary_domain,
            "matched_domains": matched_domains,
            "domain_evidence": domain_evidence,
            "domain_codes": request.domain_codes or matched_domains,
            "primary_organization_unit_id": primary_unit_id,
            "organization_unit_ids": assigned_unit_ids,
            "organization_assignment_state": assignment_state,
        },
        "content": request.content,
        "uploaded_file": uploaded_file,
        "extraction_result": extraction_result or _text_review_metadata(request.content, filename="Nội dung nhập trực tiếp", extractor_used="manual"),
    }

    candidate_data["raw_metadata"]["legal_identity"] = identity_metadata(candidate_data)
    related_rows = await candidate_duplicate_rows([candidate_data], query=repo_query)
    related_rows = [row for row in related_rows if compare_legal_documents(candidate_data, row)["kind"] != "unrelated"]
    existing_rows = []
    for row in related_rows:
        comparison = compare_legal_documents(candidate_data, row)
        same_observation = row.get("external_id") == external_id or comparison["kind"] == "duplicate_content"
        metadata_enrichment = comparison["kind"] == "identity_match" and (
            "source_url" in comparison["matched_fields"]
            or all(key in comparison["matched_fields"] for key in ("number", "agency", "issued_date"))
        ) and not str(row.get("content") or "").strip()
        if same_observation or metadata_enrichment:
            existing_rows.append(row)
    if existing_rows:
        existing = min(existing_rows, key=lambda row: (str(row.get("created") or ""), str(row.get("id"))))
        existing_status = str(existing.get("status") or "").strip().lower()
        existing_metadata = existing.get("raw_metadata") or {}
        existing_content = str(existing.get("content") or "").strip()
        incoming_content = request.content.strip()
        should_enrich_pending = (
            existing_status in {"pending", "changes_requested"}
            and bool(incoming_content)
            and (
                bool(existing_metadata.get("metadata_only")) or not existing_content
            )
        )
        if should_enrich_pending:
            enriched = await update_candidate_if_current(
                existing,
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
            reopened = await update_candidate_if_current(
                existing,
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
            "status": existing_status,
        }

    candidate_data["duplicate_candidates"] = [match for row in related_rows if (match := public_duplicate_match(candidate_data, row, "candidate"))]
    if candidate_data["duplicate_candidates"]:
        candidate_data["comparison_status"] = candidate_data["duplicate_candidates"][0]["kind"]
    try:
        res = await repo_create("legal_crawl_candidate", candidate_data)
    except RuntimeError:
        existing = await repo_query("SELECT id, status FROM legal_crawl_candidate WHERE external_id = $external_id LIMIT 1;", {"external_id": external_id})
        if existing:
            return {"candidate_id": str(existing[0]["id"]), "deduplicated": True, "enriched": False,
                    "status": existing[0].get("status"), "message": "Candidate đã được tạo bởi lượt gửi trước; không nhập hai lần."}
        raise
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
    from api.organization_service import current_officer_scope

    operating_scope = await current_officer_scope(user_id, profile or {})
    allowed_domains = list(operating_scope.domains)
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

    responsible_unit_id = None
    if operating_scope.mode in {"hybrid", "unit_primary"}:
        requested_unit = str(proposal_metadata.get("primary_organization_unit_id") or "").strip()
        eligible = operating_scope.proposal_units(domain)
        preferred = operating_scope.primary_organization_unit_id
        responsible_unit_id = requested_unit or (preferred if preferred in eligible else eligible[0] if len(eligible) == 1 else None)
        if not responsible_unit_id or responsible_unit_id not in eligible:
            raise HTTPException(status_code=403, detail="Chọn phòng ban chịu trách nhiệm thuộc phạm vi được phân công hoặc quyền hỗ trợ còn hạn.")

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
    duplicates = await _duplicate_candidates(law_number.strip() or None, source_url.strip() or None, content_hash, {
        "law_number": law_number.strip(), "source_url": source_url.strip(),
        "issuing_agency": issuing_agency.strip(), "document_type": document_type.strip(),
        "issued_date": issued_date.strip(), "content": extracted_text,
    })
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
        "submitter_organization_snapshot": {
            "organization_unit_id": (profile or {}).get("organization_unit_id"),
            "department": (profile or {}).get("department"),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
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
    raw_metadata["legal_identity"] = identity_metadata(candidate_data)
    if responsible_unit_id:
        candidate_data.update({
            "assignment_state": "assigned",
            "primary_organization_unit_id": responsible_unit_id,
            "proposed_organization_unit_ids": [responsible_unit_id],
            "organization_assignment_confidence": 1.0,
        })
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
            "submitter_organization_snapshot": raw_metadata["submitter_organization_snapshot"],
            "primary_organization_unit_id": responsible_unit_id,
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


@router.get("/validity/events", response_model=LegalValidityEventPageResponse)
async def legal_validity_events(
    raw_request: Request,
    review_status: Literal[
        "open",
        "confirmed",
        "rejected_match",
        "recheck_requested",
        "historical_release_pending",
        "quarantine_release_pending",
    ]
    | None = None,
    severity: Literal["low", "medium", "high", "critical"] | None = None,
    scope: Literal["central", "haiphong", "local"] | None = None,
    expired_within_days: int | None = Query(default=None, ge=1, le=365),
    current_snapshot_only: bool = False,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=80),
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    try:
        document_ids: list[str] | None = None
        if current_snapshot_only:
            snapshot = default_snapshot_cache.load() or {}
            documents = snapshot.get("documents") or {}
            document_ids = sorted(
                {
                    str(entry.get("document_id") or "").strip()
                    for entry in documents.values()
                    if isinstance(entry, dict)
                    and str(entry.get("normalized_status") or "")
                    in {"expired", "replaced", "repealed", "suspended"}
                    and str(entry.get("document_id") or "").strip()
                }
            )
        return await default_registry.list_events(
            review_status=review_status,
            severity=severity,
            scope=scope,
            expired_within_days=(None if current_snapshot_only else expired_within_days),
            document_ids=document_ids,
            latest_per_document=current_snapshot_only,
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


@router.post("/validity/documents/{document_id}/replacement-workflow")
async def legal_replacement_workflow(
    document_id: str,
    request: LegalReplacementWorkflowRequest,
    raw_request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Activate an Admin-confirmed official replacement in current Q&A retrieval."""

    _require_crawl_admin(raw_request)
    canonical = _validity_record_id(document_id, "legal_document")
    event_canonical = _validity_record_id(request.event_id, "legal_validity_event")
    event = await default_registry.get_event(event_canonical)
    if event is None or str(event.get("document_id") or "") not in {
        document_id,
        canonical,
    }:
        raise HTTPException(status_code=404, detail="Không tìm thấy việc xử lý của văn bản cũ.")
    try:
        serving_document_id = canonical_document_id(event.get("document_id"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Sự kiện chưa được ánh xạ tới mã văn bản trong kho phục vụ.") from exc
    actor_user_id = get_request_user_id(raw_request)
    source_url = request.source_url.strip()
    uploaded_content = str(request.uploaded_content or "")
    content_sha256 = hashlib.sha256(uploaded_content.encode("utf-8")).hexdigest() if uploaded_content else None
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "old_document_id": canonical,
                "event_id": event_canonical,
                "source_url": source_url.rstrip("/"),
                "reason": request.reason.strip(),
                "uploaded_content_sha256": content_sha256,
                "uploaded_filename": request.uploaded_filename or "",
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    idem = (idempotency_key or "").strip() or f"replacement-{canonical}-{request_fingerprint}"

    existing = get_replacement_workflow_by_idempotency_key(idem)
    replay = _replacement_replay(existing, request_fingerprint)
    if replay:
        return replay

    timeline = await default_registry.document_timeline(
        canonical if ":" in str(event.get("document_id") or "") else document_id
    )
    discovery = project_replacement_candidates((timeline or {}).get("observations") or [])
    candidate = next(
        (
            item
            for item in discovery.get("candidates") or []
            if str(item.get("source_url") or "").rstrip("/") == source_url.rstrip("/")
        ),
        None,
    )
    preview = await _normalized_crawl_preview(source_url)
    try:
        workflow = create_replacement_workflow(
            old_document_id=canonical,
            source_url=source_url,
            preview=preview,
            reason=request.reason.strip(),
            requested_by=actor_user_id or "admin",
            idempotency_key=idem,
            request_fingerprint=request_fingerprint,
        )
    except ValueError as exc:
        if str(exc) == "replacement_idempotency_conflict":
            raise HTTPException(status_code=409, detail="Idempotency-Key đã được dùng cho nội dung thay thế khác.") from exc
        raise
    if workflow.get("idempotent_replay"):
        return _replacement_replay(workflow, request_fingerprint)
    workflow_id = str(workflow.get("workflow_id") or "")
    update_replacement_workflow(
        workflow_id,
        status="processing",
        steps={"official_url_identity": "passed", "official_relation": "verified" if candidate else "admin_confirmed"},
    )

    try:
        await write_audit_log(
            action="admin.legal_validity.replacement_activate_requested",
            entity_type="legal_validity_event",
            entity_id=event_canonical,
            actor_user_id=actor_user_id,
            actor_role=get_request_role(raw_request),
            details={
                "old_document_id": canonical,
                "source_url": source_url,
                "reason": request.reason.strip(),
                "candidate_law_number": (candidate or {}).get("law_number"),
                "relation_source": "official_observation" if candidate else "admin_confirmed_official_url",
                "workflow_id": workflow_id,
                "uploaded_filename": request.uploaded_filename,
                "uploaded_content_sha256": content_sha256,
            },
            request=raw_request,
        )
        required = {
            "title": preview.get("title"),
            "law_number": preview.get("law_number") or preview.get("document_number"),
            "document_type": preview.get("document_type"),
            "issuing_agency": preview.get("issuing_agency"),
            "issued_date": preview.get("issued_date"),
            "effective_date": preview.get("effective_date"),
            "content": (
                uploaded_content
                if len(uploaded_content.strip()) >= 100
                else preview.get("clean_markdown") or preview.get("content")
            ),
        }
        missing = [key for key, value in required.items() if not str(value or "").strip()]
        if missing:
            raise HTTPException(
                status_code=422,
                detail="Văn bản thay thế thiếu metadata chính thức: " + ", ".join(missing),
            )
        update_replacement_workflow(
            workflow_id,
            steps={"crawl": "passed", "normalize": "passed", "chunk": "pending_import"},
        )
        domain = str(preview.get("primary_domain") or "hanh_chinh_cong")
        field_by_domain = {
            "ho_tich_chung_thuc": 7,
            "dat_dai_xay_dung": 8,
            "an_sinh_y_te_giao_duc": 2,
            "hanh_chinh_cong": 2,
            "noi_vu_hanh_chinh": 2,
            "trat_tu_do_thi": 10,
            "cu_tru_an_ninh": 9,
            "khieu_nai_to_cao_xu_phat": 10,
            "xay_dung_do_thi": 8,
        }
        agency_scope = " ".join(
            str(value or "") for value in (required["title"], required["issuing_agency"])
        ).casefold()
        scope = "haiphong" if "hải phòng" in agency_scope or "hai phong" in agency_scope else "central"
        try:
            inherited_unit_fields = await _document_unit_import_fields(
                serving_document_id
            )
        except Exception:
            inherited_unit_fields = {}
        inherited_confirmation_status = str(
            inherited_unit_fields.pop("organization_assignment_status", "confirmed")
        ).strip()
        inherited_confirmation_status = (
            "confirmed"
            if inherited_confirmation_status == "confirmed"
            else "needs_confirmation"
        )
        import_result = await _request(
            "POST",
            f"/documents/{serving_document_id}/replace",
            base_url=LEGAL_MANAGEMENT_URL,
            json={
                **required,
                "scope": scope,
                "sector": "",
                "field_id": field_by_domain.get(domain, 2),
                "expired_date": preview.get("expired_date"),
                "source_url": source_url,
                "uploaded_pdf_sha256": content_sha256,
                "applicability_info": (
                    f"Thay thế {event.get('law_number') or canonical}. "
                    + (f"Toàn văn trích xuất từ tệp {request.uploaded_filename}." if request.uploaded_filename else "")
                ).strip(),
                "confirmed_official_source": True,
                "domain_slug": domain if domain in LEGAL_IMPORT_DOMAIN_SLUGS else None,
                **inherited_unit_fields,
                "replacement_of_document_id": serving_document_id,
                "replacement_action": "historical",
                "structure": "auto",
            },
        )
        retrieval_smoke = _verified_replacement_result(import_result)
        try:
            inherited_units = await copy_document_unit_assignments(
                old_document_id=serving_document_id,
                new_document_id=import_result.get("document_id"),
                confirmation_status=inherited_confirmation_status,
            )
            unit_assignment_sync = {
                "status": inherited_confirmation_status,
                "count": len(inherited_units),
            }
        except Exception as exc:
            unit_assignment_sync = {
                "status": "pending",
                "error_class": type(exc).__name__,
            }
        try:
            decision = await default_registry.record_decision(
                event_id=event_canonical, action="mark_historical",
                reason=request.reason.strip(), actor_user_id=actor_user_id,
                serving_confirmation={
                    **(import_result.get("old_document") or {}),
                    "document_id": str(serving_document_id),
                },
            )
            registry_sync = {"status": "recorded"}
        except Exception as exc:
            # Serving has already committed. Never claim the old record is
            # unchanged, or repeat an import because this secondary audit failed.
            decision = None
            registry_sync = {"status": "pending", "error_class": type(exc).__name__}
        update_replacement_workflow(
            workflow_id,
            steps={
                "chunk": "passed",
                "embedding": "passed",
                "retrieval_smoke": "passed",
                "exact_lexical_rebuild": "not_in_overlay_import",
                "manifest_verify": "not_in_overlay_import",
                "publish": "overlay_active_manifest_unchanged",
            },
            hard_gates={
                "official_identity_verified": True,
                "replacement_relation_recorded": True,
                "import_active": True,
                "chunks_indexed": int(import_result.get("chunk_count") or 0) > 0,
                "vector_collection_available": bool(
                    str(import_result.get("vector_collection") or "").strip()
                ),
                "exact_retrieval_smoke": bool(retrieval_smoke.get("exact_match")),
                "semantic_retrieval_smoke": bool(
                    retrieval_smoke.get("semantic_match")
                ),
            },
        )
        result = {
            "status": "activated",
            "workflow_id": workflow_id,
            "replacement": import_result,
            "unit_assignment_sync": unit_assignment_sync,
            "old_document": {
                **(import_result.get("old_document") or {}),
                "document_id": canonical,
                "serving_action": "historical_only",
                "decision": decision,
            },
            "retrieval_smoke": retrieval_smoke,
            "registry_sync": registry_sync,
            "chatbot_ready": bool(retrieval_smoke.get("passed")),
            "retrieval_scope": "admin_overlay",
            "manifest_included": False,
            "message": "Văn bản thay thế đã được index vào lớp tra cứu bổ sung; văn bản cũ chuyển sang tra cứu lịch sử.",
        }
        update_replacement_workflow(
            workflow_id,
            status="activated",
            result=result,
        )
        return result
    except HTTPException as exc:
        update_replacement_workflow(workflow_id, status="failed", error=str(exc.detail))
        raise
    except Exception as exc:
        update_replacement_workflow(workflow_id, status="failed", error=str(exc)[:1000])
        raise HTTPException(
            status_code=502,
            detail="Không xác nhận được toàn bộ workflow thay thế; kiểm tra trạng thái hai văn bản trước khi thử lại.",
        ) from exc


@router.get("/validity/replacement-workflows/by-idempotency-key")
async def legal_replacement_workflow_status_by_key(
    raw_request: Request,
    key: str = Query(min_length=8, max_length=500),
) -> dict[str, Any]:
    """Expose content-free replacement progress to the initiating Admin UI."""

    _require_crawl_admin(raw_request)
    workflow = get_replacement_workflow_by_idempotency_key(key.strip())
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow thay thế chưa được khởi tạo.")
    return {"status": "available", "workflow": workflow}


@router.get("/validity/replacement-workflows/{workflow_id}")
async def legal_replacement_workflow_status(
    workflow_id: str, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    workflow = get_replacement_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy workflow thay thế.")
    return {"status": "available", "workflow": workflow}


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
        base_url=LEGAL_MANAGEMENT_URL,
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
        base_url=LEGAL_MANAGEMENT_URL,
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
    operation: dict[str, Any] | None = None
    if request.action == "request_recheck":
        try:
            operation = await get_legal_validity_sync_service().recheck_event(event)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Văn bản thiếu định danh hợp lệ để kiểm tra lại.",
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Không kết nối được nguồn chính thức; sự kiện vẫn được giữ trong danh sách để thử lại.",
            ) from exc
        if operation.get("status") != "completed":
            reason_code = str(operation.get("reason_code") or "OFFICIAL_SOURCE_UNAVAILABLE")
            raise HTTPException(
                status_code=502,
                detail=(
                    "Nguồn chính thức chưa trả về bằng chứng hợp lệ "
                    f"({reason_code}); sự kiện vẫn được giữ trong danh sách để thử lại."
                ),
            )
    if request.action in {"mark_historical", "quarantine"}:
        try:
            serving_document_id = canonical_document_id(event.get("document_id"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Sự kiện chưa có mã văn bản trong kho phục vụ; chưa thay đổi trạng thái.") from exc
        # The SQL document + scope transaction is the actual serving trigger.
        # A snapshot-only decision is not a successful lifecycle operation.
        applied = await _request(
            "POST", f"/documents/{serving_document_id}/search-state",
            base_url=LEGAL_MANAGEMENT_URL,
            json={
                "action": "historical" if request.action == "mark_historical" else "quarantine",
                "requested_by": actor_user_id or "admin", "reason": request.reason.strip(),
            },
        )
        expected_action = "historical_only" if request.action == "mark_historical" else "block"
        if applied.get("serving_action") != expected_action or applied.get("search_included") is not False:
            raise HTTPException(status_code=502, detail="Kho phục vụ chưa xác nhận trạng thái mới; quyết định chưa hoàn tất.")
        operation = {**applied, "status": "applied"}
    try:
        decision = await default_registry.record_decision(
            event_id=canonical,
            action=request.action,
            reason=request.reason.strip(),
            actor_user_id=actor_user_id,
            **({"serving_confirmation": operation} if operation and operation.get("status") == "applied" else {}),
        )
    except Exception as exc:
        if operation and operation.get("status") == "applied":
            raise HTTPException(status_code=503, detail={
                "code": "serving_applied_registry_pending", "operation": operation,
                "message": "Đã cập nhật kho phục vụ nhưng chưa ghi xong sổ quyết định; có thể gửi lại để đồng bộ, không nhập lại văn bản.",
            }) from exc
        if isinstance(exc, LookupError):
            raise HTTPException(status_code=404, detail="Không tìm thấy sự kiện hiệu lực pháp lý.") from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail="Quyết định hiệu lực không hợp lệ.") from exc
        raise
    updated_event = await default_registry.get_event(canonical)
    return {
        "event": updated_event,
        "decision": decision,
        "operation": operation,
    }


@router.get("/crawl/summary")
async def legal_crawl_summary(raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    summary, sources, unread = await asyncio.gather(
        LegalCrawlService.summary(),
        LegalCrawlService.source_status(),
        LegalCrawlService.get_notifications(unread_only=True),
    )
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


@router.get("/proposals/summary")
async def officer_proposal_summary(raw_request: Request) -> dict[str, Any]:
    user_id = get_request_user_id(raw_request)
    if get_request_role(raw_request) != "officer" or not user_id:
        raise HTTPException(status_code=403, detail="Cần đăng nhập bằng tài khoản cán bộ.")
    rows = await repo_query(
        "SELECT status, count() AS count FROM legal_crawl_candidate "
        "WHERE submitted_by = $officer AND raw_metadata.candidate_origin = 'officer_document_proposal' "
        "AND status != 'superseded' GROUP BY status;",
        {"officer": _user_account_record(user_id)},
    )
    counts = {str(row.get("status") or "pending"): int(row.get("count") or 0) for row in rows}
    return {"total": sum(counts.values()), "by_status": counts}


@router.get("/proposals/candidates")
async def officer_visible_candidates(raw_request: Request, status: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Officer-safe candidate view outside the admin-only crawler namespace."""
    role = get_request_role(raw_request)
    if role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="Chỉ cán bộ hoặc quản trị viên được xem đề xuất.")
    if role == "admin":
        candidates = await LegalCrawlService.list_candidates(status=status, limit=min(max(limit, 1), 200))
        # Superseded revisions remain in storage/audit history but never enter
        # the default review queue where an Admin could approve them by mistake.
        if status == "superseded":
            return {"candidates": candidates}
        return {"candidates": [item for item in candidates if str(item.get("status") or item.get("review_status") or "").lower() != "superseded"]}
    user_id = get_request_user_id(raw_request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Cần đăng nhập bằng tài khoản cán bộ.")
    # Viewing progress is not an approval operation. Only inspect this
    # officer's rows; duplicate checks remain mandatory in the review/import path.
    candidates = await LegalCrawlService.list_candidates(
        status=status, limit=min(max(limit, 1), 200), origin="officer",
        submitted_by=user_id, summary_only=True, check_duplicates=False,
    )
    visible = []
    for candidate in candidates:
        raw = candidate.get("raw_metadata") or {}
        if raw.get("candidate_origin") != "officer_document_proposal":
            continue
        if not _same_user_account(candidate.get("submitted_by"), user_id):
            continue
        # Retain an author's history after transfer; writes revalidate current
        # unit/grant authority separately in the resubmission endpoint.
        visible.append(_officer_candidate_projection(candidate))
    # A resubmission creates a linked immutable revision. The officer sees the
    # latest revision in the main list and can expand the safe history.
    threads: dict[str, list[dict[str, Any]]] = {}
    for item in visible:
        threads.setdefault(str(item.get("proposal_thread_id") or item.get("id")), []).append(item)
    latest: list[dict[str, Any]] = []
    for thread_items in threads.values():
        thread_items.sort(key=lambda item: int(item.get("proposal_revision") or 1), reverse=True)
        current = next((item for item in thread_items if item.get("status") != "superseded"), thread_items[0])
        current["revision_history"] = [
            {
                "id": item.get("id"),
                "revision": item.get("proposal_revision") or 1,
                "status": item.get("status"),
                "review_status": item.get("review_status"),
                "review_note": item.get("review_note"),
                "requested_changes_note": item.get("requested_changes_note"),
                "updated": item.get("updated") or item.get("updated_at"),
            }
            for item in thread_items
        ]
        latest.append(current)
    latest.sort(key=lambda item: str(item.get("updated") or item.get("updated_at") or ""), reverse=True)
    return {"candidates": latest}


@router.get("/proposals/candidates/{candidate_id}")
async def officer_proposal_detail(candidate_id: str, raw_request: Request) -> dict[str, Any]:
    """Return bounded editable data for the submitting officer only."""
    role = get_request_role(raw_request)
    if role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="Chỉ cán bộ hoặc admin được xem đề xuất.")
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề xuất.")
    if role == "admin":
        return {"candidate": _candidate_review_preview(candidate)}
    user_id = get_request_user_id(raw_request)
    raw = candidate.get("raw_metadata") if isinstance(candidate.get("raw_metadata"), dict) else {}
    if not user_id or raw.get("candidate_origin") != "officer_document_proposal":
        raise HTTPException(status_code=404, detail="Không tìm thấy đề xuất.")
    if not _same_user_account(candidate.get("submitted_by"), user_id):
        raise HTTPException(status_code=403, detail="Bạn không có quyền xem đề xuất này.")
    return {"candidate": _officer_candidate_edit_projection(candidate)}


@router.post("/proposals/{candidate_id}/resubmit")
async def officer_resubmit_proposal(
    candidate_id: str,
    raw_request: Request,
    expected_revision: int = Form(..., ge=1),
    domain: str | None = Form(default=None),
    source_type: str | None = Form(default=None, pattern="^(document|form|procedure|reference)$"),
    title: str | None = Form(default=None, min_length=5, max_length=2000),
    reason: str | None = Form(default=None, min_length=10, max_length=2000),
    source_url: str | None = Form(default=None, max_length=4000),
    law_number: str | None = Form(default=None, max_length=255),
    document_type: str | None = Form(default=None, max_length=255),
    issuing_agency: str | None = Form(default=None, max_length=255),
    scope: str | None = Form(default=None, max_length=100),
    sector: str | None = Form(default=None, max_length=255),
    issued_date: str | None = Form(default=None),
    effective_date: str | None = Form(default=None),
    expired_date: str | None = Form(default=None),
    content: str | None = Form(default=None),
    file_sha256: str = Form("", max_length=64),
    file: UploadFile | None = File(None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    """Create a new linked revision after Admin requests supplementation."""
    role = get_request_role(raw_request)
    user_id = get_request_user_id(raw_request)
    if role != "officer" or not user_id:
        raise HTTPException(status_code=403, detail="Chỉ cán bộ sở hữu đề xuất mới được nộp lại.")
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy đề xuất.")
    raw = dict(candidate.get("raw_metadata") or {})
    if raw.get("candidate_origin") != "officer_document_proposal" or not _same_user_account(candidate.get("submitted_by"), user_id):
        raise HTTPException(status_code=403, detail="Bạn không có quyền nộp lại đề xuất này.")
    profile = await get_user_profile(user_id)
    current_domain = str(candidate.get("domain") or raw.get("domain") or "").strip()
    from api.organization_service import current_officer_scope
    operating_scope = await current_officer_scope(user_id, profile or {})
    assignment_ids = [str(candidate.get("primary_organization_unit_id") or ""), *(candidate.get("proposed_organization_unit_ids") or [])]
    if not operating_scope.permits(assignment_ids, current_domain):
        raise HTTPException(status_code=403, detail="Đề xuất không thuộc lĩnh vực được phân công.")
    idem = (idempotency_key or "").strip()
    # A replay must be safe even after the original revision has moved to
    # ``superseded``.  Return the already-created revision before checking the
    # mutable status/revision guards below.
    if idem and str(raw.get("last_resubmit_idempotency_key") or "") == idem and raw.get("resubmitted_to"):
        existing = await LegalCrawlService.get_candidate(str(raw["resubmitted_to"]))
        if existing:
            return {"candidate": _officer_candidate_projection(existing), "idempotent_replay": True}
    current_revision = int(candidate.get("proposal_revision") or raw.get("proposal_revision") or 1)
    if current_revision != expected_revision:
        raise HTTPException(status_code=409, detail="Đề xuất đã có revision mới; hãy tải lại trước khi nộp lại.")
    current_status = str(candidate.get("status") or candidate.get("review_status") or "").strip().lower()
    if current_status != "changes_requested":
        raise HTTPException(status_code=409, detail="Chỉ đề xuất đang yêu cầu bổ sung mới được nộp lại.")

    values: dict[str, Any] = {
        "domain": (domain or current_domain).strip(),
        "source_type": (source_type or candidate.get("source_type") or raw.get("source_type") or "document").strip().lower(),
        "title": title if title is not None else candidate.get("title") or "",
        "reason": reason if reason is not None else candidate.get("proposal_reason") or raw.get("proposal_reason") or "",
        "source_url": source_url if source_url is not None else candidate.get("source_url") or raw.get("source_url") or candidate.get("detail_url") or "",
        "law_number": law_number if law_number is not None else candidate.get("law_number") or "",
        "document_type": document_type if document_type is not None else candidate.get("document_type") or raw.get("document_type") or "Tài liệu đề xuất",
        "issuing_agency": issuing_agency if issuing_agency is not None else candidate.get("issuing_agency") or raw.get("issuing_agency") or "",
        "scope": scope if scope is not None else candidate.get("scope") or raw.get("scope") or "central",
        "sector": sector if sector is not None else raw.get("sector") or "",
        "issued_date": issued_date if issued_date is not None else raw.get("issued_date") or "",
        "effective_date": effective_date if effective_date is not None else raw.get("effective_date") or "",
        "expired_date": expired_date if expired_date is not None else raw.get("expired_date") or "",
    }
    if not operating_scope.permits(assignment_ids, values["domain"]):
        raise HTTPException(status_code=403, detail="Không được đổi đề xuất sang lĩnh vực ngoài quyền được phân công.")
    if len(values["title"].strip()) < 5 or len(values["reason"].strip()) < 10:
        raise HTTPException(status_code=422, detail="Tên và lý do đề xuất chưa đủ độ dài tối thiểu.")

    previous_content = str(candidate.get("content") or "")
    previous_extraction = candidate.get("extraction_result") or {}
    previous_file = candidate.get("uploaded_file")
    extracted_text = previous_content
    extraction_result = previous_extraction
    uploaded_file = previous_file
    if file is not None:
        file_bytes = await file.read(DOCUMENT_UPLOAD_POLICY.max_bytes + 1)
        try:
            uploaded_file = await _save_proposal_upload(file, file_bytes, expected_sha256=file_sha256 or None)
        except UploadSecurityError as exc:
            raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from exc
        extracted_text, extraction_result = _extract_upload_text(file.filename or "uploaded", file_bytes)
        pii = redact_upload_text(extracted_text)
        extracted_text = pii["text"]
        extraction_result = {
            **extraction_result,
            "source": "upload",
            "filename": file.filename,
            "characters": len(extracted_text),
            "contains_pii": pii["contains_pii"],
            "pii_types": pii["pii_types"],
            "pii_counts": pii["pii_counts"],
            "pii_masked": pii["masked"],
            "retention": pii["retention"],
        }
    if content is not None:
        extracted_text = content.strip()
    if not values["source_url"].strip() and not extracted_text.strip():
        raise HTTPException(status_code=422, detail="Đề xuất phải còn URL nguồn, nội dung hoặc tệp.")
    content_hash = hashlib.sha256(extracted_text.encode("utf-8")).hexdigest() if extracted_text else None
    thread_id = str(candidate.get("proposal_thread_id") or raw.get("proposal_thread_id") or candidate.get("id") or candidate_id)
    next_revision = current_revision + 1
    revision_raw = {
        **raw,
        **values,
        "proposal_reason": values["reason"].strip(),
        "source_url": values["source_url"].strip(),
        "submitted_by": user_id,
        "candidate_origin": "officer_document_proposal",
        "proposal_thread_id": thread_id,
        "proposal_revision": next_revision,
        "resubmitted_from": str(candidate.get("id") or candidate_id),
        "last_resubmit_idempotency_key": idem or None,
        "resubmitted_at": datetime.now(timezone.utc).isoformat(),
        "confirmed_official_source": False,
    }
    external_seed = "|".join(["officer-proposal", thread_id, str(next_revision), idem or uuid.uuid4().hex])
    new_candidate_data = dict(candidate)
    for key in {"id", "created", "updated", "status", "review_status", "review_note", "requested_changes_note", "reviewed_at", "reviewed_by", "approved_at", "approved_by", "superseded_by"}:
        new_candidate_data.pop(key, None)
    new_candidate_data.update({
        "external_id": "officer-proposal-revision:" + hashlib.sha256(external_seed.encode("utf-8")).hexdigest()[:32],
        "title": values["title"].strip(),
        "law_number": values["law_number"].strip() or None,
        "document_type": values["document_type"].strip() or "Tài liệu đề xuất",
        "issuing_agency": values["issuing_agency"].strip() or None,
        "scope": values["scope"].strip() or "central",
        "source_url": values["source_url"].strip(),
        "detail_url": values["source_url"].strip(),
        "description": values["reason"].strip(),
        "proposal_reason": values["reason"].strip(),
        "domain": values["domain"],
        "source_type": values["source_type"],
        "content": extracted_text or None,
        "content_hash": content_hash,
        "extraction_result": extraction_result,
        "uploaded_file": uploaded_file,
        "raw_metadata": revision_raw,
        "legal_validity_flags": _legal_validity_flags(effective_date=values["effective_date"].strip() or None, expired_date=values["expired_date"].strip() or None, source_url=values["source_url"].strip() or None),
        "status": "pending",
        "review_status": "pending",
        "pipeline_stage": "submitted",
        "proposal_thread_id": thread_id,
        "proposal_revision": next_revision,
        "supersedes_candidate_id": str(candidate.get("id") or candidate_id),
        "requested_changes_note": None,
    })
    created = await repo_create("legal_crawl_candidate", new_candidate_data)
    new_candidate = created[0] if isinstance(created, list) and created else created
    if not isinstance(new_candidate, dict) or not new_candidate.get("id"):
        raise HTTPException(status_code=503, detail="Không tạo được revision đề xuất mới.")
    new_id = str(new_candidate["id"])
    try:
        old_raw = dict(raw)
        old_raw["resubmitted_to"] = new_id
        old_raw["last_resubmit_idempotency_key"] = idem or None
        await repo_update("legal_crawl_candidate", candidate["id"], {
            "status": "superseded",
            "review_status": "superseded",
            "superseded_by": new_id,
            "raw_metadata": old_raw,
            "updated": datetime.now(timezone.utc),
        })
    except Exception as exc:
        try:
            await repo_update("legal_crawl_candidate", new_id, {"status": "superseded", "review_status": "superseded", "review_note": "Revision không hoàn tất do lỗi liên kết lịch sử."})
        except Exception:
            pass
        raise HTTPException(status_code=503, detail="Không thể liên kết revision; dữ liệu cũ vẫn được giữ nguyên.") from exc
    await write_audit_log(
        action="legal.proposal.resubmit",
        entity_type="legal_crawl_candidate",
        entity_id=new_id,
        actor_user_id=user_id,
        actor_role=role,
        details={"supersedes": str(candidate.get("id") or candidate_id), "revision": next_revision, "fields": sorted(values.keys())},
        request=raw_request,
    )
    return {"candidate": _officer_candidate_projection({**new_candidate, **new_candidate_data, "id": new_id}), "idempotent_replay": False}


@router.patch("/crawl/sources/{source_id}")
async def legal_crawl_update_source(
    source_id: str, request: LegalCrawlSourceUpdateRequest, raw_request: Request
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    before = await _crawl_source_snapshot(source_id)
    payload = request.model_dump(exclude_none=True)
    # An explicit null means "remove the default department", not "unchanged".
    # Keep omission semantics for old clients and all other optional fields.
    if "default_organization_unit_id" in request.model_fields_set:
        payload["default_organization_unit_id"] = request.default_organization_unit_id
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


@router.post("/crawl/sources/preview")
async def legal_crawl_preview_source(
    request: LegalCrawlSourceCreateRequest, raw_request: Request
) -> dict[str, Any]:
    """Render and parse a bounded sample without writing source or candidate data."""
    _require_crawl_admin(raw_request)
    try:
        return await LegalCrawlService.preview_source(request.model_dump(), limit=10)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/crawl/sources/{source_id}")
async def legal_crawl_delete_source(source_id: str, raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    before = await _crawl_source_snapshot(source_id)
    try:
        result = await LegalCrawlService.delete_source(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
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
        result["audit_sync"] = {"status": "recorded"}
    except Exception as exc:
        # The source is already disabled/archived. Do not turn a successful,
        # idempotent delete into an HTTP error that encourages a second click.
        result["audit_sync"] = {
            "status": "pending",
            "error_class": type(exc).__name__,
        }
    try:
        await record_config_revision(
            config_type="crawler_sources",
            before=before,
            after=await _crawl_source_snapshot(source_id),
            actor_user_id=get_request_user_id(raw_request),
            reason="crawler source delete_or_archive",
        )
        result["config_revision_sync"] = {"status": "recorded"}
    except Exception as exc:
        result["config_revision_sync"] = {
            "status": "pending",
            "error_class": type(exc).__name__,
        }
    return result


@router.post("/crawl/scan")
async def legal_crawl_scan_now(raw_request: Request, source_id: str | None = None) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    runs: list[dict[str, Any]] = []
    if source_id:
        try:
            runs.append(await LegalCrawlService.scan_source(source_id))
        except Exception as exc:
            logger.warning(
                "Manual crawler dispatch failed: source={} error={}",
                source_id,
                exc.__class__.__name__,
            )
            runs.append({
                "status": "failed",
                "source_id": source_id,
                "failure_reason": str(exc)[:500] or exc.__class__.__name__,
                "statistics": {},
            })
    else:
        sources = await LegalCrawlService.ensure_vbpl_sources()
        for source in sources:
            if not source.get("enabled", False) or not LegalCrawlService.is_crawlable_source(source):
                continue
            current_source_id = str(source["id"])
            try:
                runs.append(await LegalCrawlService.scan_source(current_source_id))
            except Exception as exc:
                logger.warning(
                    "Manual crawler dispatch failed: source={} error={}",
                    current_source_id,
                    exc.__class__.__name__,
                )
                runs.append({
                    "status": "failed",
                    "source_id": current_source_id,
                    "failure_reason": str(exc)[:500] or exc.__class__.__name__,
                    "statistics": {},
                })

    result = LegalCrawlService.summarize_scan_runs(runs)
    await write_audit_log(
        action="legal.crawl.run.manual",
        entity_type="legal_crawl_source",
        entity_id=source_id or "all_enabled_sources",
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={
            "status": result["status"],
            "run_count": result["run_count"],
            "created": result["created"],
            "updated": result["updated"],
            "failed_count": result["failed_count"],
            "warning_count": result["warning_count"],
            "busy_count": result["busy_count"],
        },
        request=raw_request,
    )
    return result


@router.get("/crawl/candidates")
async def legal_crawl_candidates(
    raw_request: Request,
    status: str | None = None,
    domain: str | None = Query(default=None, max_length=100),
    source_type: str | None = Query(
        default=None,
        pattern="^(all|document|form|procedure|reference|unclassified)$",
    ),
    limit: int = 100,
    offset: int | None = Query(default=None, ge=0),
    origin: str | None = Query(default=None, pattern="^(all|officer|crawler)$"),
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    try:
        if offset is not None:
            return await LegalCrawlService.list_candidate_page(
                status=status, domain=domain, source_type=source_type, origin=origin,
                limit=limit, offset=offset,
            )
        candidates = await LegalCrawlService.list_candidates(
            status=status, domain=domain, source_type=source_type, limit=limit
        )
    except DuplicateCheckUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"candidates": candidates}


@router.get("/crawl/candidates/{candidate_id}")
async def legal_crawl_candidate(candidate_id: str, raw_request: Request) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy văn bản chờ duyệt.")
    return {"candidate": candidate, "review_preview": _candidate_review_preview(candidate)}


@router.get("/crawl/candidates/{candidate_id}/original")
async def legal_candidate_original(candidate_id: str, raw_request: Request):
    from fastapi.responses import FileResponse
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    uploaded = (candidate or {}).get("uploaded_file") or {}
    root = (Path("data") / "uploads" / "officer_proposals").resolve()
    target = Path(str(uploaded.get("path") or "")).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "Không có tệp gốc đã lưu cho đề xuất này.")
    if hashlib.sha256(target.read_bytes()).hexdigest() != uploaded.get("sha256"):
        raise HTTPException(409, "Tệp gốc không khớp bản đã lưu; cần kiểm tra lại.")
    return FileResponse(target, filename=uploaded.get("filename") or target.name,
                        media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


@router.post("/crawl/candidates/{candidate_id}/duplicate-resolution")
async def legal_candidate_resolve_duplicate(
    candidate_id: str, request: LegalCandidateDuplicateResolutionRequest, raw_request: Request,
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    candidate = await LegalCrawlService.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Không tìm thấy candidate.")
    try:
        result = await resolve_candidate_duplicate(
            candidate, **request.model_dump(), actor_id=get_request_user_id(raw_request) or "admin",
            management_url=LEGAL_MANAGEMENT_URL,
        )
    except CandidateChanged as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DuplicateCheckUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The durable receipt is in the same candidate write, even if an audit-log
    # sink is temporarily unavailable. A repeat request returns that receipt.
    if not result["idempotent"]:
        await write_audit_log(
            action=f"legal.candidate.{request.action}", entity_type="legal_crawl_candidate",
            entity_id=candidate_id, actor_user_id=get_request_user_id(raw_request), actor_role="admin",
            details={"target_type": request.target_type, "target_id": request.target_id, "reason": request.reason, "corpus_changed": False},
            request=raw_request,
        )
    return result


@router.post("/crawl/candidates/{candidate_id}/review")
async def legal_crawl_review_candidate(
    candidate_id: str,
    request: LegalCandidateDecisionRequest,
    raw_request: Request,
    response: Response,
) -> dict[str, Any]:
    _require_crawl_admin(raw_request)
    try:
        settings = await active_settings()
        candidate = await LegalCrawlService.review_candidate(
            candidate_id,
            request.decision,
            request.review_note,
            reviewed_by=get_request_user_id(raw_request),
            reviewed_role=get_request_role(raw_request),
            organization_routing_mode=getattr(
                settings, "organization_routing_mode", "legacy"
            ),
        )
    except CandidateChanged as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
            "organization_assignment": {
                "assignment_state": candidate.get("assignment_state"),
                "primary_organization_unit_id": candidate.get("primary_organization_unit_id"),
                "organization_unit_ids": candidate.get("proposed_organization_unit_ids") or [],
            },
            "submitter_organization_snapshot": (candidate.get("raw_metadata") or {}).get("submitter_organization_snapshot"),
        },
        request=raw_request,
    )
    if request.decision == "approved" and candidate.get("status") == "import_queued":
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
    if candidate.get("status") not in {"pending", "changes_requested", "approved", "import_failed", "replacement_review"}:
        raise HTTPException(status_code=409, detail="Không sửa metadata của candidate đang nhập, đã nhập hoặc đã lưu trữ.")
    changes = request.model_dump(exclude_unset=True)
    assignment_reason = changes.pop("assignment_reason", None)
    assignment_state = changes.get("assignment_state")
    assignment_unit_id = str(
        changes.get("primary_organization_unit_id") or ""
    ).strip() or None
    if assignment_unit_id and assignment_state is None:
        assignment_state = "assigned"
        changes["assignment_state"] = "assigned"
    if assignment_state == "assigned":
        if not assignment_unit_id:
            raise HTTPException(
                status_code=422,
                detail="Hãy chọn phòng ban chịu trách nhiệm cho tài liệu.",
            )
        units = await active_organization_units()
        unit = find_unit(units, unit_id=assignment_unit_id)
        if unit is None or not unit.is_active:
            raise HTTPException(
                status_code=422,
                detail="Phòng ban được chọn không tồn tại hoặc đã ngừng hoạt động.",
            )
        changes["proposed_organization_unit_ids"] = [assignment_unit_id]
        changes["organization_assignment_confidence"] = 1.0
    elif assignment_state in {"shared", "unassigned"}:
        changes["primary_organization_unit_id"] = None
        changes["proposed_organization_unit_ids"] = []
        changes["organization_assignment_confidence"] = (
            1.0 if assignment_state == "shared" else 0.0
        )
    for date_field in ("issued_date", "effective_date", "expired_date"):
        if date_field in changes and not str(changes[date_field] or "").strip():
            changes[date_field] = None
    raw_metadata = dict(candidate.get("raw_metadata") or {})
    raw_metadata.update(changes)
    top_level = {
        key: value for key, value in changes.items()
        if key in {
            "title", "law_number", "document_type", "issuing_agency", "scope",
            "source_url", "assignment_state", "primary_organization_unit_id",
            "proposed_organization_unit_ids", "organization_assignment_confidence",
        }
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
    raw_metadata["legal_identity"] = identity_metadata({**candidate, **top_level, "raw_metadata": raw_metadata})
    identity_fields = {
        "title",
        "law_number",
        "document_type",
        "issuing_agency",
        "source_url",
        "issued_date",
    }
    duplicate_reset = {}
    if identity_fields.intersection(changes):
        # Duplicate evidence is tied to the candidate identity at the time it
        # was computed.  Keeping it after an administrator corrects the URL,
        # number or source metadata makes the UI show "no duplicate" while
        # import validation still fails on stale duplicate_candidates.
        duplicate_reset = {
            "duplicate_candidates": [],
            "duplicate_matches": [],
            "duplicate_runtime_document": None,
            "duplicate_check_status": "pending",
            "duplicate_check_errors": [],
            "duplicate_check_revision": None,
            "comparison_status": "new",
            "comparison_details": {},
        }
    try:
        updated = await update_candidate_if_current(candidate, {
            **top_level, **duplicate_reset, "raw_metadata": raw_metadata,
            "legal_validity_flags": validity, "review_recommendation": recommendation,
        })
    except CandidateChanged as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await write_audit_log(
        action="legal.candidate.metadata.update",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={
            "fields": sorted(changes.keys()),
            **({
                "reason": assignment_reason or "Admin xác nhận phân công tài liệu trong hàng đề xuất.",
                "assignment_before": {
                    key: candidate.get(key) for key in (
                        "assignment_state", "primary_organization_unit_id", "proposed_organization_unit_ids"
                    )
                },
                "assignment_after": {
                    key: updated.get(key) for key in (
                        "assignment_state", "primary_organization_unit_id", "proposed_organization_unit_ids"
                    )
                },
            } if assignment_state is not None else {}),
        },
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
    job = rows[0]
    # Jobs created before migration 46 may only have the collection in the
    # persisted embedding result.  Project it read-only so the admin sees the
    # same serving state without rewriting audit history.
    if not job.get("vector_collection"):
        embedding_result = job.get("embedding_result") or {}
        if isinstance(embedding_result, dict) and embedding_result.get("vector_collection"):
            job = {**job, "vector_collection": embedding_result["vector_collection"]}
    return {"job": job}


@router.post("/crawl/candidates/{candidate_id}/reconcile")
async def legal_crawl_reconcile_candidate(
    candidate_id: str, raw_request: Request
) -> dict[str, Any]:
    """Reconcile a stale import receipt after live serving checks pass."""

    _require_crawl_admin(raw_request)
    result = await LegalCrawlService.reconcile_import_projection(
        candidate_id, apply=True
    )
    if result.get("status") not in {"verified", "missing_document_quarantined"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": result.get("reason_code", "candidate_not_reconciled"),
                "message": "Chưa thể đối soát kết quả nhập kho; chưa có đủ thông tin xác nhận.",
                "result": result,
            },
        )
    await write_audit_log(
        action="legal.candidate.import.reconcile",
        entity_type="legal_crawl_candidate",
        entity_id=candidate_id,
        actor_user_id=get_request_user_id(raw_request),
        actor_role=get_request_role(raw_request),
        details={
            "document_id": result.get("document_id"),
            "job_id": result.get("job_id"),
            "retrieval_smoke": result.get("retrieval_smoke"),
            "reconciliation_status": result.get("status"),
            "document_ids": result.get("document_ids"),
            "before": result.get("before"),
        },
        request=raw_request,
    )
    if result.get("status") == "missing_document_quarantined":
        result["message"] = "Văn bản được tham chiếu không còn trong kho. Đề xuất đã chuyển về cần kiểm tra; kết quả nhập cũ được giữ trong lịch sử."
    return result


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
        reviewer_id = get_request_user_id(raw_request)
        job = await LegalCrawlService.enqueue_import_job(
            candidate_id,
            approval={
                "reviewed_at": datetime.now(timezone.utc),
                "reviewed_by": ensure_record_id(reviewer_id) if reviewer_id else None,
                "reviewed_role": get_request_role(raw_request),
                "approved_by": ensure_record_id(reviewer_id) if reviewer_id else None,
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
