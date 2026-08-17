from __future__ import annotations

from datetime import date
import inspect
from unittest.mock import MagicMock

import pytest

from api.legal_serving_scope import ServingManifestScope
from scripts.legal_search_server import LegalRetriever


def _retriever() -> tuple[LegalRetriever, MagicMock]:
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._quality_sidecar_available = False
    retriever._serving_allowed_document_ids = {11, 22}
    retriever._shadow_allowed_chunk_ids = {
        "core": {101, 102},
        "expanded": {101, 102},
    }
    retriever._engine = MagicMock()
    connection = retriever._engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = []
    return retriever, connection


def _assert_document_scope(connection: MagicMock) -> None:
    statement, params = connection.execute.call_args.args
    assert "d.id = ANY" in str(statement)
    assert params["serving_document_ids"] == [11, 22]


def test_lexical_scope_is_applied_inside_sql_before_limit():
    retriever, connection = _retriever()
    retriever._fetch_lexical_chunks(
        "đăng ký tạm trú",
        "cu_tru_an_ninh",
        date(2026, 8, 14),
        "core",
        limit=10,
    )
    _assert_document_scope(connection)
    statement = str(connection.execute.call_args.args[0])
    assert statement.index("d.id = ANY") < statement.index("LIMIT")


def test_chunk_hydration_scope_is_applied_inside_sql():
    retriever, connection = _retriever()
    retriever._fetch_chunks([101, 102], "core")
    _assert_document_scope(connection)


def test_fallback_scope_is_applied_inside_sql_before_limit():
    retriever, connection = _retriever()
    retriever._fetch_fallback_chunks(
        ["tạm trú"],
        "cu_tru_an_ninh",
        date(2026, 8, 14),
        "core",
    )
    _assert_document_scope(connection)
    statement = str(connection.execute.call_args.args[0])
    assert statement.index("d.id = ANY") < statement.index("LIMIT")


def test_parent_hydration_scope_is_applied_inside_sql():
    retriever, connection = _retriever()
    retriever._fetch_parent_contexts([5, 6])
    _assert_document_scope(connection)


def test_staging_is_not_served_without_explicit_benchmark_opt_in():
    retriever, _ = _retriever()
    assert retriever._document_status_predicate() == "d.status = 'active'"
    assert retriever._article_status_predicate() == "a.status = 'active'"


def test_staging_is_allowed_only_for_manifest_documents_in_benchmark():
    retriever, _ = _retriever()
    retriever._benchmark_allow_staging = True
    assert "d.status = 'staging'" in retriever._document_status_predicate()
    assert "a.status = 'staging'" in retriever._article_status_predicate()
    assert ":serving_document_ids" in retriever._document_status_predicate()


def test_candidate_staging_row_projects_active_legal_effectivity_only_in_manifest():
    retriever, _ = _retriever()
    retriever._benchmark_allow_staging = True
    projected = retriever._normalize_candidate_effective_status(
        {
            "document_id": 11,
            "document_status": "staging",
            "article_status": "staging",
        }
    )
    assert projected["document_status"] == "staging"
    assert projected["effective_status"] == "active"
    assert projected["effective_article_status"] == "active"


def test_staging_row_outside_manifest_is_not_projected_active():
    retriever, _ = _retriever()
    retriever._benchmark_allow_staging = True
    projected = retriever._normalize_candidate_effective_status(
        {"document_id": 99, "document_status": "staging"}
    )
    assert "effective_status" not in projected


def test_baseline_row_keeps_storage_status_without_benchmark_mode():
    retriever, _ = _retriever()
    projected = retriever._normalize_candidate_effective_status(
        {"document_id": 11, "document_status": "staging"}
    )
    assert "effective_status" not in projected


def _runtime_scope(tmp_path) -> ServingManifestScope:
    return ServingManifestScope(
        path=tmp_path / "manifest.json", file_sha256="f" * 64,
        manifest_sha256="m" * 64, schema_version="legal-serving-manifest-v2",
        collection_name="baseline", legal_as_of="2026-08-15",
        document_ids=frozenset({11, 22}), chunk_ids=frozenset({101, 102}),
        manifest_version="m2-v1", dataset_version="fixture-v1",
        benchmark_only=False,
        document_visibility=((11, "public"), (22, "officer")),
        chunk_document_ids=((101, 11), (102, 22)),
    )


def test_runtime_scope_filters_documents_and_chunks_by_audience(tmp_path):
    retriever, _ = _retriever()
    retriever._serving_scope = _runtime_scope(tmp_path)
    retriever._set_request_audience("citizen")
    assert retriever._request_serving_document_ids() == {11}
    assert retriever._shadow_filter_ids([101, 102], "core") == [101]
    retriever._set_request_audience("officer")
    assert retriever._shadow_filter_ids([101, 102], "expanded") == [101, 102]


def test_citation_lookup_denies_document_outside_manifest(tmp_path):
    retriever, _ = _retriever()
    retriever._serving_scope = _runtime_scope(tmp_path)
    retriever.assert_document_access(11, "citizen")
    with pytest.raises(PermissionError, match="outside_serving_manifest"):
        retriever.assert_document_access(22, "citizen")


def test_reranker_is_preceded_by_manifest_row_filter():
    source = inspect.getsource(LegalRetriever.search)
    defensive_filter = source.index(
        "ranked = self._shadow_filter_rows(ranked, request.retrieval_tier)"
    )
    reranker = source.index("ranked = self._rerank_candidates(", defensive_filter)
    assert defensive_filter < reranker
