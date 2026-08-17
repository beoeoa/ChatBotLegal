from scripts.check_section_grounding_quality_gate import evaluate_gate


def _artifact():
    return {
        "schema_version": 1,
        "mode": "warm",
        "base_url": "http://127.0.0.1:5055",
        "concurrency": 20,
        "request_count": 20,
        "completed_count": 20,
        "status_counts": {"completed": 20},
        "section_status_counts": {"sufficiently_evidenced": 20},
        "error_category_counts": {"none": 20},
        "quality_gate_counts": {"passed": 20},
        "fallback_count": 0,
        "repair_count": 0,
        "latency_ms": {"p50": 2000, "p95": 14000},
        "stage_latency_ms": {
            "retrieval": {"p50": 1000, "p95": 2000},
            "provisioning": {"p50": 0, "p95": 100},
            "generation": {"p50": 10000, "p95": 20000},
            "validation": {"p50": 5, "p95": 10},
            "end_to_end": {"p50": 12000, "p95": 24000},
        },
    }


def test_quality_gate_never_auto_approves_legal_review():
    result = evaluate_gate(_artifact())

    assert result["decision"] == "ready_for_legal_review"
    assert result["checks"]["legal_reviewer_approval"] is False
    assert result["legal_reviewer_required"] is True


def test_quality_gate_blocks_failed_or_slow_benchmark():
    artifact = _artifact()
    artifact["completed_count"] = 19
    artifact["latency_ms"]["p95"] = 16000

    assert evaluate_gate(artifact)["decision"] == "blocked"


def test_quality_gate_uses_step4_stage_thresholds_and_blocks_fallback():
    artifact = _artifact()
    artifact["concurrency"] = 1
    artifact["latency_ms"]["p95"] = 29_000
    artifact["stage_latency_ms"] = {
        "retrieval": {"p95": 3_000},
        "provisioning": {"p95": 100},
        "generation": {"p95": 24_000},
        "validation": {"p95": 10},
        "end_to_end": {"p95": 40_000},
    }
    assert evaluate_gate(artifact)["decision"] == "ready_for_legal_review"

    artifact["fallback_count"] = 1
    artifact["quality_gate_counts"] = {"passed": 19, "failed": 1}
    assert evaluate_gate(artifact)["decision"] == "blocked"


def test_quality_gate_blocks_mass_generation_timeout_even_when_fallback_validates():
    artifact = _artifact()
    artifact["concurrency"] = 5
    artifact["request_count"] = 5
    artifact["completed_count"] = 5
    artifact["status_counts"] = {"completed": 5}
    artifact["error_category_counts"] = {"timeout": 5}
    artifact["quality_gate_counts"] = {"passed": 5}
    artifact["fallback_count"] = 5

    result = evaluate_gate(artifact)

    assert result["decision"] == "blocked"
    assert result["checks"]["generation_timeout_rate_under_50_percent"] is False
