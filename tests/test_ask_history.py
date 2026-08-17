"""
Test for log_ask_history and list_ask_history with extended fields.
"""

import pytest
from unittest.mock import AsyncMock, patch

from api.observability import RuntimeTelemetry


def _mock_repo_create():
    return AsyncMock(return_value=[{"id": "user:fake", "created": "2026-07-07", "updated": "2026-07-07"}])


def _mock_repo_query():
    return AsyncMock(return_value=[])


@pytest.mark.asyncio
class TestLogAskHistory:

    @patch("open_notebook.database.repository.repo_create", new_callable=_mock_repo_create)
    @patch("api.user_service.repo_create")
    async def test_log_ask_history_stores_all_required_fields(self, mock_svc_create, mock_db_create):
        """log_ask_history should accept and store all extended fields."""
        from api.user_service import log_ask_history

        sources = [{"chunk_id": "456", "document_title": "Luat Ho tich 2014", "law_number": "60/2014/QH13"}]
        file_meta = {"filename": "don_xin_phep.doc", "original_size_bytes": 12345, "mime_type": "application/msword"}

        await log_ask_history(
            owner_user_id="user:1", owner_role="citizen",
            question="Lam sao de dang ky khai sinh?",
            answer="Ban can den UBND phuong noi cu tru.",
            domain="ho_tich", department=None,
            strategy_model="gpt-5-mini", answer_model="gpt-5-mini", final_answer_model="gpt-5-mini",
            offline_mode=False, offline_model=None, rag_trace={"retrieval_count": 3},
            sources=sources, grounding_status="fully_grounded", duration_ms=2500,
            file_upload_metadata=file_meta,
        )

        mock_svc_create.assert_called_once()
        table_name, data = mock_svc_create.call_args[0]
        assert table_name == "user_ask_history"
        assert data["owner_role"] == "citizen"
        assert data["sources"] == sources
        assert data["grounding_status"] == "fully_grounded"
        assert data["duration_ms"] == 2500
        assert data["file_upload_metadata"] == file_meta

    @patch("open_notebook.database.repository.repo_create", new_callable=_mock_repo_create)
    @patch("api.user_service.repo_create")
    async def test_log_ask_history_defaults_null_fields(self, mock_svc_create, mock_db_create):
        """Default values when optional fields omitted."""
        from api.user_service import log_ask_history

        await log_ask_history(
            owner_user_id="user:2", owner_role="citizen",
            question="Test?", answer="Test.", domain=None,
            strategy_model="gpt-5-mini", answer_model="gpt-5-mini", final_answer_model="gpt-5-mini",
            offline_mode=False, offline_model=None, rag_trace=None, duration_ms=100,
        )

        _, data = mock_svc_create.call_args[0]
        assert data["department"] is None
        assert data["sources"] == []
        assert data["grounding_status"] == "unknown"
        assert data["file_upload_metadata"] == {}

    @patch("open_notebook.database.repository.repo_create", new_callable=_mock_repo_create)
    @patch("api.user_service.repo_create")
    async def test_log_ask_history_with_officer_department(self, mock_svc_create, mock_db_create):
        """Officer department recorded."""
        from api.user_service import log_ask_history

        await log_ask_history(
            owner_user_id="officer:1", owner_role="officer",
            question="Can cu xu phat?", answer="Nghi dinh 100...",
            domain="trat_tu_do_thi", department="trat-tu-xa-hoi",
            strategy_model="gpt-5-mini", answer_model="gpt-5-mini", final_answer_model="gpt-5-mini",
            offline_mode=False, offline_model=None, rag_trace=None, duration_ms=800,
        )

        _, data = mock_svc_create.call_args[0]
        assert data["owner_role"] == "officer"
        assert data["department"] == "trat-tu-xa-hoi"

    @patch("api.user_service.repo_create")
    async def test_log_ask_history_compacts_sources_to_a_metadata_allowlist(self, mock_create):
        from api.user_service import log_ask_history

        source = {
            "id": "legal:456",
            "chunk_id": "456",
            "document_id": "doc-12",
            "article_id": "article-3",
            "law_number": "60/2014/QH13",
            "document_title": "Luật Hộ tịch 2014",
            "article_number": "16",
            "article_title": "Thủ tục đăng ký khai sinh",
            "effective_status": "active",
            "effective_date": "2016-01-01",
            "issuing_agency": "Quốc hội",
            "scope": "toàn quốc",
            "source_url": "https://example.test/legal/60-2014",
            "score": 0.91,
            "content": "Nội dung nguồn không được sao chép vào audit.",
            "chunk_heading": "Dữ liệu prompt không cần cho audit",
            "_retrieval_trace": {"query": "nội dung riêng tư"},
        }

        await log_ask_history(
            owner_user_id="user:1",
            owner_role="citizen",
            question="Câu hỏi riêng tư",
            answer="Câu trả lời riêng tư",
            domain="ho_tich_chung_thuc",
            strategy_model="strategy",
            answer_model="answer",
            final_answer_model="final",
            offline_mode=False,
            offline_model=None,
            rag_trace=None,
            sources=[source],
            grounding_status="fully_grounded",
            duration_ms=100,
        )

        snapshot = mock_create.await_args.args[1]["sources"][0]
        assert snapshot == {
            "chunk_id": "456",
            "document_id": "doc-12",
            "article_id": "article-3",
            "law_number": "60/2014/QH13",
            "document_title": "Luật Hộ tịch 2014",
            "article_number": "16",
            "article_title": "Thủ tục đăng ký khai sinh",
            "effective_status": "active",
            "effective_date": "2016-01-01",
            "issuing_agency": "Quốc hội",
            "scope": "toàn quốc",
            "source_url": "https://example.test/legal/60-2014",
            "score": 0.91,
        }
        assert "content" not in snapshot
        assert "_retrieval_trace" not in snapshot

    @patch("api.user_service.repo_create")
    async def test_log_ask_history_records_sanitized_audit_outcomes(
        self, mock_create, monkeypatch
    ):
        import api.user_service as user_service

        local_telemetry = RuntimeTelemetry()
        monkeypatch.setattr(user_service, "telemetry", local_telemetry)
        mock_create.side_effect = RuntimeError(
            "question=private token=do-not-retain filename=secret.pdf"
        )

        with pytest.raises(RuntimeError):
            await user_service.log_ask_history(
                owner_user_id="user:1",
                owner_role="citizen",
                question="Câu hỏi riêng tư",
                answer="Câu trả lời riêng tư",
                domain=None,
                strategy_model=None,
                answer_model=None,
                final_answer_model=None,
                offline_mode=False,
                offline_model=None,
                rag_trace=None,
                duration_ms=10,
            )

        summary = local_telemetry.summary()
        assert summary["ask_outcomes"]["audit"]["failed"] == 1
        assert summary["by_category"]["ask.persistence"]["error_count"] == 1
        assert summary["issue_counts"]["ask_audit_write_failed"] == 1
        serialized = str(summary).casefold()
        assert "private" not in serialized
        assert "do-not-retain" not in serialized
        assert "secret.pdf" not in serialized


@pytest.mark.asyncio
class TestListAskHistory:

    def _make_row(self, **overrides):
        return {
            "id": "rec:123", "owner_user": "u", "owner_role": "citizen",
            "question": "q", "answer": "a", "domain": None, "department": None,
            "strategy_model": "m", "answer_model": "m", "final_answer_model": "m",
            "offline_mode": False, "offline_model": None, "rag_trace": {}, "sources": [],
            "grounding_status": "unknown", "duration_ms": 100,
            "created": "2026-07-01T00:00:00Z", "file_upload_metadata": {},
            **overrides,
        }

    @patch("open_notebook.database.repository.repo_query", new_callable=_mock_repo_query)
    @patch("api.user_service.repo_query")
    async def test_list_ask_history_filters_by_role(self, mock_svc_query, mock_db_query):
        from api.user_service import list_ask_history
        mock_svc_query.return_value = [self._make_row(owner_role="citizen")]
        results = await list_ask_history(limit=10, role="citizen")
        assert len(results) == 1
        assert results[0]["owner_role"] == "citizen"

    @patch("open_notebook.database.repository.repo_query", new_callable=_mock_repo_query)
    @patch("api.user_service.repo_query")
    async def test_list_ask_history_filters_by_grounding_status(self, mock_svc_query, mock_db_query):
        from api.user_service import list_ask_history
        mock_svc_query.return_value = [self._make_row(
            owner_role="officer", department="dat-dai",
            grounding_status="partially_grounded",
        )]
        results = await list_ask_history(limit=10, grounding_status="partially_grounded")
        assert len(results) == 1
        assert results[0]["grounding_status"] == "partially_grounded"
        query, params = mock_svc_query.await_args.args
        assert "grounding_status = $grounding_status" in query
        assert params["grounding_status"] == "partially_grounded"

    @patch("open_notebook.database.repository.repo_query", new_callable=_mock_repo_query)
    @patch("api.user_service.repo_query")
    async def test_list_ask_history_filters_by_account_and_department(self, mock_svc_query, mock_db_query):
        from api.user_service import list_ask_history

        mock_svc_query.return_value = []
        await list_ask_history(
            limit=25,
            user_id="user_account:citizen-1",
            department="Tư pháp - Hộ tịch",
        )

        query, params = mock_svc_query.await_args.args
        assert "owner_user = type::record($owner_user_id)" in query
        assert "department = $department" in query
        assert params["owner_user_id"] == "user_account:citizen-1"
        assert params["department"] == "Tư pháp - Hộ tịch"

    @patch("open_notebook.database.repository.repo_query", new_callable=_mock_repo_query)
    @patch("api.user_service.repo_query")
    async def test_list_ask_history_filters_by_domain(self, mock_svc_query, mock_db_query):
        from api.user_service import list_ask_history
        mock_svc_query.return_value = []
        results = await list_ask_history(limit=50, domain="cu_tru")
        assert len(results) == 0

    @patch("open_notebook.database.repository.repo_query", new_callable=_mock_repo_query)
    @patch("api.user_service.repo_query")
    async def test_list_ask_history_includes_all_new_fields(self, mock_svc_query, mock_db_query):
        from api.user_service import list_ask_history
        mock_svc_query.return_value = [self._make_row(
            owner_role="admin", domain="trat_tu", department="trat-tu",
            offline_mode=True, offline_model="qwen2.5:3b",
            rag_trace={"retrieval_count": 5}, sources=[{"chunk_id": "c1"}],
            grounding_status="fully_grounded", duration_ms=5000,
            file_upload_metadata={"filename": "test.pdf"},
        )]
        results = await list_ask_history(limit=10)
        assert len(results) == 1
        r = results[0]
        assert r["department"] == "trat-tu"
        assert r["sources"] == [{"chunk_id": "c1"}]
        assert r["grounding_status"] == "fully_grounded"
        assert r["file_upload_metadata"] == {"filename": "test.pdf"}

    @patch("open_notebook.database.repository.repo_query", new_callable=_mock_repo_query)
    @patch("api.user_service.repo_query")
    async def test_list_ask_history_summary_omits_large_payloads(self, mock_svc_query, mock_db_query):
        from api.user_service import list_ask_history

        mock_svc_query.return_value = [self._make_row(
            answer="very large answer",
            rag_trace={"large": ["payload"] * 100},
            sources=[{"chunk_id": "c1"}],
            file_upload_metadata={"filename": "private.pdf"},
        )]

        results = await list_ask_history(limit=10, summary_only=True)

        assert len(results) == 1
        assert results[0]["question"] == "q"
        assert "answer" not in results[0]
        assert "rag_trace" not in results[0]
        assert "sources" not in results[0]
        assert "file_upload_metadata" not in results[0]
        query = mock_svc_query.await_args.args[0]
        assert "SELECT id, owner_user, owner_role, question" in query
        assert "SELECT *" not in query

    @patch("api.user_service.repo_query")
    async def test_list_ask_history_reads_outer_json_and_legacy_json_strings(self, mock_query):
        from api.user_service import list_ask_history

        mock_query.return_value = [
            self._make_row(
                sources='[{"chunk_id":"legacy-1"},{"law_number":"60/2014/QH13"}]'
            ),
            self._make_row(
                id="rec:456",
                sources=['{"chunk_id":"legacy-2"}', {"chunk_id": "new-1"}],
            ),
        ]

        results = await list_ask_history(limit=10)

        assert results[0]["sources"] == [
            {"chunk_id": "legacy-1"},
            {"law_number": "60/2014/QH13"},
        ]
        assert results[1]["sources"] == [
            {"chunk_id": "legacy-2"},
            {"chunk_id": "new-1"},
        ]
