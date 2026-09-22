"""Admin-only backup, verification and isolated restore-drill endpoints."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.admin_backup_service import (
    create_backup_job,
    get_backup,
    list_backups,
    preflight,
    restore_drill,
    run_backup,
    verify_backup,
)
from api.auth import get_request_role, get_request_user_id
from api.user_service import write_audit_log


router = APIRouter(prefix="/admin/backups", tags=["admin-backups"])
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


class BackupCreateRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    kind: Literal["full", "application", "retrieval"] = "full"


async def _require_admin(request: Request) -> str:
    if get_request_role(request) != "admin":
        raise HTTPException(status_code=403, detail="ADMIN_ROLE_REQUIRED")
    return str(get_request_user_id(request) or "admin")


async def _audit(
    request: Request,
    *,
    action: str,
    backup_id: str,
    actor_id: str,
    reason: str | None = None,
    result: str | None = None,
) -> None:
    details: dict[str, Any] = {"manifest_id": backup_id}
    if reason:
        details["reason"] = reason[:1000]
    if result:
        details["result"] = result
    try:
        await write_audit_log(
            action=action,
            entity_type="backup_manifest",
            entity_id=backup_id,
            actor_user_id=actor_id,
            actor_role="admin",
            details=details,
            request=request,
        )
    except Exception:
        # A backup result must remain visible even if an optional audit sink is
        # temporarily unavailable. The API log can still correlate the ID.
        return


async def _run_background(backup_id: str, actor_id: str, request: Request) -> None:
    result = await asyncio.to_thread(run_backup, backup_id)
    await _audit(
        request,
        action="admin.backup.completed",
        backup_id=backup_id,
        actor_id=actor_id,
        result=str(result.get("status") or "unknown"),
    )


def _remember(task: asyncio.Task[Any]) -> None:
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


@router.get("/preflight")
async def backup_preflight(request: Request) -> dict[str, Any]:
    await _require_admin(request)
    return preflight()


@router.get("")
async def backups(request: Request) -> dict[str, Any]:
    await _require_admin(request)
    rows = list_backups()
    return {"items": rows, "count": len(rows)}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_backup(payload: BackupCreateRequest, request: Request) -> dict[str, Any]:
    actor_id = await _require_admin(request)
    try:
        manifest = create_backup_job(reason=payload.reason, actor_id=actor_id, kind=payload.kind)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    backup_id = str(manifest["backup_id"])
    await _audit(
        request,
        action="admin.backup.create",
        backup_id=backup_id,
        actor_id=actor_id,
        reason=payload.reason,
        result="planned",
    )
    task = asyncio.create_task(_run_background(backup_id, actor_id, request))
    _remember(task)
    return {
        "backup_id": backup_id,
        "status": "planned",
        "message": "Đã xếp bản sao vào hàng đợi xử lý.",
    }


@router.get("/{backup_id}")
async def backup_status(backup_id: str, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    try:
        payload = get_backup(backup_id)
        # Polling should remain small even when a backup contains millions of
        # file records. The complete redacted manifest has its own endpoint.
        payload["file_count"] = len(payload.get("files") or [])
        payload.pop("files", None)
        payload.pop("safe_config", None)
        return payload
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="BACKUP_NOT_FOUND") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail="BACKUP_MANIFEST_INVALID") from exc


@router.post("/{backup_id}/verify")
async def verify_backup_route(backup_id: str, request: Request) -> dict[str, Any]:
    actor_id = await _require_admin(request)
    try:
        result = await asyncio.to_thread(verify_backup, backup_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="BACKUP_NOT_FOUND") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _audit(
        request,
        action="admin.backup.verify",
        backup_id=backup_id,
        actor_id=actor_id,
        result="passed" if result.get("passed") else "failed",
    )
    return result


@router.post("/{backup_id}/restore-drill")
async def restore_drill_route(backup_id: str, request: Request) -> dict[str, Any]:
    actor_id = await _require_admin(request)
    try:
        result = await asyncio.to_thread(restore_drill, backup_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="BACKUP_NOT_FOUND") from exc
    await _audit(
        request,
        action="admin.backup.restore_drill",
        backup_id=backup_id,
        actor_id=actor_id,
        result=str(result.get("status") or "failed"),
    )
    return result


@router.get("/{backup_id}/manifest")
async def backup_manifest(backup_id: str, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    try:
        return get_backup(backup_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="BACKUP_NOT_FOUND") from exc
