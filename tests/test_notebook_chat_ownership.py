from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import api.legal_profile_access as access
import api.routers.chat as chat_router


def _request(user_id: str, role: str = "admin") -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/chat/sessions",
            "headers": [],
            "query_string": b"",
        }
    )
    request.state.user_id = user_id
    request.state.user_role = role
    return request


@pytest.mark.asyncio
async def test_admin_chat_session_is_assigned_to_authenticated_user(monkeypatch):
    now = datetime.now(timezone.utc)

    class FakeSession:
        def __init__(self, title, model_override):
            self.id = "chat_session:session-1"
            self.title = title
            self.model_override = model_override
            self.created = now
            self.updated = now

        async def save(self):
            return None

        async def relate_to_notebook(self, notebook_id):
            assert notebook_id == "notebook:notebook-1"

    monkeypatch.setattr(chat_router, "_assert_notebook_access", AsyncMock())
    monkeypatch.setattr(
        chat_router.Notebook,
        "get",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(chat_router, "ChatSession", FakeSession)
    query = AsyncMock(return_value=[])
    monkeypatch.setattr(chat_router, "repo_query", query)

    response = await chat_router.create_session(
        chat_router.CreateSessionRequest(notebook_id="notebook:notebook-1"),
        _request("user_account:admin-1"),
    )

    assert response.id == "chat_session:session-1"
    sql, params = query.await_args.args
    assert "owner_user = type::record($owner_user)" in sql
    assert params["owner_user"] == "user_account:admin-1"


@pytest.mark.asyncio
async def test_chat_access_error_keeps_original_http_status(monkeypatch):
    monkeypatch.setattr(
        chat_router,
        "_assert_notebook_access",
        AsyncMock(side_effect=HTTPException(status_code=403, detail="denied")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await chat_router.create_session(
            chat_router.CreateSessionRequest(notebook_id="notebook:notebook-1"),
            _request("admin-1"),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "denied"


@pytest.mark.asyncio
async def test_legacy_ownerless_session_uses_owned_notebook(monkeypatch):
    query = AsyncMock(
        side_effect=[
            [{"id": "chat_session:session-1", "owner_user": None, "ownership_status": "resolved"}],
            ["notebook:notebook-1"],
            [{"id": "notebook:notebook-1", "owner_user": "user_account:admin-1", "ownership_status": "resolved"}],
        ]
    )
    monkeypatch.setattr(access, "repo_query", query)

    record = await access.assert_chat_session_access(
        "chat_session:session-1",
        _request("admin-1"),
    )

    assert record["id"] == "chat_session:session-1"
    assert "FROM refers_to" in query.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_legacy_ownerless_session_rejects_different_notebook_owner(monkeypatch):
    query = AsyncMock(
        side_effect=[
            [{"id": "chat_session:session-1", "owner_user": None, "ownership_status": "resolved"}],
            ["notebook:notebook-2"],
            [{"id": "notebook:notebook-2", "owner_user": "user_account:admin-2", "ownership_status": "resolved"}],
        ]
    )
    monkeypatch.setattr(access, "repo_query", query)

    with pytest.raises(HTTPException) as exc_info:
        await access.assert_chat_session_access(
            "chat_session:session-1",
            _request("admin-1"),
        )

    assert exc_info.value.status_code == 403
