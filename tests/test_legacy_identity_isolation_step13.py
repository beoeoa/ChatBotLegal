"""Bước 13 regression: shared legacy roles never own personal data."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request(role: str, user_id: str | None) -> Request:
    request = Request({
        "type": "http", "method": "GET", "path": "/api/test",
        "headers": [], "query_string": b"", "client": ("127.0.0.1", 1),
    })
    request.state.user_role = role
    request.state.user_id = user_id
    request.state.username = None
    return request


def test_conversations_require_individual_account_identity():
    from api.routers.conversations import _owner_state

    with pytest.raises(HTTPException) as denied:
        _owner_state(_request("citizen", None))
    assert denied.value.status_code == 401

    owner_key, user_id, role, _ = _owner_state(_request("citizen", "citizen-a"))
    assert owner_key == "citizen-a"
    assert user_id == "citizen-a"
    assert role == "citizen"


def test_ask_session_storage_rejects_shared_role_identity():
    from api.routers.ask_sessions import _resolve_user_key

    with pytest.raises(HTTPException) as denied:
        _resolve_user_key(_request("citizen", None))
    assert denied.value.status_code == 401
    assert _resolve_user_key(_request("citizen", "citizen-a")) == "citizen-a"


def test_support_ticket_storage_rejects_shared_role_identity():
    from api.routers.live_support import _resolve_user_key

    with pytest.raises(HTTPException) as denied:
        _resolve_user_key(_request("citizen", None))
    assert denied.value.status_code == 401
    assert _resolve_user_key(_request("citizen", "citizen-a")) == "citizen-a"


@pytest.mark.asyncio
async def test_live_support_websocket_requires_real_user_session(monkeypatch):
    from api.routers import live_support

    class FakeSocket:
        query_params = {"token": "legacy-password", "role": "citizen", "user_id": "forged"}

    async def no_real_users():
        return False

    monkeypatch.setattr(live_support, "has_real_users", no_real_users)
    assert await live_support._ws_identity(FakeSocket()) is None


def test_search_history_does_not_use_legacy_role_owner_key():
    from api.routers.search import _resolve_ask_session_owner_key

    assert _resolve_ask_session_owner_key(user_id=None, role="citizen") is None
    assert _resolve_ask_session_owner_key(user_id="citizen-a", role="citizen") == "citizen-a"


@pytest.mark.asyncio
async def test_auth_middleware_does_not_bypass_download_routes(monkeypatch):
    """Private download paths require the same Bearer auth as every API route."""
    from api.auth import PasswordAuthMiddleware
    from starlette.responses import Response

    async def real_users_exist():
        return True

    monkeypatch.setattr("api.auth.has_real_users", real_users_exist)
    middleware = PasswordAuthMiddleware(app=None)
    request = Request({
        "type": "http", "method": "GET",
        "path": "/api/support/tickets/t-1/attachments/a-1/download",
        "headers": [], "query_string": b"", "client": ("127.0.0.1", 1),
        "scheme": "http", "server": ("testserver", 80),
    })

    async def should_not_run(_request):
        raise AssertionError("download endpoint bypassed authentication")

    response = await middleware.dispatch(request, should_not_run)
    assert response.status_code == 401
