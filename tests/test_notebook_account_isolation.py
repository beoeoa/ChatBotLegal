from __future__ import annotations

import pytest
from starlette.requests import Request

import api.routers.notebooks as notebooks_router


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
