from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pytest
import torch

import scripts.legal_search_server as legal_search


def _row(chunk_id: int, article_id: int, content: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "chunk_index": chunk_id,
        "chunk_heading": f"Mục {chunk_id}",
        "content": content,
        "article_id": article_id,
        "article_number": "16",
        "article_title": "Đăng ký khai sinh",
        "article_status": "active",
        "article_effective_from": None,
        "article_effective_to": None,
        "document_id": 100,
        "document_title": "Luật Hộ tịch",
        "law_number": "60/2014/QH13",
        "document_type": "Luật",
        "issuing_agency": "Quốc hội",
        "scope": "central",
        "sector": "hộ tịch",
        "document_status": "active",
        "issued_date": None,
        "effective_date": None,
        "expired_date": None,
        "source_url": "https://example.test/legal",
        "field_id": 1,
        "field_name": "Hộ tịch",
        "domain_slug": "tu_phap_ho_tich",
        "domain_name": "Hộ tịch",
    }


def test_auto_device_selects_cpu_with_explicit_reason_when_cuda_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    retriever = legal_search.LegalRetriever()
    retriever._requested_device = "auto"

    device, dtype, fallback_reason = retriever._resolve_embedding_device()

    assert device.type == "cpu"
    assert dtype == torch.float32
    assert fallback_reason == "cuda_unavailable"


def test_explicit_cuda_configuration_fails_when_cuda_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    retriever = legal_search.LegalRetriever()
    retriever._requested_device = "cuda"

    with pytest.raises(RuntimeError, match="CUDA"):
        retriever._resolve_embedding_device()


def test_query_vector_cache_reuses_embedding_across_retrieval_tiers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = legal_search.LegalRetriever()
    retriever._model_fingerprint = "vnlegal-lal:test"
    computed = MagicMock(return_value=np.array([0.1, 0.2], dtype=np.float32))
    monkeypatch.setattr(retriever, "_compute_query_embedding", computed)

    core = retriever.encode_query("Đăng ký khai sinh")
    expanded = retriever.encode_query("  đăng ký   khai sinh ")

    assert np.array_equal(core, expanded)
    computed.assert_called_once()
    assert retriever._query_vector_cache.stats()["hits"] == 1


def test_explicit_exact_law_and_article_skips_redundant_ann(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = legal_search.LegalRetriever()
    collection = MagicMock()
    retriever._collection = collection
    retriever._source_collection = collection
    row = _row(1, 10, "Nội dung Điều 16 Luật Hộ tịch.")
    row.update(
        {
            "chunk_index": 0,
            "article_content": row["content"],
            "exact_article_chunk_count": 1,
            # An explicit law+article remains authoritative when the current
            # document has not yet been assigned a reviewed domain.
            "domain_slug": None,
        }
    )
    monkeypatch.setattr(retriever, "_fetch_exact_chunks", lambda *args, **kwargs: [row])
    monkeypatch.setattr(retriever, "_fetch_chunks", lambda *args, **kwargs: [row])
    monkeypatch.setattr(retriever, "_fetch_neighbor_chunk_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "_fetch_relationships", lambda *args, **kwargs: {})
    encode = MagicMock(return_value=np.zeros(2, dtype=np.float32))
    monkeypatch.setattr(retriever, "encode_query", encode)

    result = retriever.search(
        legal_search.SearchRequest(
            query="Theo 60/2014/QH13, Điều 16 quy định gì?",
            domain="ho_tich_chung_thuc",
            as_of=date(2026, 7, 28),
            limit=2,
            include_trace=True,
            allow_broad_fallback=False,
        )
    )

    encode.assert_not_called()
    collection.query.assert_not_called()
    assert result["results"][0]["chunk_id"] == 1
    assert result["trace"]["reranker"]["mode"] == "exact_article_bypass"
    assert result["trace"]["pipeline_counts"]["ann_skipped_for_exact_provision"] is True
def test_cuda_oom_in_auto_mode_switches_to_cpu_and_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = legal_search.LegalRetriever()
    retriever._requested_device = "auto"
    retriever._embedding_device = torch.device("cuda")
    retriever._model_fingerprint = "vnlegal-lal:test"
    calls = {"count": 0}

    def compute(_query: str) -> np.ndarray:
        calls["count"] += 1
        if calls["count"] == 1:
            raise torch.cuda.OutOfMemoryError("test oom")
        return np.array([0.3, 0.4], dtype=np.float32)

    def fallback(reason: str) -> None:
        assert reason == "cuda_oom"
        retriever._embedding_device = torch.device("cpu")
        retriever._embedding_dtype = torch.float32
        retriever._embedding_fallback_reason = reason

    monkeypatch.setattr(retriever, "_compute_query_embedding", compute)
    monkeypatch.setattr(retriever, "_activate_cpu_fallback", fallback)

    vector = retriever.encode_query("khai sinh")

    assert vector.tolist() == pytest.approx([0.3, 0.4])
    assert calls["count"] == 2
    assert retriever._embedding_fallback_reason == "cuda_oom"


def test_prewarm_marks_retriever_ready_only_after_both_indexes_are_warm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = legal_search.LegalRetriever()
    monkeypatch.setattr(retriever, "_load", MagicMock())
    encode = MagicMock(return_value=np.zeros(2, dtype=np.float32))
    monkeypatch.setattr(retriever, "encode_query", encode)
    retriever._collection = MagicMock()
    retriever._source_collection = MagicMock()
    for collection in (retriever._collection, retriever._source_collection):
        collection.query.return_value = {
            "ids": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

    retriever.prewarm()

    assert retriever._ready is True
    encode.assert_called_once()
    retriever._collection.query.assert_called_once()
    retriever._source_collection.query.assert_called_once()
    assert retriever._collection.query.call_args.kwargs["n_results"] == 1
    assert retriever._source_collection.query.call_args.kwargs["n_results"] == 1


def test_health_reports_device_readiness_and_sanitized_cache_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = legal_search.LegalRetriever()
    retriever._embedding_device = torch.device("cpu")
    retriever._embedding_dtype = torch.float32
    retriever._embedding_fallback_reason = "cuda_unavailable"
    retriever._ready = True
    monkeypatch.setattr(retriever, "prewarm", MagicMock())
    retriever._collection = MagicMock()
    retriever._collection.count.return_value = 12
    connection = MagicMock()
    connection.execute.return_value.scalar_one.return_value = 34
    context = MagicMock()
    context.__enter__.return_value = connection
    context.__exit__.return_value = False
    retriever._engine = MagicMock()
    retriever._engine.connect.return_value = context

    payload = retriever.health()

    assert payload["ready"] is True
    assert payload["embedding_device"] == "cpu"
    assert payload["embedding_dtype"] == "float32"
    assert payload["fallback_reason"] == "cuda_unavailable"
    assert payload["query_vector_cache"]["size"] == 0
    assert "keys" not in payload["query_vector_cache"]


def test_search_rewrites_once_and_computes_bm25_scores_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = legal_search.LegalRetriever()
    collection = MagicMock()
    collection.query.return_value = {
        "ids": [["chunk-1", "chunk-2"]],
        "metadatas": [[{"chunk_id": 1}, {"chunk_id": 2}]],
        "distances": [[0.1, 0.2]],
    }
    retriever._collection = collection
    retriever._source_collection = collection
    encode = MagicMock(return_value=np.zeros(2, dtype=np.float32))
    monkeypatch.setattr(retriever, "encode_query", encode)
    monkeypatch.setattr(retriever, "_fetch_exact_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "_fetch_lexical_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "_fetch_fallback_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        retriever,
        "_fetch_chunks",
        lambda *args, **kwargs: [
            _row(1, 10, "Hồ sơ đăng ký khai sinh"),
            _row(2, 20, "Thẩm quyền đăng ký khai sinh"),
        ],
    )
    monkeypatch.setattr(retriever, "_fetch_relationships", lambda *args, **kwargs: {})
    monkeypatch.setattr(retriever, "_fetch_neighbor_chunk_ids", lambda *args, **kwargs: [])
    scores = MagicMock(return_value=np.array([2.0, 1.0]))
    bm25_instance = MagicMock()
    bm25_instance.get_scores = scores
    monkeypatch.setattr(legal_search, "BM25Okapi", MagicMock(return_value=bm25_instance))

    result = retriever.search(
        legal_search.SearchRequest(
            query="Đăng ký khai sinh",
            as_of=date(2026, 7, 16),
            limit=2,
        )
    )

    encode.assert_called_once_with("Đăng ký khai sinh")
    scores.assert_called_once()
    assert len(result["results"]) == 2


def test_evidence_dedupe_keeps_two_complementary_chunks_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = legal_search.LegalRetriever()
    collection = MagicMock()
    collection.query.return_value = {
        "ids": [["chunk-1", "chunk-2", "chunk-3"]],
        "metadatas": [[{"chunk_id": 1}, {"chunk_id": 2}, {"chunk_id": 3}]],
        "distances": [[0.1, 0.2, 0.3]],
    }
    retriever._collection = collection
    retriever._source_collection = collection
    monkeypatch.setattr(retriever, "encode_query", lambda _query: np.zeros(2, dtype=np.float32))
    monkeypatch.setattr(retriever, "_fetch_exact_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "_fetch_lexical_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "_fetch_fallback_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        retriever,
        "_fetch_chunks",
        lambda *args, **kwargs: [
            _row(1, 10, "Giấy chứng sinh"),
            _row(2, 10, "Giấy tờ tùy thân của người đi đăng ký"),
            _row(3, 10, "Giấy chứng sinh"),
        ],
    )
    monkeypatch.setattr(retriever, "_fetch_relationships", lambda *args, **kwargs: {})
    monkeypatch.setattr(retriever, "_fetch_neighbor_chunk_ids", lambda *args, **kwargs: [])

    result = retriever.search(
        legal_search.SearchRequest(
            query="khai sinh",
            as_of=date(2026, 7, 16),
            limit=2,
        )
    )

    assert [item["chunk_id"] for item in result["results"]] == [1, 2]
