from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.ask_progress import cloud_fallback_reason
from api.models import AskRequest, AskResponse
from api.routers import search
from open_notebook.exceptions import (
    ExternalServiceError,
    LegalRetrievalUnavailableError,
    NetworkError,
    RateLimitError,
)


@pytest.mark.parametrize(
    ("exception", "reason"),
    [
        (httpx.ConnectError("provider offline"), "provider_connect_error"),
        (httpx.ReadTimeout("provider timeout"), "provider_timeout"),
        (RateLimitError("quota 429"), "provider_rate_limit"),
        (NetworkError("provider timed out"), "provider_network_error"),
        (ExternalServiceError("AI provider is temporarily unavailable (503)"), "provider_5xx"),
    ],
)
def test_cloud_fallback_accepts_only_transient_provider_failures(
    exception: BaseException,
    reason: str,
) -> None:
    assert cloud_fallback_reason(exception) == reason


@pytest.mark.parametrize("status_code", [429, 500, 502, 503, 504])
def test_cloud_fallback_accepts_supported_http_statuses(status_code: int) -> None:
    request = httpx.Request("POST", "https://provider.test/chat")
    response = httpx.Response(status_code, request=request)
    error = httpx.HTTPStatusError("provider error", request=request, response=response)

    assert cloud_fallback_reason(error) is not None


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_cloud_fallback_rejects_non_transient_http_statuses(status_code: int) -> None:
    request = httpx.Request("POST", "https://provider.test/chat")
    response = httpx.Response(status_code, request=request)
    error = httpx.HTTPStatusError("provider error", request=request, response=response)

    assert cloud_fallback_reason(error) is None


def test_cloud_fallback_never_masks_retrieval_or_insufficient_evidence() -> None:
    assert cloud_fallback_reason(
        LegalRetrievalUnavailableError("local retrieval connection refused")
    ) is None
    assert cloud_fallback_reason(
        ExternalServiceError("insufficient_evidence: no current legal sources")
    ) is None


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/search/ask/simple",
            "query_string": b"",
            "headers": [],
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


def _stub_online_dependencies(monkeypatch: pytest.MonkeyPatch, graph) -> None:
    monkeypatch.setattr(
        search,
        "_resolve_ask_model_ids",
        AsyncMock(return_value=("model:s", "model:a", "model:f")),
    )
    monkeypatch.setattr(
        search.Model,
        "get",
        AsyncMock(side_effect=lambda model_id: SimpleNamespace(id=model_id)),
    )
    monkeypatch.setattr(
        search,
        "_build_conversation_context",
        AsyncMock(return_value=(None, [])),
    )
    monkeypatch.setattr(search, "ask_graph", SimpleNamespace(astream=graph))
    monkeypatch.setattr(search, "_safe_log_ask_history", AsyncMock())
    monkeypatch.setattr(search, "_persist_conversation_answer", AsyncMock())
    monkeypatch.setattr(search, "_persist_conversation_error", AsyncMock())


@pytest.mark.asyncio
async def test_transient_cloud_failure_runs_validated_qwen_local_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEGAL_LOCAL_FALLBACK_ENABLED", "true")

    async def graph(*_args, **_kwargs):
        raise RateLimitError("provider quota 429")
        yield  # pragma: no cover - makes this an async generator

    _stub_online_dependencies(monkeypatch, graph)
    local = AsyncMock(
        return_value=AskResponse(
            question="Quy định hiện hành áp dụng thế nào?",
            answer="Câu trả lời local đã qua kiểm tra nguồn.",
            grounding_status="fully_grounded",
            citations=[],
        )
    )
    monkeypatch.setattr(search, "_ask_local", local)

    response = await search._execute_ask_simple(
        AskRequest(question="Quy định hiện hành áp dụng thế nào?"),
        _request(),
    )

    assert response.error and response.error["code"] == "LOCAL_MODEL_FALLBACK"
    assert "local_model_fallback" in response.quality_flags
    fallback_request = local.await_args.args[0]
    assert fallback_request.offline_mode is True
    assert fallback_request.offline_model == "qwen2.5:3b"


@pytest.mark.asyncio
async def test_raw_httpx_timeout_runs_validated_qwen_local_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider adapters may expose httpx errors without wrapping them."""

    monkeypatch.setenv("LEGAL_LOCAL_FALLBACK_ENABLED", "true")

    async def graph(*_args, **_kwargs):
        raise httpx.ReadTimeout(
            "provider timeout",
            request=httpx.Request("POST", "https://provider.test/chat"),
        )
        yield  # pragma: no cover - makes this an async generator

    _stub_online_dependencies(monkeypatch, graph)
    local = AsyncMock(
        return_value=AskResponse(
            question="Quy định hiện hành áp dụng thế nào?",
            answer="Câu trả lời local đã qua kiểm tra nguồn.",
            grounding_status="fully_grounded",
            citations=[],
        )
    )
    monkeypatch.setattr(search, "_ask_local", local)

    response = await search._execute_ask_simple(
        AskRequest(question="Quy định hiện hành áp dụng thế nào?"),
        _request(),
    )

    assert response.error and response.error["code"] == "LOCAL_MODEL_FALLBACK"
    assert response.error["reason"] == "provider_timeout"
    local.assert_awaited_once()


@pytest.mark.asyncio
async def test_retrieval_failure_never_calls_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEGAL_LOCAL_FALLBACK_ENABLED", "true")

    async def graph(*_args, **_kwargs):
        raise LegalRetrievalUnavailableError("retrieval unavailable")
        yield  # pragma: no cover - makes this an async generator

    _stub_online_dependencies(monkeypatch, graph)
    local = AsyncMock()
    monkeypatch.setattr(search, "_ask_local", local)

    with pytest.raises(HTTPException) as exc_info:
        await search._execute_ask_simple(
            AskRequest(question="Quy định hiện hành áp dụng thế nào?"),
            _request(),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "LEGAL_RETRIEVAL_UNAVAILABLE"
    local.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_local_fallback_preserves_cloud_rate_limit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEGAL_LOCAL_FALLBACK_ENABLED", "false")

    async def graph(*_args, **_kwargs):
        raise RateLimitError("provider quota 429")
        yield  # pragma: no cover - makes this an async generator

    _stub_online_dependencies(monkeypatch, graph)
    local = AsyncMock()
    monkeypatch.setattr(search, "_ask_local", local)

    with pytest.raises(HTTPException) as exc_info:
        await search._execute_ask_simple(
            AskRequest(question="Quy định hiện hành áp dụng thế nào?"),
            _request(),
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "AI_PROVIDER_QUOTA_OR_RATE_LIMIT"
    local.assert_not_awaited()
