from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pytest

from api.legal_retrieval_trace import (
    M3_REQUIRED_LATENCY_STAGES,
    M3_REQUIRED_TRACE_FIELDS,
    M3_TRACE_SCHEMA_VERSION,
    validate_m3_retrieval_trace,
)
import scripts.legal_search_server as legal_search


def _row(chunk_id: int) -> dict:
    return {
        "chunk_id": chunk_id, "chunk_index": 0, "chunk_heading": "Điều 16",
        "content": "Nội dung đăng ký khai sinh", "article_id": 10,
        "article_number": "16", "article_title": "Đăng ký khai sinh",
        "article_status": "active", "article_effective_from": None,
        "article_effective_to": None, "document_id": 100,
        "document_title": "Luật Hộ tịch", "law_number": "60/2014/QH13",
        "document_type": "Luật", "issuing_agency": "Quốc hội",
        "scope": "central", "sector": "hộ tịch", "document_status": "active",
        "issued_date": None, "effective_date": None, "expired_date": None,
        "source_url": "https://vbpl.vn/example", "field_id": 1,
        "field_name": "Hộ tịch", "domain_slug": "ho_tich_chung_thuc",
        "domain_name": "Hộ tịch",
    }


def test_search_emits_complete_m3_trace_without_candidate_content(monkeypatch):
    retriever = legal_search.LegalRetriever()
    collection = MagicMock()
    collection.query.return_value = {
        "ids": [["chunk-1"]],
        "metadatas": [[{"chunk_id": 1, "document_id": 100}]],
        "distances": [[0.1]],
    }
    retriever._collection = collection
    retriever._source_collection = collection
    monkeypatch.setattr(
        retriever, "encode_query", MagicMock(return_value=np.zeros(2, dtype=np.float32))
    )
    monkeypatch.setattr(retriever, "_fetch_exact_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "_fetch_lexical_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "_fetch_chunks", lambda *args, **kwargs: [_row(1)])
    monkeypatch.setattr(retriever, "_fetch_neighbor_chunk_ids", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "_fetch_relationships", lambda *args, **kwargs: {})
    monkeypatch.setattr(retriever, "_fetch_fallback_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(retriever, "_fetch_parent_contexts", lambda *args, **kwargs: [])

    response = retriever.search(legal_search.SearchRequest(
        query="Đăng ký khai sinh", as_of=date(2026, 8, 15), limit=1,
        domain="ho_tich_chung_thuc", include_trace=True,
        allow_broad_fallback=False, enable_learned_reranker=False,
    ))
    trace = response["trace"]
    assert validate_m3_retrieval_trace(trace)["status"] == "pass"
    assert set(M3_REQUIRED_TRACE_FIELDS).issubset(trace)
    assert set(M3_REQUIRED_LATENCY_STAGES).issubset(trace["stage_latency_ms"])
    assert trace["vector_candidates"][0]["chunk_id"] == 1
    assert "content" not in trace["vector_candidates"][0]
    assert trace["fusion_candidates"][0]["chunk_id"] == 1
    assert trace["reranker_candidates"]["input"]
    assert trace["reranker_candidates"]["output"]
    assert trace["final_evidence"][0]["chunk_id"] == 1


def test_m3_trace_validator_rejects_missing_stage():
    payload = {
        "schema_version": M3_TRACE_SCHEMA_VERSION,
        "raw_query": "query", "normalized_query": "query",
        "query_classification": {}, "vector_candidates": [],
        "lexical_candidates": [], "fusion_candidates": [],
        "reranker_candidates": {"input": [], "output": []},
        "expanded_evidence": {}, "final_evidence": [],
        "stage_latency_ms": {field: 0.0 for field in M3_REQUIRED_LATENCY_STAGES},
    }
    del payload["fusion_candidates"]
    with pytest.raises(ValueError, match="fusion_candidates"):
        validate_m3_retrieval_trace(payload)
