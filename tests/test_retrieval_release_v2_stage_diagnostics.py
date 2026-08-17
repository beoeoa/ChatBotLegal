from __future__ import annotations

from scripts.run_retrieval_release_v2_stage_diagnostics import _root_cause, _summarize


def _row(**overrides):
    value = {
        "answer_required": True,
        "source_available": True,
        "article_chunk_available": True,
        "exact_expected": False,
        "exact_hit_at_50": False,
        "vector_hit_at_20": True,
        "vector_hit_at_50": True,
        "lexical_hit_at_20": False,
        "lexical_hit_at_50": False,
        "fusion_hit_at_20": True,
        "fusion_hit_at_50": True,
        "candidate_hit_at_50": True,
        "final_hit_at_10": True,
        "outside_manifest_count": 0,
        "invalid_temporal_count": 0,
        "latency_ms": 10.0,
        "domain": "Hộ tịch/chứng thực",
        "expected_refusal": False,
    }
    value.update(overrides)
    return value


def test_stage_diagnostics_assigns_single_primary_root_cause():
    assert _root_cause(_row(source_available=False)) == "source_absent"
    assert _root_cause(_row(article_chunk_available=False)) == "article_chunk_absent"
    assert _root_cause(_row(exact_expected=True, exact_hit_at_50=False)) == "exact_lookup_failure"
    assert _root_cause(_row(vector_hit_at_50=False, lexical_hit_at_50=False)) == "candidate_miss"
    assert _root_cause(_row(fusion_hit_at_50=False)) == "fusion_rank_loss"
    assert _root_cause(_row(final_hit_at_10=False)) == "fusion_rank_loss"


def test_stage_summary_exposes_candidate_and_per_domain_metrics():
    summary = _summarize([_row(), _row(final_hit_at_10=False)])
    assert summary["candidate_recall_at_50"] == 1.0
    assert summary["final_recall_at_10"] == 0.5
    assert summary["per_domain"]["Hộ tịch/chứng thực"]["final_recall_at_10"] == 0.5
