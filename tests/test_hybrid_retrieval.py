from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from scripts.legal_search_server import SearchRequest, _domain_values, app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def isolate_neighbor_expansion(monkeypatch):
    """Legacy ranking tests do not require a live SQL neighbor lookup."""
    monkeypatch.setattr(
        "scripts.legal_search_server.LegalRetriever._fetch_neighbor_chunk_ids",
        lambda *_args, **_kwargs: [],
    )


def test_domain_alias_keeps_legacy_and_reviewed_civil_status_slugs():
    """A UI domain must not hide the legacy core records it represents."""
    assert set(_domain_values("ho_tich_chung_thuc")) == {
        "ho_tich_chung_thuc",
        "ho_tich",
        "chung_thuc",
        "tu_phap_ho_tich",
    }


def test_domain_alias_keeps_legacy_and_reviewed_land_slugs():
    assert set(_domain_values("dat_dai_xay_dung")) == {
        "dat_dai_xay_dung",
        "dat_dai",
        "xay_dung",
        "dat_dai_moi_truong",
        "xay_dung_do_thi",
    }


@patch("scripts.legal_search_server.LegalRetriever.encode_query")
@patch("scripts.legal_search_server.LegalRetriever._fetch_lexical_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_fallback_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_relationships")
def test_hybrid_rag_returns_correct_scores(
    mock_relationships, mock_fallback, mock_fetch_chunks, mock_lexical, mock_encode, client
):
    """Kiểm tra retrieval trả về đầy đủ các điểm thô vector_score, bm25_score và metadata_score."""
    # Mock vector encode
    mock_encode.return_value = np.zeros(768)
    
    # Mock Chroma collection query
    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "ids": [["chunk-1"]],
        "metadatas": [[{
            "chunk_id": 1,
            "document_title": "Nghị định hộ tịch Phường Lê Chân",
            "scope": "local",
            "official_level": "official",
            "source_url": "http://example.com"
        }]],
        "distances": [[0.3]]
    }
    
    # Mock database queries
    mock_lexical.return_value = []
    mock_fallback.return_value = []
    mock_relationships.return_value = {}
    
    mock_fetch_chunks.return_value = [{
        "chunk_id": 1,
        "chunk_index": 0,
        "heading": "Điều 10",
        "content": "Thủ tục kết hôn tại phường Lê Chân Hải Phòng",
        "article_id": 101,
        "article_number": "Điều 10",
        "article_title": "Giấy tờ nộp",
        "article_status": "active",
        "document_id": 201,
        "document_title": "Nghị định hộ tịch Phường Lê Chân",
        "law_number": "123/2015/NĐ-CP",
        "document_type": "Nghị định",
        "issuing_agency": "Chính phủ",
        "scope": "local",
        "status": "active",
        "document_status": "active",
        "domain_slug": "ho_tich_chung_thuc",
        "domain_name": "Hộ tịch - Chứng thực",
        "field_name": "Hộ tịch"
    }]
    
    # This case verifies hybrid score fields, not the independent validity
    # overlay.  Keep its synthetic 2015 fixture from being rejected by the
    # current partial-scope snapshot before the score assertions run.
    with patch(
        "scripts.legal_search_server.apply_validity_overlay",
        side_effect=lambda payload, **_: {
            **payload,
            "validity_sync": {"mode": "isolated_score_fixture", "filtered_count": 0},
        },
    ), patch("scripts.legal_search_server.retriever._collection", mock_collection):
        response = client.post(
            "/search",
            json={
                "query": "thủ tục kết hôn phường lê chân",
                "limit": 5,
                "include_trace": True
            }
        )
        
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["results"]) > 0
    
    # Đảm bảo các điểm thô có mặt trong kết quả
    first_result = data["results"][0]
    assert "vector_score" in first_result
    assert "bm25_score" in first_result
    assert "metadata_score" in first_result
    
    # Kiểm tra trace chứa đầy đủ điểm số
    assert "trace" in data
    chunk_trace = data["trace"]["retrieved_chunks"][0]
    assert "vector_score" in chunk_trace
    assert "bm25_score" in chunk_trace
    assert "metadata_score" in chunk_trace


@patch("scripts.legal_search_server.LegalRetriever.encode_query")
@patch("scripts.legal_search_server.LegalRetriever._fetch_lexical_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_fallback_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_relationships")
def test_hybrid_rag_metadata_filter(
    mock_relationships, mock_fallback, mock_fetch_chunks, mock_lexical, mock_encode, client
):
    """Domain filtering is delegated to the reviewed SQL legal scope."""
    mock_encode.return_value = np.zeros(768)
    
    mock_collection = MagicMock()
    mock_collection.query.return_value = {"ids": [[]], "metadatas": [[]], "distances": [[]]}
    mock_lexical.return_value = []
    mock_fallback.return_value = []
    mock_relationships.return_value = {}
    mock_fetch_chunks.return_value = []
    
    with (
        patch(
            "scripts.legal_search_server.retriever._fetch_exact_chunks",
            return_value=[],
        ),
        patch(
            "scripts.legal_search_server.retriever._collection",
            mock_collection,
        ),
    ):
        client.post(
            "/search",
            json={
                "query": "đăng ký kết hôn",
                "domain": "ho_tich_chung_thuc"
            }
        )
        
    # A domain contains many field_ids, so stale Chroma metadata must not be
    # treated as the authoritative domain filter.
    mock_collection.query.assert_called_once()
    called_kwargs = mock_collection.query.call_args[1]
    assert "where" not in called_kwargs
    mock_lexical.assert_called_once()
    assert mock_lexical.call_args.args[1] == "ho_tich_chung_thuc"


@patch("scripts.legal_search_server.LegalRetriever.encode_query")
@patch("scripts.legal_search_server.LegalRetriever._fetch_lexical_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_fallback_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_relationships")
def test_hybrid_rag_enforces_hard_legal_hierarchy(
    mock_relationships, mock_fallback, mock_fetch_chunks, mock_lexical, mock_encode, client
):
    """Local relevance must not move a lower authority above central law."""
    mock_encode.return_value = np.zeros(768)
    
    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "ids": [["chunk-1", "chunk-2"]],
        "metadatas": [
            [
                {"chunk_id": 1, "scope": "local", "official_level": "official"},
                {"chunk_id": 2, "scope": "central", "official_level": "reference"}
            ]
        ],
        "distances": [[0.2, 0.2]]
    }
    mock_lexical.return_value = []
    mock_fallback.return_value = []
    mock_relationships.return_value = {}
    
    mock_fetch_chunks.return_value = [
        {
            "chunk_id": 1,
            "chunk_index": 0,
            "heading": "Điều 10",
            "content": "Hộ tịch tại Phường Lê Chân",
            "article_id": 101,
            "article_number": "Điều 10",
            "article_title": "Đăng ký khai sinh",
            "article_status": "active",
            "document_id": 201,
            "document_title": "Nghị định Phường Lê Chân",
            "law_number": "01/QĐ-UBND",
            "document_type": "Quyết định",
            "issuing_agency": "UBND Phường Lê Chân",
            "scope": "local",
            "status": "active",
            "document_status": "active",
            "domain_slug": "ho_tich_chung_thuc",
            "domain_name": "Hộ tịch - Chứng thực",
            "field_name": "Hộ tịch"
        },
        {
            "chunk_id": 2,
            "chunk_index": 0,
            "heading": "Điều 5",
            "content": "Văn bản hướng dẫn chung cả nước",
            "article_id": 102,
            "article_number": "Điều 5",
            "article_title": "Quy định chung",
            "article_status": "active",
            "document_id": 202,
            "document_title": "Thông tư Trung ương",
            "law_number": "02/TT-BTP",
            "document_type": "Thông tư",
            "issuing_agency": "Bộ Tư pháp",
            "scope": "central",
            "status": "active",
            "document_status": "active",
            "domain_slug": "ho_tich_chung_thuc",
            "domain_name": "Hộ tịch - Chứng thực",
            "field_name": "Hộ tịch"
        }
    ]
    
    with patch("scripts.legal_search_server.retriever._collection", mock_collection):
        response = client.post(
            "/search",
            json={
                "query": "hộ tịch",
                "limit": 5,
                "include_trace": True
            }
        )
        
    data = response.json()
    results = data["results"]
    
    assert [result["chunk_id"] for result in results] == [2, 1]
    doc_lechan = next(r for r in results if r["chunk_id"] == 1)
    doc_central = next(r for r in results if r["chunk_id"] == 2)

    assert doc_central["authority_rank"] > doc_lechan["authority_rank"]
    assert doc_central["authority_level"] == "ministerial"
    assert doc_lechan["authority_level"] == "commune_people_committee"
    assert doc_central["metadata_score"] == doc_lechan["metadata_score"] == 0.0
    assert "priority_boost" not in doc_central
    assert "priority_boost" not in doc_lechan

    trace = data["trace"]
    assert trace["hierarchy"]["policy_version"] == "vn-lvbqppl-64-87-2025-v1"
    assert trace["hierarchy"]["verified_count"] == 2
    assert trace["retrieved_chunks"][0]["authority_level"] == "ministerial"


@patch("scripts.legal_search_server.LegalRetriever.encode_query")
@patch("scripts.legal_search_server.LegalRetriever._fetch_lexical_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_fallback_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_relationships")
def test_hybrid_rag_only_active_docs(
    mock_relationships, mock_fallback, mock_fetch_chunks, mock_lexical, mock_encode, client
):
    """Kiểm tra chỉ những document có status = 'active' mới được trả về."""
    mock_encode.return_value = np.zeros(768)
    
    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "ids": [["chunk-1", "chunk-2"]],
        "metadatas": [
            [
                {"chunk_id": 1, "scope": "local", "official_level": "official"},
                {"chunk_id": 2, "scope": "central", "official_level": "reference"}
            ]
        ],
        "distances": [[0.2, 0.2]]
    }
    mock_lexical.return_value = []
    mock_fallback.return_value = []
    mock_relationships.return_value = {}
    
    # Chỉ chunk 1 trả về từ fetch_chunks (vì fetch_chunks lọc AND status = 'active')
    mock_fetch_chunks.return_value = [
        {
            "chunk_id": 1,
            "chunk_index": 0,
            "heading": "Điều 10",
            "content": "Nội dung active",
            "article_id": 101,
            "article_number": "Điều 10",
            "article_title": "Đăng ký khai sinh",
            "article_status": "active",
            "document_id": 201,
            "document_title": "Nghị định Phường Lê Chân",
            "law_number": "01/QĐ-UBND",
            "document_type": "Quyết định",
            "issuing_agency": "UBND Phường Lê Chân",
            "scope": "local",
            "status": "active",
            "document_status": "active",
            "domain_slug": "ho_tich_chung_thuc",
            "domain_name": "Hộ tịch - Chứng thực",
            "field_name": "Hộ tịch"
        }
    ]
    
    with patch("scripts.legal_search_server.retriever._collection", mock_collection):
        response = client.post(
            "/search",
            json={
                "query": "hộ tịch",
                "limit": 5
            }
        )
        
    data = response.json()
    results = data["results"]
    
    # Chỉ có 1 kết quả active được trả về
    assert len(results) == 1
    assert results[0]["chunk_id"] == 1


@patch("scripts.legal_search_server.LegalRetriever.encode_query")
@patch("scripts.legal_search_server.LegalRetriever._fetch_lexical_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_fallback_chunks")
@patch("scripts.legal_search_server.LegalRetriever._fetch_relationships")
def test_hierarchy_runs_after_as_of_and_legal_basis_filters(
    mock_relationships,
    mock_fallback,
    mock_fetch_chunks,
    mock_lexical,
    mock_encode,
    client,
):
    mock_encode.return_value = np.zeros(768)
    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "ids": [["chunk-1", "chunk-2", "chunk-3"]],
        "metadatas": [[
            {"chunk_id": 1, "scope": "central"},
            {"chunk_id": 2, "scope": "central"},
            {"chunk_id": 3, "scope": "haiphong"},
        ]],
        "distances": [[0.1, 0.2, 0.3]],
    }
    mock_lexical.return_value = []
    mock_fallback.return_value = []
    common = {
        "chunk_index": 0,
        "heading": "Điều 1",
        "content": "Nội dung kiểm thử",
        "article_number": "1",
        "article_title": "Quy định",
        "article_status": "active",
        "article_effective_from": None,
        "article_effective_to": None,
        "document_status": "active",
        "expired_date": None,
        "domain_slug": "ho_tich_chung_thuc",
        "domain_name": "Hộ tịch - Chứng thực",
        "field_name": "Hộ tịch",
    }
    mock_fetch_chunks.return_value = [
        {
            **common,
            "chunk_id": 1,
            "article_id": 101,
            "document_id": 201,
            "document_title": "Luật chưa có hiệu lực",
            "law_number": "99/2027/QH16",
            "document_type": "Luật",
            "issuing_agency": "Quốc hội",
            "scope": "central",
            "issued_date": date(2026, 7, 1),
            "effective_date": date(2027, 1, 1),
        },
        {
            **common,
            "chunk_id": 2,
            "article_id": 102,
            "document_id": 202,
            "document_title": "Quyết định có căn cứ hết hiệu lực",
            "law_number": "78/2025/QĐ-UBND",
            "document_type": "Quyết định",
            "issuing_agency": "UBND thành phố Hải Phòng",
            "scope": "haiphong",
            "issued_date": date(2025, 4, 1),
            "effective_date": date(2025, 4, 1),
        },
        {
            **common,
            "chunk_id": 3,
            "article_id": 103,
            "document_id": 203,
            "document_title": "Quyết định Hải Phòng hiện hành",
            "law_number": "12/2026/QĐ-UBND",
            "document_type": "Quyết định",
            "issuing_agency": "UBND thành phố Hải Phòng",
            "scope": "haiphong",
            "issued_date": date(2026, 1, 1),
            "effective_date": date(2026, 1, 15),
        },
    ]
    mock_relationships.return_value = {
        202: [{
            "relationship_type": "văn bản căn cứ",
            "related_status": "expired",
            "related_expired_date": date(2025, 1, 1),
        }]
    }

    with patch("scripts.legal_search_server.retriever._collection", mock_collection):
        response = client.post(
            "/search",
            json={
                "query": "nội dung kiểm thử",
                "limit": 5,
                "as_of": "2026-08-08",
                "include_trace": True,
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [item["chunk_id"] for item in payload["results"]] == [3]
    assert payload["results"][0]["authority_level"] == "provincial_people_committee"
    assert payload["trace"]["pipeline_counts"]["filtered_by_reason"] == {
        "expired_or_not_yet_effective": 1,
        "expired_legal_basis": 1,
    }
