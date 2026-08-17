from datetime import date
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

import numpy as np
from fastapi.testclient import TestClient

from scripts import legal_search_server
from scripts.legal_search_server import LegalRetriever, app


def test_direct_script_runtime_can_import_batch_quality_policy():
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path = [
    entry for entry in sys.path
    if entry and Path(entry).resolve() != root
]
sys.path.insert(0, str(root / "scripts"))
source_path = root / "scripts" / "legal_search_server.py"
bootstrap = source_path.read_text(encoding="utf-8").split("\\nimport chromadb\\n", 1)[0]
exec(compile(bootstrap, str(source_path), "exec"), {"__file__": str(source_path)})
import api.legal_retrieval_quality
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_lexical_distinct_query_orders_by_selected_score_alias():
    retriever = LegalRetriever()
    result = MagicMock()
    result.mappings.return_value = []
    connection = MagicMock()
    connection.execute.return_value = result
    context = MagicMock()
    context.__enter__.return_value = connection
    retriever._engine = MagicMock()
    retriever._engine.connect.return_value = context
    retriever._quality_sidecar_available = False

    retriever._fetch_lexical_chunks(
        "dang ky khai sinh",
        domain=None,
        as_of=date(2026, 7, 22),
        retrieval_tier="expanded",
    )

    statement = str(connection.execute.call_args.args[0])
    assert "AS lexical_score" in statement
    assert "ORDER BY\n                lexical_score DESC" in statement


def test_database_url_falls_back_to_current_repo_release_config(
    monkeypatch,
    tmp_path,
):
    missing_legacy = tmp_path / "missing-legacy.env"
    repo_env = tmp_path / ".env"
    repo_env.write_text(
        "LEGAL_RELEASE_DATABASE_URL="
        "postgresql+psycopg2://test:test@host.docker.internal:5432/legal_chatbot\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LEGAL_DATABASE_URL", raising=False)
    monkeypatch.setattr(legal_search_server, "OLD_ENV_PATH", missing_legacy)
    monkeypatch.setattr(legal_search_server, "REPO_ENV_PATH", repo_env)

    assert legal_search_server._database_url() == (
        "postgresql+psycopg2://test:test@127.0.0.1:5432/legal_chatbot"
    )


def test_search_batch_accepts_bounded_issues_and_preserves_provenance():
    client = TestClient(app)

    def fake_search(request):
        suffix = int(request.issue_id.rsplit("-", 1)[-1])
        return {
            "results": [
                {
                    "chunk_id": suffix * 100 + index,
                    "document_id": f"document-{index % 3}",
                    "article_id": suffix * 100 + index,
                    "score": 100 - index,
                    "request_id": request.request_id,
                    "issue_id": request.issue_id,
                }
                for index in range(8)
            ],
            "timing_ms": {"total": 10.0},
        }

    payload = {
        "request_id": "request-1",
        "as_of": "2026-07-22",
        "issues": [
            {
                "issue_id": "issue-1",
                "query": "đăng ký kết hôn hồ sơ",
                "domain": "tu_phap_ho_tich",
                "intent": "documents",
            },
            {
                "issue_id": "issue-2",
                "query": "đăng ký kết hôn lệ phí",
                "domain": "tu_phap_ho_tich",
                "intent": "fee",
            },
        ],
    }

    with (
        patch(
            "scripts.legal_search_server.retriever.prefetch_batch_vectors",
            return_value=[
                {"ids": [[]], "metadatas": [[]], "distances": [[]]},
                {"ids": [[]], "metadatas": [[]], "distances": [[]]},
            ],
        ) as prefetch_batch_vectors,
        patch("scripts.legal_search_server.retriever.search", side_effect=fake_search) as search,
    ):
        response = client.post("/search/batch", json=payload)

    assert response.status_code == 200
    body = response.json()
    prefetch_batch_vectors.assert_called_once_with(
        [item["query"] for item in payload["issues"]],
        retrieval_tier="core",
        candidate_count=30,
    )
    assert search.call_count == 2
    assert all(call.args[0].candidate_count == 30 for call in search.call_args_list)
    assert all(
        call.args[0].lexical_candidate_count == 0
        for call in search.call_args_list
    )
    assert len(body["issues"]) == 2
    assert len(body["context_results"]) <= 12
    assert all(item["request_id"] == "request-1" for item in body["context_results"])
    assert {item["issue_id"] for item in body["context_results"]} == {"issue-1", "issue-2"}


def test_expanded_batch_uses_bounded_support_candidate_budget():
    """A supplemental pass must not pay for a 100-neighbour source query.

    Expanded retrieval only fills a concrete coverage gap after the core pass,
    so a smaller ANN packet is enough before deterministic legal filtering and
    materially reduces local Chroma latency on the 500k+ source collection.
    """

    client = TestClient(app)
    payload = {
        "request_id": "request-expanded-budget",
        "as_of": "2026-07-22",
        "retrieval_tier": "expanded",
        "issues": [
            {
                "issue_id": "issue-1",
                "query": "xay dung khong co giay phep xu phat",
                "domain": "dat_dai_xay_dung",
                "intent": "rule",
            }
        ],
    }

    with (
        patch(
            "scripts.legal_search_server.retriever.prefetch_batch_vectors",
            return_value=[
                {"ids": [[]], "metadatas": [[]], "distances": [[]]},
            ],
        ) as prefetch_batch_vectors,
        patch(
            "scripts.legal_search_server.retriever.search",
            return_value={"results": [], "timing_ms": {"total": 1.0}},
        ) as search,
    ):
        response = client.post("/search/batch", json=payload)

    assert response.status_code == 200
    prefetch_batch_vectors.assert_called_once_with(
        [payload["issues"][0]["query"]],
        retrieval_tier="expanded",
        candidate_count=32,
    )
    assert search.call_args.args[0].candidate_count == 32
    assert search.call_args.args[0].lexical_candidate_count == 0


def test_prefetch_batch_vectors_uses_one_chroma_query_for_all_variants():
    retriever = LegalRetriever()
    retriever.encode_queries = MagicMock(
        return_value=[np.zeros(3), np.ones(3)]
    )
    retriever._collection = MagicMock()
    retriever._collection.query.return_value = {
        "ids": [["chunk-1"], ["chunk-2"]],
        "metadatas": [[{"chunk_id": 1}], [{"chunk_id": 2}]],
        "distances": [[0.1], [0.2]],
    }

    results = retriever.prefetch_batch_vectors(
        ["query one", "query two"],
        retrieval_tier="core",
        candidate_count=25,
    )

    retriever._collection.query.assert_called_once()
    assert retriever._collection.query.call_args.kwargs["query_embeddings"] == [
        [0.0, 0.0, 0.0],
        [1.0, 1.0, 1.0],
    ]
    assert [item["ids"][0][0] for item in results] == ["chunk-1", "chunk-2"]


def test_search_batch_reuses_non_admin_results_but_rebinds_request_provenance():
    client = TestClient(app)
    payload = {
        "request_id": "request-cache-1",
        "as_of": "2026-07-22",
        "issues": [{
            "issue_id": "issue-cache-1",
            "query": "cache unique legal query",
            "domain": "tu_phap_ho_tich",
            "intent": "documents",
        }],
    }
    calls = 0

    def fake_search(request):
        nonlocal calls
        calls += 1
        return {
            "results": [{
                "chunk_id": 1,
                "request_id": request.request_id,
                "issue_id": request.issue_id,
            }],
            "timing_ms": {"total": 10.0},
        }

    with (
        patch(
            "scripts.legal_search_server.retriever.prefetch_batch_vectors",
            return_value=[
                {"ids": [[]], "metadatas": [[]], "distances": [[]]},
            ],
        ) as prefetch_batch_vectors,
        patch("scripts.legal_search_server.retriever.search", side_effect=fake_search),
    ):
        first = client.post("/search/batch", json=payload).json()
        payload["request_id"] = "request-cache-2"
        payload["issues"][0]["issue_id"] = "issue-cache-2"
        second = client.post("/search/batch", json=payload).json()

    assert calls == 1
    assert prefetch_batch_vectors.call_count == 1
    assert first["context_results"][0]["request_id"] == "request-cache-1"
    assert second["context_results"][0]["request_id"] == "request-cache-2"
    assert second["context_results"][0]["issue_id"] == "issue-cache-2"


def test_search_batch_rejects_more_than_eight_issues():
    client = TestClient(app)
    response = client.post(
        "/search/batch",
        json={
            "request_id": "request-1",
            "as_of": "2026-07-22",
            "issues": [
                {"issue_id": f"issue-{index}", "query": f"vấn đề {index}"}
                for index in range(1, 10)
            ],
        },
    )

    assert response.status_code == 422


def test_search_batch_preserves_expired_filter_reason_when_no_chunk_survives():
    client = TestClient(app)

    def expired_search(_request):
        return {
            "results": [],
            "validity_sync": {
                "mode": "protect",
                "filtered_count": 3,
                "filtered_reasons": {"expired": 3},
                "warning_count": 0,
            },
            "timing_ms": {"total": 2.0},
        }

    with patch(
        "scripts.legal_search_server.retriever.search",
        side_effect=expired_search,
    ):
        response = client.post(
            "/search/batch",
            json={
                "request_id": "request-expired-1",
                "as_of": "2026-08-10",
                "issues": [
                    {
                        "issue_id": "issue-expired-1",
                        "query": "Điều 26 của 96/2014/TT-BQP",
                        "intent": "rule",
                    }
                ],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["issues"][0]["results"] == []
    assert payload["issues"][0]["validity_sync"]["filtered_reasons"] == {
        "expired": 3
    }
    assert payload["validity_sync"]["filtered_reasons"] == {"expired": 3}


def test_neighbor_chunk_lookup_is_bounded_to_adjacent_chunks_and_four_articles():
    retriever = LegalRetriever()
    rows = [
        {"article_id": article_id, "chunk_index": 2, "chunk_id": article_id * 10 + 2}
        for article_id in range(1, 7)
    ]
    result = MagicMock()
    result.mappings.return_value = [
        {"chunk_id": article_id * 10 + chunk_index, "article_id": article_id, "chunk_index": chunk_index}
        for article_id in range(1, 5)
        for chunk_index in (0, 1, 2, 3, 4)
    ]
    connection = MagicMock()
    connection.execute.return_value = result
    context = MagicMock()
    context.__enter__.return_value = connection
    retriever._engine = MagicMock()
    retriever._engine.connect.return_value = context

    chunk_ids = retriever._fetch_neighbor_chunk_ids(rows, max_articles=4, max_neighbors=12)

    assert chunk_ids == [11, 13, 21, 23, 31, 33, 41, 43]
    params = connection.execute.call_args.args[1]
    assert params["article_ids"] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_api_search_helper_uses_shared_validity_guarded_client(monkeypatch):
    from api.routers import search as search_router

    class GuardedClient:
        def __init__(self):
            self.calls = []

        async def search(self, payload):
            self.calls.append(payload)
            return {
                "results": [
                    {
                        "chunk_id": "active-1",
                        "document_id": "1",
                        "law_number": "31/2024/QH15",
                        "document_title": "Luật Đất đai",
                        "document_status": "active",
                        "validity_sync": {"status": "active", "serving_action": "allow"},
                    }
                ],
                "validity_sync": {"filtered_count": 1, "filtered_reasons": {"expired": 1}},
            }

    guarded = GuardedClient()
    monkeypatch.setattr(search_router, "get_legal_search_client", lambda: guarded)

    results = await search_router._search_legal_documents(
        query="đăng ký đất đai",
        limit=5,
        domain=None,
    )

    assert len(guarded.calls) == 1
    assert guarded.calls[0]["query"] == "đăng ký đất đai"
    assert results[0]["law_number"] == "31/2024/QH15"
    assert results[0]["validity_sync"]["status"] == "active"
