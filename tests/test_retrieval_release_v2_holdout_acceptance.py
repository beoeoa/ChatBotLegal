from __future__ import annotations

import pytest

from scripts.run_retrieval_release_v2_holdout_acceptance import (
    ROOT,
    _require_external_custody_bundle,
    holdout_gates,
)


def _summary(**overrides):
    value = {
        "case_count": 500,
        "recall_at_10": 0.96,
        "mrr_at_10": 0.91,
        "correct_refusal_rate": 1.0,
        "multi_issue_case_count": 10,
        "multi_issue_all_required_sources_coverage": 0.96,
        "exact_law_article_case_count": 10,
        "exact_law_article_lookup_recall_at_50": 1.0,
        "exact_law_article_final_recall_at_10": 1.0,
        "errors": 0,
        "outside_manifest_count": 0,
        "invalid_temporal_count": 0,
        "latency_ms": {"p95": 2500.0},
        "per_domain": {
            "Hộ tịch/chứng thực": {"answer_required_count": 80, "recall_at_10": 0.95},
        },
    }
    value.update(overrides)
    return value


def test_holdout_gate_requires_the_full_safety_and_quality_contract():
    gates = holdout_gates(_summary(), mode="m5")
    assert all(gates.values())

    unsafe = holdout_gates(_summary(outside_manifest_count=1), mode="m5")
    assert unsafe["safety"] is False


def test_holdout_gate_uses_m6_latency_budget():
    assert holdout_gates(_summary(latency_ms={"p95": 14999}), mode="m6")["latency_p95"] is True
    assert holdout_gates(_summary(latency_ms={"p95": 15001}), mode="m6")["latency_p95"] is False


def test_holdout_runner_rejects_custody_bundle_inside_repository():
    with pytest.raises(RuntimeError, match="outside_repository"):
        _require_external_custody_bundle(ROOT / "reports" / "secret-holdout.json")
