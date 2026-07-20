from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.models import AskResponse
from api.routers import search


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(search.router, prefix="/api")
    return app


def _payloads(body: str) -> list[dict]:
    payloads: list[dict] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        data_lines = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
        if data_lines:
            payloads.append(json.loads("\n".join(data_lines)))
    return payloads


def test_public_legacy_ask_emits_lifecycle_and_validated_final_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
            answer="Bản cuối đã kiểm chứng.",
            question="Cần giấy tờ gì để đăng ký khai sinh?",
            grounding_status="fully_grounded",
            rag_trace={"private": "admin-only"},
            trace_id=trace_id_override,
        )

    monkeypatch.setattr(search, "_execute_ask_simple", execute)
    client = TestClient(_app())

    response = client.post(
        "/api/search/ask",
        headers={"x-user-role": "citizen", "x-user-id": "citizen01"},
        json={
            "question": "Cần giấy tờ gì để đăng ký khai sinh?",
            "show_rag_trace": True,
            "idempotency_key": "legacy-safe-001",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    payloads = _payloads(response.text)
    event_types = [payload["type"] for payload in payloads]
    assert event_types == [
        "accepted",
        "status",
        "status",
        "sources",
        "status",
        "status",
        "final",
        "final_answer",
        "complete",
    ]
    assert not {
        "strategy",
        "answer",
        "draft",
        "token",
        "rag_trace",
    }.intersection(event_types)
    final = next(payload["response"] for payload in payloads if payload["type"] == "final")
    assert final["answer"] == "Bản cuối đã kiểm chứng."
    assert final["rag_trace"] is None
    compatibility_final = next(
        payload for payload in payloads if payload["type"] == "final_answer"
    )
    assert compatibility_final == {
        "type": "final_answer",
        "content": final["answer"],
        "validated": True,
        "trace_id": final["trace_id"],
    }
    assert event_types.index("final_answer") > event_types.index("final")


def test_public_legacy_ask_has_only_one_registered_route() -> None:
    app = _app()
    routes = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/search/ask"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(routes) == 1
