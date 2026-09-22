"""Filtered, content-minimized Admin Activity Center for Feature 018."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id
from api.user_service import list_audit_logs, list_users_with_profiles, write_audit_log


router = APIRouter(prefix="/admin/activity", tags=["admin-activity"])


class SensitiveViewRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


_ACTOR_DIRECTORY: dict[str, dict[str, Any]] = {}

ROLE_LABELS = {
    "admin": "Admin",
    "officer": "Cán bộ",
    "citizen": "Người dân",
    "system": "Hệ thống",
}

WORKFLOW_STATUS_LABELS = {
    "queued": "Đang chờ",
    "running": "Đang xử lý",
    "succeeded": "Đã hoàn tất",
    "partial": "Hoàn tất một phần",
    "failed": "Thất bại",
    "blocked": "Bị chặn",
    "cancelled": "Đã hủy",
    "retrying": "Đang thử lại",
}

SEVERITY_LABELS = {
    "info": "Thông tin",
    "warning": "Cần chú ý",
    "critical": "Nghiêm trọng",
}

DETAIL_LABELS = {
    "job_id": "Mã công việc",
    "status": "Trạng thái xử lý",
    "workflow_status": "Trạng thái xử lý",
    "result": "Kết quả",
    "reason": "Lý do",
    "failure_reason": "Nguyên nhân lỗi",
    "record_count": "Số bản ghi",
    "run_count": "Số lượt chạy",
    "created": "Số bản ghi tạo mới",
    "updated": "Số bản ghi cập nhật",
    "skipped": "Số bản ghi bỏ qua",
    "failed": "Số bản ghi thất bại",
    "format": "Định dạng xuất",
    "filters_applied": "Có áp dụng bộ lọc",
    "fingerprint": "Fingerprint",
}

ACTIVITY_EXPORT_COLUMNS = [
    "Thời điểm", "Người thực hiện", "Vai trò", "Đơn vị", "Hoạt động",
    "Đối tượng", "Trạng thái", "Kết quả", "Mức độ", "Mã sự kiện",
    "Mã đối tượng", "Hành động tiếp theo",
]


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
    "admin.backup.create": "Đã tạo công việc sao lưu",
    "admin.backup.completed": "Đã hoàn tất công việc sao lưu",
    "admin.backup.verify": "Đã kiểm chứng bản sao lưu",
    "admin.backup.restore_drill": "Đã diễn tập khôi phục bản sao lưu",
    "admin.activity.sensitive_view": "Đã xem dữ liệu nhạy cảm",
    "admin.activity.export": "Đã xuất nhật ký quản trị",
    "support.ticket.assign_failed": "Phân công hỗ trợ thất bại",
    "admin.config_history.restore": "Đã khôi phục cấu hình",
    "admin.knowledge.candidate.review": "Đã rà soát đề xuất tri thức",
    "admin.legal_case.detail.view": "Đã xem hồ sơ pháp lý",
    "admin.legal_effectivity.run": "Đã chạy kiểm tra hiệu lực văn bản",
    "admin.retention.run": "Đã chạy tác vụ lưu trữ",
    "admin.support.detail.view": "Đã xem chi tiết hỗ trợ",
    "admin.legal_validity.run": "Đã chạy kiểm tra tính hợp lệ văn bản",
    "admin.legal_validity.vector_cleanup": "Đã dọn vector theo quyết định hợp lệ",
    "admin.legal_validity.decision": "Đã ghi nhận quyết định tính hợp lệ",
    "legal.candidate.assess": "Đã đánh giá văn bản chờ nhập",
    "legal.candidate.extract.queue": "Đã đưa văn bản vào hàng chờ trích xuất",
    "legal.candidate.metadata.update": "Đã cập nhật metadata văn bản",
    "legal.candidate.review": "Đã rà soát văn bản chờ nhập",
    "legal.crawl.source.create": "Đã thêm nguồn thu thập",
    "legal.crawl.source.update": "Đã cập nhật nguồn thu thập",
    "legal.crawl.source.delete": "Đã xóa nguồn thu thập",
    "legal.lifecycle.change_candidate_created": "Đã tạo sự kiện vòng đời văn bản",
    "legal.lifecycle.change_event_confirmed": "Đã xác nhận sự kiện vòng đời văn bản",
    "legal.lifecycle.impact_decided": "Đã ghi nhận đánh giá tác động văn bản",
    "support.attachment.download": "Đã tải tệp hỗ trợ",
    "support.chat.messages.view": "Đã xem tin nhắn hỗ trợ",
    "support.chat.view": "Đã xem phiên hỗ trợ",
    "support.ticket.reassign": "Đã chuyển phân công hỗ trợ",
    "user.password.change": "Đã đổi mật khẩu",
    "user.password.reset_by_admin": "Admin đã đặt lại mật khẩu",
    "user.password.reset_requested": "Đã yêu cầu đặt lại mật khẩu",
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
    "backup_manifest": "Bản sao lưu dữ liệu",
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


def _actor_identity(row: dict[str, Any]) -> dict[str, Any]:
    actor = str(row.get("actor_user") or "system")
    directory = _ACTOR_DIRECTORY
    identity = directory.get(actor)
    if identity is None:
        suffix = actor.rsplit(":", 1)[-1]
        identity = directory.get(suffix)
    return identity or {}


def _actor_label(row: dict[str, Any]) -> str:
    actor = str(row.get("actor_user") or "system")
    role = str(row.get("actor_role") or "system").casefold()
    if actor.casefold() == "system" or role == "system":
        return "Hệ thống"
    identity = _actor_identity(row)
    profile = identity.get("profile") if isinstance(identity.get("profile"), dict) else {}
    if str(profile.get("full_name") or "").strip():
        return str(profile["full_name"]).strip()
    if str(identity.get("username") or "").strip():
        return str(identity["username"]).strip()
    short_actor = actor.rsplit(":", 1)[-1]
    if short_actor.casefold() in {"admin", "officer", "citizen"}:
        return ROLE_LABELS.get(role, short_actor)
    # Keep the opaque identifier in the technical/audit payload, never in the
    # primary human-facing label or exported report.
    return f"{ROLE_LABELS.get(role, 'Người dùng')} · tài khoản chưa xác định"


def _workflow_status(row: dict[str, Any]) -> str:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    explicit = str(
        details.get("workflow_status") or details.get("status") or details.get("state") or ""
    ).casefold().replace(" ", "_")
    aliases = {
        "pending": "queued", "waiting": "queued", "in_progress": "running",
        "processing": "running", "success": "succeeded", "completed": "succeeded",
        "complete": "succeeded", "error": "failed", "denied": "blocked",
        "rejected": "blocked", "canceled": "cancelled", "retry": "retrying",
    }
    if explicit in WORKFLOW_STATUS_LABELS:
        return explicit
    if explicit in aliases:
        return aliases[explicit]
    action = str(row.get("action") or "").casefold()
    if any(term in action for term in ("enqueue", "queued", "queue")):
        return "queued"
    if any(term in action for term in ("failed", "error", "denied", "blocked", "reject")):
        return "failed"
    return "succeeded"


def _severity(row: dict[str, Any]) -> str:
    status = _workflow_status(row)
    action = str(row.get("action") or "").casefold()
    if status in {"failed", "blocked"}:
        return "critical"
    if any(term in action for term in ("sensitive", "credential", "delete", "purge", "deactivate")):
        return "warning"
    return "info"


def _business_explanation(row: dict[str, Any]) -> str:
    action = str(row.get("action") or "").casefold()
    if "candidate.import.enqueue" in action:
        return "Văn bản đã được tiếp nhận vào hàng chờ để kiểm tra metadata và nội dung trước khi phục vụ."
    if "retention.purge" in action:
        return "Hệ thống đã chạy chính sách lưu trữ đối với dữ liệu hết hạn theo lịch đã cấu hình."
    if "sensitive_view" in action:
        return "Admin đã mở dữ liệu chi tiết có kiểm soát; lý do truy cập được ghi lại để kiểm toán."
    if "export" in action:
        return "Hệ thống đã tạo một bản xuất nhật ký theo bộ lọc được yêu cầu."
    if "login.failed" in action:
        return "Một lần đăng nhập không thành công đã được ghi nhận để theo dõi bảo mật."
    return _action_label(row)


def _impact(row: dict[str, Any]) -> str:
    action = str(row.get("action") or "").casefold()
    if "candidate.import.enqueue" in action:
        return "Văn bản chưa được dùng làm căn cứ trả lời cho đến khi hoàn tất các cổng kiểm tra."
    if "retention.purge" in action:
        return "Dữ liệu hết hạn có thể đã được xử lý; cần xem số lượng bỏ qua hoặc thất bại nếu có."
    if "release.activate" in action:
        return "Bản phát hành mới có thể ảnh hưởng tới dữ liệu được hiển thị hoặc phục vụ."
    if "credential" in action or "configuration" in action:
        return "Thay đổi có thể ảnh hưởng tới khả năng kết nối và vận hành hệ thống."
    return "Không có ảnh hưởng pháp lý trực tiếp được suy ra từ sự kiện này."


def _next_action(row: dict[str, Any]) -> str:
    status = _workflow_status(row)
    action = str(row.get("action") or "").casefold()
    if status in {"failed", "blocked"}:
        return "Mở chi tiết lỗi, xử lý nguyên nhân và chạy lại theo quyền được cấp."
    if "candidate.import.enqueue" in action:
        return "Mở hàng chờ nhập văn bản để kiểm tra metadata và quyết định bước tiếp theo."
    if "retention.purge" in action:
        return "Kiểm tra báo cáo lưu trữ; chỉ xử lý lại nếu còn bản ghi thất bại."
    if "release.activate" in action:
        return "Kiểm tra manifest và theo dõi trạng thái phát hành sau khi kích hoạt."
    if "export" in action:
        return "Kiểm tra tệp đã tải xuống và lưu checksum nếu báo cáo cần dùng làm bằng chứng."
    return "Không cần hành động tiếp theo nếu trạng thái đã hoàn tất."


def _format_detail_value(value: Any) -> str:
    if value is None or value == "":
        return "Không có"
    if isinstance(value, bool):
        return "Có" if value else "Không"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return f"{len(value)} mục"
    return str(value)


def _detail_rows(row: dict[str, Any], projected: dict[str, Any], details: dict[str, Any]) -> list[dict[str, str]]:
    rows = [
        {"label": "Mã sự kiện", "value": projected["id"]},
        {"label": "Đối tượng", "value": projected["resource_label"]},
    ]
    if projected.get("resource_id"):
        rows.append({"label": "Mã đối tượng", "value": projected["resource_id"]})
    for key, value in details.items():
        if key.casefold() in SENSITIVE_KEYS or value in (None, ""):
            continue
        label = DETAIL_LABELS.get(key, key.replace("_", " ").capitalize())
        if key in {"status", "workflow_status"}:
            value = WORKFLOW_STATUS_LABELS.get(str(value), str(value))
        rows.append({"label": label, "value": _format_detail_value(value)})
    return rows


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
    identity = _actor_identity(row)
    profile = identity.get("profile") if isinstance(identity.get("profile"), dict) else {}
    workflow_status = _workflow_status(row)
    severity = _severity(row)
    return {
        "id": str(row.get("id") or ""),
        "occurred_at": str(row.get("created") or ""),
        "actor": str(row.get("actor_user") or "system"),
        "actor_label": _actor_label(row),
        "actor_role": str(row.get("actor_role") or "system"),
        "actor_role_label": ROLE_LABELS.get(str(row.get("actor_role") or "system").casefold(), "Người dùng"),
        "actor_username": str(identity.get("username") or "") or None,
        "actor_department": str(profile.get("department") or profile.get("ward") or "") or None,
        "actor_job_title": str(profile.get("job_title") or "") or None,
        "activity_type": _activity_type(row),
        "module": _module(row),
        "result": _result(row),
        "workflow_status": workflow_status,
        "workflow_status_label": WORKFLOW_STATUS_LABELS[workflow_status],
        "severity": severity,
        "severity_label": SEVERITY_LABELS[severity],
        "action": str(row.get("action") or "system.activity"),
        "action_label": _action_label(row),
        "resource_type": resource_type,
        "resource_label": RESOURCE_LABELS.get(resource_type, "Dữ liệu hệ thống"),
        "resource_id": str(row.get("entity_id") or ""),
        "explanation": _business_explanation(row),
        "impact": _impact(row),
        "next_action": _next_action(row),
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


async def _refresh_actor_directory() -> None:
    """Load display snapshots without exposing profile data in the activity API."""
    global _ACTOR_DIRECTORY
    try:
        users = await list_users_with_profiles()
    except Exception:
        return
    directory: dict[str, dict[str, Any]] = {}
    for user in users:
        user_id = str(user.get("id") or "").strip()
        username = str(user.get("username") or "").strip()
        source_profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
        profile = {
            key: source_profile.get(key)
            for key in ("full_name", "department", "ward", "job_title")
            if source_profile.get(key) not in (None, "")
        }
        identity = {"username": username, "role": user.get("role"), "profile": profile}
        for key in (user_id, username, f"user:{username}", user_id.rsplit(":", 1)[-1]):
            if key:
                directory[key] = identity
    _ACTOR_DIRECTORY = directory


async def _filtered_rows(
    *, role: str | None = None, module: str | None = None, result: str | None = None,
    actor: str | None = None, search: str | None = None,
    activity_type: str | None = None, date_from: str | None = None,
    date_to: str | None = None, status: str | None = None,
) -> list[dict[str, Any]]:
    rows = await list_audit_logs(
        limit=5001,
        actor_role=role,
        date_from=date_from,
        date_to=date_to,
    )
    await _refresh_actor_directory()
    if actor:
        rows = [row for row in rows if str(row.get("actor_user") or "") == actor]
    if module:
        rows = [row for row in rows if _module(row) == module]
    if result:
        rows = [row for row in rows if _result(row) == result]
    if status:
        rows = [row for row in rows if _workflow_status(row) == status]
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
    status: Literal["queued", "running", "succeeded", "partial", "failed", "blocked", "cancelled", "retrying"] | None = None,
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
        date_from=date_from, date_to=date_to, status=status,
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
            "status": status, "from": date_from, "to": date_to,
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
    await _refresh_actor_directory()
    projected = _project(row)
    redacted_details = _redact(row.get("details") or {})
    detail_rows = _detail_rows(row, projected, redacted_details)
    detail_rows.append({"label": "Lý do truy cập", "value": reason})
    return {
        **projected,
        "details": redacted_details,
        "detail_rows": detail_rows,
        "ip_address": "[REDACTED]" if row.get("ip_address") else None,
        "user_agent": "[REDACTED]" if row.get("user_agent") else None,
        "access_reason": reason,
    }


def _activity_export_xlsx(
    projected: list[dict[str, Any]], *, filters: dict[str, Any], exported_by: str,
) -> tuple[bytes, str]:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Chưa cài thành phần xuất Excel.") from exc

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Tổng quan"
    events = workbook.create_sheet("Nhật ký")
    navy = "1F4E78"
    blue = "4472C4"
    pale = "EAF2F8"
    border = Border(bottom=Side(style="thin", color="D9E2F3"))

    summary.merge_cells("A1:F1")
    summary["A1"] = "BÁO CÁO NHẬT KÝ QUẢN TRỊ"
    summary["A1"].fill = PatternFill("solid", fgColor=navy)
    summary["A1"].font = Font(bold=True, color="FFFFFF", size=14)
    summary["A1"].alignment = Alignment(horizontal="center")
    summary_rows = [
        ("Thời điểm xuất", datetime.now(timezone.utc).astimezone().replace(tzinfo=None)),
        ("Người xuất", exported_by or "Admin"),
        ("Bộ lọc", json.dumps({key: value for key, value in filters.items() if value not in (None, "")}, ensure_ascii=False)),
        ("Số sự kiện", len(projected)),
        ("Thành công", sum(1 for item in projected if item["result"] == "success")),
        ("Thất bại", sum(1 for item in projected if item["result"] == "failed")),
        ("Đang xử lý/chờ", sum(1 for item in projected if item["workflow_status"] in {"queued", "running", "retrying"})),
    ]
    for row_number, (label, value) in enumerate(summary_rows, start=3):
        summary.cell(row_number, 1, label)
        summary.cell(row_number, 2, value)
        summary.cell(row_number, 1).font = Font(bold=True, color="17365D")
        summary.cell(row_number, 1).fill = PatternFill("solid", fgColor=pale)
        summary.cell(row_number, 1).border = border
        summary.cell(row_number, 2).border = border
        summary.cell(row_number, 2).alignment = Alignment(wrap_text=True, vertical="top")
    summary["B3"].number_format = "dd/mm/yyyy hh:mm:ss"
    summary.column_dimensions["A"].width = 24
    summary.column_dimensions["B"].width = 100
    summary.freeze_panes = "A3"

    events.append(ACTIVITY_EXPORT_COLUMNS)
    for cell in events[1]:
        cell.fill = PatternFill("solid", fgColor=blue)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for item in projected:
        events.append([
            item["occurred_at"], item["actor_label"], item["actor_role_label"],
            item.get("actor_department") or "", item["action_label"], item["resource_label"],
            item["workflow_status_label"], "Thành công" if item["result"] == "success" else "Thất bại",
            item["severity_label"], item["id"], item["resource_id"], item["next_action"],
        ])
    events.freeze_panes = "A2"
    events.auto_filter.ref = f"A1:{get_column_letter(len(ACTIVITY_EXPORT_COLUMNS))}{max(1, len(projected) + 1)}"
    widths = [24, 24, 14, 28, 42, 26, 18, 14, 16, 28, 30, 64]
    for index, width in enumerate(widths, start=1):
        events.column_dimensions[get_column_letter(index)].width = width
    for row_number in range(2, events.max_row + 1):
        if row_number % 2 == 0:
            for cell in events[row_number]:
                cell.fill = PatternFill("solid", fgColor="F7FBFD")
        for cell in events[row_number]:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
        status_cell = events.cell(row_number, 7)
        result_cell = events.cell(row_number, 8)
        if status_cell.value in {"Thất bại", "Bị chặn"} or result_cell.value == "Thất bại":
            status_cell.fill = PatternFill("solid", fgColor="FCE4D6")
            result_cell.fill = PatternFill("solid", fgColor="FCE4D6")
        elif status_cell.value in {"Đang chờ", "Đang xử lý", "Đang thử lại", "Cần chú ý"}:
            status_cell.fill = PatternFill("solid", fgColor="FFF2CC")

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/export")
async def export_activity(
    request: Request,
    format: Literal["csv", "xlsx", "pdf"] = "xlsx",
    role: str | None = None,
    module: str | None = None,
    result: Literal["success", "failed"] | None = None,
    status: Literal["queued", "running", "succeeded", "partial", "failed", "blocked", "cancelled", "retrying"] | None = None,
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
        date_from=date_from, date_to=date_to, status=status,
    )
    if len(rows) > 5000:
        raise HTTPException(status_code=422, detail="ACTIVITY_EXPORT_FILTER_REQUIRED")
    projected = [_project(row) for row in rows]
    filters = {
        "role": role, "module": module, "result": result, "status": status,
        "actor": actor, "search": search, "activity_type": activity_type,
        "date_from": date_from, "date_to": date_to,
    }
    if format == "xlsx":
        exporter_identity = _ACTOR_DIRECTORY.get(actor_id, {})
        exporter_profile = exporter_identity.get("profile") if isinstance(exporter_identity.get("profile"), dict) else {}
        exporter_label = str(exporter_profile.get("full_name") or exporter_identity.get("username") or "Admin")
        payload, media_type = _activity_export_xlsx(
            projected, filters=filters, exported_by=exporter_label,
        )
    else:
        export_rows = [{
            "Thời điểm": item["occurred_at"], "Người thực hiện": item["actor_label"],
            "Vai trò": item["actor_role_label"], "Đơn vị": item.get("actor_department") or "",
            "Hoạt động": item["action_label"], "Đối tượng": item["resource_label"],
            "Trạng thái": item["workflow_status_label"],
            "Kết quả": "Thành công" if item["result"] == "success" else "Thất bại",
            "Mức độ": item["severity_label"], "Mã sự kiện": item["id"],
            "Mã đối tượng": item["resource_id"], "Hành động tiếp theo": item["next_action"],
        } for item in projected]
        from api.routers.admin_control import _audit_export_bytes
        payload, media_type = _audit_export_bytes(export_rows, format, columns=ACTIVITY_EXPORT_COLUMNS)
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
            "record_count": len(projected),
            "filters_applied": bool(any(value not in (None, "") for value in filters.values())),
        },
        request=request,
    )
    return StreamingResponse(
        io.BytesIO(payload), media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="nhat-ky-quan-tri.{format}"',
            "X-Content-SHA256": checksum,
            "X-Record-Count": str(len(projected)),
        },
    )
