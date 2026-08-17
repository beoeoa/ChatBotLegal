from __future__ import annotations

from scripts.audit_feature017_completion import _canonical_hash, build_completion_audit


def test_completion_audit_does_not_accept_preview_as_attestation() -> None:
    candidate = {
        "schema_version": "feature017-legal-enrichment-candidate-v1",
        "scope_complete": True,
        "source_approved_identity_count": 60,
        "deferred_identity_count": 40,
        "attestation_preview_ready_count": 60,
        "runtime_catalog_mutated": False,
        "manifest_sha256": "a" * 64,
    }
    preview = {
        "schema_version": "feature017-batch-attestation-preview-v1",
        "attestation_status": "awaiting_explicit_fingerprint_confirmation",
        "candidate_manifest_sha256": "a" * 64,
        "attestation_fingerprint": "b" * 64,
        "asset_count": 60,
        "runtime_catalog_mutated": False,
    }
    result = build_completion_audit(
        candidate=candidate,
        preview=preview,
        attestation=None,
        release_delta=None,
        full_release=None,
        golden=None,
    )

    assert result["complete"] is False
    assert result["checks"][0]["passed"] is True
    assert result["checks"][1]["passed"] is True
    assert "explicit_admin_attestation" in result["incomplete_requirements"]
    assert "complete_validated_191_131_229_release" in result[
        "incomplete_requirements"
    ]
    assert result["safe_to_activate_public"] is False


def test_completion_audit_rejects_incomplete_release_coverage() -> None:
    result = build_completion_audit(
        candidate=None,
        preview=None,
        attestation=None,
        release_delta=None,
        full_release={
            "schema_version": "form-release-v1",
            "coverage": {
                "complete": False,
                "procedure_total": 191,
                "procedure_decided": 151,
            },
            "gate_report": {"passed": True},
        },
        golden=None,
    )
    release_check = next(
        item
        for item in result["checks"]
        if item["requirement"] == "complete_validated_191_131_229_release"
    )
    assert release_check["passed"] is False
    assert result["complete"] is False


def test_completion_audit_accepts_frozen_user_approved_golden_checksum() -> None:
    release = {
        "schema_version": "form-release-v1",
        "release_id": "r1",
        "coverage": {
            "complete": True,
            "procedure_total": 191,
            "procedure_decided": 191,
            "identity_total": 131,
            "identity_decided": 131,
            "binding_total": 229,
            "binding_decided": 229,
        },
        "gate_report": {"passed": True},
    }
    release_hash = _canonical_hash(release)
    cases = [
        {
            "case_id": f"f017_case_{index:04d}",
            "release_manifest_sha256": release_hash,
            "review_status": "approved",
        }
        for index in range(1000)
    ]
    golden = {
        "schema_version": "feature017-golden-v3-dataset-v1",
        "review_status": "approved",
        "approved_checksum": _canonical_hash(
            {
                "schema_version": "feature017-golden-v3-dataset-v1",
                "review_status": "approved",
                "cases": cases,
            }
        ),
        "cases": cases,
    }

    result = build_completion_audit(
        candidate=None,
        preview=None,
        attestation=None,
        release_delta=None,
        full_release=release,
        golden=golden,
    )

    approved_check = next(
        item
        for item in result["checks"]
        if item["requirement"] == "golden_v3_user_approved_checksum_frozen"
    )
    assert approved_check["passed"] is True
