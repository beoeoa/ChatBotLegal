import pytest

from api.retrieval_release_v2_runtime import (
    V2RuntimeError,
    V2ServingRuntime,
    _manifest_chunk_allowlist,
    normalize_exact,
    normalize_query,
)


def _candidate(identifier: str, source: str, score: float) -> dict:
    return {
        "chunk_revision_id": identifier,
        "retrieval_source": source,
        "retrieval_sources": [source],
        "score": score,
        "law_number": "91/2015/QH13",
        "article_number": "27",
        "content": "Điều 27",
    }


def test_v2_exact_normalization_handles_vietnamese_aliases():
    assert normalize_exact("Quyết định 158/2025/QĐ-UBND") == "QUYET DINH 158 2025 QD UBND"


def test_v2_query_normalization_preserves_wording_and_removes_transport_noise():
    assert normalize_query("  Hồ sơ\n\tđăng ký\u00a0khai sinh  ") == "Hồ sơ đăng ký khai sinh"


def test_v2_fusion_is_manifest_id_stable_and_deterministic():
    runtime = object.__new__(V2ServingRuntime)
    exact = [_candidate("b", "exact", 1.0)]
    vector = [_candidate("a", "vector", 0.9), _candidate("b", "vector", 0.8)]
    lexical = [_candidate("a", "lexical", 0.7)]
    fused = runtime._fuse(exact, vector, lexical, strategy="legacy_stack", vector_weight=0.6, lexical_weight=0.4)
    assert [row["chunk_revision_id"] for row in fused] == ["b", "a"]
    assert fused[0]["retrieval_sources"] == ["exact", "vector"]
    assert fused[1]["retrieval_sources"] == ["vector", "lexical"]


def test_v2_trace_exposes_candidate_union_before_fusion_without_changing_fusion():
    runtime = object.__new__(V2ServingRuntime)
    runtime.release_id = "release"
    runtime.source_snapshot_sha256 = "snapshot"
    runtime._exact_candidates = lambda query, **kwargs: [_candidate("exact", "exact", 1.0)]
    runtime._vector_candidates = lambda query, **kwargs: [_candidate("vector", "vector", 0.9)]
    runtime._lexical_candidates = lambda query, **kwargs: [_candidate("lexical", "lexical", 0.8)]
    response = runtime.search(
        "câu hỏi",
        legal_as_of="2026-08-16",
        temporal_scope="current",
        final_evidence=2,
    )
    trace = response["trace"]
    assert [row["chunk_revision_id"] for row in trace["candidate_union"]] == [
        "exact", "vector", "lexical"
    ]
    assert [row["chunk_revision_id"] for row in trace["fusion_candidates"]] == [
        "exact", "vector", "lexical"
    ]
    assert "candidate_union" in trace["stage_latency_ms"]


def test_v2_expansion_can_enter_final_window_and_is_deduplicated():
    runtime = object.__new__(V2ServingRuntime)
    reranked = [_candidate("seed", "reranker", 0.8), _candidate("tail", "reranker", 0.1)]
    expanded = [_candidate("parent", "parent", 0.4), _candidate("seed", "parent", 0.2)]
    final = runtime._select_final_evidence(reranked, expanded, limit=2)
    assert [row["chunk_revision_id"] for row in final] == ["seed", "parent"]


def test_v2_unknown_temporal_scope_is_not_retrieved():
    runtime = object.__new__(V2ServingRuntime)
    runtime.release_id = "release"
    runtime.source_snapshot_sha256 = "snapshot"
    response = runtime.search("vraag", legal_as_of="2026-08-16", temporal_scope="unknown")
    assert response["status"] == "clarification_required"
    assert response["results"] == []


def test_v2_manifest_allowlist_is_required_and_filters_requested_ids():
    import sqlite3

    runtime = object.__new__(V2ServingRuntime)
    runtime._manifest_chunk_ids = frozenset({"allowed"})
    runtime.release_id = "release-v2"
    runtime.db = sqlite3.connect(":memory:")
    runtime.db.row_factory = sqlite3.Row
    runtime.db.execute(
        """
        CREATE TABLE chunks (
            chunk_revision_id TEXT, document_id INTEGER, article_id INTEGER,
            chunk_index INTEGER, release_id TEXT, document_serving_state TEXT,
            law_number TEXT, article_number TEXT, domain_slug TEXT,
            source_url TEXT, effective_from TEXT, effective_to TEXT,
            content TEXT, structural_path TEXT, passage_sha256 TEXT,
            content_sha256 TEXT, token_count INTEGER
        )
        """
    )
    rows = [
        ("allowed", 1, 2, 0, "release-v2", "current_retrievable", "1/QH", "1", "domain", "https://vbpl.vn/1", None, None, "ok", "Điều 1", "a" * 64, "b" * 64, 10),
        ("outside", 9, 9, 0, "release-v2", "current_retrievable", "9/QH", "9", "domain", "https://vbpl.vn/9", None, None, "outside", "Điều 9", "c" * 64, "d" * 64, 10),
    ]
    runtime.db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    runtime.db.commit()
    observed = runtime._rows_for_ids(
        ["allowed", "outside"], temporal_scope="current", as_of="2026-08-16"
    )
    assert set(observed) == {"allowed"}

    missing = object.__new__(V2ServingRuntime)
    missing.release_id = "release"
    missing.source_snapshot_sha256 = "snapshot"
    with pytest.raises(V2RuntimeError, match="eligible_chunk_allowlist_required"):
        missing._rows_for_ids(
            ["allowed"],
            temporal_scope="current",
            as_of="2026-08-16",
        )


def test_v2_scope_sql_binds_release_and_temporal_state():
    runtime = object.__new__(V2ServingRuntime)
    runtime.release_id = "release-v2"
    current_sql, current_params = runtime._scope_sql(
        temporal_scope="current", as_of="2026-08-16"
    )
    historical_sql, historical_params = runtime._scope_sql(
        temporal_scope="historical", as_of="2026-08-16"
    )
    assert "release_id = ?" in current_sql
    assert current_params == ["release-v2", "2026-08-16", "2026-08-16"]
    assert "current_retrievable" in historical_sql
    assert "historical_only" in historical_sql
    assert historical_params == current_params


def test_v2_scope_runtime_requires_manifest_chunk_provenance_contract():
    payload = {
        "release_id": "release-v2",
        "chunks": [
            {
                "chunk_revision_id": "duplicate",
                "release_id": "release-v2",
                "eligible": True,
                "serving_state": "retrievable",
                "document_id": 1,
                "article_id": 1,
                "content_sha256": "a" * 64,
                "embedding_text_sha256": "b" * 64,
                "passage_sha256": "c" * 64,
                "token_count": 10,
            },
            {
                "chunk_revision_id": "duplicate",
                "release_id": "release-v2",
                "eligible": True,
                "serving_state": "retrievable",
                "document_id": 1,
                "article_id": 1,
                "content_sha256": "a" * 64,
                "embedding_text_sha256": "b" * 64,
                "passage_sha256": "c" * 64,
                "token_count": 10,
            },
        ],
    }
    with pytest.raises(V2RuntimeError, match="duplicate_chunk_revision_id"):
        _manifest_chunk_allowlist(payload)


def test_v2_scope_runtime_rejects_missing_chunk_identity_checksum():
    payload = {
        "release_id": "release-v2",
        "chunks": [
            {
                "chunk_revision_id": "one",
                "release_id": "release-v2",
                "eligible": True,
                "serving_state": "retrievable",
                "document_id": 1,
                "article_id": 1,
                "content_sha256": "a" * 64,
                "embedding_text_sha256": "b" * 64,
                "passage_sha256": "c" * 64,
                "token_count": 10,
            }
        ],
    }
    with pytest.raises(V2RuntimeError, match="chunk_identity_checksum_required"):
        _manifest_chunk_allowlist(payload)


def test_v2_out_of_scope_and_missing_facts_are_blocked_before_retrieval():
    runtime = object.__new__(V2ServingRuntime)
    runtime.release_id = "release"
    runtime.source_snapshot_sha256 = "snapshot"
    for classification, status in (
        ({"intent": "OUT_OF_SCOPE"}, "refusal"),
        ({"requires_clarification": True}, "clarification_required"),
    ):
        response = runtime.search(
            "câu hỏi",
            legal_as_of="2026-08-16",
            temporal_scope="current",
            query_classification=classification,
        )
        assert response["status"] == status
        assert response["results"] == []
