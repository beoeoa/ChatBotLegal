from __future__ import annotations

from scripts.evaluate_feature016_gate_c import evaluate_gate_c


def test_gate_c_allows_explicit_degraded_reranker_but_never_shadow_activation():
    result = evaluate_gate_c(
        reranker={
            "status": "disabled",
            "reason_code": "model_path_missing",
            "fallback_verified": True,
            "deterministic": True,
            "oom_count": 0,
            "safety_regression_count": 0,
        },
        embedding_shadow={
            "status": "disabled",
            "reason_code": "model_path_missing",
            "activation_requested": False,
            "active_collection_before": "active-v1",
            "active_collection_after": "active-v1",
            "safety_regression_count": 0,
        },
    )

    assert result["gate_c_pass"] is True
    assert result["status"] == "pass_degraded"
    assert result["active_collection_unchanged"] is True


def test_gate_c_fails_on_safety_regression_or_pointer_switch():
    result = evaluate_gate_c(
        reranker={
            "status": "pass",
            "fallback_verified": True,
            "deterministic": True,
            "oom_count": 0,
            "safety_regression_count": 1,
        },
        embedding_shadow={
            "status": "pass",
            "activation_requested": True,
            "active_collection_before": "active-v1",
            "active_collection_after": "shadow-v1",
            "safety_regression_count": 0,
        },
    )

    assert result["gate_c_pass"] is False
    assert "safety_regression" in result["reason_codes"]
    assert "active_collection_changed" in result["reason_codes"]

