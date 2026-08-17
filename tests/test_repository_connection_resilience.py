from __future__ import annotations

import asyncio

import pytest

from open_notebook.database import repository


class _BrokenClient:
    async def query(self, _query, _vars=None):
        raise ConnectionError("keepalive ping timeout")


class _HealthyClient:
    async def query(self, _query, _vars=None):
        return [{"count": 1}]


@pytest.mark.asyncio
async def test_repo_query_resets_and_retries_a_broken_shared_connection(monkeypatch):
    clients = iter([_BrokenClient(), _HealthyClient()])
    resets = {"count": 0}

    async def fake_get_client():
        return next(clients)

    async def fake_reset():
        resets["count"] += 1

    monkeypatch.setattr(repository, "get_db_client", fake_get_client)
    monkeypatch.setattr(repository, "reset_db_client", fake_reset)

    assert await repository.repo_query("SELECT 1") == [{"count": 1}]
    assert resets["count"] == 1


@pytest.mark.asyncio
async def test_repo_query_serializes_shared_websocket_operations(monkeypatch):
    active = {"count": 0, "max": 0}

    class Client:
        async def query(self, _query, _vars=None):
            active["count"] += 1
            active["max"] = max(active["max"], active["count"])
            await asyncio.sleep(0.01)
            active["count"] -= 1
            return []

    monkeypatch.setattr(repository, "get_db_client", lambda: asyncio.sleep(0, result=Client()))
    await asyncio.gather(*(repository.repo_query("SELECT 1") for _ in range(4)))
    assert active["max"] == 1
