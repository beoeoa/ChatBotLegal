from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.requests import Request

import api.routers.sources as sources_router


@pytest.mark.asyncio
async def test_retry_source_resolves_notebooks_from_reference_edges(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/sources/source:source-1/retry",
            "headers": [],
            "query_string": b"",
        }
    )
    source = MagicMock()
    source.id = "source:source-1"
    source.command = None
    source.asset = None
    source.full_text = "Retry this text source"
    source.title = "Test source"
    source.topics = []
    source.created = datetime.now(timezone.utc)
    source.updated = datetime.now(timezone.utc)
    source.save = AsyncMock()
    source.get_embedded_chunks = AsyncMock(return_value=0)

    monkeypatch.setattr(
        sources_router, "assert_source_access", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        sources_router.Source, "get", AsyncMock(return_value=source)
    )
    query = AsyncMock(side_effect=[["notebook:notebook-1"], []])
    monkeypatch.setattr(sources_router, "repo_query", query)
    execute = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            error_message=None,
        )
    )
    monkeypatch.setattr(sources_router, "process_source_command", execute)

    result = await sources_router.retry_source_processing(
        "source:source-1", request
    )

    assert result.command_id is None
    assert result.status == "completed"
    payload = execute.await_args.args[0]
    assert payload.notebook_ids == ["notebook:notebook-1"]
    assert payload.embed is False
    assert "SELECT VALUE out FROM reference WHERE in = $source_id" in query.await_args_list[0].args[0]
