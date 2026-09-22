"""Bridge approved operational drafts to the existing live incremental importer."""
from datetime import date, datetime, timezone
import os
import httpx
from api.legal_lifecycle_service import LifecycleError


async def _record_approved_validity(draft, result):
    """Bind an approved draft's validity metadata to its real document ID."""

    document_id = str(result.get("document_id") or "").strip()
    law_number = str(draft.get("law_number") or "").strip()
    source_url = str(draft.get("source_url") or "").strip()
    if not document_id or not law_number or not source_url:
        return None

    effective_from = str(draft.get("effective_date") or "").strip() or None
    effective_to = str(draft.get("expired_date") or "").strip() or None
    from api.legal_validity_models import vietnam_legal_date

    today = vietnam_legal_date()
    try:
        starts = date.fromisoformat(effective_from) if effective_from else None
        ends = date.fromisoformat(effective_to) if effective_to else None
    except ValueError as exc:
        raise LifecycleError(
            "lifecycle_validity_metadata_invalid",
            "Ngày hiệu lực đã duyệt không hợp lệ; chưa thể đồng bộ trạng thái văn bản.",
            status_code=409,
        ) from exc
    normalized_status = (
        "expired"
        if ends and ends <= today
        else "not_yet_effective"
        if starts and starts > today
        else "active"
    )
    now = datetime.now(timezone.utc)
    from api.legal_validity_models import LegalValidityObservation
    from api.legal_validity_registry import default_registry

    observation = LegalValidityObservation.from_dict(
        {
            "document_id": document_id,
            "law_number": law_number,
            "issuing_agency": draft.get("issuing_agency"),
            "issued_date": draft.get("issued_date"),
            "source_url": source_url,
            "source_kind": "admin_confirmed_stored_metadata",
            "raw_status": "approved lifecycle draft metadata",
            "normalized_status": normalized_status,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "affected_provisions": [],
            "identity_status": "exact",
            "evidence_status": "sufficient",
            "observed_at": now.isoformat(),
            "verified_by": draft.get("approved_by") or draft.get("reviewed_by"),
            "verified_at": now.isoformat(),
        }
    )
    recorded = await default_registry.record_observation(
        observation,
        scope=draft.get("scope"),
        document_title=draft.get("title"),
    )
    snapshot = await default_registry.refresh_snapshot_projection(
        additional_document_ids=[document_id]
    )
    if not snapshot or document_id not in (snapshot.get("document_ids") or {}):
        raise LifecycleError(
            "lifecycle_validity_pending_retry",
            "Văn bản đã lập chỉ mục; đang chờ đồng bộ hiệu lực. Thử lại không nhập trùng văn bản.",
            status_code=503,
        )
    return {
        "observation_id": str((recorded.get("observation") or {}).get("id") or ""),
        "status": normalized_status,
        "verified_at": now.isoformat(),
    }

async def activate_incremental_draft(draft):
    version_key = (draft.get("version") or {}).get("version_key")
    if not version_key:
        raise LifecycleError("lifecycle_version_missing", "Văn bản chưa có mã phiên bản đã duyệt.", status_code=409)
    required = ("field_id", "effective_date", "content", "primary_organization_unit_id", "domain_slug")
    missing = [name for name in required if not draft.get(name)]
    if missing:
        labels = {"field_id": "lĩnh vực văn bản", "effective_date": "ngày có hiệu lực",
                  "content": "toàn văn", "primary_organization_unit_id": "phòng ban phụ trách",
                  "domain_slug": "nhóm lĩnh vực"}
        raise LifecycleError("lifecycle_activation_metadata_missing", "Bổ sung trước khi nhập kho: " + ", ".join(labels[name] for name in missing), status_code=409)
    from api.system_settings import active_organization_units
    from api.organization_service import effective_domain_assignments
    units = await active_organization_units()
    unit = next((item for item in units if item.id == draft["primary_organization_unit_id"] and item.is_active), None)
    if unit is None:
        raise LifecycleError("lifecycle_department_unavailable", "Phòng ban phụ trách không còn hoạt động.", status_code=409)
    allowed = {item.domain_code for item in effective_domain_assignments(unit)}
    if draft["domain_slug"] not in allowed:
        raise LifecycleError("lifecycle_department_domain_mismatch", "Lĩnh vực không thuộc phòng ban đã chọn.", status_code=409)
    fields = ("title", "law_number", "document_type", "issuing_agency", "scope", "sector", "field_id", "issued_date", "effective_date", "expired_date", "source_url", "content", "domain_slug", "primary_organization_unit_id")
    document = {name: draft[name] for name in fields if draft.get(name) not in (None, "")}
    document.update(confirmed_official_source=True, replacement_of_document_id=draft.get("logical_document_id"))
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(os.getenv("LEGAL_MANAGEMENT_URL", "http://127.0.0.1:8765") + "/lifecycle/activate", json={"version_key":version_key,"base_fingerprint":draft.get("base_fingerprint"),"document":document})
    except httpx.HTTPError as exc:
        raise LifecycleError("lifecycle_indexing_pending_retry", "Mất kết nối khi lập chỉ mục. Văn bản vẫn được giữ để thử lại mà không nhập trùng.", status_code=503) from exc
    if response.is_error:
        raise LifecycleError("lifecycle_indexing_pending_retry", "Văn bản đã duyệt nhưng chưa hoàn tất lập chỉ mục. Có thể thử lại; hệ thống giữ nguyên mã phiên bản để tránh nhập trùng.", status_code=503)
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("invalid activation receipt")
    except ValueError as exc:
        raise LifecycleError("lifecycle_indexing_not_verified", "Dịch vụ lập chỉ mục chưa trả về xác nhận hợp lệ. Có thể thử lại mà không nhập trùng.", status_code=503) from exc
    if not result.get("sql_verified") or not result.get("collections_verified") or not (result.get("retrieval_smoke") or {}).get("passed"):
        raise LifecycleError("lifecycle_indexing_not_verified", "Chưa xác minh được văn bản trong kho tra cứu.", status_code=503)
    # SQL ingestion already stores the approved assignment. Complete its
    # authority record before reporting activation complete. On retry the
    # stable activation receipt avoids reimport, and an existing admin decision
    # is never replaced by stale draft metadata.
    try:
        from api.organization_service import replace_document_unit_assignments
        await replace_document_unit_assignments(
            document_id=result['document_id'],
            primary_organization_unit_id=draft['primary_organization_unit_id'],
            organization_unit_ids=[draft['primary_organization_unit_id']],
            assignment_state='assigned', assignment_source='approved_draft',
            confirmation_status='confirmed', only_if_absent=True,
        )
    except Exception as exc:
        raise LifecycleError('lifecycle_assignment_pending_retry',
            'Văn bản đã lập chỉ mục; đang chờ đồng bộ phòng ban. Thử lại không nhập trùng văn bản.',
            status_code=503) from exc
    try:
        validity_receipt = await _record_approved_validity(draft, result)
        if validity_receipt:
            result["validity_observation"] = validity_receipt
    except LifecycleError:
        raise
    except Exception as exc:
        raise LifecycleError(
            "lifecycle_validity_pending_retry",
            "Văn bản đã lập chỉ mục; đang chờ đồng bộ hiệu lực. Thử lại không nhập trùng văn bản.",
            status_code=503,
        ) from exc
    return result

async def read_base_fingerprint(document_id):
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(os.getenv("LEGAL_MANAGEMENT_URL", "http://127.0.0.1:8765") + f"/lifecycle/documents/{document_id}/revision")
        response.raise_for_status()
        fingerprint = response.json()["fingerprint"]
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            raise ValueError("missing fingerprint")
        return fingerprint
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise LifecycleError("lifecycle_base_unavailable", "Chưa đọc được văn bản gốc để tạo phiên bản thay thế.", status_code=503) from exc
