from pathlib import Path
from datetime import UTC, date, datetime, timedelta
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
    claim_sql = mock_query.call_args_list[1].args[0]
    assert "UPDATE legal_import_job" in claim_sql
    assert "status = 'queued'" in claim_sql
    assert any(
        args[0] == "legal_crawl_candidate" and args[2]["import_status"] == "running"
        for args in (call.args for call in mock_update.call_args_list)
    )


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
@patch("api.legal_crawl_service.LegalCrawlService.import_candidate", new_callable=AsyncMock)
async def test_import_worker_skips_job_claimed_by_another_worker(
    mock_import,
    mock_query,
    mock_update,
):
    """A stale queue read must not let two workers import one candidate."""
    from api.legal_crawl_service import LegalCrawlService

    job = {
        "id": "legal_import_job:claimed-elsewhere",
        "candidate": "legal_crawl_candidate:claimed-elsewhere",
        "status": "queued",
        "attempts": 0,
    }
    # The first worker read the job, but another worker claimed it before this
    # worker's conditional UPDATE ran.
    mock_query.side_effect = [[job], []]

    result = await LegalCrawlService.process_next_import_job()

    assert result is None
    mock_import.assert_not_awaited()
    mock_update.assert_not_awaited()
    assert "WHERE id = $id AND status = 'queued'" in mock_query.call_args_list[1].args[0]


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
async def test_processing_worker_skips_job_claimed_by_another_worker(mock_query):
    """OCR must not read or process a file after a lost job claim."""
    from api.legal_crawl_service import LegalCrawlService

    job = {
        "id": "legal_candidate_processing_job:claimed-elsewhere",
        "candidate": "legal_crawl_candidate:claimed-elsewhere",
        "status": "queued",
        "attempts": 0,
    }
    mock_query.side_effect = [[job], []]

    result = await LegalCrawlService.process_next_processing_job()

    assert result is None
    assert "UPDATE legal_candidate_processing_job" in mock_query.call_args_list[1].args[0]
    assert "WHERE id = $id AND status = 'queued'" in mock_query.call_args_list[1].args[0]


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


@pytest.mark.asyncio
async def test_import_worker_reclassifies_legacy_duplicate_as_human_review(monkeypatch):
    """A late duplicate response must not leave an endlessly retryable job."""
    from api import legal_crawl_service
    from api.legal_crawl_service import LegalCrawlService

    job = {
        "id": "legal_import_job:legacy-duplicate",
        "candidate": "legal_crawl_candidate:legacy-duplicate",
        "status": "queued",
        "attempts": 0,
    }
    candidate = {
        "id": "legal_crawl_candidate:legacy-duplicate",
        "law_number": "16/2022/NĐ-CP",
    }
    updates = []
    marked = []

    async def query(_sql, _params=None):
        return [job]

    async def update(*args):
        updates.append(args)
        return []

    async def import_candidate(*_args, **_kwargs):
        raise RuntimeError("Legal Search import rejected: số hiệu đã tồn tại")

    async def get_candidate(_candidate_id):
        return candidate

    async def find_conflict(_candidate):
        return {"id": 109385, "law_number": "16/2022/NĐ-CP"}

    async def mark_conflict(found_candidate, conflict):
        marked.append((found_candidate, conflict))
        return {**found_candidate, "status": "changes_requested"}

    monkeypatch.setattr(legal_crawl_service, "repo_query", query)
    monkeypatch.setattr(legal_crawl_service, "repo_update", update)
    monkeypatch.setattr(LegalCrawlService, "import_candidate", import_candidate)
    monkeypatch.setattr(LegalCrawlService, "get_candidate", get_candidate)
    monkeypatch.setattr(LegalCrawlService, "find_runtime_document_conflict", find_conflict)
    monkeypatch.setattr(LegalCrawlService, "mark_runtime_document_conflict", mark_conflict)

    result = await LegalCrawlService.process_next_import_job()

    assert result["status"] == "failed"
    assert result["failure_kind"] == "duplicate_conflict"
    assert marked == [(candidate, {"id": 109385, "law_number": "16/2022/NĐ-CP"})]
    assert any(
        args[0] == "legal_import_job" and args[2]["status"] == "failed"
        for args in updates
    )
    assert not any(
        args[0] == "legal_crawl_candidate" and args[2]["status"] == "import_failed"
        for args in updates
    )


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
@patch("api.legal_crawl_service.LegalCrawlService.import_candidate", new_callable=AsyncMock)
async def test_import_worker_marks_job_failed_when_candidate_running_state_cannot_be_recorded(
    mock_import,
    mock_query,
    mock_update,
):
    from api.legal_crawl_service import LegalCrawlService

    job = {
        "id": "legal_import_job:status-write-failed",
        "candidate": "legal_crawl_candidate:status-write-failed",
        "status": "queued",
        "attempts": 0,
    }
    mock_query.return_value = [job]
    mock_update.side_effect = [RuntimeError("candidate status update failed"), None, None]

    result = await LegalCrawlService.process_next_import_job()

    assert result["status"] == "failed"
    mock_import.assert_not_awaited()
    updates = [call.args for call in mock_update.call_args_list]
    assert any(
        args[0] == "legal_import_job" and args[2]["status"] == "failed"
        for args in updates
    )
    assert any(
        args[0] == "legal_crawl_candidate" and args[2]["status"] == "import_failed"
        for args in updates
    )


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
async def test_import_worker_recovers_only_timed_out_running_jobs(mock_query, mock_update, monkeypatch):
    from api import legal_crawl_service
    from api.legal_crawl_service import LegalCrawlService

    now = datetime(2026, 8, 7, tzinfo=UTC)
    monkeypatch.setenv("LEGAL_IMPORT_JOB_STALE_AFTER_SECONDS", "1800")
    monkeypatch.setattr(legal_crawl_service, "_utcnow", lambda: now)
    mock_query.return_value = [
        {
            "id": "legal_import_job:stale",
            "candidate": "legal_crawl_candidate:stale",
            "status": "running",
            "started_at": now - timedelta(minutes=31),
        },
        {
            "id": "legal_import_job:fresh",
            "candidate": "legal_crawl_candidate:fresh",
            "status": "running",
            "started_at": now - timedelta(minutes=1),
        },
        {
            "id": "legal_import_job:unknown-time",
            "candidate": "legal_crawl_candidate:unknown-time",
            "status": "running",
            "started_at": None,
        },
    ]

    recovered = await LegalCrawlService.recover_stale_import_jobs()

    assert recovered == 1
    updates = [call.args for call in mock_update.call_args_list]
    assert len(updates) == 2
    assert updates[0][0] == "legal_import_job"
    assert updates[0][1] == "legal_import_job:stale"
    assert updates[0][2]["status"] == "queued"
    assert updates[1][0] == "legal_crawl_candidate"
    assert updates[1][2]["import_status"] == "queued"


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
async def test_processing_worker_recovers_only_timed_out_running_jobs(mock_query, mock_update, monkeypatch):
    from api import legal_crawl_service
    from api.legal_crawl_service import LegalCrawlService

    now = datetime(2026, 8, 7, tzinfo=UTC)
    monkeypatch.setenv("LEGAL_IMPORT_JOB_STALE_AFTER_SECONDS", "1800")
    monkeypatch.setattr(legal_crawl_service, "_utcnow", lambda: now)
    mock_query.return_value = [
        {
            "id": "legal_candidate_processing_job:stale",
            "candidate": "legal_crawl_candidate:stale",
            "status": "running",
            "started_at": now - timedelta(minutes=31),
        },
        {
            "id": "legal_candidate_processing_job:fresh",
            "candidate": "legal_crawl_candidate:fresh",
            "status": "running",
            "started_at": now - timedelta(minutes=1),
        },
        {
            "id": "legal_candidate_processing_job:unknown-time",
            "candidate": "legal_crawl_candidate:unknown-time",
            "status": "running",
            "started_at": None,
        },
    ]

    recovered = await LegalCrawlService.recover_stale_processing_jobs()

    assert recovered == 1
    updates = [call.args for call in mock_update.call_args_list]
    assert len(updates) == 2
    assert updates[0][0] == "legal_candidate_processing_job"
    assert updates[0][1] == "legal_candidate_processing_job:stale"
    assert updates[0][2]["status"] == "queued"
    assert updates[1][0] == "legal_crawl_candidate"
    assert updates[1][2]["processing_status"] == "queued"


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
    assert '"doc_status": "active", "status": "active"' in source
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
            "document_type": "Quyet dinh",
            "issuing_agency": "UBND Thanh pho Hai Phong",
            "issued_date": "2023-12-20",
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


class _ImportResult:
    """Small SQLAlchemy-result stand-in for isolated import lifecycle tests."""

    def __init__(
        self,
        *,
        scalar: int | None = None,
        first: dict | None = None,
        one: dict | None = None,
        rows: list[dict] | None = None,
    ) -> None:
        self._scalar = scalar
        self._first = first
        self._one = one
        self._rows = rows or []

    def scalar_one(self):
        return self._scalar

    def mappings(self):
        return self

    def first(self):
        return self._first

    def one(self):
        return self._one

    def all(self):
        return self._rows


class _ImportConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self._article_id = 700
        self._chunk_id = 800

    def execute(self, statement, _params=None):
        sql = str(statement)
        if "SELECT id, title, law_number" in sql:
            return _ImportResult(rows=[])
        if "SELECT id, name FROM legal_fields" in sql:
            return _ImportResult(first={"id": 1, "name": "Hộ tịch"})
        if "INSERT INTO legal_documents" in sql:
            self.events.append("document_staging")
            return _ImportResult(scalar=501)
        if "INSERT INTO legal_search_scope" in sql:
            self.events.append("scope_staging")
            return _ImportResult()
        if "INSERT INTO legal_articles" in sql:
            self.events.append("article_staging")
            return _ImportResult(scalar=self._article_id)
        if "INSERT INTO legal_article_chunks" in sql:
            self._chunk_id += 1
            self.events.append("chunk_staging")
            return _ImportResult(one={"id": self._chunk_id})
        if "UPDATE legal_documents SET status = 'active'" in sql:
            self.events.append("document_active")
            return _ImportResult()
        if "UPDATE legal_articles SET status = 'active'" in sql:
            self.events.append("article_active")
            return _ImportResult()
        if "SET included = TRUE" in sql:
            self.events.append("scope_active")
            return _ImportResult()
        if "DELETE FROM legal_documents" in sql:
            self.events.append("document_rolled_back")
            return _ImportResult()
        raise AssertionError(f"Unexpected SQL in isolated import test: {sql}")


class _ImportContext:
    def __init__(self, connection: _ImportConnection) -> None:
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class _ImportEngine:
    def __init__(self, connection: _ImportConnection) -> None:
        self.connection = connection

    def connect(self):
        return _ImportContext(self.connection)

    def begin(self):
        return _ImportContext(self.connection)


class _ImportCollection:
    def __init__(self, name: str, events: list[str], *, fail_upsert: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_upsert = fail_upsert
        self.upserts: list[dict] = []
        self.updates: list[dict] = []
        self.deletes: list[dict] = []

    def upsert(self, **kwargs) -> None:
        self.events.append(f"{self.name}_upsert")
        self.upserts.append(kwargs)
        if self.fail_upsert:
            raise RuntimeError(f"{self.name} unavailable")

    def delete(self, **kwargs) -> None:
        self.events.append(f"{self.name}_delete")
        self.deletes.append(kwargs)

    def update(self, **kwargs) -> None:
        self.events.append(f"{self.name}_update")
        self.updates.append(kwargs)


class _ImportChromaClient:
    def __init__(self, source_collection: _ImportCollection) -> None:
        self.source_collection = source_collection

    def get_collection(self, _name: str):
        return self.source_collection


def _isolated_import_retriever(monkeypatch, *, source_fails: bool = False):
    """Build a no-network, no-database harness around the real import method."""
    import scripts.legal_search_server as legal_search

    # These tests cover the legacy activation transaction explicitly.  The
    # production default is fail-closed and rejects direct active-collection
    # mutation; an isolated harness opts in only for this compatibility test.
    monkeypatch.setenv("LEGAL_ALLOW_LEGACY_DIRECT_IMPORT", "1")
    events: list[str] = []
    connection = _ImportConnection(events)
    primary = _ImportCollection("primary", events)
    source = _ImportCollection("source", events, fail_upsert=source_fails)
    retriever = legal_search.LegalRetriever()
    retriever._engine = _ImportEngine(connection)
    retriever._collection = primary
    retriever._load = lambda: None
    retriever.encode_passages = lambda passages: [[0.1, 0.2] for _ in passages]
    monkeypatch.setattr(
        legal_search.chromadb,
        "PersistentClient",
        lambda **_kwargs: _ImportChromaClient(source),
    )
    return legal_search, retriever, events, primary, source


def test_direct_active_import_is_disabled_by_default(monkeypatch):
    legal_search, retriever, _events, _primary, _source = _isolated_import_retriever(
        monkeypatch
    )
    monkeypatch.delenv("LEGAL_ALLOW_LEGACY_DIRECT_IMPORT", raising=False)

    with pytest.raises(
        RuntimeError,
        match="direct_active_collection_mutation_disabled",
    ):
        retriever.import_document(_isolated_import_request(legal_search))


def _isolated_import_request(legal_search):
    return legal_search.LegalImportRequest(
        title="Quy định thí điểm thủ tục hộ tịch tại Hải Phòng",
        law_number="99/2024/QĐ-UBND",
        document_type="Quyết định",
        issuing_agency="UBND thành phố Hải Phòng",
        scope="Hải Phòng",
        field_id=1,
        effective_date=date(2024, 1, 1),
        source_url="https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=1",
        confirmed_official_source=True,
        content="Điều 1. Quy định nguyên tắc giải quyết thủ tục hộ tịch.\n\nĐiều 2. Cơ quan tiếp nhận có trách nhiệm hướng dẫn công dân.",
    )


def test_real_import_lifecycle_activates_only_after_both_vector_collections(monkeypatch):
    legal_search, retriever, events, primary, source = _isolated_import_retriever(monkeypatch)

    result = retriever.import_document(_isolated_import_request(legal_search))

    assert result["status"] == "embedded_active"
    assert result["activation_status"] == "active"
    assert primary.upserts and source.upserts
    assert primary.updates and source.updates
    assert all(
        metadata["status"] == metadata["doc_status"] == "active"
        for metadata in primary.updates[0]["metadatas"]
    )
    assert events.index("primary_upsert") < events.index("source_upsert")
    assert events.index("source_upsert") < events.index("primary_update")
    assert events.index("source_update") < events.index("document_active")
    assert events[-1] == "scope_active"
    assert not primary.deletes and not source.deletes


def test_real_import_lifecycle_rolls_back_when_second_vector_collection_fails(monkeypatch):
    legal_search, retriever, events, primary, source = _isolated_import_retriever(
        monkeypatch,
        source_fails=True,
    )

    with pytest.raises(RuntimeError, match="source unavailable"):
        retriever.import_document(_isolated_import_request(legal_search))

    assert "document_active" not in events
    assert "scope_active" not in events
    assert primary.deletes and source.deletes
    assert events[-1] == "document_rolled_back"


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.LegalCrawlService.get_candidate", new_callable=AsyncMock)
async def test_candidate_import_rejects_incomplete_activation_confirmation(
    mock_get_candidate,
    mock_update,
):
    """A partial local import response must never make a candidate serving."""
    from api.legal_crawl_service import LegalCrawlService

    mock_get_candidate.return_value = {
        "id": "legal_crawl_candidate:partial-response",
        "title": "Quy định thử nghiệm",
        "law_number": "99/2024/QĐ-UBND",
        "content": "Điều 1. Nội dung đủ dài để kiểm tra import. " * 4,
        "status": "approved",
        "review_status": "approved",
        "scope": "central",
        "source_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=1",
        "raw_metadata": {
            "confirmed_official_source": True,
            "document_type": "Quyết định",
            "issuing_agency": "UBND Thành phố Hải Phòng",
            "issued_date": "2023-12-20",
            "effective_date": "2024-01-01",
        },
    }

    class PartialResponse:
        status_code = 200
        content = b"{}"
        text = ""

        def json(self):
            return {
                "status": "staging",
                "activation_status": "active",
                "document_id": 42,
                "chunk_count": 1,
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *_args, **_kwargs):
            return PartialResponse()

    with patch("api.legal_crawl_service.httpx.AsyncClient", return_value=FakeClient()):
        with pytest.raises(RuntimeError, match="chưa xác nhận đủ"):
            await LegalCrawlService.import_candidate(
                "legal_crawl_candidate:partial-response",
            )

    assert any(
        call.args[0] == "legal_crawl_candidate"
        and call.args[2]["status"] == "import_failed"
        for call in mock_update.call_args_list
    )


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
            "document_type": "Quyết định",
            "issuing_agency": "UBND Thành phố Hải Phòng",
            "issued_date": "2023-12-20",
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
    imported_update = next(
        call.args[2]
        for call in mock_update.call_args_list
        if call.args[0] == "legal_crawl_candidate" and call.args[2].get("status") == "imported"
    )
    assert imported_update["imported_document"]["document_id"] == 42
    job_update = next(
        call.args[2]
        for call in mock_update.call_args_list
        if call.args[0] == "legal_import_job" and call.args[2].get("status") == "completed"
    )
    assert job_update["document_id"] == "42"

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


def test_lookup_route_precedes_dynamic_document_route():
    """A law-number lookup must not be captured as a document id."""

    source = Path("scripts/legal_search_server.py").read_text(encoding="utf-8")
    assert source.index('@app.get("/documents/lookup")') < source.index(
        '@app.get("/documents/{doc_id}")'
    )
