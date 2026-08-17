from __future__ import annotations

import hashlib
from pathlib import Path

import scripts.build_feature017_scoped_candidate as module
from scripts.build_feature017_scoped_candidate import (
    build_attestation_preview,
    build_package_deferred_manifest,
    build_release_delta,
    build_scoped_candidate,
    confirm_scoped_attestation,
)


def test_package_deferral_is_fail_closed() -> None:
    result = build_package_deferred_manifest(
        {
            "legal_as_of": "2026-08-11",
            "records": [
                {
                    "identity_id": "package-1",
                    "canonical_name": "Mẫu 01",
                    "procedure_ids": ["P1"],
                    "reason_code": "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW",
                }
            ],
        },
        decision_note="Bỏ qua đợt này",
    )
    record = result["records"][0]
    assert record["public_eligible"] is False
    assert record["router_eligible"] is False
    assert record["verified_gap"] is False
    assert result["runtime_catalog_mutated"] is False


def test_scoped_candidate_contains_only_source_ready_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(module, "ROOT", tmp_path)
    artifact = tmp_path / "staging" / "form.pdf"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"official form")
    checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
    collection = {
        "legal_as_of": "2026-08-11",
        "records": [
            {
                "identity_id": "ready-1",
                "decision": "source_approved",
                "status": "READY_FOR_LEGAL_ENRICHMENT",
                "asset_kind": "file",
                "form_code": "01",
                "issuing_instrument": "1/2026/NĐ-CP",
                "canonical_name": "Mẫu 01",
                "domains": ["cu_tru_an_ninh"],
                "procedure_ids": ["P1"],
                "official_source_url": "https://dichvucong.gov.vn/P1",
                "source_checksum": checksum,
                "canonical_artifact": {
                    "asset_kind": "file",
                    "sha256": checksum,
                    "staging_path": "staging/form.pdf",
                    "referer_url": "https://dichvucong.gov.vn/P1",
                },
            },
            {
                "identity_id": "blocked-1",
                "status": "OFFICIAL_INSTRUMENT_PACKAGE_REQUIRED",
            },
            {
                "identity_id": "supplement-1",
                "status": "NEEDS_SUPPLEMENT",
            },
        ],
    }
    supplement_scope = {
        "records": [
            {
                "identity_id": "supplement-1",
                "scope_status": "OWNER_DEFERRED_MISSING_EVIDENCE",
            }
        ]
    }
    package_scope = {
        "records": [
            {
                "identity_id": "blocked-1",
                "scope_status": "OWNER_DEFERRED_PACKAGE_REVIEW",
            }
        ]
    }
    baseline = {
        "procedures": [
            {
                "procedure_id": "P1",
                "procedure_name": "Thủ tục 1",
                "domain": "cu_tru_an_ninh",
                "executing_level": "commune",
                "official_source_page": "https://dichvucong.gov.vn/P1",
                "required_form_identity_ids": ["ready-1"],
            }
        ]
    }

    result = build_scoped_candidate(
        collection,
        {
            "records": [
                {
                    "identity_id": "ready-1",
                    "components": [
                        {
                            "procedure_id": "P1",
                            "component_id": "component-1",
                            "required": True,
                            "original_quantity": 1,
                            "copy_quantity": 0,
                        }
                    ],
                }
            ]
        },
        supplement_scope,
        package_scope,
        baseline,
    )

    assert result["source_approved_identity_count"] == 1
    assert result["deferred_identity_count"] == 2
    assert {item["identity_id"] for item in result["assets"]} == {"ready-1"}
    assert all(item["runtime_eligible"] is False for item in result["assets"])
    assert result["attestation_ready_count"] == 0
    assert result["bindings"][0]["requirement"] == "required"
    assert result["binding_review_required_count"] == 0
    assert result["attestation_preview_ready_count"] == 1
    assert result["assets"][0]["effectivity_evidence"]["evidence_basis"] == (
        "official_current_dvc_attachment"
    )

    preview = build_attestation_preview(result)
    assert preview["asset_count"] == 1
    assert preview["binding_count"] == 1
    assert preview["attestation_status"] == (
        "awaiting_explicit_fingerprint_confirmation"
    )
    assert preview["attested_by"] is None
    assert preview["release_manifest_created"] is False
    assert preview["runtime_catalog_mutated"] is False
    assert result["release_manifest_created"] is False
    assert result["runtime_catalog_mutated"] is False
    assert result["active_pointer_changed"] is False


def test_attestation_preview_rejects_unreviewed_binding() -> None:
    candidate = {
        "scope_complete": True,
        "source_approved_identity_count": 1,
        "assets": [
            {
                "form_id": "form-1",
                "attestation_preview_ready": True,
                "runtime_eligible": False,
            }
        ],
        "procedures": [{"procedure_id": "P1"}],
        "bindings": [
            {
                "procedure_id": "P1",
                "form_id": "form-1",
                "requirement": "conditional_review_required",
                "attestation_ready": False,
            }
        ],
        "deferred": [],
    }
    try:
        build_attestation_preview(candidate)
    except ValueError as exc:
        assert str(exc) == "FEATURE017_ATTESTATION_BINDING_NOT_READY"
    else:
        raise AssertionError("unreviewed binding must fail closed")


def test_attestation_requires_exact_fingerprint() -> None:
    candidate = {
        "scope_complete": True,
        "candidate_id": "candidate-1",
        "manifest_sha256": "a" * 64,
        "legal_as_of": "2026-08-11",
        "source_approved_identity_count": 1,
        "assets": [
            {
                "form_id": "form-1",
                "attestation_preview_ready": True,
                "runtime_eligible": False,
            }
        ],
        "procedures": [{"procedure_id": "P1"}],
        "bindings": [
            {
                "procedure_id": "P1",
                "form_id": "form-1",
                "requirement": "required",
                "attestation_ready": True,
            }
        ],
        "deferred": [],
    }
    preview = build_attestation_preview(candidate)
    try:
        confirm_scoped_attestation(
            candidate,
            preview,
            confirmed_fingerprint="wrong",
            attested_by="admin",
        )
    except ValueError as exc:
        assert str(exc) == "FEATURE017_ATTESTATION_FINGERPRINT_MISMATCH"
    else:
        raise AssertionError("wrong fingerprint must fail closed")

    attestation = confirm_scoped_attestation(
        candidate,
        preview,
        confirmed_fingerprint=preview["attestation_fingerprint"],
        attested_by="admin",
    )
    assert attestation["attestation_status"] == "attested"
    assert attestation["attested_by"] == "admin"
    assert attestation["release_manifest_created"] is False
    assert attestation["active_pointer_changed"] is False

    delta = build_release_delta(candidate, attestation)
    assert delta["schema_version"] == "feature017-release-delta-v1"
    assert delta["previous_active_release_required"] is True
    assert delta["full_release_gate_required"] is True
    assert delta["activation_allowed"] is False
    assert delta["runtime_catalog_mutated"] is False
