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
