"""Reject Feature 005 artifacts that exceed the approved privacy-safe shape."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


ALLOWED_TOP_LEVEL = {
    "schema_version",
    "mode",
    "base_url",
    "concurrency",
    "request_count",
    "completed_count",
    "status_counts",
    "section_status_counts",
    "error_category_counts",
    "quality_gate_counts",
    "fallback_count",
    "repair_count",
    "latency_ms",
    "stage_latency_ms",
}
FORBIDDEN_KEY_PARTS = {
    "question", "answer", "citation", "attachment", "credential", "exception",
    "content", "prompt", "token", "secret", "password", "trace", "chunk", "packet",
}
ALLOWED_NESTED_KEYS = {"p50", "p95"}
ROLE_MATRIX_TOP_LEVEL = {
    "schema_version",
    "model_override_used",
    "case_count",
    "pass_count",
    "critical_failure_count",
    "root_cause_counts",
    "cases",
}
ROLE_MATRIX_CASE_KEYS = {
    "case_id",
    "role",
    "http_status",
    "elapsed_ms",
    "score",
    "pass",
    "critical_failure",
    "coverage_ratio",
    "covered_facets",
    "unavailable_facets",
    "handled_facets",
    "missing_facets",
    "claim_source_ok",
    "clarification_ok",
    "role_ok",
    "form_status",
    "root_causes",
    "rendered",
    "coverage_ok",
    "form_contract_ok",
    "role_contract_ok",
    "jurisdiction_ok",
    "procedure_topic_ok",
    "internal_leak",
    "admin_diagnostics_visibility_ok",
}
BULK_LEGAL_REVIEW_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "status",
    "catalog_counts",
    "reason_counts",
    "test_counts",
    "gate_status",
    "runtime_safety",
}
BULK_LEGAL_REVIEW_CATALOG_KEYS = {
    "total_forms",
    "eligible_forms",
    "excluded_forms",
    "already_approved_forms",
    "runtime_approved_forms",
    "approved_bindings",
    "attestation_count",
}
BULK_LEGAL_REVIEW_TEST_KEYS = {
    "bulk_review_passed",
    "focused_backend_passed",
    "full_backend_passed",
    "full_frontend_passed",
    "failed",
}
BULK_LEGAL_REVIEW_GATE_KEYS = {
    "rollback",
    "idempotency",
    "type_check",
    "lint",
    "production_build",
    "live_admin_preview",
}
BULK_LEGAL_REVIEW_RUNTIME_KEYS = {
    "legal_section_grounding_enabled",
    "active_collection_changed",
    "corpus_deleted",
    "corpus_rewritten",
    "corpus_reembedded",
    "automated_legal_approval",
}
FORM_RECONCILIATION_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "legal_as_of",
    "status",
    "applied",
    "baseline_unmapped_forms",
    "attempted_unmapped_forms",
    "summary",
    "outcome_reason_counts",
    "preview_stages",
    "human_attestation_required",
    "auto_approved",
    "runtime_promoted",
    "corpus_modified",
    "embedding_run",
    "active_collection_changed",
    "feature_flag_required_value",
}
FORM_RECONCILIATION_SUMMARY_KEYS = {
    "total_forms",
    "mapped_forms",
    "verified_data_gaps",
    "effectivity_enriched_candidates",
    "auto_approved",
    "runtime_promoted",
    "corpus_modified",
    "embedding_run",
    "active_collection_changed",
}
FORM_RECONCILIATION_PREVIEW_STAGES = {
    "baseline",
    "after_exact_mapping",
    "after_effectivity_provenance",
}
FORM_RECONCILIATION_PREVIEW_SUMMARY_KEYS = {
    "total_forms",
    "eligible_forms",
    "excluded_forms",
    "already_approved_forms",
}
FORM_COMPLETION_TOP_LEVEL = {
    "schema_version",
    "run_id",
    "generated_at",
    "legal_as_of",
    "status",
    "stage",
    "counts",
    "reason_counts",
    "automated_approval",
    "human_attestation_required",
}
FORM_COMPLETION_COUNT_KEYS = {
    "active_catalog_forms",
    "already_approved",
    "blocked_external",
    "candidate_sync_created",
    "candidate_sync_updated",
    "downloaded_candidates",
    "excluded_no_official_form",
    "queued_jobs",
    "ready_for_attestation",
    "remaining_unresolved",
    "technical_incomplete",
    "technical_passed",
    "verified_data_gap",
}
POST_ATTESTATION_TOP_LEVEL = {
    "schema_version",
    "status",
    "stage",
    "generated_at",
    "legal_as_of",
    "attestation_ref",
    "passed",
    "failed",
    "blocked",
    "checks",
    "feature_flag_enabled",
}
POST_ATTESTATION_CHECK_KEYS = {
    "name",
    "status",
    "exit_code",
    "reason_code",
    "duration_seconds",
}
FEATURE006_RECONCILIATION_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "legal_as_of",
    "campaign_run_id",
    "release_verdict",
    "counts",
    "gap_reason_counts",
    "checksums",
    "quality",
    "candidate_only",
    "automated_approval_count",
    "runtime_catalog_mutated_by_campaign",
    "feature_flag_enabled",
    "human_attestation_required",
}
FEATURE006_RECONCILIATION_COUNT_KEYS = {
    "target_identities",
    "runtime_approved_identities",
    "non_serving_identities",
    "ready_for_human_attestation_identities",
    "terminal_gap_identities",
    "unaccounted_pending_identities",
    "review_batches",
    "review_batch_identity_counts",
}
FEATURE006_RECONCILIATION_CHECKSUM_KEYS = {
    "requirement_manifest",
    "source_snapshot",
    "campaign_status",
    "review_batches",
    "research_queue",
    "form_lookup_quality",
    "five_domain_quality_warm",
}
FEATURE006_RECONCILIATION_QUALITY_KEYS = {
    "form_lookup_cases",
    "exact_form_recall",
    "wrong_form_count",
    "pending_form_exposure_count",
    "expired_form_exposure_count",
    "role_leakage_count",
    "five_domain_minimum_score",
    "five_domain_average_score",
    "five_domain_median_seconds",
    "five_domain_p95_seconds",
    "five_domain_critical_errors",
}
FEATURE006_FORM_LOOKUP_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "legal_as_of",
    "procedure_count",
    "role_count",
    "case_count",
    "expected_state_counts",
    "exact_form_recall",
    "expected_form_count",
    "returned_form_count",
    "wrong_form_count",
    "missing_form_count",
    "pending_form_exposure_count",
    "expired_form_exposure_count",
    "role_leakage_count",
    "procedure_top1_rate",
    "wrong_procedure_count",
    "lookup_p50_ms",
    "lookup_p95_ms",
    "per_domain",
    "privacy",
    "technical_pass",
}
FEATURE006_FORM_LOOKUP_DOMAIN_KEYS = {
    "procedure_count",
    "form_bearing_procedure_count",
    "expected_form_count",
    "returned_form_count",
    "exact_form_hits",
    "wrong_form_count",
    "missing_form_count",
    "exact_form_recall",
}
FEATURE006_FIVE_DOMAIN_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "legal_as_of",
    "thresholds",
    "summary",
    "cases",
}
FEATURE006_FIVE_DOMAIN_THRESHOLD_KEYS = {
    "minimum_each_score",
    "minimum_average_score",
    "maximum_median_latency_seconds",
    "maximum_p95_latency_seconds",
    "maximum_critical_errors",
}
FEATURE006_FIVE_DOMAIN_SUMMARY_KEYS = {
    "case_count",
    "minimum_score",
    "average_score",
    "median_latency_seconds",
    "p95_latency_seconds",
    "critical_error_count",
    "verdict",
}
FEATURE006_FIVE_DOMAIN_CASE_KEYS = {
    "case_id",
    "domain",
    "http_status",
    "latency_seconds",
    "api_score_preview",
    "audit_score",
    "release_score",
    "grounding_status",
    "citation_count",
    "returned_form_codes",
    "expected_form_codes",
    "wrong_form",
    "duplicate_claim_count",
    "quality_flags",
    "critical_errors",
}
FEATURE006_DATA_MANIFEST_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "legal_as_of",
    "status",
    "counts",
    "artifacts",
    "git_head",
    "active_collection",
    "model_fingerprint",
    "restore_status",
    "five_domain_status",
    "release_gate_status",
    "official_index_drift",
    "automated_approval_count",
    "feature_flag_enabled",
    "checksum_verification",
    "release_blockers",
}
FEATURE006_ARTIFACT_CATEGORIES = {
    "forms",
    "procedures",
    "legal_sources",
    "datasets",
    "indexes",
    "code",
    "attestations",
    "release_evidence",
}
FEATURE006_OFFICIAL_INDEX_DRIFT_KEYS = {
    "status",
    "prior_sha256",
    "current_sha256",
    "prior_reference_count",
    "current_reference_count",
    "added_reference_ids",
    "removed_reference_ids",
    "runtime_ready_delta",
    "requires_release_rebind",
}
FEATURE006_UNIFIED_MANIFEST_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "release_verdict",
    "rollout_stage",
    "feature_flag_enabled",
    "data_manifest_sha256",
    "gates",
    "human_decisions",
    "rollout_policy",
}
FEATURE006_FORM_ROLE_MATRIX_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "legal_as_of",
    "procedure_count",
    "role_count",
    "case_count",
    "pass_count",
    "roles",
    "lookup_median_ms",
    "lookup_p95_ms",
    "privacy",
    "technical_pass",
}
FEATURE006_FORM_ROLE_KEYS = {
    "case_count",
    "pass_count",
    "wrong_form_count",
    "missing_form_count",
    "pending_form_exposure_count",
    "expired_form_exposure_count",
    "role_leakage_count",
    "lookup_p95_ms",
}
FEATURE006_PROBE_VERIFICATION_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "legal_as_of",
    "queue_sha256",
    "probe_checksums",
    "candidate_count",
    "passed_count",
    "failed_count",
    "file_format_counts",
    "checks",
    "candidate_only",
    "automated_approval",
    "human_attestation_created",
    "runtime_eligible_count",
    "feature_flag_enabled",
    "contains_question_text",
    "contains_answer_text",
    "contains_credentials",
    "technical_pass",
}
FEATURE006_PROBE_CHECK_KEYS = {
    "requirement_identity_id",
    "sha256",
    "file_format",
    "size_bytes",
    "status",
    "reason_codes",
}
FEATURE006_PROBE_INTEGRATION_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "source_run_id",
    "destination_run_id",
    "manifest_sha256",
    "source_snapshot_sha256",
    "target_identities",
    "runtime_approved_identities",
    "ready_for_human_attestation_identities",
    "terminal_gap_identities",
    "unaccounted_pending_identities",
    "supplemental_identity_count",
    "supplemental_candidate_binding_count",
    "review_batch_count",
    "review_batch_identity_counts",
    "candidate_store_created_count",
    "candidate_store_preserved_count",
    "candidate_store_sha256",
    "candidate_backup_sha256",
    "automated_approval",
    "runtime_catalog_mutated",
    "feature_flag_enabled",
    "human_attestation_required",
    "status",
}
FEATURE006_FORM_COMPLETION_FINAL_TOP_LEVEL = {
    "schema_version",
    "generated_at",
    "legal_as_of",
    "status",
    "release_verdict",
    "counts",
    "newly_verified",
    "blocked_reason_counts",
    "blocked_identities",
    "quality",
    "release_gate",
    "human_decisions",
    "artifact_checksums",
    "candidate_only",
    "automated_approval",
    "runtime_catalog_mutated",
    "feature_flag_enabled",
    "contains_question_text",
    "contains_answer_text",
    "contains_credentials",
}
FEATURE006_FORM_COMPLETION_FINAL_BLOCKED_IDENTITY_KEYS = {
    "requirement_identity_id",
    "reason_code",
    "procedure_ids",
}


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).casefold()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                return True
            if _contains_forbidden_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _aggregate_reason_counts(value: Any) -> bool:
    return isinstance(value, dict) and all(
        re.fullmatch(r"[A-Z][A-Z0-9_]*", str(key))
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 0
        for key, count in value.items()
    )


def validate_artifact(payload: Any) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "artifact must be a JSON object"
    if set(payload) == ROLE_MATRIX_TOP_LEVEL:
        cases = payload.get("cases")
        if not isinstance(cases, list) or any(
            not isinstance(item, dict)
            or bool(set(item).difference(ROLE_MATRIX_CASE_KEYS))
            for item in cases
        ):
            return False, "role matrix cases exceed the approved aggregate shape"
        if _contains_forbidden_key(payload):
            return False, "artifact includes a forbidden content-bearing key"
        return True, "privacy-safe role matrix aggregate"
    if set(payload) == BULK_LEGAL_REVIEW_TOP_LEVEL:
        if _contains_forbidden_key(payload):
            return False, "artifact includes a forbidden content-bearing key"
        nested_shapes = (
            ("catalog_counts", BULK_LEGAL_REVIEW_CATALOG_KEYS),
            ("test_counts", BULK_LEGAL_REVIEW_TEST_KEYS),
            ("gate_status", BULK_LEGAL_REVIEW_GATE_KEYS),
            ("runtime_safety", BULK_LEGAL_REVIEW_RUNTIME_KEYS),
        )
        for key, allowed in nested_shapes:
            value = payload.get(key)
            if not isinstance(value, dict) or set(value) != allowed:
                return False, f"{key} exceeds the approved aggregate shape"
        reason_counts = payload.get("reason_counts")
        if not _aggregate_reason_counts(reason_counts):
            return False, "reason_counts must contain aggregate reason codes"
        return True, "privacy-safe bulk legal-review aggregate"
    if set(payload) == FORM_RECONCILIATION_TOP_LEVEL:
        if _contains_forbidden_key(payload):
            return False, "artifact includes a forbidden content-bearing key"
        summary = payload.get("summary")
        if (
            not isinstance(summary, dict)
            or set(summary) != FORM_RECONCILIATION_SUMMARY_KEYS
        ):
            return False, "summary exceeds the approved reconciliation shape"
        if not _aggregate_reason_counts(payload.get("outcome_reason_counts")):
            return False, "outcome reason counts must be aggregate reason codes"
        stages = payload.get("preview_stages")
        if not isinstance(stages, dict) or set(stages) != FORM_RECONCILIATION_PREVIEW_STAGES:
            return False, "preview stages exceed the approved reconciliation shape"
        for stage in stages.values():
            if not isinstance(stage, dict) or set(stage) != {
                "summary",
                "reason_counts",
            }:
                return False, "preview stage exceeds the approved reconciliation shape"
            stage_summary = stage.get("summary")
            if (
                not isinstance(stage_summary, dict)
                or set(stage_summary)
                != FORM_RECONCILIATION_PREVIEW_SUMMARY_KEYS
            ):
                return False, "preview summary exceeds the approved reconciliation shape"
            if not _aggregate_reason_counts(stage.get("reason_counts")):
                return False, "preview reason counts must be aggregate reason codes"
        return True, "privacy-safe form reconciliation aggregate"
    if set(payload) == FORM_COMPLETION_TOP_LEVEL:
        if _contains_forbidden_key(payload):
            return False, "artifact includes a forbidden content-bearing key"
        counts = payload.get("counts")
        if (
            not isinstance(counts, dict)
            or set(counts).difference(FORM_COMPLETION_COUNT_KEYS)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in counts.values()
            )
        ):
            return False, "form completion counts exceed the approved aggregate shape"
        if not _aggregate_reason_counts(payload.get("reason_counts")):
            return False, "reason_counts must contain aggregate reason codes"
        return True, "privacy-safe form completion aggregate"
    if set(payload) == POST_ATTESTATION_TOP_LEVEL:
        if _contains_forbidden_key(payload):
            return False, "artifact includes a forbidden content-bearing key"
        checks = payload.get("checks")
        if (
            not isinstance(checks, list)
            or any(
                not isinstance(item, dict)
                or set(item).difference(POST_ATTESTATION_CHECK_KEYS)
                for item in checks
            )
        ):
            return False, "post-attestation checks exceed the approved aggregate shape"
        return True, "privacy-safe post-attestation gate aggregate"
    if set(payload) == FEATURE006_RECONCILIATION_TOP_LEVEL:
        if _contains_forbidden_key(payload):
            return False, "artifact includes a forbidden content-bearing key"
        counts = payload.get("counts")
        if (
            not isinstance(counts, dict)
            or set(counts) != FEATURE006_RECONCILIATION_COUNT_KEYS
        ):
            return False, "Feature 006 counts exceed the approved aggregate shape"
        batch_counts = counts.get("review_batch_identity_counts")
        scalar_counts = {
            key: value
            for key, value in counts.items()
            if key != "review_batch_identity_counts"
        }
        if (
            any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in scalar_counts.values()
            )
            or not isinstance(batch_counts, list)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in batch_counts
            )
        ):
            return False, "Feature 006 counts must be non-negative integers"
        if not _aggregate_reason_counts(payload.get("gap_reason_counts")):
            return False, "gap reason counts must contain aggregate reason codes"
        checksums = payload.get("checksums")
        if (
            not isinstance(checksums, dict)
            or set(checksums) != FEATURE006_RECONCILIATION_CHECKSUM_KEYS
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in checksums.values()
            )
        ):
            return False, "Feature 006 checksums exceed the approved aggregate shape"
        quality = payload.get("quality")
        if (
            not isinstance(quality, dict)
            or set(quality) != FEATURE006_RECONCILIATION_QUALITY_KEYS
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or value < 0
                for value in quality.values()
            )
        ):
            return False, "Feature 006 quality exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 reconciliation aggregate"
    if set(payload) == FEATURE006_FORM_LOOKUP_TOP_LEVEL:
        expected_counts = payload.get("expected_state_counts")
        domains = payload.get("per_domain")
        privacy = payload.get("privacy")
        if (
            not _aggregate_reason_counts(expected_counts)
            or not isinstance(domains, dict)
            or any(
                not isinstance(item, dict)
                or bool(set(item).difference(FEATURE006_FORM_LOOKUP_DOMAIN_KEYS))
                for item in domains.values()
            )
            or privacy
            != {
                "contains_question_text": False,
                "contains_answer_text": False,
                "contains_credentials": False,
            }
        ):
            return False, "Feature 006 form lookup exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 form lookup aggregate"
    if set(payload) == FEATURE006_FIVE_DOMAIN_TOP_LEVEL:
        thresholds = payload.get("thresholds")
        summary = payload.get("summary")
        cases = payload.get("cases")
        if (
            not isinstance(thresholds, dict)
            or set(thresholds) != FEATURE006_FIVE_DOMAIN_THRESHOLD_KEYS
            or not isinstance(summary, dict)
            or set(summary) != FEATURE006_FIVE_DOMAIN_SUMMARY_KEYS
            or not isinstance(cases, list)
            or any(
                not isinstance(item, dict)
                or set(item) != FEATURE006_FIVE_DOMAIN_CASE_KEYS
                or not isinstance(item.get("quality_flags"), list)
                or not isinstance(item.get("critical_errors"), list)
                or not isinstance(item.get("returned_form_codes"), list)
                or not isinstance(item.get("expected_form_codes"), list)
                for item in cases
            )
        ):
            return False, "Feature 006 five-domain report exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 five-domain aggregate"
    # The detailed final report shares the legacy aggregate's top-level shape,
    # but it carries a `newly_verified` section that must receive the stricter
    # validation below.  Do not let the legacy branch accept it first.
    if (
        set(payload) == FEATURE006_FORM_COMPLETION_FINAL_TOP_LEVEL
        and "newly_verified" not in payload
    ):
        blocked = payload.get("blocked_identities")
        if (
            not _aggregate_reason_counts(payload.get("blocked_reason_counts"))
            or not isinstance(blocked, list)
            or any(
                not isinstance(item, dict)
                or set(item) != FEATURE006_FORM_COMPLETION_FINAL_BLOCKED_IDENTITY_KEYS
                or not re.fullmatch(
                    r"[0-9a-f]{24}",
                    str(item.get("requirement_identity_id") or ""),
                )
                or not re.fullmatch(
                    r"[A-Z][A-Z0-9_]*",
                    str(item.get("reason_code") or ""),
                )
                or not isinstance(item.get("procedure_ids"), list)
                or any(not isinstance(value, str) or not value for value in item["procedure_ids"])
                for item in blocked
            )
            or payload.get("contains_question_text") is not False
            or payload.get("contains_answer_text") is not False
            or payload.get("contains_credentials") is not False
        ):
            return False, "Feature 006 final completion report exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 final completion aggregate"
    if set(payload) == FEATURE006_DATA_MANIFEST_TOP_LEVEL:
        artifacts = payload.get("artifacts")
        drift = payload.get("official_index_drift")
        if (
            not isinstance(artifacts, dict)
            or set(artifacts) != FEATURE006_ARTIFACT_CATEGORIES
            or any(
                not isinstance(records, list)
                or any(
                    not isinstance(record, dict)
                    or set(record) != {"artifact", "sha256", "size_bytes"}
                    or Path(str(record.get("artifact") or "")).is_absolute()
                    or re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256") or ""))
                    is None
                    for record in records
                )
                for records in artifacts.values()
            )
            or not isinstance(drift, dict)
            or set(drift) != FEATURE006_OFFICIAL_INDEX_DRIFT_KEYS
            or not isinstance(payload.get("release_blockers"), list)
        ):
            return False, "Feature 006 data manifest exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 data manifest"
    if set(payload) == FEATURE006_UNIFIED_MANIFEST_TOP_LEVEL:
        if (
            not isinstance(payload.get("gates"), dict)
            or set(payload["gates"])
            != {
                "data",
                "technical_release",
                "five_domain_quality",
                "restore",
                "security",
                "soak",
            }
            or not isinstance(payload.get("human_decisions"), dict)
            or set(payload["human_decisions"])
            != {"legal_reviewer", "release_owner"}
        ):
            return False, "Feature 006 unified manifest exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 unified release manifest"
    if set(payload) == FEATURE006_FORM_ROLE_MATRIX_TOP_LEVEL:
        roles = payload.get("roles")
        if (
            not isinstance(roles, dict)
            or set(roles) != {"citizen", "officer", "admin"}
            or any(
                not isinstance(row, dict)
                or set(row) != FEATURE006_FORM_ROLE_KEYS
                for row in roles.values()
            )
            or payload.get("privacy")
            != {
                "contains_question_text": False,
                "contains_answer_text": False,
                "contains_credentials": False,
            }
        ):
            return False, "Feature 006 form-role matrix exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 form-role matrix"
    if set(payload) == FEATURE006_PROBE_VERIFICATION_TOP_LEVEL:
        checks = payload.get("checks")
        probe_checksums = payload.get("probe_checksums")
        if (
            not isinstance(checks, list)
            or any(
                not isinstance(item, dict)
                or set(item) != FEATURE006_PROBE_CHECK_KEYS
                or not isinstance(item.get("reason_codes"), list)
                for item in checks
            )
            or not isinstance(probe_checksums, list)
            or any(
                not isinstance(item, dict)
                or set(item) != {"probe", "code_resolution_sha256"}
                for item in probe_checksums
            )
            or any(
                payload.get(key) is not False
                for key in (
                    "automated_approval",
                    "human_attestation_created",
                    "feature_flag_enabled",
                    "contains_question_text",
                    "contains_answer_text",
                    "contains_credentials",
                )
            )
        ):
            return False, "Feature 006 probe verification exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 probe verification"
    if set(payload) == FEATURE006_PROBE_INTEGRATION_TOP_LEVEL:
        if (
            not isinstance(payload.get("review_batch_identity_counts"), list)
            or payload.get("automated_approval") is not False
            or payload.get("runtime_catalog_mutated") is not False
            or payload.get("feature_flag_enabled") is not False
            or payload.get("human_attestation_required") is not True
        ):
            return False, "Feature 006 probe integration exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 probe campaign integration"
    if set(payload) == FEATURE006_FORM_COMPLETION_FINAL_TOP_LEVEL:
        newly_verified = payload.get("newly_verified")
        if (
            not isinstance(payload.get("counts"), dict)
            or not isinstance(newly_verified, dict)
            or set(newly_verified)
            != {
                "identity_count",
                "candidate_binding_count",
                "requirement_identity_ids",
                "file_format_counts",
                "file_openability_passed",
                "file_openability_failed",
            }
            or not isinstance(
                newly_verified.get("requirement_identity_ids"), list
            )
            or not _aggregate_reason_counts(
                payload.get("blocked_reason_counts")
            )
            or any(
                payload.get(key) is not False
                for key in (
                    "automated_approval",
                    "runtime_catalog_mutated",
                    "feature_flag_enabled",
                    "contains_question_text",
                    "contains_answer_text",
                    "contains_credentials",
                )
            )
        ):
            return False, "Feature 006 final completion report exceeds the approved aggregate shape"
        return True, "privacy-safe Feature 006 final completion report"
    if set(payload) != ALLOWED_TOP_LEVEL:
        return False, "artifact keys are not the approved aggregate shape"
    if _contains_forbidden_key(payload):
        return False, "artifact includes a forbidden content-bearing key"
    if not isinstance(payload.get("latency_ms"), dict) or set(payload["latency_ms"]).difference(ALLOWED_NESTED_KEYS):
        return False, "latency summary has unsupported fields"
    if not isinstance(payload.get("stage_latency_ms"), dict):
        return False, "stage latency must be a summary object"
    return True, "privacy-safe aggregate artifact"


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Feature 005 benchmark artifact for privacy violations.")
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(args.artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("REJECTED: artifact is not readable JSON")
        return 1
    valid, reason = validate_artifact(payload)
    print(("PASS" if valid else "REJECTED") + ": " + reason)
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
