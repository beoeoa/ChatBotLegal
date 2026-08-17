from __future__ import annotations

from api.legal_learned_reranker import OptionalCrossEncoderReranker
from scripts.benchmark_feature016_reranker_real import (
    activation_decision,
    compare_rankings,
    source_matches,
)


def test_source_matching_requires_document_and_one_explicit_article():
    candidate = {"law_number": "60/2014/QH13", "article_number": "33"}

    assert source_matches(
        candidate, {"law_number": "60/2014/QH13", "article": "32,33,34"}
    )
    assert not source_matches(
        candidate, {"law_number": "60/2014/QH13", "article": "18"}
    )
    assert not source_matches(
        candidate, {"law_number": "68/2020/QH14", "article": "33"}
    )


def test_real_benchmark_uses_actual_content_and_reports_improvement():
    dataset = {
        "examples": [
            {
                "case_id": "case-1",
                "question": "Điều kiện đăng ký thường trú tại nhà thuê?",
                "positive_sources": [{"law_number": "68/2020/QH14", "article": "20"}],
                "hard_negatives": [{"law_number": "68/2020/QH14", "article": "24"}],
            }
        ]
    }
    cache = {
        "cases": {
            "case-1": {
                "candidates": [
                    {
                        "chunk_id": "wrong",
                        "content": "Xóa đăng ký thường trú.",
                        "score": 0.9,
                        "law_number": "68/2020/QH14",
                        "article_number": "24",
                        "document_type": "Luật",
                        "issuing_agency": "Quốc hội",
                    },
                    {
                        "chunk_id": "right",
                        "content": "Điều kiện đăng ký thường trú tại chỗ ở thuê.",
                        "score": 0.7,
                        "law_number": "68/2020/QH14",
                        "article_number": "20",
                        "document_type": "Luật",
                        "issuing_agency": "Quốc hội",
                    },
                ]
            }
        }
    }
    reranker = OptionalCrossEncoderReranker(
        enabled=True,
        scorer=lambda pairs: [
            3.0 if "chỗ ở thuê" in passage else -3.0 for _, passage in pairs
        ],
        model_label="fixture",
        max_candidates=2,
        learned_weight=1.0,
    )

    report = compare_rankings(dataset, cache, reranker=reranker, latency_target_ms=3000)

    assert report["baseline"]["top1_accuracy"] == 0.0
    assert report["learned"]["top1_accuracy"] == 1.0
    assert report["delta"]["mrr"] == 0.5
    assert report["activation_decision"]["activate"] is True
    assert report["reranker_runtime"]["candidate_identity_preserved"] is True


def test_activation_gate_rejects_quality_gain_with_recall_or_safety_regression():
    baseline = {
        "top5_accuracy": 0.70,
        "mrr": 0.50,
        "recall_at_10": 0.80,
        "forbidden_top1_count": 0,
        "forbidden_top5_count": 0,
        "ineligible_top10_count": 0,
    }
    learned = {
        "top5_accuracy": 0.90,
        "mrr": 0.70,
        "recall_at_10": 0.79,
        "forbidden_top1_count": 0,
        "forbidden_top5_count": 1,
        "ineligible_top10_count": 0,
    }

    decision = activation_decision(
        baseline,
        learned,
        evaluated_cases=100,
        required_cases=100,
        learned_mode_count=100,
        identity_preserved=True,
        oom_count=0,
        latency_p95_ms=500,
    )

    assert decision["activate"] is False
    assert "recall_at_10_non_decrease" in decision["failed_gates"]
    assert "safety_no_regression" in decision["failed_gates"]


def test_empty_retrieval_is_scored_as_failure_instead_of_being_omitted():
    dataset = {
        "examples": [
            {
                "case_id": "missing-case",
                "question": "Dieu 9 31/2024/QH15 quy dinh gi?",
                "positive_sources": [{"law_number": "31/2024/QH15", "article": "9"}],
                "hard_negatives": [],
            }
        ]
    }
    cache = {
        "cases": {
            "missing-case": {
                "candidates": [],
                "error": None,
                "exact_article_packet": {
                    "status": "incomplete",
                    "reason_codes": ["article_context_too_large"],
                },
                "validity_sync": {"filtered_reasons": {}},
            }
        }
    }
    reranker = OptionalCrossEncoderReranker(enabled=False)

    report = compare_rankings(dataset, cache, reranker=reranker)

    assert report["evaluated_case_count"] == 1
    assert report["retrieved_case_count"] == 0
    assert report["baseline"]["case_count"] == 1
    assert report["baseline"]["top1_accuracy"] == 0.0
    assert report["cases"][0]["candidate_status"] == "empty"
    assert report["retrieval_gap_reasons"] == {"article_context_too_large": 1}
    assert report["activation_decision"]["activate"] is False


def test_oom_is_labeled_and_falls_back_without_reordering():
    candidates = [
        {"chunk_id": "a", "content": "A", "score": 0.9},
        {"chunk_id": "b", "content": "B", "score": 0.8},
    ]
    reranker = OptionalCrossEncoderReranker(
        enabled=True,
        scorer=lambda _: (_ for _ in ()).throw(RuntimeError("CUDA out of memory")),
    )

    result = reranker.rerank("query", candidates)

    assert result.reason_code == "inference_oom"
    assert [item["chunk_id"] for item in result.candidates] == ["a", "b"]
    assert result.public_status()["max_length"] == 512


def test_environment_profile_is_bounded_and_part_of_public_status(monkeypatch):
    monkeypatch.setenv("LEGAL_LEARNED_RERANKER_ENABLED", "false")
    monkeypatch.setenv("LEGAL_RERANKER_WINDOW", "999")
    monkeypatch.setenv("LEGAL_RERANKER_BATCH_SIZE", "999")
    monkeypatch.setenv("LEGAL_RERANKER_MAX_LENGTH", "64")
    reranker = OptionalCrossEncoderReranker.from_environment()

    result = reranker.rerank("query", [{"chunk_id": "a", "content": "A", "score": 1.0}])

    assert result.candidate_count == 1
    assert result.public_status()["max_length"] == 128
    assert result.public_status()["batch_size"] == 32
    # M6 extends the bounded experiment window through Top-N 100 while
    # preserving a hard upper cap for runtime safety.
    assert reranker.max_candidates == 100
