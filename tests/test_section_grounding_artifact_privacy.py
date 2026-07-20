from scripts.scan_section_grounding_artifacts import validate_artifact


def _artifact():
    return {
        "schema_version": 1,
        "mode": "warm",
        "base_url": "http://127.0.0.1:5055",
        "concurrency": 20,
        "request_count": 2,
        "completed_count": 2,
        "status_counts": {"completed": 2},
        "section_status_counts": {"sufficiently_evidenced": 1},
        "error_category_counts": {"none": 2},
        "repair_count": 0,
        "latency_ms": {"p50": 10, "p95": 20},
        "stage_latency_ms": {},
    }


def test_privacy_scanner_accepts_only_aggregate_artifact_shape():
    assert validate_artifact(_artifact())[0] is True


def test_privacy_scanner_rejects_question_or_answer_fields():
    artifact = _artifact()
    artifact["question"] = "must never be written"

    assert validate_artifact(artifact)[0] is False
