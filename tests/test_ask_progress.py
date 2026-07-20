from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.models import AskResponse
from api.routers import search


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(search.router, prefix="/api")
    return app


def _events(body: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event = "message"
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if data is not None:
            parsed.append((event, data))
    return parsed


def test_progress_endpoint_is_hidden_when_feature_flag_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LEGAL_ASK_PROGRESS_ENABLED", raising=False)
    client = TestClient(_app())

    response = client.post(
        "/api/search/ask/progress",
        json={"question": "Cần giấy tờ gì để đăng ký khai sinh?"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ASK_PROGRESS_DISABLED"


def test_progress_stream_emits_safe_ordered_contract_and_strips_non_admin_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "true")

    async def execute(_ask_request, _request, *, progress=None, trace_id_override=None):
        assert progress is not None
        await progress("status", {"stage": "generating"})
        await progress(
            "sources",
            {
                "citations": [
                    {
                        "law_number": "60/2014/QH13",
                        "article_number": "16",
                        "source_url": "https://example.test/law",
                    }
                ]
            },
        )
        await progress("status", {"stage": "validating"})
        await progress("status", {"stage": "persisting"})
        return AskResponse(
            answer="Câu trả lời đã kiểm chứng.",
            question="Cần giấy tờ gì để đăng ký khai sinh?",
            grounding_status="fully_grounded",
            rag_trace={"private": "admin-only"},
            trace_id=trace_id_override,
        )

    monkeypatch.setattr(search, "_execute_ask_simple", execute)
    client = TestClient(_app())

    response = client.post(
        "/api/search/ask/progress",
        headers={"x-user-role": "citizen", "x-user-id": "citizen01"},
        json={
            "question": "Cần giấy tờ gì để đăng ký khai sinh?",
            "idempotency_key": "progress-request-001",
            "show_rag_trace": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    names = [name for name, _ in events]
    assert names == [
        "accepted",
        "status",
        "status",
        "sources",
        "status",
        "status",
        "final",
        "complete",
    ]
    assert not {"token", "draft", "answer"}.intersection(names)
    assert events[1][1]["stage"] == "retrieving"
    assert events[3][1]["citations"][0]["law_number"] == "60/2014/QH13"
    final_response = next(data["response"] for name, data in events if name == "final")
    assert final_response["answer"] == "Câu trả lời đã kiểm chứng."
    assert final_response["rag_trace"] is None
    assert final_response["trace_id"] == events[0][1]["trace_id"]


def test_progress_stream_uses_structured_error_and_complete_without_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "true")

    async def execute(*_args, **_kwargs):
        raise HTTPException(
            status_code=503,
            detail={"code": "AI_PROVIDER_UNAVAILABLE", "message": "Tạm thời gián đoạn", "retryable": True},
        )

    monkeypatch.setattr(search, "_execute_ask_simple", execute)
    client = TestClient(_app())

    response = client.post(
        "/api/search/ask/progress",
        json={"question": "Thẩm quyền đăng ký khai sinh?"},
    )

    events = _events(response.text)
    assert [name for name, _ in events] == [
        "accepted",
        "status",
        "error",
        "complete",
    ]
    error = events[2][1]
    assert error["code"] == "AI_PROVIDER_UNAVAILABLE"
    assert error["retryable"] is True
    assert "trace_id" in error
    assert "draft" not in response.text


@pytest.mark.asyncio
async def test_cancelled_progress_execution_propagates_without_error_event() -> None:
    from api.ask_progress import stream_ask_progress

    async def execute(_progress):
        raise asyncio.CancelledError()

    stream = stream_ask_progress(
        execute,
        trace_id="trace-cancel",
        conversation_id=None,
        idempotency_key=None,
        effective_role="citizen",
    )
    assert "event: accepted" in await anext(stream)
    assert "event: status" in await anext(stream)
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)


@pytest.mark.asyncio
async def test_invalid_executor_result_is_a_structured_terminal_error() -> None:
    """A broken internal executor must not tear down SSE without `complete`."""

    from api.ask_progress import stream_ask_progress

    async def execute(_progress):
        return {"answer": "unvalidated"}

    chunks = [
        chunk
        async for chunk in stream_ask_progress(
            execute,
            trace_id="trace-invalid-result",
            conversation_id=None,
            idempotency_key="invalid-result-key",
            effective_role="citizen",
        )
    ]

    body = "".join(chunks)
    events = _events(body)
    assert [name for name, _ in events] == [
        "accepted",
        "status",
        "error",
        "complete",
    ]
    assert events[-2][1]["code"] == "ASK_FAILED"
    assert events[-1][1]["outcome"] == "error"
    assert "unvalidated" not in body
