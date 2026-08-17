from __future__ import annotations

import pytest

import scripts.legal_search_server as legal_search
from api.legal_learned_reranker import OptionalCrossEncoderReranker


def _candidates(count: int = 120) -> list[dict]:
    return [
        {
            "chunk_id": index + 1,
            "content": f"candidate {index + 1}",
            "score": 1.0 - index / 1000.0,
        }
        for index in range(count)
    ]


@pytest.mark.parametrize("top_n", [20, 30, 50, 100])
def test_cross_encoder_honors_each_m6_rerank_top_n(top_n: int):
    calls: list[int] = []

    def scorer(pairs: list[tuple[str, str]]) -> list[float]:
        calls.append(len(pairs))
        return [float(index) for index, _ in enumerate(pairs)]

    reranker = OptionalCrossEncoderReranker(
        enabled=True,
        scorer=scorer,
        max_candidates=100,
        batch_size=16,
    )
    outcome = reranker.rerank("query", _candidates(), top_n=top_n)

    assert outcome.mode == "learned"
    assert outcome.scored_count == top_n
    assert sum(calls) == top_n
    assert all("learned_rerank_score" in item for item in outcome.candidates[:top_n])
    assert all(
        "learned_rerank_score" not in item for item in outcome.candidates[top_n:]
    )


def test_search_request_exposes_m6_controls_without_changing_defaults():
    default = legal_search.SearchRequest(query="đăng ký khai sinh")
    experiment = legal_search.SearchRequest(
        query="đăng ký khai sinh",
        candidate_count=20,
        lexical_candidate_count=20,
        rerank_top_n=100,
        enable_parent_expansion=False,
        enable_neighbor_expansion=False,
    )

    assert default.rerank_top_n == 40
    assert default.enable_parent_expansion is True
    assert default.enable_neighbor_expansion is True
    assert experiment.rerank_top_n == 100
    assert experiment.enable_parent_expansion is False
    assert experiment.enable_neighbor_expansion is False


def test_batch_request_propagates_the_same_m6_controls():
    request = legal_search.BatchSearchRequest(
        request_id="m6-test",
        issues=[
            legal_search.BatchSearchIssue(
                issue_id="issue-1",
                query="đăng ký khai sinh",
            )
        ],
        rerank_top_n=50,
        enable_parent_expansion=False,
        enable_neighbor_expansion=True,
    )

    assert request.rerank_top_n == 50
    assert request.enable_parent_expansion is False
    assert request.enable_neighbor_expansion is True


def test_m6_runner_freezes_m5_quality_parent_and_complete_matrix():
    from scripts.run_m6_reranker_expansion_experiments import experiment_specs

    specs = experiment_specs()
    rerank_values = {
        row["config"]["rerank_top_n"]
        for row in specs
        if row["stage"] == "reranker_top_n"
    }

    assert len(specs) == 7
    assert rerank_values == {20, 30, 50, 100}
    assert all(row["config"]["candidate_count"] == 20 for row in specs)
    assert all(row["config"]["lexical_candidate_count"] == 20 for row in specs)
    assert all(row["config"]["fusion_strategy"] == "legacy_stack" for row in specs)
    assert {
        (
            row["config"]["enable_parent_expansion"],
            row["config"]["enable_neighbor_expansion"],
        )
        for row in specs
        if row["stage"] == "expansion"
    } == {(True, False), (False, True)}
