from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api.auth import PasswordAuthMiddleware
from api.auth_rate_limit import AuthRateLimitMiddleware
from api.routers import auth as auth_router
from api import user_service


def _user(role: str = "officer") -> dict:
    return {
        "id": f"user_account:{role}",
        "username": role,
        "email": f"{role}@example.test",
        "role": role,
        "is_active": True,
        "profile": {},
    }


def test_server_acl_rejects_non_admin_session_even_with_spoofed_role_header(monkeypatch):
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", "release-test-key-at-least-32-bytes")
    monkeypatch.setattr("api.auth.has_real_users", AsyncMock(return_value=True))
    monkeypatch.setattr("api.auth.configured_role_passwords", lambda: {})
    monkeypatch.setattr(
        "api.auth.get_user_from_session_token",
        AsyncMock(
            return_value={
                "session_id": "user_session:officer",
                "role": "officer",
                "user": _user("officer"),
            }
        ),
    )
    app = FastAPI()
    app.add_middleware(PasswordAuthMiddleware, excluded_paths=[])

    @app.get("/api/admin/activity")
    async def admin_activity(request: Request):
        return {"role": request.state.user_role}

    response = TestClient(app).get(
        "/api/admin/activity",
        headers={"Authorization": "Bearer opaque", "X-User-Role": "admin"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Admin role required"


@pytest.mark.asyncio
async def test_revoked_session_is_removed_from_cache_and_cannot_authenticate(monkeypatch):
    user_service.clear_session_l1_cache()
    state = {"revoked": False}
    session = {
        "id": "user_session:one",
        "user": "user_account:officer",
        "role": "officer",
        "updated": "v1",
        "last_seen_at": user_service._utcnow(),
    }

    async def query(sql, _params):
        if "FROM user_session" not in sql or state["revoked"]:
            return []
        return [session]

    async def update(_table, _record_id, values):
        if values.get("revoked_at"):
            state["revoked"] = True
        return values

    monkeypatch.setattr(user_service, "repo_query", query)
    monkeypatch.setattr(user_service, "repo_update", update)
    monkeypatch.setattr(user_service, "get_user_with_profile", AsyncMock(return_value=_user("officer")))

    token = "opaque-revocable-session"
    assert await user_service.get_user_from_session_token(token)
    assert user_service.session_l1_cache_keys()
    assert await user_service.revoke_session_token(token) is True
    assert user_service.session_l1_cache_keys() == ()
    assert await user_service.get_user_from_session_token(token) is None


def test_production_admin_login_rejects_missing_and_invalid_mfa(monkeypatch):
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", "release-test-key-at-least-32-bytes")
    monkeypatch.setattr(auth_router, "ensure_bootstrap_admin_user", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_router, "configured_role_passwords", lambda: {})
    monkeypatch.setattr(auth_router, "verify_user_credentials", AsyncMock(return_value=_user("admin")))
    monkeypatch.setattr(auth_router, "get_user_totp_secret", AsyncMock(return_value="totp-secret"))
    monkeypatch.setattr(auth_router, "verify_totp_code", lambda *_args: False)
    issue_session = AsyncMock()
    monkeypatch.setattr(auth_router, "issue_user_session", issue_session)
    app = FastAPI()
    app.include_router(auth_router.router)
    client = TestClient(app)

    missing = client.post("/auth/login", json={"identifier": "admin", "password": "valid"})
    invalid = client.post(
        "/auth/login",
        json={"identifier": "admin", "password": "valid", "totp_code": "123456"},
    )
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "MFA_CODE_REQUIRED"
    assert invalid.status_code == 401
    assert invalid.json()["detail"]["code"] == "MFA_CODE_INVALID"
    issue_session.assert_not_awaited()


def test_mfa_confirmation_rate_limit_is_server_enforced_and_content_free():
    app = FastAPI()
    app.add_middleware(
        AuthRateLimitMiddleware,
        limits={"/api/auth/totp/confirm": (1, 60)},
    )

    @app.post("/api/auth/totp/confirm")
    async def confirm():
        return {"ok": False}

    client = TestClient(app)
    client.post("/api/auth/totp/confirm", json={"code": "111111", "secret": "private"})
    blocked = client.post(
        "/api/auth/totp/confirm", json={"code": "222222", "secret": "private"}
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "auth_rate_limited"
    assert "111111" not in blocked.text and "222222" not in blocked.text
    assert "private" not in blocked.text
