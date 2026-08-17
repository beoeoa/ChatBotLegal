"""Read-only Admin API for the source-controlled capability registry."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.auth import get_request_role
from api.capability_registry import (
    CapabilityRecord,
    load_registry,
    registry_summary,
)


router = APIRouter(prefix="/admin/capabilities", tags=["admin-capabilities"])


def _require_admin(request: Request) -> None:
    if get_request_role(request) != "admin":
        raise HTTPException(
            status_code=403,
            detail="Chỉ quản trị viên được xem sổ đăng ký chức năng.",
        )


def _public_admin_projection(record: CapabilityRecord) -> dict[str, object]:
    """Return operational metadata without filesystem or secret material."""

    return record.model_dump(mode="json")


@router.get("")
async def list_capabilities(request: Request) -> dict[str, object]:
    _require_admin(request)
    records = load_registry()
    return {
        "summary": registry_summary(records),
        "capabilities": [_public_admin_projection(record) for record in records],
    }


@router.get("/{capability_id}")
async def get_capability(capability_id: str, request: Request) -> dict[str, object]:
    _require_admin(request)
    for record in load_registry():
        if record.capability_id == capability_id:
            return _public_admin_projection(record)
    raise HTTPException(status_code=404, detail="Không tìm thấy chức năng.")

