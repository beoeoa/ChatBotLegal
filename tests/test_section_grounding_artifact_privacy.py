import json
from pathlib import Path

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
        "quality_gate_counts": {"passed": 2},
        "fallback_count": 0,
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


def test_privacy_scanner_accepts_role_matrix_summary_without_legal_content():
    artifact = {
        "schema_version": 1,
        "model_override_used": False,
        "case_count": 1,
        "pass_count": 1,
        "critical_failure_count": 0,
        "root_cause_counts": {},
        "cases": [
            {
                "case_id": "citizen-case-1",
                "role": "citizen",
                "http_status": 200,
                "elapsed_ms": 100,
                "score": 10.0,
                "pass": True,
                "critical_failure": False,
                "coverage_ratio": 1.0,
                "covered_facets": ["documents"],
                "unavailable_facets": [],
                "handled_facets": ["documents"],
                "missing_facets": [],
                "claim_source_ok": True,
                "clarification_ok": True,
                "role_ok": True,
                "form_status": "not_applicable",
                "root_causes": [],
            }
        ],
    }

    assert validate_artifact(artifact) == (
        True,
        "privacy-safe role matrix aggregate",
    )


def test_privacy_scanner_rejects_role_matrix_question_content():
    artifact = {
        "schema_version": 1,
        "model_override_used": False,
        "case_count": 1,
        "pass_count": 1,
        "critical_failure_count": 0,
        "root_cause_counts": {},
        "cases": [{"case_id": "case-1", "role": "citizen", "question": "secret"}],
    }

    assert validate_artifact(artifact)[0] is False


def test_privacy_scanner_accepts_bulk_legal_review_aggregate():
    artifact = {
        "schema_version": "feature005-bulk-legal-review-v1",
        "generated_at": "2026-07-27",
        "status": "BLOCKED_LEGAL_REVIEW_DATA",
        "catalog_counts": {
            "total_forms": 93,
            "eligible_forms": 0,
            "excluded_forms": 93,
            "already_approved_forms": 0,
            "runtime_approved_forms": 0,
            "approved_bindings": 0,
            "attestation_count": 0,
        },
        "reason_counts": {"EFFECTIVITY_UNKNOWN": 13},
        "test_counts": {
            "bulk_review_passed": 21,
            "focused_backend_passed": 44,
            "full_backend_passed": 964,
            "full_frontend_passed": 89,
            "failed": 0,
        },
        "gate_status": {
            "rollback": "pass",
            "idempotency": "pass",
            "type_check": "pass",
            "lint": "pass",
            "production_build": "pass",
            "live_admin_preview": "pass_fail_closed",
        },
        "runtime_safety": {
            "legal_section_grounding_enabled": False,
            "active_collection_changed": False,
            "corpus_deleted": False,
            "corpus_rewritten": False,
            "corpus_reembedded": False,
            "automated_legal_approval": False,
        },
    }

    assert validate_artifact(artifact) == (
        True,
        "privacy-safe bulk legal-review aggregate",
    )


def test_privacy_scanner_rejects_bulk_review_report_with_raw_question():
    artifact = {
        "schema_version": "feature005-bulk-legal-review-v1",
        "generated_at": "2026-07-27",
        "status": "BLOCKED_LEGAL_REVIEW_DATA",
        "catalog_counts": {},
        "reason_counts": {},
        "test_counts": {},
        "gate_status": {},
        "runtime_safety": {"question": "must never be written"},
    }

    assert validate_artifact(artifact)[0] is False


def _form_reconciliation_artifact():
    preview = {
        "summary": {
            "total_forms": 93,
            "eligible_forms": 10,
            "excluded_forms": 83,
            "already_approved_forms": 0,
        },
        "reason_counts": {"CANDIDATE_NOT_FOUND": 72},
    }
    return {
        "schema_version": "canonical-form-reconciliation-report-v1",
        "generated_at": "2026-07-27",
        "legal_as_of": "2026-07-27",
        "status": "READY_FOR_HUMAN_ATTESTATION",
        "applied": True,
        "baseline_unmapped_forms": 80,
        "attempted_unmapped_forms": 80,
        "summary": {
            "total_forms": 93,
            "mapped_forms": 10,
            "verified_data_gaps": 83,
            "effectivity_enriched_candidates": 10,
            "auto_approved": 0,
            "runtime_promoted": 0,
            "corpus_modified": False,
            "embedding_run": False,
            "active_collection_changed": False,
        },
        "outcome_reason_counts": {
            "EXACT_PROCEDURE_AND_FORM_IDENTITY": 8,
            "NO_EXACT_OFFICIAL_CANDIDATE": 11,
        },
        "preview_stages": {
            "baseline": preview,
            "after_exact_mapping": preview,
            "after_effectivity_provenance": preview,
        },
        "human_attestation_required": True,
        "auto_approved": 0,
        "runtime_promoted": 0,
        "corpus_modified": False,
        "embedding_run": False,
        "active_collection_changed": False,
        "feature_flag_required_value": False,
    }


def test_privacy_scanner_accepts_form_reconciliation_aggregate():
    assert validate_artifact(_form_reconciliation_artifact()) == (
        True,
        "privacy-safe form reconciliation aggregate",
    )


def test_privacy_scanner_rejects_form_reconciliation_with_raw_question():
    artifact = _form_reconciliation_artifact()
    artifact["raw_question"] = "must never be written"

    assert validate_artifact(artifact)[0] is False


def test_privacy_scanner_accepts_form_completion_campaign_aggregate():
    artifact = {
        "schema_version": "form-completion-campaign-v1",
        "run_id": "opaque-run",
        "generated_at": "2026-07-27T12:00:00Z",
        "legal_as_of": "2026-07-27",
        "status": "completed_fail_closed",
        "stage": "complete",
        "counts": {
            "active_catalog_forms": 65,
            "ready_for_attestation": 0,
            "remaining_unresolved": 55,
        },
        "reason_counts": {
            "NO_UNAMBIGUOUS_OFFICIAL_SOURCE_MATCH": 29,
        },
        "automated_approval": False,
        "human_attestation_required": True,
    }

    assert validate_artifact(artifact) == (
        True,
        "privacy-safe form completion aggregate",
    )


def test_privacy_scanner_accepts_feature006_reconciliation_aggregate():
    artifact = {
        "schema_version": "feature006-form-completion-reconciliation-v1",
        "generated_at": "2026-07-30T03:34:22+00:00",
        "legal_as_of": "2026-07-29",
        "campaign_run_id": "run-opaque",
        "release_verdict": "BLOCKED_DATA",
        "counts": {
            "target_identities": 278,
            "runtime_approved_identities": 51,
            "non_serving_identities": 227,
            "ready_for_human_attestation_identities": 68,
            "terminal_gap_identities": 159,
            "unaccounted_pending_identities": 0,
            "review_batches": 3,
            "review_batch_identity_counts": [25, 22, 21],
        },
        "gap_reason_counts": {"OFFICIAL_FORM_FILE_NOT_FOUND": 16},
        "checksums": {
            key: "a" * 64
            for key in (
                "requirement_manifest",
                "source_snapshot",
                "campaign_status",
                "review_batches",
                "research_queue",
                "form_lookup_quality",
                "five_domain_quality_warm",
            )
        },
        "quality": {
            "form_lookup_cases": 1254,
            "exact_form_recall": 1.0,
            "wrong_form_count": 0,
            "pending_form_exposure_count": 0,
            "expired_form_exposure_count": 0,
            "role_leakage_count": 0,
            "five_domain_minimum_score": 10.0,
            "five_domain_average_score": 10.0,
            "five_domain_median_seconds": 9.721,
            "five_domain_p95_seconds": 27.098,
            "five_domain_critical_errors": 0,
        },
        "candidate_only": True,
        "automated_approval_count": 0,
        "runtime_catalog_mutated_by_campaign": False,
        "feature_flag_enabled": False,
        "human_attestation_required": True,
    }

    assert validate_artifact(artifact) == (
        True,
        "privacy-safe Feature 006 reconciliation aggregate",
    )


def test_privacy_scanner_accepts_post_attestation_gate_aggregate():
    artifact = {
        "schema_version": "form-post-attestation-gate-v1",
        "status": "BLOCKED_RELEASE",
        "stage": "complete",
        "generated_at": "2026-07-27T12:00:00Z",
        "legal_as_of": "2026-07-27",
        "attestation_ref": "form-batch-opaque",
        "passed": 8,
        "failed": 1,
        "blocked": 1,
        "checks": [
            {
                "name": "generation_benchmark_concurrency_1_5",
                "status": "BLOCKED",
                "reason_code": "LEGAL_BENCHMARK_TOKEN_REQUIRED",
                "duration_seconds": 0,
            }
        ],
        "feature_flag_enabled": False,
    }

    assert validate_artifact(artifact) == (
        True,
        "privacy-safe post-attestation gate aggregate",
    )


def test_privacy_scanner_accepts_feature006_release_artifacts():
    root = Path(__file__).resolve().parents[1]
    expected = {
        "reports/feature006/form-lookup-quality.json":
            "privacy-safe Feature 006 form lookup aggregate",
        "reports/feature006/five-domain-chatbot-quality-restored.json":
            "privacy-safe Feature 006 five-domain aggregate",
        "reports/feature006/data-readiness-manifest.json":
            "privacy-safe Feature 006 data manifest",
        "reports/feature006/unified-release-manifest.json":
            "privacy-safe Feature 006 unified release manifest",
        "reports/feature006/form-role-api-matrix.json":
            "privacy-safe Feature 006 form-role matrix",
        "reports/feature006/reconciled-probe-candidate-verification.json":
            "privacy-safe Feature 006 probe verification",
        "reports/feature006/probe-campaign-integration.json":
            "privacy-safe Feature 006 probe campaign integration",
        "reports/feature006/form-completion-final.json":
            "privacy-safe Feature 006 final completion report",
    }
    for relative, message in expected.items():
        artifact = json.loads((root / relative).read_text(encoding="utf-8"))
        assert validate_artifact(artifact) == (True, message)


def test_privacy_scanner_rejects_raw_content_in_feature006_artifacts():
    root = Path(__file__).resolve().parents[1]
    artifact = json.loads(
        (
            root
            / "reports/feature006/five-domain-chatbot-quality-restored.json"
        ).read_text(encoding="utf-8")
    )
    artifact["cases"][0]["raw_question"] = "must never be persisted"

    assert validate_artifact(artifact)[0] is False
