from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api.auth import PasswordAuthMiddleware


def _app():
    app = FastAPI()
    app.add_middleware(PasswordAuthMiddleware, excluded_paths=[])

    @app.get("/protected")
    async def protected():
        return {"ok": True}

    return app


def test_disabled_auth_does_not_invent_database_user(monkeypatch):
    monkeypatch.setenv("DISABLE_AUTH", "true")

    app = FastAPI()
    app.add_middleware(PasswordAuthMiddleware, excluded_paths=[])

    @app.get("/identity")
    async def identity(request: Request):
        return {
            "role": request.state.user_role,
            "user_id": request.state.user_id,
            "username": request.state.username,
        }

    response = TestClient(app).get(
        "/identity",
        headers={"X-User-Role": "admin"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "role": "admin",
        "user_id": None,
        "username": None,
    }


def test_production_rejects_shared_legacy_password(monkeypatch):
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setattr(
        "api.auth.configured_role_passwords",
        lambda: {"citizen": "shared", "officer": "shared", "admin": "shared"},
    )
    monkeypatch.setattr("api.auth.has_real_users", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "api.auth.get_user_from_session_token",
        AsyncMock(return_value=None),
    )

    response = TestClient(_app()).get(
        "/protected",
        headers={
            "Authorization": "Bearer shared",
            "X-User-Role": "admin",
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "session_required"


def test_production_without_real_accounts_fails_closed(monkeypatch):
    monkeypatch.setenv("PRODUCTION_MODE", "true")
    monkeypatch.setattr("api.auth.configured_role_passwords", lambda: {})
    monkeypatch.setattr("api.auth.has_real_users", AsyncMock(return_value=False))

    response = TestClient(_app()).get("/protected")

    assert response.status_code == 503
    assert response.json()["code"] == "production_auth_not_configured"
