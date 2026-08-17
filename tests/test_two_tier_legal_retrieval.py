from unittest.mock import MagicMock, patch

import numpy as np
from fastapi.testclient import TestClient

from scripts.legal_search_server import _domain_matches, _domain_values, app


def test_broad_ui_domain_maps_to_reviewed_legal_scope_domains():
    assert _domain_values("dat_dai_xay_dung") == (
        "dat_dai_xay_dung",
        "dat_dai",
        "xay_dung",
        "dat_dai_moi_truong",
        "xay_dung_do_thi",
    )
    assert _domain_matches("dat_dai_xay_dung", "dat_dai_moi_truong")
    assert _domain_matches("dat_dai_xay_dung", "xay_dung_do_thi")
    assert not _domain_matches("dat_dai_xay_dung", "tu_phap_ho_tich")


def test_expanded_tier_queries_source_collection_and_reports_trace():
    client = TestClient(app)
    source_collection = MagicMock()
    source_collection.query.return_value = {
        "ids": [["chunk-91"]],
        "metadatas": [[{"chunk_id": 91}]],
        "distances": [[0.2]],
    }
    row = {
        "chunk_id": 91,
        "chunk_index": 0,
        "heading": "Điều 10",
        "content": "Quy định về đăng ký hộ tịch.",
        "article_id": 901,
        "article_number": "10",
        "article_title": "Đăng ký hộ tịch",
        "article_status": "active",
        "document_id": 9001,
        "document_title": "Luật Hộ tịch",
        "law_number": "60/2014/QH13",
        "document_type": "Luật",
        "issuing_agency": "Quốc hội",
        "scope": "Trung ương",
        "document_status": "active",
        "domain_slug": "tu_phap_ho_tich",
        "domain_name": "Hộ tịch - chứng thực",
        "field_name": "Hộ tịch",
    }
    with (
        patch("scripts.legal_search_server.LegalRetriever.encode_query", return_value=np.zeros(768)),
        patch("scripts.legal_search_server.LegalRetriever._fetch_exact_chunks", return_value=[]),
        patch("scripts.legal_search_server.LegalRetriever._fetch_lexical_chunks", return_value=[]),
        patch("scripts.legal_search_server.LegalRetriever._fetch_chunks", return_value=[row]),
            patch("scripts.legal_search_server.LegalRetriever._fetch_fallback_chunks", return_value=[]),
            patch("scripts.legal_search_server.LegalRetriever._fetch_relationships", return_value={}),
            patch("scripts.legal_search_server.LegalRetriever._fetch_neighbor_chunk_ids", return_value=[]),
            patch("scripts.legal_search_server.retriever._source_collection", source_collection),
    ):
        response = client.post(
            "/search",
            json={
                "query": "đăng ký khai sinh",
                "retrieval_tier": "expanded",
                "include_trace": True,
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    source_collection.query.assert_called_once()
    assert payload["trace"]["retrieval_tier"] == "expanded"
    assert payload["trace"]["scope_filter_applied"] is False
    assert payload["results"][0]["document_id"] == 9001
