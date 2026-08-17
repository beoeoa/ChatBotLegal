from unittest.mock import AsyncMock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import api.auth as auth
from api.auth import path_requires_admin


def test_officer_profile_source_writes_are_not_blocked_by_blanket_admin_gate():
    assert path_requires_admin("/api/sources", "POST") is False
    assert path_requires_admin("/api/sources/source-1", "PUT") is False
    assert path_requires_admin("/api/sources/source-1/chat/sessions", "POST") is False


def test_true_administration_endpoints_remain_admin_only():
    assert path_requires_admin("/api/legal/import", "POST") is True
    assert path_requires_admin("/api/legal/crawl", "POST") is True
    assert path_requires_admin("/api/settings", "PUT") is True


def test_authenticated_officer_reaches_source_write_but_not_admin_import(monkeypatch):
    monkeypatch.delenv("PRODUCTION_MODE", raising=False)
    monkeypatch.setattr(auth, "has_real_users", AsyncMock(return_value=True))
    monkeypatch.setattr(auth, "configured_role_passwords", lambda: {})
    monkeypatch.setattr(
        auth,
        "get_user_from_session_token",
        AsyncMock(
            return_value={
                "role": "officer",
                "user": {
                    "id": "user_account:officer-1",
                    "username": "officer-1",
                    "email": "officer-1@example.test",
                    "profile": {},
                },
            }
        ),
    )

    app = FastAPI()
    app.add_middleware(auth.PasswordAuthMiddleware, excluded_paths=[])

    @app.post("/api/sources")
    async def write_profile_source(request: Request):
        return {"role": request.state.user_role, "user_id": request.state.user_id}

    @app.post("/api/legal/import")
    async def import_legal_corpus():
        return {"ok": True}

    client = TestClient(app)
    headers = {"Authorization": "Bearer officer-session", "X-User-Role": "officer"}

    source_response = client.post("/api/sources", headers=headers)
    admin_response = client.post("/api/legal/import", headers=headers)

    assert source_response.status_code == 200
    assert source_response.json() == {
        "role": "officer",
        "user_id": "user_account:officer-1",
    }
    assert admin_response.status_code == 403
