"""Authentication router for Open Notebook API."""

from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from api.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    allowed_roles_for_password,
    build_csrf_token,
    configured_role_passwords,
    production_mode_enabled,
)
from api.user_service import (
    authenticate_user_account,
    create_password_reset_request,
    enable_user_totp,
    ensure_bootstrap_admin_user,
    get_user_totp_secret,
    get_user_with_profile,
    has_real_users,
    issue_user_session,
    record_failed_login,
    register_citizen_account,
    reset_password_with_token,
    revoke_session_token,
    verify_user_credentials,
)
from api.totp_auth import (
    build_provisioning_uri,
    generate_totp_secret,
    issue_mfa_ticket,
    read_mfa_ticket,
    verify_totp_code,
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
    totp_code: Optional[str] = Field(default=None, min_length=6, max_length=8)


class LoginResponse(BaseModel):
    authenticated: bool
    role: Literal["citizen", "officer", "admin"]
    token: Optional[str] = None
    auth_mode: Literal["legacy_password", "user_session", "cookie_session"]
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


class TotpSetupRequest(BaseModel):
    setup_token: str = Field(min_length=32)


class TotpConfirmRequest(BaseModel):
    confirm_token: str = Field(min_length=32)
    code: str = Field(min_length=6, max_length=8)


def _set_production_session_cookies(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        build_csrf_token(token),
        secure=True,
        httponly=False,
        samesite="strict",
        path="/",
    )


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
async def login(payload: LoginRequest, response: Response):
    await ensure_bootstrap_admin_user()
    passwords = configured_role_passwords()

    if payload.identifier and payload.identifier.strip():
        if production_mode_enabled():
            verified_user = await verify_user_credentials(
                payload.identifier,
                payload.password,
                payload.role,
            )
            if verified_user["role"] in {"officer", "admin"}:
                secret = await get_user_totp_secret(verified_user["id"])
                if not secret:
                    setup_token = issue_mfa_ticket(
                        "setup",
                        {
                            "user_id": verified_user["id"],
                            "username": verified_user["username"],
                            "role": verified_user["role"],
                        },
                    )
                    raise HTTPException(
                        status_code=428,
                        detail={
                            "code": "MFA_SETUP_REQUIRED",
                            "setup_token": setup_token,
                        },
                    )
                if not payload.totp_code:
                    await record_failed_login(
                        payload.identifier,
                        payload.role,
                        "mfa_code_required",
                    )
                    raise HTTPException(
                        status_code=401,
                        detail={"code": "MFA_CODE_REQUIRED"},
                    )
                if not verify_totp_code(secret, payload.totp_code):
                    await record_failed_login(
                        payload.identifier,
                        payload.role,
                        "mfa_code_invalid",
                    )
                    raise HTTPException(
                        status_code=401,
                        detail={"code": "MFA_CODE_INVALID"},
                    )
            auth_result = await issue_user_session(verified_user)
        else:
            auth_result = await authenticate_user_account(
                payload.identifier,
                payload.password,
                payload.role,
            )
        user = auth_result["user"]
        production = production_mode_enabled()
        if production:
            _set_production_session_cookies(response, auth_result["token"])
        return LoginResponse(
            authenticated=True,
            role=auth_result["role"],
            token=None if production else auth_result["token"],
            auth_mode="cookie_session" if production else "user_session",
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
        await record_failed_login(
            f"legacy:{payload.role or 'citizen'}",
            payload.role,
            "invalid_legacy_password",
        )
        raise HTTPException(status_code=401, detail="Invalid password")
    requested_role = payload.role or "citizen"
    if requested_role not in allowed_roles:
        await record_failed_login(
            f"legacy:{requested_role}",
            requested_role,
            "legacy_role_mismatch",
        )
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


@router.post("/totp/setup")
async def setup_totp(payload: TotpSetupRequest):
    try:
        claims = read_mfa_ticket(payload.setup_token, expected_kind="setup")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="MFA_SETUP_TOKEN_INVALID") from exc
    user = await get_user_with_profile(str(claims.get("user_id") or ""))
    if (
        not user
        or user.get("role") not in {"officer", "admin"}
        or user.get("role") != claims.get("role")
        or not user.get("is_active", True)
    ):
        raise HTTPException(status_code=403, detail="MFA_SETUP_NOT_ALLOWED")
    if await get_user_totp_secret(user["id"]):
        raise HTTPException(status_code=409, detail="MFA_ALREADY_ENABLED")
    secret = generate_totp_secret()
    confirm_token = issue_mfa_ticket(
        "confirm",
        {
            "user_id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "secret": secret,
        },
    )
    return {
        "secret": secret,
        "provisioning_uri": build_provisioning_uri(
            secret=secret,
            username=user["username"],
        ),
        "confirm_token": confirm_token,
        "expires_in_seconds": 600,
    }


@router.post("/totp/confirm", response_model=LoginResponse)
async def confirm_totp(payload: TotpConfirmRequest, response: Response):
    try:
        claims = read_mfa_ticket(payload.confirm_token, expected_kind="confirm")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="MFA_CONFIRM_TOKEN_INVALID") from exc
    secret = str(claims.get("secret") or "")
    if not verify_totp_code(secret, payload.code):
        raise HTTPException(status_code=401, detail="MFA_CODE_INVALID")
    user = await get_user_with_profile(str(claims.get("user_id") or ""))
    if (
        not user
        or user.get("role") not in {"officer", "admin"}
        or user.get("role") != claims.get("role")
        or not user.get("is_active", True)
    ):
        raise HTTPException(status_code=403, detail="MFA_CONFIRM_NOT_ALLOWED")
    if await get_user_totp_secret(user["id"]):
        raise HTTPException(status_code=409, detail="MFA_ALREADY_ENABLED")
    await enable_user_totp(user["id"], secret)
    auth_result = await issue_user_session(user)
    _set_production_session_cookies(response, auth_result["token"])
    return LoginResponse(
        authenticated=True,
        role=user["role"],
        token=None,
        auth_mode="cookie_session",
        user_id=user["id"],
        username=user["username"],
        email=user["email"],
        must_change_password=bool(
            (user.get("profile") or {}).get("must_change_password", False)
        ),
    )


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        auth_header = request.headers.get("Authorization", "")
        try:
            scheme, candidate = auth_header.split(" ", 1)
            if scheme.casefold() == "bearer":
                token = candidate
        except ValueError:
            token = None
    if token:
        await revoke_session_token(token)
    for cookie_name in (SESSION_COOKIE_NAME, CSRF_COOKIE_NAME):
        response.delete_cookie(
            cookie_name,
            path="/",
            secure=production_mode_enabled(),
            httponly=cookie_name == SESSION_COOKIE_NAME,
            samesite="strict",
        )
    return {"success": True}


@router.post("/register", response_model=LoginResponse, status_code=201)
async def register(payload: RegisterRequest, request: Request, response: Response):
    await ensure_bootstrap_admin_user()
    user = await register_citizen_account(payload.model_dump(), request=request)
    auth_result = await authenticate_user_account(
        payload.username,
        payload.password,
        "citizen",
    )
    production = production_mode_enabled()
    if production:
        _set_production_session_cookies(response, auth_result["token"])
    return LoginResponse(
        authenticated=True,
        role="citizen",
        token=None if production else auth_result["token"],
        auth_mode="cookie_session" if production else "user_session",
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
