from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
@patch("api.legal_crawl_service.LegalCrawlService.import_candidate", new_callable=AsyncMock)
async def test_import_worker_completes_queued_job(mock_import, mock_query, mock_update):
    from api.legal_crawl_service import LegalCrawlService

    job = {
        "id": "legal_import_job:one",
        "candidate": "legal_crawl_candidate:one",
        "status": "queued",
        "attempts": 0,
    }
    mock_query.return_value = [job]
    mock_import.return_value = {
        "legal_import_ok": True,
        "legal_import_result": {
            "document_id": 91,
            "activation_status": "active",
            "structure": "structured",
        },
    }

    result = await LegalCrawlService.process_next_import_job()

    assert result == {
        "job_id": "legal_import_job:one",
        "status": "completed",
        "result": mock_import.return_value,
    }
    mock_import.assert_awaited_once_with(
        "legal_crawl_candidate:one", job_id="legal_import_job:one"
    )
    assert mock_update.call_args_list[0].args[2]["status"] == "running"


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
@patch("api.legal_crawl_service.LegalCrawlService.import_candidate", new_callable=AsyncMock)
async def test_import_worker_marks_candidate_failed(mock_import, mock_query, mock_update):
    from api.legal_crawl_service import LegalCrawlService

    job = {
        "id": "legal_import_job:broken",
        "candidate": "legal_crawl_candidate:broken",
        "status": "queued",
        "attempts": 1,
    }
    mock_query.return_value = [job]
    mock_import.side_effect = RuntimeError("embedding failed")

    result = await LegalCrawlService.process_next_import_job()

    assert result["status"] == "failed"
    updates = [call.args for call in mock_update.call_args_list]
    assert any(
        args[0] == "legal_import_job" and args[2]["status"] == "failed"
        for args in updates
    )
    assert any(
        args[0] == "legal_crawl_candidate" and args[2]["status"] == "import_failed"
        for args in updates
    )


def test_citations_skip_staging_documents():
    from api.routers.search import _build_citations_from_retrieval

    citations = _build_citations_from_retrieval([
        {
            "chunk_id": "1", "doc_id": "10", "law_number": "10/2024/ND-CP",
            "document_title": "Active", "article_number": "6", "status": "active",
        },
        {
            "chunk_id": "2", "doc_id": "11", "law_number": "11/2024/ND-CP",
            "document_title": "Staging", "article_number": "7", "status": "staging",
        },
    ])

    assert [citation["doc_id"] for citation in citations] == ["10"]
    assert citations[0]["internal_url"] == "/legal-documents/10?article=6"
    assert citations[0]["pdf_url"].endswith("?article=6")


def test_import_activation_and_viewer_are_active_gated():
    source = Path("scripts/legal_search_server.py").read_text(encoding="utf-8")
    assert ":expired_date, 'staging', :source_url" in source
    assert ":effective_from, :effective_to, 'staging'" in source
    assert "'pending_embedding_activation'" in source
    assert "'embedding_completed'" in source
    assert "UPDATE legal_documents SET status = 'active'" in source
    assert source.count("AND d.status = 'active'") >= 2


def test_validate_candidate_import_guards():
    from api.legal_crawl_service import LegalCrawlService

    candidate = {
        "title": "Van ban hop le",
        "law_number": "01/2024/QD-UBND",
        "scope": "central",
        "status": "approved",
        "review_status": "approved",
        "source_url": "https://example.test/document",
        "content": "Dieu 1. " + "Noi dung hop le. " * 10,
        "raw_metadata": {
            "confirmed_official_source": True,
            "effective_date": "2024-01-01",
        },
    }
    assert LegalCrawlService.validate_candidate_for_import(candidate) == []

    candidate["duplicate_candidates"] = ["legal_crawl_candidate:old"]
    assert LegalCrawlService.validate_candidate_for_import(candidate)


def test_structure_chunking_and_asset_provenance_paths():
    from scripts.legal_search_server import _build_import_units, LegalImportRequest

    request = LegalImportRequest(
        title="Van ban", law_number="01/2024/QD-UBND", document_type="Quyet dinh",
        issuing_agency="UBND", scope="central", field_id=1,
        effective_date="2024-01-01", confirmed_official_source=True,
        content="Điều 1. Nội dung một.\n\nĐiều 2. Nội dung hai.",
    )
    structure, articles, _chunks = _build_import_units(request)
    assert structure == "structured"
    assert [article["article_number"] for article in articles] == ["1", "2"]

    request = request.model_copy(update={
        "content": "Muc I\n\nNoi dung thu nhat.\n\nMuc II\n\nNoi dung thu hai.",
    })
    structure, articles, chunks = _build_import_units(request)
    assert structure == "unstructured"
    assert articles[0]["article_number"] == "0"
    assert len(chunks) >= 2

    api_source = Path("api/routers/legal_search.py").read_text(encoding="utf-8")
    service_source = Path("api/legal_crawl_service.py").read_text(encoding="utf-8")
    assert "legal_sources" in api_source
    assert "_persist_approved_source_asset" in service_source

@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.LegalCrawlService.get_candidate", new_callable=AsyncMock)
async def test_e2e_approved_candidate_to_active_citation_article_and_vietnamese_pdf(
    mock_get_candidate, mock_update
):
    """Contract E2E across candidate import, active citation, viewer article and PDF.

    The legal-search HTTP service is deliberately mocked at its public boundary;
    its own staged activation and Unicode PDF behaviour are covered by the real
    retriever tests in this same focused suite.
    """
    import fitz
    from api.legal_crawl_service import LegalCrawlService
    from api.routers.search import _build_citations_from_retrieval
    from scripts.legal_search_server import LegalRetriever

    candidate = {
        "id": "legal_crawl_candidate:e2e-approved",
        "title": "Quy định về quyền sử dụng đất",
        "law_number": "10/2024/NĐ-CP",
        "content": "Điều 2. Người sử dụng đất tại Phường Lê Chân, Hải Phòng được bảo vệ quyền và lợi ích hợp pháp theo quy định của pháp luật.",
        "status": "approved",
        "review_status": "approved",
        "scope": "central",
        "source_url": "https://example.test/official-10-2024",
        "raw_metadata": {
            "confirmed_official_source": True,
            "domain": "dat_dai_xay_dung",
            "effective_date": "2024-01-01",
        },
    }
    mock_get_candidate.return_value = candidate

    class FakeResponse:
        status_code = 200
        content = b"{}"
        text = ""
        def json(self):
            return {
                "status": "embedded_active",
                "activation_status": "active",
                "document_id": 42,
                "law_number": "10/2024/NĐ-CP",
                "structure": "structured",
                "article_count": 1,
                "chunk_count": 1,
                "model": "VNLegal-LAL",
            }

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    with patch("api.legal_crawl_service.httpx.AsyncClient", return_value=FakeClient()):
        result = await LegalCrawlService.import_candidate(
            "legal_crawl_candidate:e2e-approved", job_id="legal_import_job:e2e"
        )

    assert result["legal_import_ok"] is True
    assert result["legal_import_result"]["activation_status"] == "active"
    assert any(
        call.args[0] == "legal_crawl_candidate" and call.args[2]["status"] == "imported"
        for call in mock_update.call_args_list
    )

    citations = _build_citations_from_retrieval([{
        "chunk_id": "900", "doc_id": "42", "law_number": "10/2024/NĐ-CP",
        "document_title": candidate["title"], "article_number": "2",
        "article_title": "Quyền của người sử dụng đất", "status": "active",
        "source_url": candidate["source_url"],
    }])
    assert citations[0]["doc_id"] == "42"
    assert citations[0]["internal_url"] == "/legal-documents/42?article=2"
    assert citations[0]["pdf_url"].endswith("/42/download.pdf?article=2")

    class ViewerRetriever:
        def document_detail(self, doc_id, article=None, include_content=False):
            assert doc_id == "42"
            assert article == "2"
            assert include_content is True
            return {
                "document_title": candidate["title"],
                "law_number": candidate["law_number"],
                "source_url": candidate["source_url"],
                "article_index": [{"article_number": "2", "article_title": "Quyền của người sử dụng đất"}],
                "articles": [{
                    "article_number": "2",
                    "article_title": "Quyền của người sử dụng đất",
                    "chunks": [{"content": "Phường Lê Chân, Hải Phòng"}],
                }],
            }

    pdf_bytes, filename = LegalRetriever.document_pdf(ViewerRetriever(), "42", article="2")
    assert filename.endswith(".pdf")
    assert filename.startswith("10-2024-N")
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = "\n".join(page.get_text("text") for page in pdf).replace("\u00a0", " ")
    pdf.close()
    assert "Điều 2" in text
    assert "Phường Lê Chân, Hải Phòng" in text
