import pytest
import numpy as np
from fastapi.testclient import TestClient
from pydantic import ValidationError
from unittest.mock import patch

from scripts.legal_search_server import BatchSearchRequest, app


def _issue(index: int, query_count: int = 1):
    return {
        "issue_id": f"issue-{index}",
        "query": f"legacy query {index}" if query_count == 1 else None,
        "queries": [
            {
                "query_id": f"issue-{index}-q-{query_index}",
                "query_type": "semantic",
                "query": f"query {index} {query_index}",
            }
            for query_index in range(query_count)
        ],
    }


def test_batch_accepts_eight_issues_and_legacy_single_queries():
    payload = BatchSearchRequest(
        request_id="request-8",
        issues=[{"issue_id": f"issue-{i}", "query": f"query {i}"} for i in range(8)],
    )
    assert len(payload.issues) == 8


def test_batch_accepts_four_queries_per_issue_with_sixteen_total():
    payload = BatchSearchRequest(
        request_id="request-16",
        issues=[_issue(index, 4) for index in range(4)],
    )
    assert sum(len(issue.expanded_queries()) for issue in payload.issues) == 16


def test_batch_rejects_more_than_sixteen_queries_total():
    with pytest.raises(ValidationError):
        BatchSearchRequest(
            request_id="request-too-many",
            issues=[_issue(index, 4) for index in range(5)],
        )


def test_batch_multi_query_merges_duplicates_and_preserves_query_provenance():
    client = TestClient(app)
    payload = {
        "request_id": "request-provenance",
        "issues": [
            {
                "issue_id": "issue-1",
                "queries": [
                    {
                        "query_id": "issue-1-q1",
                        "query_type": "exact_legal",
                        "query": "hồ sơ cấp giấy chứng nhận lần đầu",
                    },
                    {
                        "query_id": "issue-1-q2",
                        "query_type": "semantic",
                        "query": "giấy tờ cần nộp khi xin sổ đỏ lần đầu",
                    },
                ],
            }
        ],
    }

    def fake_search(request):
        return {
            "results": [
                {
                    "chunk_id": 42,
                    "document_id": "doc-1",
                    "article_id": 7,
                    "score": 1.0,
                    "request_id": request.request_id,
                    "issue_id": request.issue_id,
                    "query_id": request.query_id,
                }
            ],
            "timing_ms": {"total": 1.0},
        }

    with (
        patch(
            "scripts.legal_search_server.retriever.prefetch_batch_vectors",
            return_value=[
                {"ids": [[]], "metadatas": [[]], "distances": [[]]},
                {"ids": [[]], "metadatas": [[]], "distances": [[]]},
            ],
        ),
        patch("scripts.legal_search_server.retriever.search", side_effect=fake_search),
    ):
        response = client.post("/search/batch", json=payload)

    assert response.status_code == 200
    issue = response.json()["issues"][0]
    assert [item["query_id"] for item in issue["queries"]] == [
        "issue-1-q1",
        "issue-1-q2",
    ]
    assert issue["results"][0]["query_ids"] == ["issue-1-q1", "issue-1-q2"]


def test_exact_article_batch_skips_vector_prefetch_and_propagates_gate():
    client = TestClient(app)
    payload = {
        "request_id": "request-exact-article-no-ann",
        "issues": [
            {
                "issue_id": "issue-1",
                "query": "Điều 73 của văn bản 60/2014/QH13 kiểm tra no-ann",
            }
        ],
        "retrieval_tier": "expanded",
    }

    def fake_search(request):
        return {
            "results": [
                {
                    "chunk_id": 1,
                    "article_id": 73,
                    "document_id": 60,
                    "request_id": request.request_id,
                    "issue_id": request.issue_id,
                    "exact_article_order": 0,
                },
                {
                    "chunk_id": 2,
                    "article_id": 73,
                    "document_id": 60,
                    "request_id": request.request_id,
                    "issue_id": request.issue_id,
                    "exact_article_order": 1,
                },
            ],
            "exact_article_packet": {
                "status": "complete",
                "packet_ref": "document:60:article:73",
                "law_number": "60/2014/QH13",
                "article_number": "73",
                "loaded_chunk_count": 2,
                "expected_chunk_count": 2,
                "reason_codes": [],
            },
            "timing_ms": {"total": 1.0},
        }

    with (
        patch(
            "scripts.legal_search_server.retriever.prefetch_batch_vectors"
        ) as prefetch_batch_vectors,
        patch(
            "scripts.legal_search_server.retriever.search",
            side_effect=fake_search,
        ),
    ):
        response = client.post("/search/batch", json=payload)

    assert response.status_code == 200
    prefetch_batch_vectors.assert_not_called()
    issue = response.json()["issues"][0]
    assert [row["exact_article_order"] for row in issue["results"]] == [0, 1]
    assert issue["exact_article_packets"][0]["status"] == "complete"
