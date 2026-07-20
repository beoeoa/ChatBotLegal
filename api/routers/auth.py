"""Authentication router for Open Notebook API."""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.auth import allowed_roles_for_password, configured_role_passwords
from api.user_service import (
    authenticate_user_account,
    create_password_reset_request,
    ensure_bootstrap_admin_user,
    has_real_users,
    register_citizen_account,
    reset_password_with_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    identifier: Optional[str] = Field(
        default=None, description="Username hoặc email cho đăng nhập theo user"
    )
    password: str = Field(min_length=1)
    # Kept optional for backward compatibility with old clients. Public UI must
    # not ask for this; the account itself decides the actual role.
    role: Optional[Literal["citizen", "officer", "admin"]] = None


class LoginResponse(BaseModel):
    authenticated: bool
    role: Literal["citizen", "officer", "admin"]
    token: str
    auth_mode: Literal["legacy_password", "user_session"]
    user_id: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    must_change_password: bool = False


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=6, max_length=256)
    full_name: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=50)
    ward: Optional[str] = Field(default=None, max_length=255)


class ForgotPasswordRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=255)


class ForgotPasswordResponse(BaseModel):
    success: bool
    message: str
    reset_token: Optional[str] = None
    expires_in_minutes: Optional[int] = None


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=16)
    new_password: str = Field(min_length=12, max_length=256)


@router.get("/status")
async def get_auth_status():
    """
    Check if authentication is enabled.
    Returns whether a password is required to access the API.
    Supports Docker secrets via OPEN_NOTEBOOK_PASSWORD_FILE.
    """
    await ensure_bootstrap_admin_user()
    passwords = configured_role_passwords()
    auth_enabled = bool(passwords) or await has_real_users()

    return {
        "auth_enabled": auth_enabled,
        # Public clients must not advertise privileged roles. Real user roles are
        # resolved by the account returned after login.
        "available_roles": ["citizen"],
        "message": "Authentication is required"
        if auth_enabled
        else "Authentication is disabled",
    }


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest):
    await ensure_bootstrap_admin_user()
    passwords = configured_role_passwords()

    if payload.identifier and payload.identifier.strip():
        auth_result = await authenticate_user_account(
            payload.identifier,
            payload.password,
            payload.role,
        )
        user = auth_result["user"]
        return LoginResponse(
            authenticated=True,
            role=auth_result["role"],
            token=auth_result["token"],
            auth_mode="user_session",
            user_id=user["id"],
            username=user["username"],
            email=user["email"],
            must_change_password=bool(
                (user.get("profile") or {}).get("must_change_password", False)
            ),
        )

    if not passwords:
        resolved_role = payload.role or "citizen"
        return LoginResponse(
            authenticated=True,
            role=resolved_role,
            token="not-required",
            auth_mode="legacy_password",
        )

    allowed_roles = allowed_roles_for_password(payload.password)
    if not allowed_roles:
        raise HTTPException(status_code=401, detail="Invalid password")
    requested_role = payload.role or "citizen"
    if requested_role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{requested_role}' is not allowed for this password",
        )

    return LoginResponse(
        authenticated=True,
        role=requested_role,
        token=payload.password,
        auth_mode="legacy_password",
    )


@router.post("/register", response_model=LoginResponse, status_code=201)
async def register(payload: RegisterRequest, request: Request):
    await ensure_bootstrap_admin_user()
    user = await register_citizen_account(payload.model_dump(), request=request)
    auth_result = await authenticate_user_account(
        payload.username,
        payload.password,
        "citizen",
    )
    return LoginResponse(
        authenticated=True,
        role="citizen",
        token=auth_result["token"],
        auth_mode="user_session",
        user_id=user["id"],
        username=user["username"],
        email=user["email"],
        must_change_password=False,
    )


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(payload: ForgotPasswordRequest, request: Request):
    await ensure_bootstrap_admin_user()
    return await create_password_reset_request(payload.identifier, request=request)


@router.post("/reset-password")
async def reset_password(payload: ResetPasswordRequest, request: Request):
    await ensure_bootstrap_admin_user()
    await reset_password_with_token(payload.token, payload.new_password, request=request)
    return {
        "success": True,
        "message": "Đã đặt lại mật khẩu. Bạn có thể đăng nhập bằng mật khẩu mới.",
    }
