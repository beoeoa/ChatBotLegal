from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from api.auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    PasswordAuthMiddleware,
    build_csrf_token,
    check_api_password,
)
from api.routers import auth as auth_router


def _protected_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(PasswordAuthMiddleware, excluded_paths=[])

    @app.get("/protected")
    async def protected(request: Request):
        return {
            "ok": True,
            "user_id": request.state.user_id,
            "auth_mode": request.state.auth_mode,
        }

    @app.post("/protected")
    async def protected_write():
        return {"ok": True}

    return app


def _session() -> dict:
    return {
        "session_id": "user_session:test",
        "role": "admin",
        "user": {
            "id": "user_account:test",
            "username": "admin",
            "email": "admin@example.test",
            "profile": {},
        },
    }


def _verified_user() -> dict:
    return {
        "id": "user_account:test",
        "username": "admin",
        "email": "admin@example.test",
        "role": "admin",
        "is_active": True,
        "profile": {},
    }


def _configure_production_session(monkeypatch) -> None:
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", "test-cookie-secret-32-bytes-minimum")
    monkeypatch.setattr("api.auth.has_real_users", AsyncMock(return_value=True))
    monkeypatch.setattr("api.auth.configured_role_passwords", lambda: {})
    monkeypatch.setattr(
        "api.auth.get_user_from_session_token",
        AsyncMock(return_value=_session()),
    )


def test_production_accepts_http_only_session_cookie_without_bearer(
    monkeypatch,
) -> None:
    _configure_production_session(monkeypatch)
    client = TestClient(_protected_app())
    client.cookies.set(SESSION_COOKIE_NAME, "opaque-session")

    response = client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "user_id": "user_account:test",
        "auth_mode": "cookie_session",
    }


def test_cookie_session_satisfies_legacy_password_dependency(monkeypatch) -> None:
    _configure_production_session(monkeypatch)
    app = FastAPI()
    app.add_middleware(PasswordAuthMiddleware, excluded_paths=[])

    @app.get("/dependent", dependencies=[Depends(check_api_password)])
    async def dependent():
        return {"ok": True}

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, "opaque-session")

    response = client.get("/dependent")

    assert response.status_code == 200


def test_production_cookie_write_requires_session_bound_csrf(monkeypatch) -> None:
    _configure_production_session(monkeypatch)
    client = TestClient(_protected_app())
    client.cookies.set(SESSION_COOKIE_NAME, "opaque-session")

    missing = client.post("/protected")
    assert missing.status_code == 403
    assert missing.json()["code"] == "csrf_validation_failed"

    csrf = build_csrf_token("opaque-session")
    client.cookies.set(CSRF_COOKIE_NAME, csrf)
    accepted = client.post(
        "/protected",
        headers={"X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 200


def test_production_login_sets_secure_cookies_and_does_not_return_token(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", "test-cookie-secret-32-bytes-minimum")
    monkeypatch.setattr(
        auth_router,
        "ensure_bootstrap_admin_user",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(auth_router, "configured_role_passwords", lambda: {})
    monkeypatch.setattr(
        auth_router,
        "verify_user_credentials",
        AsyncMock(return_value=_verified_user()),
    )
    monkeypatch.setattr(
        auth_router,
        "get_user_totp_secret",
        AsyncMock(return_value="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"),
    )
    monkeypatch.setattr(auth_router, "verify_totp_code", lambda *_args: True)
    monkeypatch.setattr(
        auth_router,
        "issue_user_session",
        AsyncMock(
            return_value={
                "token": "opaque-session",
                "role": "admin",
                "user": _verified_user(),
            }
        ),
    )
    app = FastAPI()
    app.include_router(auth_router.router)

    response = TestClient(app).post(
        "/auth/login",
        json={
            "identifier": "admin",
            "password": "valid-password",
            "totp_code": "123456",
        },
    )

    assert response.status_code == 200
    assert response.json()["token"] is None
    assert response.json()["auth_mode"] == "cookie_session"
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(item for item in cookies if item.startswith(SESSION_COOKIE_NAME))
    csrf_cookie = next(item for item in cookies if item.startswith(CSRF_COOKIE_NAME))
    assert "HttpOnly" in session_cookie
    assert "Secure" in session_cookie
    assert "SameSite=strict" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "Secure" in csrf_cookie


def test_production_elevated_login_without_enrollment_gets_setup_ticket_not_session(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", "test-cookie-secret-32-bytes-minimum")
    monkeypatch.setattr(auth_router, "ensure_bootstrap_admin_user", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_router, "configured_role_passwords", lambda: {})
    monkeypatch.setattr(
        auth_router,
        "verify_user_credentials",
        AsyncMock(return_value=_verified_user()),
    )
    monkeypatch.setattr(
        auth_router,
        "get_user_totp_secret",
        AsyncMock(return_value=None),
    )
    issue_session = AsyncMock()
    monkeypatch.setattr(auth_router, "issue_user_session", issue_session)
    app = FastAPI()
    app.include_router(auth_router.router)

    response = TestClient(app).post(
        "/auth/login",
        json={"identifier": "admin", "password": "valid-password"},
    )

    assert response.status_code == 428
    assert response.json()["detail"]["code"] == "MFA_SETUP_REQUIRED"
    assert response.json()["detail"]["setup_token"]
    assert not response.headers.get_list("set-cookie")
    issue_session.assert_not_awaited()


def test_totp_confirmation_enables_factor_before_issuing_cookie_session(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", "test-cookie-secret-32-bytes-minimum")
    user = _verified_user()
    monkeypatch.setattr(
        auth_router,
        "read_mfa_ticket",
        lambda *_args, **_kwargs: {
            "user_id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "secret": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        },
    )
    monkeypatch.setattr(auth_router, "verify_totp_code", lambda *_args: True)
    monkeypatch.setattr(
        auth_router,
        "get_user_with_profile",
        AsyncMock(return_value=user),
    )
    monkeypatch.setattr(
        auth_router,
        "get_user_totp_secret",
        AsyncMock(return_value=None),
    )
    enable = AsyncMock(return_value=None)
    issue = AsyncMock(
        return_value={"token": "opaque-session", "user": user, "role": "admin"}
    )
    monkeypatch.setattr(auth_router, "enable_user_totp", enable)
    monkeypatch.setattr(auth_router, "issue_user_session", issue)
    app = FastAPI()
    app.include_router(auth_router.router)

    response = TestClient(app).post(
        "/auth/totp/confirm",
        json={"confirm_token": "x" * 40, "code": "123456"},
    )

    assert response.status_code == 200
    assert response.json()["auth_mode"] == "cookie_session"
    assert response.json()["token"] is None
    enable.assert_awaited_once_with(
        "user_account:test",
        "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
    )
    issue.assert_awaited_once()
    assert any(
        item.startswith(SESSION_COOKIE_NAME)
        and "HttpOnly" in item
        and "Secure" in item
        for item in response.headers.get_list("set-cookie")
    )


def test_production_logout_revokes_server_session_and_clears_cookies(
    monkeypatch,
) -> None:
    _configure_production_session(monkeypatch)
    revoke = AsyncMock(return_value=True)
    monkeypatch.setattr(auth_router, "revoke_session_token", revoke)
    app = FastAPI()
    app.add_middleware(PasswordAuthMiddleware, excluded_paths=[])
    app.include_router(auth_router.router)
    client = TestClient(app)
    csrf = build_csrf_token("opaque-session")
    client.cookies.set(SESSION_COOKIE_NAME, "opaque-session")
    client.cookies.set(CSRF_COOKIE_NAME, csrf)

    response = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    revoke.assert_awaited_once_with("opaque-session")
    cookies = response.headers.get_list("set-cookie")
    assert any(
        item.startswith(SESSION_COOKIE_NAME) and "Max-Age=0" in item
        for item in cookies
    )
    assert any(
        item.startswith(CSRF_COOKIE_NAME) and "Max-Age=0" in item
        for item in cookies
    )
