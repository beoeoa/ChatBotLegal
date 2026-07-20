from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.routers import users


def _request(*, user_id: str | None, role: str) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user_id = user_id
    request.state.user_role = role
    return request


@pytest.mark.asyncio
async def test_cross_account_profile_read_is_hidden(monkeypatch):
    loader = AsyncMock()
    monkeypatch.setattr(users, "get_user_with_profile", loader)

    with pytest.raises(HTTPException) as exc_info:
        await users.get_user_detail(
            _request(user_id="user_account:citizen-a", role="citizen"),
            "user_account:citizen-b",
        )

    assert exc_info.value.status_code == 404
    loader.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_owner_can_read_sanitized_profile(monkeypatch):
    profile = {"id": "user_account:citizen-a", "role": "citizen", "profile": {}}
    loader = AsyncMock(return_value=profile)
    monkeypatch.setattr(users, "get_user_with_profile", loader)

    result = await users.get_user_detail(
        _request(user_id="user_account:citizen-a", role="citizen"),
        "user_account:citizen-a",
    )

    assert result == profile
    loader.assert_awaited_once_with("user_account:citizen-a")


@pytest.mark.asyncio
async def test_admin_can_read_profile(monkeypatch):
    profile = {"id": "user_account:citizen-a", "role": "citizen", "profile": {}}
    loader = AsyncMock(return_value=profile)
    monkeypatch.setattr(users, "get_user_with_profile", loader)

    result = await users.get_user_detail(
        _request(user_id="user_account:admin", role="admin"),
        "user_account:citizen-a",
    )

    assert result == profile
