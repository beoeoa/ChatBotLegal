from fastapi import FastAPI
from fastapi.testclient import TestClient


async def _noop_async(*_args, **_kwargs):
    return None


def test_auth_status_only_exposes_citizen(monkeypatch):
    from api.routers.auth import router as auth_router

    async def fake_has_real_users():
        return True

    monkeypatch.setattr("api.routers.auth.ensure_bootstrap_admin_user", _noop_async)
    monkeypatch.setattr("api.routers.auth.has_real_users", fake_has_real_users)
    monkeypatch.setattr("api.routers.auth.configured_role_passwords", lambda: {"admin": "x", "officer": "y"})

    app = FastAPI()
    app.include_router(auth_router, prefix="/api")

    with TestClient(app) as client:
        response = client.get("/api/auth/status")

    assert response.status_code == 200
    assert response.json()["available_roles"] == ["citizen"]


def test_login_without_role_uses_account_role(monkeypatch):
    from api.routers.auth import router as auth_router

    async def fake_auth(identifier, password, role):
        assert role is None
        return {
            "token": "session-token",
            "user": {
                "id": "user:officer_hotich",
                "username": "officer_hotich",
                "email": "officer_hotich@local",
                "role": "officer",
                "profile": {"must_change_password": False},
            },
            "role": "officer",
            "expires_at": "2026-12-31T00:00:00Z",
        }

    monkeypatch.setattr("api.routers.auth.ensure_bootstrap_admin_user", _noop_async)
    monkeypatch.setattr("api.routers.auth.configured_role_passwords", lambda: {})
    monkeypatch.setattr("api.routers.auth.authenticate_user_account", fake_auth)

    app = FastAPI()
    app.include_router(auth_router, prefix="/api")

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"identifier": "officer_hotich", "password": "secret"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "officer"
    assert body["token"] == "session-token"


def test_register_public_creates_only_citizen(monkeypatch):
    from api.routers.auth import router as auth_router

    async def fake_register(payload, request=None):
        assert payload.get("role") is None
        return {
            "id": "user:new",
            "username": payload["username"],
            "email": payload["email"],
            "role": "citizen",
            "profile": {},
        }

    async def fake_auth(identifier, password, role):
        assert role == "citizen"
        return {
            "token": "new-session",
            "user": {
                "id": "user:new",
                "username": identifier,
                "email": "new@example.com",
                "role": "citizen",
                "profile": {},
            },
            "role": "citizen",
            "expires_at": "2026-12-31T00:00:00Z",
        }

    monkeypatch.setattr("api.routers.auth.ensure_bootstrap_admin_user", _noop_async)
    monkeypatch.setattr("api.routers.auth.register_citizen_account", fake_register)
    monkeypatch.setattr("api.routers.auth.authenticate_user_account", fake_auth)

    app = FastAPI()
    app.include_router(auth_router, prefix="/api")

    with TestClient(app) as client:
        response = client.post(
            "/api/auth/register",
            json={
                "username": "citizen01",
                "email": "citizen01@example.com",
                "password": "secret123",
            },
        )

    assert response.status_code == 201
    assert response.json()["role"] == "citizen"


def test_forgot_and_reset_password_routes(monkeypatch):
    from api.routers.auth import router as auth_router

    async def fake_forgot(identifier, request=None):
        return {
            "success": True,
            "message": "Nếu tài khoản tồn tại, hệ thống đã tạo hướng dẫn đặt lại mật khẩu.",
            "reset_token": "token-1234567890abcdef",
            "expires_in_minutes": 15,
        }

    reset_calls = []

    async def fake_reset(token, new_password, request=None):
        reset_calls.append((token, new_password))

    monkeypatch.setattr("api.routers.auth.ensure_bootstrap_admin_user", _noop_async)
    monkeypatch.setattr("api.routers.auth.create_password_reset_request", fake_forgot)
    monkeypatch.setattr("api.routers.auth.reset_password_with_token", fake_reset)

    app = FastAPI()
    app.include_router(auth_router, prefix="/api")

    with TestClient(app) as client:
        forgot = client.post("/api/auth/forgot-password", json={"identifier": "citizen01"})
        reset = client.post(
            "/api/auth/reset-password",
            json={"token": "token-1234567890abcdef", "new_password": "StrongPassword123!"},
        )

    assert forgot.status_code == 200
    assert forgot.json()["reset_token"] == "token-1234567890abcdef"
    assert reset.status_code == 200
    assert reset_calls == [("token-1234567890abcdef", "StrongPassword123!")]
