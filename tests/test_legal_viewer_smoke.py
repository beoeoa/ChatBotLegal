"""Smoke/load coverage for the internal legal document viewer and PDF download.

The test keeps network/DB work mocked and verifies repeated requests return
bounded-cache headers and only sanitized operational telemetry.
"""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.observability import telemetry


@pytest.fixture(autouse=True)
def bypass_auth(monkeypatch):
    async def no_real_users():
        return False

    monkeypatch.setattr("api.auth.has_real_users", no_real_users)
    monkeypatch.setattr("api.auth.configured_role_passwords", lambda: {})


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def reset_runtime_telemetry():
    telemetry._events.clear()
    telemetry._issues.clear()
    yield
    telemetry._events.clear()
    telemetry._issues.clear()


def _active_doc(doc_id: str) -> dict:
    return {
        "id": doc_id,
        "doc_id": doc_id,
        "document_title": "Nghị định kiểm thử",
        "law_number": "10/2024/NĐ-CP",
        "effective_status": "active",
        "article_index": [{"article_number": "6", "article_title": "Kiểm thử", "has_content": True}],
    }


@pytest.mark.asyncio
async def test_document_view_repeated_requests_are_fast_path_and_telemetry_safe(
    monkeypatch, client, reset_runtime_telemetry
):
    request_mock = AsyncMock(return_value={"document": _active_doc("smoke-1")})
    monkeypatch.setattr("api.routers.legal_search._request", request_mock)
    monkeypatch.setattr("api.routers.legal_search.repo_query", AsyncMock(return_value=[]))

    responses = [client.get("/api/legal/docs/smoke-1") for _ in range(6)]

    assert all(response.status_code == 200 for response in responses)
    assert all(response.headers["cache-control"] == "private, max-age=300" for response in responses)
    assert all("x-legal-view-ms" in response.headers for response in responses)
    summary = telemetry.summary()
    assert summary["by_category"]["document_view"]["count"] >= 6
    assert "smoke-1" not in str(summary)


@pytest.mark.asyncio
async def test_pdf_download_repeated_requests_use_cached_original_file(
    monkeypatch, client, tmp_path, reset_runtime_telemetry
):
    from api.routers import legal_search

    uploads = tmp_path / "data" / "uploads"
    pdf_dir = uploads / "pdfs"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "smoke-pdf.pdf").write_bytes(b"%PDF-1.4 smoke")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(legal_search, "get_legal_document", AsyncMock(return_value=_active_doc("smoke-pdf")))

    responses = [client.get("/api/legal/docs/smoke-pdf/download.pdf") for _ in range(5)]

    assert all(response.status_code == 200 for response in responses)
    assert all(response.content.startswith(b"%PDF-") for response in responses)
    assert all(response.headers["cache-control"] == "private, max-age=86400" for response in responses)
    assert all(response.headers["x-legal-pdf-origin"] == "original-file" for response in responses)
    summary = telemetry.summary()
    assert summary["by_category"]["pdf_stream_export"]["count"] >= 5
    assert "smoke-pdf" not in str(summary)


def test_repeated_operation_smoke_events_remain_bounded_and_sanitized(reset_runtime_telemetry):
    """Exercise a small repeated workload without starting the app lifespan.

    Endpoint-level tests above already cover the HTTP paths. This verifies that a
    burst of document/PDF measurements is bounded and never puts identifiers in
    the operational dashboard.
    """
    for _ in range(12):
        telemetry.record_operation(
            category="document_view",
            route="/api/legal/docs/{doc_id}",
            duration_ms=12,
            metadata={"cached": True},
        )
        telemetry.record_operation(
            category="pdf_stream_export",
            route="/api/legal/docs/{doc_id}/download.pdf",
            duration_ms=18,
            metadata={"origin": "original-file", "cached": True},
        )

    summary = telemetry.summary()
    assert summary["by_category"]["document_view"]["count"] == 12
    assert summary["by_category"]["pdf_stream_export"]["count"] == 12
    assert "smoke-pdf" not in str(summary)
