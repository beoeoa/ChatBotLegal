from datetime import datetime
from typing import Any, Literal, Optional
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Query, Request, Header
from pydantic import BaseModel, ConfigDict, Field

from api.auth import (
    get_request_auth_mode,
    get_request_role,
    get_request_user_id,
    get_request_username,
)
from api.user_service import (
    create_user_account,
    deactivate_user_account,
    soft_delete_user_account,
    ensure_bootstrap_admin_user,
    get_user_with_profile,
    has_real_users,
    list_ask_history,
    list_audit_logs,
    list_users_with_profiles,
    update_user_account,
    change_own_password,
    admin_reset_user_password,
    write_audit_log,
)

router = APIRouter(prefix="/users", tags=["users"])


class UserProfilePayload(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    ward: Optional[str] = Field("Phường Lê Chân, Hải Phòng", description="Phường pilot áp dụng")
    department: Optional[str] = None
    organization_unit_id: Optional[str] = Field(default=None, max_length=120)
    allowed_domains: Optional[list[str]] = Field(default_factory=list, description="Lĩnh vực được phép")
    job_title: Optional[str] = None
    notes: Optional[str] = None
    must_change_password: Optional[bool] = None
    preferences: dict[str, Any] = Field(default_factory=dict)


class UserCreateRequest(UserProfilePayload):
    username: str = Field(min_length=3)
    email: str
    password: str = Field(min_length=12, max_length=256)
    role: Literal["citizen", "officer", "admin"] = "citizen"
    is_active: bool = True


class UserUpdateRequest(UserProfilePayload):
    username: Optional[str] = Field(default=None, min_length=3)
    email: Optional[str] = None
    password: Optional[str] = Field(default=None, min_length=12, max_length=256)
    role: Optional[Literal["citizen", "officer", "admin"]] = None
    is_active: Optional[bool] = None


class UserSelfUpdateRequest(BaseModel):
    """Fields an authenticated user may change without administrator approval."""

    model_config = ConfigDict(extra="forbid")

    full_name: Optional[str] = None
    phone: Optional[str] = None
    ward: Optional[str] = Field(
        "Phường Lê Chân, Hải Phòng",
        description="Phường pilot áp dụng",
    )


class ChangeOwnPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=12, max_length=256)


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)


class OfficerUnitGrantRequest(BaseModel):
    organization_unit_id: str = Field(..., min_length=1, max_length=120)
    domain_codes: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(..., min_length=5, max_length=1000)
    expires_at: datetime


def _require_business_reason(reason: str | None) -> str:
    value = unquote(reason or "").strip()
    if len(value) < 3:
        raise HTTPException(
            status_code=400,
            detail="Cần nhập lý do nghiệp vụ cho thao tác nhạy cảm",
        )
    return value


async def _require_admin(request: Request) -> None:
    if not await has_real_users():
        return
    if get_request_role(request) != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")


@router.get("/me")
async def get_me(request: Request):
    await ensure_bootstrap_admin_user()
    user_id = get_request_user_id(request)
    role = get_request_role(request)
    if not user_id:
        return {
            "id": None,
            "username": get_request_username(request),
            "email": None,
            "role": role,
            "is_active": True,
            "auth_mode": get_request_auth_mode(request),
            "profile": {},
        }

    user = await get_user_with_profile(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return {**user, "auth_mode": get_request_auth_mode(request)}


@router.get("/me/operating-scope")
async def get_my_operating_scope(request: Request):
    from api.organization_service import resolve_officer_scope
    from api.system_settings import (
        active_organization_units,
        active_settings,
        legal_domain_labels,
        normalize_legal_domains,
    )

    user_id = get_request_user_id(request)
    if get_request_role(request) != "officer" or not user_id:
        raise HTTPException(status_code=403, detail="Cần đăng nhập bằng tài khoản cán bộ.")
    user = await get_user_with_profile(user_id)
    if not user or user.get("role") != "officer" or user.get("is_active") is False or user.get("is_deleted"):
        raise HTTPException(status_code=403, detail="Tài khoản cán bộ không hoạt động.")
    settings = await active_settings()
    units = await active_organization_units(settings)
    scope = await resolve_officer_scope(user_id=user_id, profile=user.get("profile") or {}, units=units, mode=getattr(settings, "organization_routing_mode", "legacy"))
    support_scope = await resolve_officer_scope(user_id=user_id, profile=user.get("profile") or {}, units=units, mode=scope.mode, support_only=True)
    names = {unit.id: unit.name for unit in units}
    domain_labels = legal_domain_labels(
        normalize_legal_domains(getattr(settings, "legal_domains", None))
    )
    return {
        "mode": scope.mode,
        "primary_organization_unit_id": scope.primary_organization_unit_id,
        "primary_organization_unit_name": names.get(scope.primary_organization_unit_id),
        "domains": list(scope.domains),
        # Include every retained label, not only active assignments. Proposal
        # history continues to show an administrator's latest rename after a
        # domain has been retired, while authorization still comes from
        # ``scope.domains`` above.
        "domain_labels": domain_labels,
        "can_manage_content": bool(scope.domains),
        "can_receive_support": bool(support_scope.domains) and ((user.get("profile") or {}).get("preferences") or {}).get("can_receive_live_support") is not False,
        "organization_unit_ids": list(scope.organization_unit_ids),
        "organization_unit_domain_grants": list(scope.organization_unit_domain_grants),
        "proposal_units": [
            {"id": unit_id, "name": names.get(unit_id, unit_id),
             "domains": [domain for pair_unit, domain in scope.proposal_unit_domains if pair_unit == unit_id]}
            for unit_id in dict.fromkeys(pair_unit for pair_unit, _ in scope.proposal_unit_domains)
        ],
    }


@router.post("/me/change-password")
async def change_my_password(request: Request, payload: ChangeOwnPasswordRequest):
    user_id = get_request_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail="Chế độ đăng nhập cũ không hỗ trợ đổi mật khẩu tài khoản riêng",
        )
    await change_own_password(
        user_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
        request=request,
    )
    return {"success": True, "message": "Đã đổi mật khẩu. Vui lòng đăng nhập lại."}


@router.post("/{user_id}/reset-password")
async def reset_user_password(request: Request, user_id: str, payload: AdminResetPasswordRequest, x_business_reason: str | None = Header(default=None)):
    await _require_admin(request)
    reason = _require_business_reason(x_business_reason)
    await admin_reset_user_password(
        user_id,
        new_password=payload.new_password,
        actor_user_id=get_request_user_id(request),
        reason=reason,
        request=request,
    )
    return {
        "success": True,
        "message": "Đã đặt mật khẩu tạm; người dùng phải đổi mật khẩu khi đăng nhập.",
    }


@router.put("/me")
async def update_me(request: Request, payload: UserSelfUpdateRequest):
    user_id = get_request_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail="Chế độ đăng nhập cũ không có hồ sơ user để cập nhật",
        )
    updated = await update_user_account(
        user_id,
        payload.model_dump(exclude_unset=True),
        actor_user_id=user_id,
        actor_role=get_request_role(request),
        request=request,
    )
    return updated


@router.get("")
async def get_users(request: Request):
    await _require_admin(request)
    return await list_users_with_profiles()


@router.post("")
async def create_user(request: Request, payload: UserCreateRequest):
    await _require_admin(request)
    return await create_user_account(
        payload.model_dump(),
        actor_user_id=get_request_user_id(request),
        actor_role=get_request_role(request),
        request=request,
    )


@router.put("/{user_id}")
async def update_user(request: Request, user_id: str, payload: UserUpdateRequest, x_business_reason: str | None = Header(default=None)):
    await _require_admin(request)
    reason = _require_business_reason(x_business_reason)
    return await update_user_account(
        user_id,
        {**payload.model_dump(exclude_unset=True), "_business_reason": reason},
        actor_user_id=get_request_user_id(request),
        actor_role=get_request_role(request),
        request=request,
    )


@router.delete("/{user_id}")
async def delete_user(request: Request, user_id: str, x_business_reason: str | None = Header(default=None)):
    await _require_admin(request)
    reason = _require_business_reason(x_business_reason)
    if user_id == get_request_user_id(request):
        raise HTTPException(status_code=400, detail="Không thể tự vô hiệu hóa chính mình")
    await deactivate_user_account(
        user_id,
        actor_user_id=get_request_user_id(request),
        actor_role=get_request_role(request),
        reason=reason,
        request=request,
    )
    return {"success": True, "message": "Đã khóa tài khoản và kết thúc các phiên đăng nhập đang hoạt động"}


@router.post("/{user_id}/soft-delete")
async def soft_delete_user(
    request: Request,
    user_id: str,
    x_business_reason: str | None = Header(default=None),
):
    await _require_admin(request)
    reason = _require_business_reason(x_business_reason)
    if user_id == get_request_user_id(request):
        raise HTTPException(status_code=400, detail="Không thể tự xóa tài khoản đang đăng nhập")
    await soft_delete_user_account(
        user_id,
        actor_user_id=get_request_user_id(request),
        actor_role=get_request_role(request),
        reason=reason,
        request=request,
    )
    return {
        "success": True,
        "message": "Đã xóa mềm tài khoản; nhật ký quản trị vẫn được lưu giữ.",
    }


@router.get("/audit-logs")
async def get_audit_logs(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    actor_role: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    resource_type: Optional[str] = Query(None),
    resource_id: Optional[str] = Query(None),
    reason: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    await _require_admin(request)
    return await list_audit_logs(
        limit=limit,
        actor_role=actor_role,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        reason=reason,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/ask-history")
async def get_ask_history(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    user_id: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    domain: Optional[str] = Query(None),
    department: Optional[str] = Query(None),
    grounding_status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    await _require_admin(request)
    if user_id and not await get_user_with_profile(user_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

    rows = await list_ask_history(
        limit=limit,
        user_id=user_id,
        role=role,
        domain=domain,
        department=department,
        grounding_status=grounding_status,
        date_from=date_from,
        date_to=date_to,
        summary_only=not bool(user_id),
    )
    if user_id:
        await write_audit_log(
            action="user.ask_history.view",
            entity_type="user_account",
            entity_id=user_id,
            actor_user_id=get_request_user_id(request),
            actor_role=get_request_role(request),
            target_user_id=user_id,
            details={"row_count": len(rows)},
            request=request,
        )
    return rows


@router.get("/{user_id}/unit-grants")
async def list_unit_grants(request: Request, user_id: str):
    await _require_admin(request)
    from api.organization_service import active_officer_grants

    user = await get_user_with_profile(user_id)
    if not user or user.get("role") != "officer":
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản cán bộ.")
    return await active_officer_grants(user_id)


@router.post("/{user_id}/unit-grants", status_code=201)
async def add_unit_grant(
    request: Request, user_id: str, payload: OfficerUnitGrantRequest
):
    await _require_admin(request)
    from api.organization_service import create_officer_grant
    from api.system_settings import active_organization_units, active_settings

    user = await get_user_with_profile(user_id)
    if not user or user.get("role") != "officer":
        raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản cán bộ.")
    settings = await active_settings()
    try:
        grant = await create_officer_grant(
            officer_user_id=user_id,
            organization_unit_id=payload.organization_unit_id,
            domain_codes=payload.domain_codes,
            reason=payload.reason,
            expires_at=payload.expires_at,
            granted_by_user_id=get_request_user_id(request),
            units=await active_organization_units(settings),
        )
    except ValueError as exc:
        messages = {
            "organization_unit_not_found": "Phòng ban hỗ trợ không tồn tại hoặc đã ngừng hoạt động.",
            "officer_unit_grant_expiry_invalid": "Thời hạn hỗ trợ phải ở tương lai.",
            "officer_unit_grant_domain_outside_unit": "Lĩnh vực hỗ trợ không thuộc phòng ban đã chọn.",
        }
        raise HTTPException(status_code=422, detail=messages.get(str(exc), "Không thể cấp quyền hỗ trợ liên phòng.")) from exc
    await write_audit_log(
        action="officer.unit_grant.create",
        entity_type="officer_unit_grant",
        entity_id=str(grant.get("id") or ""),
        actor_user_id=get_request_user_id(request),
        actor_role=get_request_role(request),
        target_user_id=user_id,
        details={
            "organization_unit_id": payload.organization_unit_id,
            "domain_codes": payload.domain_codes,
            "expires_at": payload.expires_at.isoformat(),
            "reason": payload.reason,
        },
        request=request,
    )
    return grant


@router.delete("/{user_id}/unit-grants/{grant_id}")
async def remove_unit_grant(
    request: Request,
    user_id: str,
    grant_id: str,
    x_business_reason: str | None = Header(default=None),
):
    await _require_admin(request)
    reason = _require_business_reason(x_business_reason)
    from api.organization_service import revoke_officer_grant

    grant = await revoke_officer_grant(grant_id, officer_user_id=user_id)
    if not grant:
        raise HTTPException(status_code=404, detail="Không tìm thấy quyền hỗ trợ liên phòng.")
    await write_audit_log(
        action="officer.unit_grant.revoke",
        entity_type="officer_unit_grant",
        entity_id=grant_id,
        actor_user_id=get_request_user_id(request),
        actor_role=get_request_role(request),
        target_user_id=user_id,
        details={
            "reason": reason,
            "organization_unit_id": grant.get("organization_unit_id"),
            "domain_codes": grant.get("domain_codes") or [],
            "expires_at": str(grant.get("expires_at") or ""),
            "granted_by": str(grant.get("granted_by") or ""),
        },
        request=request,
    )
    return {"success": True, "message": "Đã thu hồi quyền hỗ trợ liên phòng."}


@router.get("/{user_id}")
async def get_user_detail(request: Request, user_id: str):
    """Return one profile only to its owner or an administrator.

    This route stays after all static GET routes so ``audit-logs`` and
    ``ask-history`` are never interpreted as user IDs. Cross-account callers
    receive 404 to avoid confirming that an account exists.
    """

    actor_user_id = str(get_request_user_id(request) or "")
    actor_role = str(get_request_role(request) or "").lower()
    if actor_role != "admin" and actor_user_id != str(user_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

    user = await get_user_with_profile(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return user
