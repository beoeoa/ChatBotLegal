from __future__ import annotations

import asyncio

import pytest

from api.routers import search


@pytest.mark.asyncio
async def test_audit_timeout_does_not_block_answer_delivery(monkeypatch):
    async def stalled(**_kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr("api.user_service.log_ask_history", stalled)
    monkeypatch.setenv("LEGAL_AUDIT_WRITE_TIMEOUT_SECONDS", "0.5")

    assert await search._safe_log_ask_history(question="private", answer="private") is False


def test_audit_trace_snapshot_excludes_query_answer_and_source_payload():
    trace = {
        "latency_by_stage": {"retrieval_ms": 12.5},
        "answer_pipeline": {
            "retrieved_chunks": 6,
            "grounding_status": "fully_grounded",
            "final_answer_chars": 120,
            "answer": "must not persist",
        },
        "query": "must not persist",
        "results": [{"content": "secret source text", "chunk_id": "x"}],
        "section_orchestration": {
            "issues": [{"validation_status": "fully_grounded", "question": "secret"}],
            "metric": {"completed": True, "repair_count": 0},
        },
    }

    snapshot = search._audit_trace_snapshot(trace)
    serialized = str(snapshot)
    assert "must not persist" not in serialized
    assert "secret source text" not in serialized
    assert "question" not in serialized
    assert snapshot["answer_pipeline"]["retrieved_chunks"] == 6
    assert snapshot["section_orchestration"]["issue_count"] == 1
