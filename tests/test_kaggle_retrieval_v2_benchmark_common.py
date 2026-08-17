from scripts.kaggle_retrieval_v2_benchmark_common import (
    fuse,
    normalize_exact,
    score_case,
)


def test_exact_candidates_are_pinned_ahead_of_hybrid_scores() -> None:
    exact = [{"chunk_revision_id": "exact", "score": 1.0}]
    vector = [{"chunk_revision_id": "vector", "score": 0.99}]
    lexical = [{"chunk_revision_id": "lexical", "score": 1.0}]

    assert fuse(exact, vector, lexical, strategy="weighted")[0]["chunk_revision_id"] == "exact"
    assert fuse(exact, vector, lexical, strategy="rrf")[0]["chunk_revision_id"] == "exact"


def test_two_issue_groups_can_be_covered_by_one_source() -> None:
    source = {"law_number": "60/2014/QH13", "article": "47"}
    case = {
        "case_id": "case",
        "split": "golden-regression",
        "domain": "Hộ tịch/chứng thực",
        "answer_required": True,
        "expected_refusal": False,
        "tags": ["multi_issue"],
        "positive_source_groups": [{"group_id": "g1", "sources": [source]}],
        "issue_groups": [
            {"issue_id": "i1", "required_source_group_ids": ["g1"]},
            {"issue_id": "i2", "required_source_group_ids": ["g1"]},
        ],
    }
    candidate = {"law_number": "60/2014/QH13", "article_number": "47"}

    result = score_case(case, [candidate], [candidate], latency_ms=1.0)

    assert result["hit_at_10"] is True
    assert result["all_required_sources_coverage"] == 1.0
    assert normalize_exact("NĐ  15/2020/NĐ-CP") == "ND 15 2020 ND CP"
