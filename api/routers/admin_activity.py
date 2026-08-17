"""Filtered, content-minimized Admin Activity Center for Feature 018."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id
from api.user_service import list_audit_logs, write_audit_log


router = APIRouter(prefix="/admin/activity", tags=["admin-activity"])


class SensitiveViewRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


ACTION_LABELS = {
    "retention.purge.completed": "Đã hoàn tất xóa dữ liệu hết hạn",
    "auth.login.failed": "Đăng nhập thất bại",
    "user.create": "Đã tạo tài khoản",
    "user.update": "Đã cập nhật tài khoản",
    "user.deactivate": "Đã khóa tài khoản",
    "user.reactivate": "Đã mở khóa tài khoản",
    "user.soft_delete": "Đã xóa tài khoản",
    "admin.legal.import": "Đã nhập văn bản pháp luật",
    "legal.proposal.create": "Đã tạo đề xuất văn bản",
    "legal.crawl.run.manual": "Đã chạy thu thập văn bản",
    "legal.candidate.import.enqueue": "Đã đưa văn bản vào hàng chờ nhập",
    "legal.candidate.import.duplicate_conflict": "Nhập văn bản thất bại do trùng dữ liệu",
    "faq.revision.create": "Đã tạo bản sửa FAQ",
    "faq.revision.confirm": "Đã duyệt FAQ",
    "faq.release.activate": "Đã phát hành FAQ",
    "form.source.approve": "Đã duyệt nguồn biểu mẫu",
    "form.attest": "Đã xác nhận biểu mẫu",
    "form.release.activate": "Đã phát hành biểu mẫu",
    "admin.model.create": "Đã thêm model AI",
    "admin.model.delete": "Đã xóa model AI",
    "admin.credential.create": "Đã thêm API key",
    "admin.credential.update": "Đã cập nhật API key",
    "admin.credential.delete": "Đã xóa API key",
    "admin.configuration.update": "Đã cập nhật cấu hình hệ thống",
    "admin.activity.sensitive_view": "Đã xem dữ liệu nhạy cảm",
    "admin.activity.export": "Đã xuất nhật ký quản trị",
    "support.ticket.assign_failed": "Phân công hỗ trợ thất bại",
}


RESOURCE_LABELS = {
    "audit_event": "Sự kiện kiểm toán",
    "credential": "API key",
    "faq_release": "Bản phát hành FAQ",
    "faq_revision": "FAQ",
    "form_release": "Bản phát hành biểu mẫu",
    "form_review_case": "Hồ sơ biểu mẫu",
    "legal_crawl_candidate": "Văn bản chờ nhập",
    "legal_crawl_source": "Nguồn thu thập",
    "legal_document": "Văn bản pháp luật",
    "model": "Model AI",
    "retention_job": "Tác vụ lưu trữ",
    "support_ticket": "Phiên hỗ trợ",
    "system_settings": "Cấu hình hệ thống",
    "user_account": "Tài khoản",
}


def _activity_type(row: dict[str, Any]) -> str:
    action = str(row.get("action") or "").casefold()
    entity = str(row.get("entity_type") or "").casefold()
    value = f"{action} {entity}"
    if "support" in value:
        return "support"
    if any(term in value for term in ("sensitive_view", "activity.export", "audit_event")):
        return "sensitive_access"
    if any(term in value for term in ("auth", "user", "session", "password", "mfa", "account")):
        return "accounts"
    if any(term in value for term in ("faq", "form", "procedure")):
        return "governance"
    if any(term in value for term in ("crawl", "import", "ocr", "extract", "source")):
        return "data_ingestion"
    if any(term in value for term in ("model", "credential", "provider", "config", "setting", "api_key")):
        return "configuration"
    if any(term in value for term in ("legal", "document", "lifecycle", "validity")):
        return "legal_documents"
    return "system"


def _action_label(row: dict[str, Any]) -> str:
    action = str(row.get("action") or "system.activity")
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    lowered = action.casefold()
    if any(term in lowered for term in ("failed", "error", "denied", "blocked", "rejected")):
        return "Thao tác không thành công"
    return "Hoạt động hệ thống"


def _actor_label(row: dict[str, Any]) -> str:
    actor = str(row.get("actor_user") or "system")
    role = str(row.get("actor_role") or "system").casefold()
    if actor.casefold() == "system" or role == "system":
        return "Hệ thống"
    role_labels = {"admin": "Admin", "officer": "Cán bộ", "citizen": "Người dân"}
    short_actor = actor.rsplit(":", 1)[-1]
    if short_actor.casefold() in {"admin", "officer", "citizen"}:
        return role_labels.get(role, short_actor)
    return short_actor or role_labels.get(role, "Người dùng")


def _module(row: dict[str, Any]) -> str:
    action = str(row.get("action") or "").casefold()
    entity = str(row.get("entity_type") or "").casefold()
    value = f"{action} {entity}"
    if "support" in value:
        return "support"
    if any(term in value for term in ("legal", "form", "faq", "crawl", "import")):
        return "legal_data"
    if any(term in value for term in ("model", "credential", "provider")):
        return "model_setup"
    if any(term in value for term in ("user", "auth", "session", "password", "mfa")):
        return "security"
    if "config" in value:
        return "configuration"
    return "system"


def _result(row: dict[str, Any]) -> str:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    explicit = str(details.get("result") or details.get("status") or details.get("outcome") or "").casefold()
    if explicit in {"success", "succeeded", "ok", "passed", "completed"}:
        return "success"
    if explicit in {"failed", "error", "denied", "blocked", "rejected"}:
        return "failed"
    action = str(row.get("action") or "").casefold()
    return "failed" if any(term in action for term in ("fail", "error", "denied", "blocked", "reject")) else "success"


def _project(row: dict[str, Any]) -> dict[str, Any]:
    resource_type = str(row.get("entity_type") or "system")
    return {
        "id": str(row.get("id") or ""),
        "occurred_at": str(row.get("created") or ""),
        "actor": str(row.get("actor_user") or "system"),
        "actor_label": _actor_label(row),
        "actor_role": str(row.get("actor_role") or "system"),
        "activity_type": _activity_type(row),
        "module": _module(row),
        "result": _result(row),
        "action": str(row.get("action") or "system.activity"),
        "action_label": _action_label(row),
        "resource_type": resource_type,
        "resource_label": RESOURCE_LABELS.get(resource_type, "Dữ liệu hệ thống"),
        "resource_id": str(row.get("entity_id") or ""),
        "sensitive_detail_available": bool(row.get("details") or row.get("ip_address") or row.get("user_agent")),
    }


def _cursor(index: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"offset": index}).encode("utf-8")).decode("ascii").rstrip("=")


def _cursor_offset(value: str | None) -> int:
    if not value:
        return 0
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return max(0, int(payload["offset"]))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="ACTIVITY_CURSOR_INVALID") from exc


async def _require_admin(request: Request) -> str:
    if get_request_role(request) != "admin":
        raise HTTPException(status_code=403, detail="ADMIN_ROLE_REQUIRED")
    return str(get_request_user_id(request) or "")


async def _filtered_rows(
    *, role: str | None = None, module: str | None = None, result: str | None = None,
    actor: str | None = None, search: str | None = None,
    activity_type: str | None = None, date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    rows = await list_audit_logs(
        limit=5001,
        actor_role=role,
        date_from=date_from,
        date_to=date_to,
    )
    if actor:
        rows = [row for row in rows if str(row.get("actor_user") or "") == actor]
    if module:
        rows = [row for row in rows if _module(row) == module]
    if result:
        rows = [row for row in rows if _result(row) == result]
    if activity_type:
        rows = [row for row in rows if _activity_type(row) == activity_type]
    if search:
        needle = search.strip().casefold()
        rows = [
            row for row in rows
            if needle in " ".join(
                str(value).casefold()
                for value in (
                    _action_label(row), _actor_label(row), row.get("action"),
                    row.get("actor_user"), row.get("entity_type"), row.get("entity_id"),
                )
            )
        ]
    return rows


@router.get("")
async def list_activity(
    request: Request,
    role: str | None = None,
    module: str | None = None,
    result: Literal["success", "failed"] | None = None,
    actor: str | None = None,
    search: str | None = None,
    activity_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    await _require_admin(request)
    rows = await _filtered_rows(
        role=role, module=module, result=result, actor=actor, search=search,
        activity_type=activity_type,
        date_from=date_from, date_to=date_to,
    )
    offset = _cursor_offset(cursor)
    page = rows[offset:offset + limit]
    next_offset = offset + len(page)
    return {
        "items": [_project(row) for row in page],
        "total": len(rows),
        "next_cursor": _cursor(next_offset) if next_offset < len(rows) else None,
        "filters": {
            "role": role, "module": module, "result": result, "actor": actor,
            "search": search, "activity_type": activity_type,
            "from": date_from, "to": date_to,
        },
        "content_policy": "metadata_only",
    }


SENSITIVE_KEYS = {
    "api_key", "token", "secret", "password", "credential", "authorization",
    "cookie", "private_key", "refresh_token", "access_token",
}


def _meaningful_access_reason(value: str) -> bool:
    normalized = " ".join(value.split())
    tokens = re.findall(r"[^\W_]+", normalized.casefold(), flags=re.UNICODE)
    characters = [character for character in normalized.casefold() if character.isalnum()]
    return (
        len(normalized) >= 12
        and len(tokens) >= 3
        and len(set(tokens)) >= 3
        and len(set(characters)) >= 4
    )


def _redact(value: Any, key: str = "") -> Any:
    if key.casefold() in SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


@router.post("/{event_id}/sensitive-view")
async def view_sensitive_activity(
    event_id: str,
    body: SensitiveViewRequest,
    request: Request,
) -> dict[str, Any]:
    actor_id = await _require_admin(request)
    reason = body.reason.strip()
    if not _meaningful_access_reason(reason):
        raise HTTPException(status_code=400, detail="ACTIVITY_REASON_NOT_MEANINGFUL")
    rows = await list_audit_logs(limit=5001)
    row = next((item for item in rows if str(item.get("id")) == event_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="ACTIVITY_EVENT_NOT_FOUND")
    await write_audit_log(
        action="admin.activity.sensitive_view",
        entity_type="audit_event",
        entity_id=event_id,
        actor_user_id=actor_id,
        actor_role="admin",
        details={"reason": reason},
        request=request,
    )
    return {
        **_project(row),
        "details": _redact(row.get("details") or {}),
        "ip_address": "[REDACTED]" if row.get("ip_address") else None,
        "user_agent": "[REDACTED]" if row.get("user_agent") else None,
        "access_reason": reason,
    }


@router.get("/export")
async def export_activity(
    request: Request,
    format: Literal["csv", "xlsx", "pdf"] = "csv",
    role: str | None = None,
    module: str | None = None,
    result: Literal["success", "failed"] | None = None,
    actor: str | None = None,
    search: str | None = None,
    activity_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> StreamingResponse:
    actor_id = await _require_admin(request)
    rows = await _filtered_rows(
        role=role, module=module, result=result, actor=actor,
        search=search, activity_type=activity_type,
        date_from=date_from, date_to=date_to,
    )
    if len(rows) > 5000:
        raise HTTPException(status_code=422, detail="ACTIVITY_EXPORT_FILTER_REQUIRED")
    projected = [_project(row) for row in rows]
    export_rows = [{
        "Thời điểm": item["occurred_at"], "Người thực hiện": item["actor_label"],
        "Hoạt động": item["action_label"], "Kết quả": item["result"],
        "Loại dữ liệu": item["resource_label"],
        "Định danh": item["resource_id"], "Lý do": "",
    } for item in projected]
    from api.routers.admin_control import _audit_export_bytes

    payload, media_type = _audit_export_bytes(export_rows, format)
    checksum = hashlib.sha256(payload).hexdigest()
    await write_audit_log(
        action="admin.activity.export",
        entity_type="audit_event",
        entity_id=checksum[:16],
        actor_user_id=actor_id,
        actor_role="admin",
        details={
            "result": "success",
            "format": format,
            "record_count": len(export_rows),
            "filters_applied": bool(search or activity_type or role or module or result or actor or date_from or date_to),
        },
        request=request,
    )
    return StreamingResponse(
        io.BytesIO(payload), media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="activity.{format}"',
            "X-Content-SHA256": checksum,
            "X-Record-Count": str(len(export_rows)),
        },
    )
