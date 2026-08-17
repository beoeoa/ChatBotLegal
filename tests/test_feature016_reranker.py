from __future__ import annotations

import pytest

from api.legal_learned_reranker import OptionalCrossEncoderReranker
from scripts.benchmark_feature016_reranker import benchmark_reranker


def _candidates(count: int = 3) -> list[dict]:
    rows = [
        {
            "chunk_id": "wrong-procedure",
            "content": "Quy định xóa đăng ký thường trú.",
            "score": 0.91,
            "law_number": "68/2020/QH14",
            "article_number": "24",
        },
        {
            "chunk_id": "right-procedure",
            "content": "Điều kiện đăng ký thường trú tại nhà thuê.",
            "score": 0.72,
            "law_number": "68/2020/QH14",
            "article_number": "20",
        },
        {
            "chunk_id": "generic",
            "content": "Nguyên tắc cư trú chung.",
            "score": 0.70,
            "law_number": "68/2020/QH14",
            "article_number": "3",
        },
    ]
    return rows[:count]


def test_disabled_reranker_preserves_heuristic_order_and_reason_code():
    reranker = OptionalCrossEncoderReranker(enabled=False)
    result = reranker.rerank(
        "Điều kiện đăng ký thường trú tại nhà thuê là gì?",
        _candidates(),
    )

    assert [item["chunk_id"] for item in result.candidates] == [
        "wrong-procedure",
        "right-procedure",
        "generic",
    ]
    assert result.mode == "heuristic"
    assert result.reason_code == "disabled_by_config"
    assert result.degraded is True


def test_injected_cross_encoder_reranks_bounded_window_stably():
    calls: list[list[tuple[str, str]]] = []

    def scorer(pairs: list[tuple[str, str]]) -> list[float]:
        calls.append(pairs)
        return [
            0.05 if "xóa đăng ký" in passage else 0.99 if "nhà thuê" in passage else 0.20
            for _, passage in pairs
        ]

    reranker = OptionalCrossEncoderReranker(
        enabled=True,
        scorer=scorer,
        model_label="fixture-cross-encoder",
        max_candidates=2,
        batch_size=1,
        learned_weight=1.0,
    )
    first = reranker.rerank("đăng ký thường trú nhà thuê", _candidates())
    second = reranker.rerank("đăng ký thường trú nhà thuê", _candidates())

    assert first.mode == "learned"
    assert first.reason_code is None
    assert [item["chunk_id"] for item in first.candidates] == [
        "right-procedure",
        "wrong-procedure",
        "generic",
    ]
    assert first.candidates == second.candidates
    assert all(len(batch) == 1 for batch in calls)
    assert sum(len(batch) for batch in calls[:2]) == 2
    assert "learned_rerank_score" not in first.candidates[-1]


def test_model_failure_degrades_without_changing_candidate_order():
    def failing_scorer(_: list[tuple[str, str]]) -> list[float]:
        raise RuntimeError("fixture inference failure")

    reranker = OptionalCrossEncoderReranker(
        enabled=True,
        scorer=failing_scorer,
        model_label="fixture-cross-encoder",
    )
    result = reranker.rerank("đăng ký thường trú", _candidates())

    assert result.mode == "heuristic"
    assert result.reason_code == "inference_failed"
    assert result.degraded is True
    assert [item["chunk_id"] for item in result.candidates] == [
        "wrong-procedure",
        "right-procedure",
        "generic",
    ]
    assert "fixture inference failure" not in str(result.public_status())


def test_missing_local_model_never_triggers_download(tmp_path):
    missing = tmp_path / "missing-model"
    reranker = OptionalCrossEncoderReranker(
        enabled=True,
        model_path=missing,
    )
    result = reranker.rerank("đăng ký thường trú", _candidates())

    assert result.mode == "heuristic"
    assert result.reason_code == "model_path_missing"
    assert missing.exists() is False


def test_disabled_benchmark_proves_deterministic_identity_preserving_fallback():
    dataset = {
        "examples": [
            {
                "case_id": "case-1",
                "question": "Điều kiện đăng ký thường trú?",
                "positive_sources": [
                    {"law_number": "68/2020/QH14", "article": "20"}
                ],
                "hard_negatives": [
                    {"law_number": "68/2020/QH14", "article": "24"}
                ],
            }
        ]
    }
    report = benchmark_reranker(
        dataset,
        reranker=OptionalCrossEncoderReranker(enabled=False),
        repetitions=10,
    )

    assert report["status"] == "disabled"
    assert report["reason_code"] == "disabled_by_config"
    assert report["deterministic"] is True
    assert report["fallback_verified"] is True
    assert report["candidate_identity_preserved"] is True
    assert report["safety_regression_count"] == 0


def test_retrieval_server_wires_cross_encoder_after_heuristic_window():
    pytest.importorskip("chromadb")
    from scripts.legal_search_server import LegalRetriever

    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._learned_reranker = OptionalCrossEncoderReranker(
        enabled=True,
        scorer=lambda pairs: [
            1.0 if "nhà thuê" in passage else -2.0 for _, passage in pairs
        ],
        model_label="fixture",
    )
    status: dict = {}
    result = retriever._rerank_candidates(
        "đăng ký thường trú tại nhà thuê",
        _candidates(),
        status_out=status,
    )

    assert result[0]["chunk_id"] == "right-procedure"
    assert status["mode"] == "learned"
    assert status["scored_count"] == 3
