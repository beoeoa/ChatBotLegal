from __future__ import annotations

import json
from pathlib import Path

from scripts.build_feature017_full_release import build_full_release


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "data" / "source_cache" / "feature017_approved_sources_20260811"


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _build():
    return build_full_release(
        baseline=_read(
            ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-30.json"
        ),
        catalog=_read(ROOT / "notebook_data" / "forms" / "canonical_forms_catalog_v1.json"),
        scoped_candidate=_read(SOURCE_DIR / "scoped-legal-enrichment-candidate.json"),
        attestation=_read(SOURCE_DIR / "scoped-attestation.json"),
        supplement_scope=_read(SOURCE_DIR / "supplement-scope-decision.json"),
        package_scope=_read(SOURCE_DIR / "instrument-package-scope-decision.json"),
        source_snapshot_sha256="a" * 64,
    )


def test_full_release_has_exact_scope_and_passes_gate() -> None:
    result = _build()
    manifest = result["manifest"]
    coverage = manifest["coverage"]

    assert result["gate_report"]["passed"] is True
    assert coverage == {
        "procedure_total": 191,
        "procedure_decided": 191,
        "identity_total": 131,
        "identity_decided": 131,
        "binding_total": 229,
        "binding_decided": 229,
        "complete": True,
    }
    assert len(manifest["assets"]) == 91
    assert len(manifest["gaps"]) == 40
    assert result["activation_allowed"] is False
    assert result["runtime_catalog_mutated"] is False
    assert result["active_pointer_changed"] is False


def test_deferred_identity_never_leaks_as_asset_or_partial_procedure() -> None:
    manifest = _build()["manifest"]
    excluded_identity_ids = {
        item["target_id"]
        for item in manifest["exclusions"]
        if item["target_type"] == "identity"
    }
    asset_identity_ids = {item["identity_id"] for item in manifest["assets"]}
    deferred_procedure_ids = {
        item["procedure_id"]
        for item in manifest["procedures"]
        if item["coverage_status"] == "owner_deferred"
    }

    assert len(excluded_identity_ids) == 40
    assert excluded_identity_ids.isdisjoint(asset_identity_ids)
    assert deferred_procedure_ids
    assert not any(
        binding["procedure_id"] in deferred_procedure_ids
        for binding in manifest["bindings"]
    )
    assert not any(
        gap["target_id"] in deferred_procedure_ids
        for gap in manifest["gaps"]
    )


def test_all_exclusions_are_audited_and_fail_closed() -> None:
    manifest = _build()["manifest"]
    for exclusion in manifest["exclusions"]:
        assert exclusion["reason_code"] in {
            "USER_EXCLUDED_SUPPLEMENT_FROM_CURRENT_RELEASE",
            "USER_EXCLUDED_PACKAGE_REVIEW_FROM_CURRENT_RELEASE",
        }
        assert len(exclusion["decision_fingerprint"]) == 64
        assert exclusion["decided_by"] == "admin"
        assert exclusion["decided_at"]
        assert exclusion["public_eligible"] is False
        assert exclusion["router_eligible"] is False
