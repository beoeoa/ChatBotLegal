from collections.abc import Iterable
import hashlib
import hmac
import os
from typing import Literal, Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from open_notebook.utils.encryption import get_secret_from_env
from api.user_service import get_user_from_session_token, has_real_users


RoleName = Literal["citizen", "officer", "admin"]
ROLE_HEADER = "X-User-Role"
ROLE_ORDER: dict[RoleName, int] = {"citizen": 0, "officer": 1, "admin": 2}
SESSION_COOKIE_NAME = "chatbotlegal_session"
CSRF_COOKIE_NAME = "chatbotlegal_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def build_csrf_token(session_token: str) -> str:
    """Derive a session-bound CSRF token without persisting the raw session."""

    encryption_key = get_secret_from_env("OPEN_NOTEBOOK_ENCRYPTION_KEY")
    if not encryption_key:
        raise RuntimeError("OPEN_NOTEBOOK_ENCRYPTION_KEY is required for CSRF")
    return hmac.new(
        encryption_key.encode("utf-8"),
        f"chatbotlegal-csrf-v1:{session_token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _csrf_is_valid(request: Request, session_token: str) -> bool:
    if request.method.upper() in _SAFE_METHODS:
        return True
    supplied_header = request.headers.get(CSRF_HEADER_NAME, "")
    supplied_cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
    if not supplied_header or not supplied_cookie:
        return False
    try:
        expected = build_csrf_token(session_token)
    except RuntimeError:
        return False
    return hmac.compare_digest(supplied_header, supplied_cookie) and hmac.compare_digest(
        supplied_header,
        expected,
    )


def auth_disabled() -> bool:
    try:
        val = os.environ.get("DISABLE_AUTH") or os.environ.get("OPEN_NOTEBOOK_DISABLE_AUTH") or ""
    except Exception:
        val = ""
    return str(val or "").strip().casefold() in {"1", "true", "yes", "on"}


def production_mode_enabled() -> bool:
    # Read through Mapping.__getitem__ instead of ``os.getenv``. Some provider
    # availability tests intentionally mock ``os.environ.get`` with a
    # single-argument callable; ``os.getenv`` forwards a default argument to
    # that mock and can otherwise break unrelated authentication middleware.
    try:
        configured = os.environ["PRODUCTION_MODE"]
    except KeyError:
        configured = ""
    return str(configured or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def must_change_password_request_allowed(path: str, method: str) -> bool:
    """Limit temporary-password sessions to auth status, profile read and password change."""
    if path.startswith("/api/auth/"):
        return True
    if path == "/api/users/me" and method in {"GET", "HEAD"}:
        return True
    return path == "/api/users/me/change-password" and method == "POST"


ADMIN_ONLY_PREFIXES = (
    "/api/credentials",
    "/api/models",
    "/api/settings",
    "/api/embedding",
    "/api/embedding-rebuild",
    "/api/sources",
    "/api/source-chat",
    "/api/legal/import",
    "/api/legal/crawl",
    "/api/legal/validity",
    "/api/legal-management",
    "/api/admin",
)


def configured_role_passwords() -> dict[RoleName, str]:
    shared_password = get_secret_from_env("OPEN_NOTEBOOK_PASSWORD")
    citizen_password = get_secret_from_env("OPEN_NOTEBOOK_CITIZEN_PASSWORD")
    officer_password = get_secret_from_env("OPEN_NOTEBOOK_OFFICER_PASSWORD")
    admin_password = get_secret_from_env("OPEN_NOTEBOOK_ADMIN_PASSWORD")

    passwords: dict[RoleName, str] = {}
    for role, value in (
        ("citizen", citizen_password),
        ("officer", officer_password),
        ("admin", admin_password),
    ):
        if value:
            passwords[role] = value

    if shared_password:
        passwords.setdefault("citizen", shared_password)
        passwords.setdefault("officer", shared_password)
        passwords.setdefault("admin", shared_password)

    return passwords


def allowed_roles_for_password(password: str) -> list[RoleName]:
    passwords = configured_role_passwords()
    return [role for role, value in passwords.items() if value == password]


def normalize_role(value: str | None) -> RoleName:
    if value in {"citizen", "officer", "admin"}:
        return value
    return "citizen"


def is_role_allowed(actual_roles: Iterable[RoleName], requested_role: RoleName) -> bool:
    return requested_role in actual_roles


def path_requires_admin(path: str, method: str = "GET") -> bool:
    # Source endpoints are part of the legal-profile workspace. Their routers
    # enforce officer/admin membership plus per-account ownership for every
    # read and write, so a blanket admin gate here would block officers from
    # adding documents or using source chat inside their own notebook.
    if path == "/api/sources" or path.startswith("/api/sources/"):
        return False
    return any(path == prefix or path.startswith(prefix + "/") for prefix in ADMIN_ONLY_PREFIXES)



class PasswordAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to check password authentication for all API requests.
    Always active with default password if OPEN_NOTEBOOK_PASSWORD is not set.
    Supports Docker secrets via OPEN_NOTEBOOK_PASSWORD_FILE.
    """

    def __init__(self, app, excluded_paths: Optional[list] = None):
        super().__init__(app)
        self.excluded_paths = excluded_paths or [
            "/",
            "/health",
            "/ready",
            "/docs",
            "/openapi.json",
            "/redoc",
        ]

    async def dispatch(self, request: Request, call_next):
        if auth_disabled():
            request.state.user_role = normalize_role(request.headers.get(ROLE_HEADER) or "admin")
            # Disabled-auth mode has no verified database identity. Using a
            # synthetic record id made /api/users/me query a record that does
            # not exist, so the frontend cleared the session on every reload.
            request.state.user_id = request.headers.get("X-User-Id")
            request.state.username = request.headers.get("X-User-Username")
            request.state.user_email = request.headers.get("X-User-Email")
            request.state.auth_mode = "legacy_password"
            request.state.authenticated = True
            return await call_next(request)

        passwords = configured_role_passwords()
        # A configured shared password is already sufficient to decide that
        # authentication must stay enabled. Avoid a SurrealDB round trip on
        # every request; real session tokens are still resolved below.
        real_users_exist = False if passwords else await has_real_users()

        # Skip authentication if no password is set
        if not passwords and not real_users_exist:
            if production_mode_enabled():
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "Production authentication is not configured",
                        "code": "production_auth_not_configured",
                    },
                )
            request.state.user_role = normalize_role(request.headers.get(ROLE_HEADER))
            request.state.user_id = request.headers.get("X-User-Id")
            request.state.username = request.headers.get("X-User-Username")
            request.state.user_email = request.headers.get("X-User-Email")
            request.state.auth_mode = "legacy_password"
            request.state.authenticated = False
            return await call_next(request)

        # Only explicitly public paths bypass authentication. Download routes
        # can disclose private sources or support attachments and must pass
        # through the same authentication/owner checks as every other route.
        if request.url.path in self.excluded_paths:
            return await call_next(request)

        # Skip authentication for CORS preflight requests (OPTIONS)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Browser production sessions use an HttpOnly cookie. Bearer sessions
        # remain supported for non-browser clients and local compatibility.
        auth_header = request.headers.get("Authorization")
        bearer_token: str | None = None
        if auth_header:
            try:
                scheme, bearer_token = auth_header.split(" ", 1)
                if scheme.lower() != "bearer":
                    raise ValueError("Invalid authentication scheme")
            except ValueError:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid authorization header format"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
        production = production_mode_enabled()
        if production and cookie_token:
            credentials = cookie_token
            auth_source = "cookie"
        elif bearer_token:
            credentials = bearer_token
            auth_source = "bearer"
        elif cookie_token:
            credentials = cookie_token
            auth_source = "cookie"
        else:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        requested_role = normalize_role(request.headers.get(ROLE_HEADER))

        # Local compatibility passwords are deterministic credentials, not
        # database session tokens. Resolve them before probing the session
        # store so the first page load does not wait for two unnecessary DB
        # queries. Production continues to require the normal session path.
        if not production:
            legacy_roles = allowed_roles_for_password(credentials)
            if legacy_roles:
                if not is_role_allowed(legacy_roles, requested_role):
                    return JSONResponse(
                        status_code=403,
                        content={
                            "detail": f"Role '{requested_role}' is not allowed for this password"
                        },
                    )
                if (
                    path_requires_admin(request.url.path, request.method)
                    and requested_role != "admin"
                ):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Admin role required"},
                    )
                request.state.user_role = requested_role
                request.state.user_id = None
                request.state.username = None
                request.state.user_email = None
                request.state.auth_mode = "legacy_password"
                request.state.authenticated = True
                return await call_next(request)

        session_auth = await get_user_from_session_token(credentials)
        if session_auth:
            if production and auth_source == "cookie" and not _csrf_is_valid(
                request,
                credentials,
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "CSRF validation failed",
                        "code": "csrf_validation_failed",
                    },
                )
            resolved_role = normalize_role(session_auth["role"])
            if requested_role != resolved_role:
                requested_role = resolved_role
            if path_requires_admin(request.url.path, request.method) and resolved_role != "admin":
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Admin role required"},
                )
            request.state.user_role = resolved_role
            request.state.user_id = session_auth["user"]["id"]
            request.state.username = session_auth["user"]["username"]
            request.state.user_email = session_auth["user"].get("email")
            request.state.auth_mode = (
                "cookie_session" if auth_source == "cookie" else "user_session"
            )
            request.state.authenticated = True
            profile = session_auth["user"].get("profile") or {}
            if profile.get("must_change_password") and not must_change_password_request_allowed(
                request.url.path, request.method
            ):
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "Bạn phải đổi mật khẩu tạm trước khi sử dụng hệ thống.",
                        "code": "must_change_password",
                    },
                )
            response = await call_next(request)
            return response

        if production:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "A valid user session is required",
                    "code": "session_required",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        allowed_roles = allowed_roles_for_password(credentials)
        if not allowed_roles:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid password"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not is_role_allowed(allowed_roles, requested_role):
            return JSONResponse(
                status_code=403,
                content={"detail": f"Role '{requested_role}' is not allowed for this password"},
            )

        if path_requires_admin(request.url.path, request.method) and requested_role != "admin":
            return JSONResponse(
                status_code=403,
                content={"detail": "Admin role required"},
            )

        request.state.user_role = requested_role
        request.state.user_id = None
        request.state.username = None
        request.state.user_email = None
        request.state.auth_mode = "legacy_password"
        request.state.authenticated = True

        # Password is correct, proceed with the request
        response = await call_next(request)
        return response


# Optional: HTTPBearer security scheme for OpenAPI documentation
security = HTTPBearer(auto_error=False)


async def check_api_password(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> bool:
    """
    Utility function to check API password.
    Can be used as a dependency in individual routes if needed.
    Supports Docker secrets via OPEN_NOTEBOOK_PASSWORD_FILE.
    Returns True without checking credentials if OPEN_NOTEBOOK_PASSWORD is not configured.
    Raises 401 if credentials are missing or don't match the configured password.
    """
    passwords = configured_role_passwords()
    real_users_exist = False if passwords else await has_real_users()
    production = production_mode_enabled()

    # No password configured - skip authentication
    if not passwords and not real_users_exist:
        if production:
            raise HTTPException(
                status_code=503,
                detail="Production authentication is not configured",
            )
        request.state.authenticated = False
        return True

    # The global middleware already validated cookie sessions, including CSRF
    # for unsafe requests. Preserve compatibility with routes that also use
    # this historical dependency.
    if production and bool(getattr(request.state, "authenticated", False)):
        return True

    token = credentials.credentials if credentials else request.cookies.get(
        SESSION_COOKIE_NAME
    )
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if production:
        session_auth = await get_user_from_session_token(token)
        if not session_auth:
            raise HTTPException(
                status_code=401,
                detail="A valid user session is required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        resolved_role = normalize_role(session_auth["role"])
        if path_requires_admin(request.url.path, request.method) and resolved_role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
        request.state.user_role = resolved_role
        request.state.user_id = session_auth["user"]["id"]
        request.state.authenticated = True
        request.state.auth_mode = "user_session"
        return True

    allowed_roles = allowed_roles_for_password(token)
    if not allowed_roles:
        raise HTTPException(
            status_code=401,
            detail="Invalid password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    requested_role = normalize_role(request.headers.get(ROLE_HEADER))
    if not is_role_allowed(allowed_roles, requested_role):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{requested_role}' is not allowed for this password",
        )

    if path_requires_admin(request.url.path, request.method) and requested_role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")

    request.state.user_role = requested_role
    request.state.authenticated = True

    return True


def get_request_role(request: Request) -> RoleName:
    role = getattr(request.state, "user_role", None)
    return normalize_role(role)


def get_request_user_id(request: Request) -> str | None:
    return getattr(request.state, "user_id", None)


def get_request_username(request: Request) -> str | None:
    return getattr(request.state, "username", None)


def get_request_auth_mode(request: Request) -> str:
    return getattr(request.state, "auth_mode", "legacy_password")
