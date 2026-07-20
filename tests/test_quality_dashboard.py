"""Operational quality dashboard tests for Step 18."""
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.observability import telemetry
from api.routers import legal_quality


@pytest.fixture(autouse=True)
def reset_telemetry():
    telemetry._events.clear()
    telemetry._issues.clear()
    yield
    telemetry._events.clear()
    telemetry._issues.clear()


def test_quality_requires_admin(monkeypatch):
    monkeypatch.setattr("api.auth.get_request_role", lambda _request: "citizen")
    with pytest.raises(HTTPException) as exc:
        legal_quality._require_admin(object())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_operational_quality_counts_runtime_issues(monkeypatch):
    telemetry.record_operation(
        category="document_view",
        route="/api/legal/docs/{doc_id}",
        duration_ms=5100,
        outcome="error",
        status_code=404,
    )
    telemetry.record_issue("citation_dead", category="document_view", status_code=404, error_class="not_found")
    telemetry.record_issue("pdf_export_failed", category="pdf_stream_export", status_code=503, error_class="HTTPError")
    telemetry.record_issue("broken_form_url", category="form_download", status_code=404, error_class="file_missing")
    telemetry.record_issue("ocr_failed", category="ocr", error_class="unavailable")
    telemetry.record_issue("unanswered_question", category="ask", error_class="empty_answer")

    async def fake_query(query, _params=None):
        if "legal_crawl_candidate" in query:
            return [{"count": 2}]
        if "legal_import_job" in query:
            return [{"count": 1}]
        return []

    monkeypatch.setattr("open_notebook.database.repository.repo_query", fake_query)
    quality = await legal_quality._operational_quality()

    issues = quality["quality_issues"]
    assert issues["citation_dead"] == 1
    assert issues["pdf_export_failed"] == 1
    assert issues["broken_form_url"] == 1
    assert issues["slow_request"] == 1
    assert issues["ocr_failed"] == 3
    assert issues["import_failed"] == 1
    assert issues["unanswered_question"] == 1
    runtime_text = str(quality["runtime"])
    assert "legal:481400" not in runtime_text
    assert "Nguyen Van A" not in runtime_text
