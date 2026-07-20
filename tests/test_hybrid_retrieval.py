import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
import numpy as np

from scripts.legal_search_server import app, SearchRequest, _domain_values

@pytest.fixture
def client():
    return TestClient(app)


def test_domain_alias_keeps_legacy_and_reviewed_civil_status_slugs():
    """A UI domain must not hide the legacy core records it represents."""
    assert set(_domain_values("ho_tich_chung_thuc")) == {
        "ho_tich_chung_thuc",
        "tu_phap_ho_tich",
    }


def test_domain_alias_keeps_legacy_and_reviewed_land_slugs():
    assert set(_domain_values("dat_dai_xay_dung")) == {
        "dat_dai_xay_dung",
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
    
    with patch("scripts.legal_search_server.retriever._collection", mock_collection):
        response = client.post(
            "/search",
            json={
                "query": "thủ tục kết hôn phường lê chân",
                "limit": 5,
                "include_trace": True
            }
        )
        
    assert response.status_code == 200
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
    
    with patch("scripts.legal_search_server.retriever._collection", mock_collection):
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
def test_hybrid_rag_priority_boost(
    mock_relationships, mock_fallback, mock_fetch_chunks, mock_lexical, mock_encode, client
):
    """Kiểm tra priority boost hoạt động chính xác theo phân cấp ưu tiên local Lê Chân > Hải Phòng > Trung ương."""
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
    
    # Document 1 (local Lê Chân) phải có score và metadata_score cao hơn Document 2 (Trung ương reference)
    doc_lechan = next(r for r in results if r["chunk_id"] == 1)
    doc_central = next(r for r in results if r["chunk_id"] == 2)
    
    assert doc_lechan["metadata_score"] > doc_central["metadata_score"]
    assert doc_lechan["score"] > doc_central["score"]


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
