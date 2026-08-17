from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.legal_retrieval_quality import hybrid_fusion_score
import scripts.legal_search_server as legal_search
from scripts.run_m5_hybrid_experiments import _config, _winner


@pytest.mark.parametrize(
    ("vector_weight", "lexical_weight", "expected"),
    [
        (0.7, 0.3, 0.62),
        (0.6, 0.4, 0.56),
        (0.5, 0.5, 0.50),
    ],
)
def test_weighted_fusion_uses_the_declared_single_configuration(
    vector_weight: float, lexical_weight: float, expected: float
):
    score = hybrid_fusion_score(
        strategy="weighted",
        vector_score=0.8,
        normalized_lexical_score=0.2,
        rrf_score=0.01,
        vector_weight=vector_weight,
        lexical_weight=lexical_weight,
    )

    assert score == pytest.approx(expected)


def test_rrf_fusion_does_not_mix_raw_vector_or_lexical_scales():
    first = hybrid_fusion_score(
        strategy="rrf",
        vector_score=0.99,
        normalized_lexical_score=0.01,
        rrf_score=0.031,
        vector_weight=0.7,
        lexical_weight=0.3,
    )
    second = hybrid_fusion_score(
        strategy="rrf",
        vector_score=0.01,
        normalized_lexical_score=0.99,
        rrf_score=0.031,
        vector_weight=0.5,
        lexical_weight=0.5,
    )

    assert first == second == pytest.approx(0.031)


def test_search_contract_supports_m5_top_k_10_and_weight_validation():
    request = legal_search.SearchRequest(
        query="đăng ký khai sinh",
        candidate_count=10,
        lexical_candidate_count=10,
        fusion_strategy="weighted",
        vector_weight=0.7,
        lexical_weight=0.3,
    )

    assert request.candidate_count == 10
    assert request.fusion_strategy == "weighted"
    with pytest.raises(ValidationError, match="fusion_weights_must_sum_to_one"):
        legal_search.SearchRequest(
            query="đăng ký khai sinh",
            fusion_strategy="weighted",
            vector_weight=0.7,
            lexical_weight=0.4,
        )


def test_legacy_fusion_remains_the_backward_compatible_default():
    request = legal_search.SearchRequest(query="đăng ký khai sinh")

    assert request.fusion_strategy == "legacy_stack"
    assert request.vector_weight == pytest.approx(0.6)
    assert request.lexical_weight == pytest.approx(0.4)


def test_m5_winner_selection_is_deterministic_and_manifest_safe():
    runs = [
        {
            "experiment_id": "fast",
            "recall_at_10": 0.94,
            "mrr": 0.93,
            "direct_source_top5": 0.95,
            "p95_retrieval_ms": 100.0,
            "errors": 0,
            "outside_manifest_result_count": 0,
        },
        {
            "experiment_id": "better-recall",
            "recall_at_10": 0.95,
            "mrr": 0.92,
            "direct_source_top5": 0.94,
            "p95_retrieval_ms": 200.0,
            "errors": 0,
            "outside_manifest_result_count": 0,
        },
        {
            "experiment_id": "unsafe",
            "recall_at_10": 1.0,
            "mrr": 1.0,
            "direct_source_top5": 1.0,
            "p95_retrieval_ms": 1.0,
            "errors": 0,
            "outside_manifest_result_count": 1,
        },
    ]

    assert _winner(runs)["experiment_id"] == "better-recall"
    assert _config(vector_k=10, lexical_k=20, fusion="rrf") == {
        "candidate_count": 10,
        "lexical_candidate_count": 20,
        "fusion_strategy": "rrf",
        "vector_weight": 0.6,
        "lexical_weight": 0.4,
        "ranking_strategy": "legacy_stack",
        "enable_learned_reranker": False,
        "result_limit": 10,
        "allow_broad_fallback": True,
        "retrieval_tier": "core",
        "parent_expansion": "baseline_enabled",
        "neighbor_expansion": "baseline_enabled",
    }
