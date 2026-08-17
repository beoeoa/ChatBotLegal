from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import api.routers.notebooks as notebooks_router
from api.models import NotebookCreate


def _request() -> Request:
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/notebooks",
        "headers": [],
        "query_string": b"",
    })
    request.state.user_role = "admin"
    request.state.user_id = "user_account:admin-1"
    return request


@pytest.mark.asyncio
async def test_notebook_list_is_filtered_by_account_even_for_admin(monkeypatch):
    captured: dict[str, object] = {}

    async def allow(_request: Request):
        return "admin", "user_account:admin-1"

    async def fake_query(query: str, params=None):
        captured["query"] = query
        captured["params"] = params
        return []

    monkeypatch.setattr(notebooks_router, "assert_legal_profile_list_access", allow)
    monkeypatch.setattr(notebooks_router, "repo_query", fake_query)

    result = await notebooks_router.get_notebooks(
        _request(), archived=None, order_by="updated desc"
    )

    assert result == []
    assert "WHERE owner_user = type::record($owner_user)" in str(captured["query"])
    assert captured["params"] == {"owner_user": "user_account:admin-1"}


@pytest.mark.asyncio
async def test_created_notebook_is_resolved_to_current_account(monkeypatch):
    captured: dict[str, object] = {}

    async def allow(_request: Request):
        return "officer", "user_account:officer-1"

    class FakeNotebook:
        def __init__(self, *, name: str, description: str):
            self.id = "notebook:new-1"
            self.name = name
            self.description = description
            self.archived = False
            self.created = "2026-08-09T00:00:00Z"
            self.updated = "2026-08-09T00:00:00Z"

        async def save(self):
            return None

    async def fake_query(query: str, params=None):
        captured["query"] = query
        captured["params"] = params
        return []

    request = _request()
    request.state.user_role = "officer"
    request.state.user_id = "user_account:officer-1"
    monkeypatch.setattr(notebooks_router, "assert_legal_profile_list_access", allow)
    monkeypatch.setattr(
        notebooks_router,
        "get_request_user_id",
        lambda _request: "user_account:officer-1",
    )
    monkeypatch.setattr(notebooks_router, "Notebook", FakeNotebook)
    monkeypatch.setattr(notebooks_router, "repo_query", fake_query)

    created = await notebooks_router.create_notebook(
        NotebookCreate(name="Hồ sơ riêng", description="Của cán bộ"),
        request,
    )

    assert created.id == "notebook:new-1"
    assert "ownership_status = 'resolved'" in str(captured["query"])
    assert captured["params"] == {
        "notebook_id": notebooks_router.ensure_record_id("notebook:new-1"),
        "owner_user": "user_account:officer-1",
    }


@pytest.mark.asyncio
async def test_create_notebook_preserves_access_denial(monkeypatch):
    async def deny(_request: Request):
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(notebooks_router, "assert_legal_profile_list_access", deny)

    with pytest.raises(HTTPException) as exc:
        await notebooks_router.create_notebook(
            NotebookCreate(name="Không được tạo", description=""),
            _request(),
        )
    assert exc.value.status_code == 403
