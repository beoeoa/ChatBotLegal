from __future__ import annotations

import pytest

from scripts.benchmark_feature016_isolated_api import (
    CONCURRENCY_LEVELS,
    _assert_privacy_safe,
    _evaluate_gates,
    run_benchmark,
)


@pytest.mark.asyncio
async def test_short_isolated_api_matrix_exercises_all_required_concurrencies():
    report = await run_benchmark(
        requests_per_level=30,
        duration_seconds=1.0,
        rps=3.0,
    )

    assert [row["concurrency"] for row in report["concurrency_matrix"]] == list(
        CONCURRENCY_LEVELS
    )
    assert all(
        row["completed_count"] == row["request_count"]
        for row in report["concurrency_matrix"]
    )
    assert set(report["concurrency_matrix"][0]["stage_latency_ms"]) == {
        "retrieval",
        "provisioning",
        "generation",
        "validation",
        "end_to_end",
    }
    assert report["execution_mode"].endswith("stubbed_external_dependencies")


def test_privacy_guard_rejects_content_bearing_artifact_keys():
    with pytest.raises(ValueError, match="privacy_forbidden_key"):
        _assert_privacy_safe({"result": {"question": "must not persist"}})


def test_final_gate_requires_real_ten_minute_duration():
    matrix = [
        {
            "concurrency": level,
            "request_count": 30,
            "completed_count": 30,
            "error_rate": 0.0,
            "latency_ms": {"p95": 10},
            "stage_latency_ms": {"retrieval": {"p95": 1}},
        }
        for level in CONCURRENCY_LEVELS
    ]
    sustained = {
        "target_rps": 3.0,
        "elapsed_seconds": 1.0,
        "achieved_rps": 3.0,
        "request_count": 3,
        "completed_count": 3,
        "error_rate": 0.0,
        "latency_ms": {"p95": 10},
        "stage_latency_ms": {"retrieval": {"p95": 1}},
    }

    assert _evaluate_gates(matrix, sustained)["sustained_3_rps_duration_met"] is False
