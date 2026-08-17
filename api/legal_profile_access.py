"""Centralized access policy for role-scoped legal profiles (notebooks, notes, sources, chats).

Policy is fail-closed for citizens and legacy officer sessions without a real account ID.
Admin may list metadata, but opening content/download/export requires a business reason
and writes a sensitive_access_audit record before data is returned.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import HTTPException, Request
from loguru import logger

from api.auth import get_request_role, get_request_user_id, get_request_username
from open_notebook.database.repository import ensure_record_id, repo_create, repo_query

Action = Literal["list", "view", "download", "export", "write"]
LEGAL_PROFILE_ROLES = frozenset({"officer", "admin"})

SUPPORT_TICKETS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "support_tickets"
)

CITIZEN_DENIAL = "Hồ sơ pháp lý chỉ dành cho cán bộ và quản trị viên."
OFFICER_DENIAL = "Bạn chỉ được truy cập hồ sơ pháp lý thuộc tài khoản của chính mình."
REASON_REQUIRED = "Vui lòng nhập lý do nghiệp vụ (ít nhất 10 ký tự) trước khi mở nội dung hồ sơ."


def _record_text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _same_user(left: Any, right: str | None) -> bool:
    if not right:
        return False
    actual = _record_text(left)
    if not actual:
        return False
    return actual == right or actual == f"user_account:{right}"


def _clean_id(value: str) -> str:
    return value.split(":", 1)[1] if ":" in value else value


def _record_id(table: str, value: str):
    """Accept API ids with or without their SurrealDB table prefix."""
    text = str(value).strip()
    return ensure_record_id(text if ":" in text else f"{table}:{text}")


def _actor(request: Request) -> tuple[str, str | None]:
    return get_request_role(request), get_request_user_id(request)


def _require_business_reason(request: Request) -> str:
    # Header is preferred; query fallback supports direct file links. Some unit
    # requests omit query_string, hence inspect scope defensively.
    query_reason = request.query_params.get("business_reason") if request.scope.get("query_string") else None
    reason = (request.headers.get("X-Business-Reason") or query_reason or "").strip()
    if len(reason) < 10:
        raise HTTPException(status_code=422, detail=REASON_REQUIRED)
    if len(reason) > 1000:
        raise HTTPException(status_code=422, detail="Lý do nghiệp vụ không được vượt quá 1000 ký tự.")
    return reason


async def _write_audit(
    request: Request,
    *,
    resource_type: str,
    resource_id: str,
    action: str,
    reason: str,
) -> None:
    """Fail closed for audited admin reads: no audit, no sensitive data release."""
    actor_id = get_request_user_id(request)
    forwarded_for = request.headers.get("x-forwarded-for")
    request_metadata = {
        "actor_role": get_request_role(request),
        "actor_username": get_request_username(request),
        "ip_address": (forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else None)),
        "user_agent": request.headers.get("user-agent"),
        "request_method": request.method,
        "request_path": request.url.path,
    }
    try:
        await repo_create(
            "sensitive_access_audit",
            {
                "actor": _record_id("user_account", actor_id) if actor_id else None,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "reason": reason,
                "action": action,
                "request_metadata": request_metadata,
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(days=730),
            },
        )
    except Exception as exc:  # Audit is mandatory, therefore deny on storage failure.
        logger.error("Failed to write sensitive access audit: {}", exc)
        raise HTTPException(
            status_code=503,
            detail="Không thể ghi nhật ký truy cập nhạy cảm; nội dung chưa được mở.",
        ) from exc


async def _officer_has_shared_notebook(user_id: str, notebook_id: str) -> bool:
    """Check citizen's explicit JSON ticket share for the officer assigned to that ticket."""
    if not os.path.isdir(SUPPORT_TICKETS_DIR):
        return False
    target = _clean_id(notebook_id)
    for filename in os.listdir(SUPPORT_TICKETS_DIR):
        if not filename.endswith(".json"):
            continue
        try:
            with open(os.path.join(SUPPORT_TICKETS_DIR, filename), encoding="utf-8") as handle:
                ticket = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if not _same_user(ticket.get("assigned_officer_id"), user_id):
            continue
        shared = ticket.get("shared_notebook_ids") or ticket.get("linked_notebook_ids") or []
        if any(_clean_id(str(item)) == target for item in shared):
            return True
    return False


async def _get_record(table: str, resource_id: str) -> dict[str, Any]:
    rows = await repo_query(
        f"SELECT id, owner_user, ownership_status, source_scope FROM $resource_id;",
        {"resource_id": _record_id(table.lower(), resource_id)},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"{table} not found")
    return rows[0]


async def assert_notebook_access(
    notebook_id: str,
    request: Request,
    *,
    action: Action = "view",
) -> dict[str, Any]:
    record = await _get_record("Notebook", notebook_id)
    role, user_id = _actor(request)
    if role not in LEGAL_PROFILE_ROLES:
        raise HTTPException(status_code=403, detail=CITIZEN_DENIAL)
    if not user_id:
        raise HTTPException(status_code=403, detail=OFFICER_DENIAL)
    if record.get("ownership_status") == "needs_admin_review":
        raise HTTPException(status_code=403, detail="Hồ sơ chưa xác định chủ sở hữu, chờ quản trị viên xử lý.")
    if _same_user(record.get("owner_user"), user_id) or role == "admin":
        return record
    raise HTTPException(status_code=403, detail=OFFICER_DENIAL)


async def assert_source_access(source_id: str, request: Request, *, action: Action = "view") -> dict[str, Any]:
    record = await _get_record("Source", source_id)
    role, user_id = _actor(request)
    if role not in LEGAL_PROFILE_ROLES:
        raise HTTPException(status_code=403, detail=CITIZEN_DENIAL)
    if not user_id:
        raise HTTPException(status_code=403, detail=OFFICER_DENIAL)
    if record.get("ownership_status") == "needs_admin_review":
        raise HTTPException(status_code=403, detail="Tài liệu chưa xác định chủ sở hữu, chờ quản trị viên xử lý.")
    if _same_user(record.get("owner_user"), user_id) or role == "admin":
        return record
    if record.get("source_scope") == "shared-admin-reviewed" and action in ("view", "download", "export"):
        return record
    raise HTTPException(status_code=403, detail=OFFICER_DENIAL)


async def assert_note_access(note_id: str, request: Request, *, action: Action = "view") -> dict[str, Any]:
    record = await _get_record("Note", note_id)
    role, user_id = _actor(request)
    if role not in LEGAL_PROFILE_ROLES:
        raise HTTPException(status_code=403, detail=CITIZEN_DENIAL)
    if not user_id or record.get("ownership_status") == "needs_admin_review":
        raise HTTPException(status_code=403, detail=OFFICER_DENIAL)
    if not _same_user(record.get("owner_user"), user_id):
        raise HTTPException(status_code=403, detail=OFFICER_DENIAL)
    return record


async def assert_chat_session_access(session_id: str, request: Request, *, action: Action = "view") -> dict[str, Any]:
    full_session_id = session_id if session_id.startswith("chat_session:") else f"chat_session:{session_id}"
    rows = await repo_query(
        "SELECT id, owner_user, ownership_status FROM $session_id;",
        {"session_id": ensure_record_id(full_session_id)},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Session not found")
    role, user_id = _actor(request)
    if role not in LEGAL_PROFILE_ROLES:
        raise HTTPException(status_code=403, detail=CITIZEN_DENIAL)
    if not user_id or rows[0].get("ownership_status") == "needs_admin_review":
        raise HTTPException(status_code=403, detail=OFFICER_DENIAL)
    record = rows[0]
    if _same_user(record.get("owner_user"), user_id):
        return record

    # Sessions created by the former admin path have no owner_user. Recover
    # access only through an owned, resolved notebook relationship; never grant
    # access to an ownerless or unrelated session.
    if record.get("owner_user") is None:
        notebook_ids = await repo_query(
            "SELECT VALUE out FROM refers_to WHERE in = $session_id LIMIT 1;",
            {"session_id": ensure_record_id(full_session_id)},
        )
        if notebook_ids:
            notebook = await _get_record("Notebook", str(notebook_ids[0]))
            if (
                notebook.get("ownership_status") != "needs_admin_review"
                and _same_user(notebook.get("owner_user"), user_id)
            ):
                return record

    raise HTTPException(status_code=403, detail=OFFICER_DENIAL)


async def assert_legal_case_access(
    case_id: str,
    request: Request,
    *,
    action: Action = "view",
) -> dict[str, Any]:
    """Authorize an account-scoped legal case.

    A citizen may only open its own case. An officer may only open a case
    explicitly assigned to that officer. Admin access remains auditable and
    requires a business reason before a sensitive detail/download is released.
    """
    rows = await repo_query(
        "SELECT id, owner_user, assigned_officer, ownership_status FROM $case_id;",
        {"case_id": _record_id("legal_case", case_id)},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Legal case not found")
    record = rows[0]
    role, user_id = _actor(request)
    if role == "admin":
        if action in {"view", "download", "export"}:
            reason = _require_business_reason(request)
            await _write_audit(
                request,
                resource_type="legal_case",
                resource_id=_clean_id(case_id),
                action=action,
                reason=reason,
            )
        return record
    if not user_id or record.get("ownership_status") == "needs_admin_review":
        raise HTTPException(status_code=403, detail=OFFICER_DENIAL)
    if role == "citizen":
        if _same_user(record.get("owner_user"), user_id):
            return record
        raise HTTPException(status_code=403, detail="Bạn chỉ được truy cập hồ sơ của chính mình.")
    if role == "officer" and _same_user(record.get("assigned_officer"), user_id):
        return record
    raise HTTPException(status_code=403, detail=OFFICER_DENIAL)


async def assert_legal_profile_list_access(request: Request) -> tuple[str, str | None]:
    role, user_id = _actor(request)
    if role not in LEGAL_PROFILE_ROLES:
        raise HTTPException(status_code=403, detail=CITIZEN_DENIAL)
    if not user_id:
        raise HTTPException(status_code=403, detail=OFFICER_DENIAL)
    return role, user_id
