from scripts.build_feature006_form_completion_final import build_report


def test_final_report_keeps_supplemental_candidates_non_serving():
    report = build_report(
        integration={
            "target_identities": 3,
            "runtime_approved_identities": 1,
            "ready_for_human_attestation_identities": 1,
            "terminal_gap_identities": 1,
            "unaccounted_pending_identities": 0,
            "review_batch_count": 1,
            "review_batch_identity_counts": [1],
            "supplemental_identity_count": 1,
            "supplemental_candidate_binding_count": 2,
        },
        research_queue={
            "records": [
                {
                    "requirement_identity_id": "ready-1",
                    "research_action": (
                        "QUEUE_FOR_FUTURE_HUMAN_ATTESTATION"
                    ),
                    "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
                },
                {
                    "requirement_identity_id": "gap-1",
                    "research_action": "RETRY_OFFICIAL_FORM_ATTACHMENT_LOOKUP",
                    "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
                },
            ]
        },
        campaign_gaps={
            "records": [
                {
                    "requirement_identity_id": "gap-1",
                    "procedure_id": "1.000001",
                    "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
                }
            ]
        },
        verification={
            "technical_pass": True,
            "passed_count": 1,
            "failed_count": 0,
            "file_format_counts": {"pdf": 1},
        },
        five_domain={
            "legal_as_of": "2026-07-30",
            "summary": {
                "verdict": "PASS",
                "minimum_score": 10,
                "average_score": 10,
                "median_latency_seconds": 10,
                "p95_latency_seconds": 30,
                "critical_error_count": 0,
            },
        },
        form_lookup={
            "case_count": 3,
            "exact_form_recall": 1,
            "wrong_form_count": 0,
            "pending_form_exposure_count": 0,
            "expired_form_exposure_count": 0,
            "role_leakage_count": 0,
        },
        form_role={
            "pass_count": 3,
            "case_count": 3,
            "lookup_p95_ms": 10,
        },
        release_gate={
            "status": "BLOCKED_RELEASE",
            "passed": 1,
            "failed": 1,
            "blocked": 0,
        },
        artifact_checksums={"evidence": "a" * 64},
    )

    assert report["release_verdict"] == "NO_GO"
    assert report["counts"]["runtime_approved_identities"] == 1
    assert report["newly_verified"]["identity_count"] == 1
    assert report["blocked_identities"] == [
        {
            "requirement_identity_id": "gap-1",
            "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
            "procedure_ids": ["1.000001"],
        }
    ]
    assert report["automated_approval"] is False
    assert report["feature_flag_enabled"] is False
