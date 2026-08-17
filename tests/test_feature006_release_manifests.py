from __future__ import annotations

from pathlib import Path

from scripts.build_feature006_release_manifests import (
    artifact_record,
    build_unified_manifest,
    verify_artifact_records,
)


def test_artifact_records_detect_checksum_drift(tmp_path: Path) -> None:
    artifact = tmp_path / "catalog.json"
    artifact.write_text('{"forms":[]}\n', encoding="utf-8")
    categories = {
        "forms": [artifact_record(artifact, root=tmp_path)],
    }

    assert verify_artifact_records(categories, root=tmp_path) == []

    artifact.write_text('{"forms":[{"id":"changed"}]}\n', encoding="utf-8")
    failures = verify_artifact_records(categories, root=tmp_path)

    assert failures == ["CHECKSUM_MISMATCH:catalog.json"]


def test_unified_manifest_stays_at_stage_zero_without_two_human_go_decisions() -> None:
    manifest = build_unified_manifest(
        data_manifest_sha256="a" * 64,
        data_status="BLOCKED_DATA",
        release_gate_status="BLOCKED_RELEASE",
        five_domain_status="PASS",
        restore_status="PASS",
        feature_flag_enabled=False,
        legal_reviewer_decision="PENDING",
        release_owner_decision="PENDING",
    )

    assert manifest["release_verdict"] == "NO_GO"
    assert manifest["rollout_stage"] == 0
    assert manifest["feature_flag_enabled"] is False
