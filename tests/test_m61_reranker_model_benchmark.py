from __future__ import annotations


def test_m61_base_matrix_changes_only_reranker_model():
    from scripts.run_m61_reranker_model_benchmark import base_experiment_specs

    specs = base_experiment_specs()

    assert [row["experiment_id"] for row in specs] == [
        "m61-e00-control",
        "m61-e01-bge-top10",
        "m61-e02-gte-top10",
    ]
    assert all(row["config"]["candidate_count"] == 20 for row in specs)
    assert all(row["config"]["lexical_candidate_count"] == 20 for row in specs)
    assert all(row["config"]["fusion_strategy"] == "legacy_stack" for row in specs)
    assert all(row["config"]["result_limit"] == 10 for row in specs)
    assert all(row["config"]["max_length"] == 512 for row in specs)
    assert all(row["config"]["batch_size"] == 8 for row in specs)
    assert all(row["config"]["enable_parent_expansion"] for row in specs)
    assert all(row["config"]["enable_neighbor_expansion"] for row in specs)
    assert specs[0]["config"]["enable_learned_reranker"] is False
    assert specs[1]["config"]["model_key"] == "bge-v2-m3"
    assert specs[2]["config"]["model_key"] == "gte-multilingual-base"
    assert specs[1]["config"]["rerank_top_n"] == 10
    assert specs[2]["config"]["rerank_top_n"] == 10


def test_m61_gte_top20_is_conditional_on_top10_latency_and_safety():
    from scripts.run_m61_reranker_model_benchmark import conditional_gte_top20

    passing = conditional_gte_top20(
        {
            "p95_retrieval_ms": 14_999.0,
            "errors": 0,
            "outside_manifest_result_count": 0,
            "invalid_evidence_result_count": 0,
        }
    )
    failing = conditional_gte_top20(
        {
            "p95_retrieval_ms": 15_001.0,
            "errors": 0,
            "outside_manifest_result_count": 0,
            "invalid_evidence_result_count": 0,
        }
    )

    assert passing is not None
    assert passing["config"]["rerank_top_n"] == 20
    assert failing is None


def test_m61_gate_requires_latency_recall_quality_and_safety():
    from scripts.run_m61_reranker_model_benchmark import evaluate_gate

    control = {
        "recall_at_10": 0.70,
        "mrr": 0.50,
        "direct_source_top5": 0.60,
    }
    passing = {
        "recall_at_10": 0.70,
        "mrr": 0.525,
        "direct_source_top5": 0.60,
        "p95_retrieval_ms": 14_000.0,
        "errors": 0,
        "outside_manifest_result_count": 0,
        "invalid_evidence_result_count": 0,
    }
    latency_failure = {**passing, "p95_retrieval_ms": 15_001.0}
    quality_failure = {**passing, "mrr": 0.51}

    assert evaluate_gate(control, passing)["passed"] is True
    assert evaluate_gate(control, latency_failure)["passed"] is False
    assert evaluate_gate(control, quality_failure)["passed"] is False
