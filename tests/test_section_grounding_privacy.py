from api.legal_section_grounding import (
    build_section_grounding_metric,
    is_privacy_safe_metric,
)


def test_metric_contains_only_opaque_ids_status_counts_timing_and_error_category():
    metric = build_section_grounding_metric(
        request_id="req-opaque-1",
        statuses=["sufficiently_evidenced", "insufficiently_evidenced"],
        stage_timings_ms={"retrieval": 31, "validation": 12},
        repair_count=1,
        completed=True,
        error_category="none",
    )

    assert metric == {
        "request_id": "req-opaque-1",
        "section_status_counts": {
            "sufficiently_evidenced": 1,
            "insufficiently_evidenced": 1,
        },
        "stage_timings_ms": {"retrieval": 31, "validation": 12},
        "repair_count": 1,
        "completed": True,
        "error_category": "none",
    }
    assert is_privacy_safe_metric(metric)


def test_metric_validator_rejects_raw_question_answer_citation_attachment_credential_or_exception():
    base = build_section_grounding_metric(
        request_id="req-opaque-1",
        statuses=[],
        stage_timings_ms={},
        repair_count=0,
        completed=False,
        error_category="retrieval_unavailable",
    )

    for forbidden_key in ("question", "answer", "citation_body", "attachment", "credential", "exception_message"):
        unsafe = {**base, forbidden_key: "sensitive value"}
        assert not is_privacy_safe_metric(unsafe)
