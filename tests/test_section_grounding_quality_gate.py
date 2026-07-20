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
        "repair_count": 0,
        "latency_ms": {"p50": 2000, "p95": 14000},
        "stage_latency_ms": {},
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
