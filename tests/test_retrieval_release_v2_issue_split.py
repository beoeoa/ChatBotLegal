from __future__ import annotations

from api.retrieval_release_v2_runtime import V2ServingRuntime


def test_v2_issue_split_retrieves_each_issue_and_merges_trace():
    runtime = object.__new__(V2ServingRuntime)
    runtime.release_id = "release-v2"
    runtime.source_snapshot_sha256 = "snapshot"
    calls = []

    def single(query, **kwargs):
        calls.append((query, kwargs["query_classification"]))
        return {
            "status": "ok",
            "results": [{
                "chunk_revision_id": query,
                "score": 1.0,
                "retrieval_source": "vector",
                "retrieval_sources": ["vector"],
            }],
            "trace": {
                "raw_query": query,
                "query_classification": kwargs["query_classification"],
                "final_evidence": [{"chunk_revision_id": query}],
                "stage_latency_ms": {"vector": 2.0, "total": 3.0},
            },
        }

    runtime._search_single = single
    response = runtime.search(
        "hỏi A và B",
        legal_as_of="2026-08-16",
        temporal_scope="current",
        query_classification={
            "issues": [
                {"issue_id": "a", "query_text": "hỏi A"},
                {"issue_id": "b", "query_text": "hỏi B"},
            ]
        },
    )
    assert [item[0] for item in calls] == ["hỏi A", "hỏi B"]
    assert [item[1]["issue_id"] for item in calls] == ["a", "b"]
    assert {item["chunk_revision_id"] for item in response["results"]} == {"hỏi A", "hỏi B"}
    assert {item["chunk_revision_id"]: item["issue_ids"] for item in response["results"]} == {
        "hỏi A": ["a"],
        "hỏi B": ["b"],
    }
    assert response["trace"]["issue_split"]["enabled"] is True
    assert response["trace"]["issue_split"]["issue_count"] == 2
