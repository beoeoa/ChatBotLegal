from datetime import datetime, timedelta, timezone

import pytest

from api import user_service


@pytest.mark.asyncio
async def test_session_cache_uses_hashed_key_and_avoids_repeated_lookup(monkeypatch):
    user_service.clear_session_l1_cache()
    calls = {"query": 0, "profile": 0}

    async def fake_query(_query, _params):
        calls["query"] += 1
        return [{
            "id": "user_session:one",
            "user": "user_account:citizen01",
            "role": "citizen",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
            "last_seen_at": datetime.now(timezone.utc),
        }]

    async def fake_profile(_user_id):
        calls["profile"] += 1
        return {
            "id": "user_account:citizen01",
            "username": "citizen01",
            "email": "citizen@example.test",
            "role": "citizen",
            "is_active": True,
            "profile": {"must_change_password": False},
        }

    monkeypatch.setattr(user_service, "repo_query", fake_query)
    monkeypatch.setattr(user_service, "get_user_with_profile", fake_profile)

    first = await user_service.get_user_from_session_token("raw-secret-token")
    second = await user_service.get_user_from_session_token("raw-secret-token")

    assert first == second
    assert calls == {"query": 1, "profile": 1}
    assert "raw-secret-token" not in user_service.session_l1_cache_keys()
    assert user_service._hash_session_token("raw-secret-token") in user_service.session_l1_cache_keys()


@pytest.mark.asyncio
async def test_revoke_invalidates_cached_token(monkeypatch):
    user_service.clear_session_l1_cache()
    user_service._session_l1_cache_put(
        user_service._hash_session_token("revoke-me"),
        "user_account:citizen01",
        {"session_id": "user_session:one", "role": "citizen", "user": {"id": "user_account:citizen01"}},
    )

    async def fake_query(_query, _params):
        return [{"id": "user_session:one"}]

    async def fake_update(*_args, **_kwargs):
        return None

    monkeypatch.setattr(user_service, "repo_query", fake_query)
    monkeypatch.setattr(user_service, "repo_update", fake_update)

    assert await user_service.revoke_session_token("revoke-me") is True
    assert user_service.session_l1_cache_keys() == ()
