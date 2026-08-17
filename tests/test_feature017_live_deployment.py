from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.deploy_feature017_live import (
    EXPECTED_GOLDEN_CHECKSUM,
    EXPECTED_RELEASE_HASH,
    assert_live_target,
    schema_state,
    validate_approved_artifacts,
    verify_backup,
)


def test_live_target_requires_exact_database_and_explicit_confirmation() -> None:
    url = "postgresql://user:secret@127.0.0.1:5432/legal_chatbot"
    assert assert_live_target(url, confirmed_database="legal_chatbot") == url
    with pytest.raises(RuntimeError, match="LIVE_DATABASE_CONFIRMATION_MISMATCH"):
        assert_live_target(url, confirmed_database="other")
    with pytest.raises(RuntimeError, match="LIVE_DATABASE_TARGET_INVALID"):
        assert_live_target(
            "postgresql://user:secret@127.0.0.1:5432/rehearsal",
            confirmed_database="rehearsal",
        )


def test_backup_verification_detects_missing_or_tampered_dump(tmp_path: Path) -> None:
    dump = tmp_path / "postgres-legal-chatbot.dump"
    dump.write_bytes(b"safe-backup")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "feature017-live-backup-v1",
                "postgres_dump": dump.name,
                "postgres_sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
                "feature017_tables_before": 0,
            }
        ),
        encoding="utf-8",
    )
    assert verify_backup(manifest)["postgres_sha256"]
    dump.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="LIVE_BACKUP_CHECKSUM_MISMATCH"):
        verify_backup(manifest)


def test_artifact_validation_pins_release_and_approved_golden(tmp_path: Path) -> None:
    release = {
        "manifest": {"release_id": "r1"},
        "manifest_sha256": EXPECTED_RELEASE_HASH,
        "gate_report": {"passed": True},
        "activation_allowed": False,
    }
    approval = {
        "status": "approved",
        "case_count": 1000,
        "approved_checksum": EXPECTED_GOLDEN_CHECKSUM,
        "release_id": "r1",
        "release_manifest_sha256": EXPECTED_RELEASE_HASH,
    }
    # The canonical hash check is intentionally strict and therefore catches
    # this synthetic manifest before any database operation.
    with pytest.raises(RuntimeError, match="LIVE_RELEASE_HASH_MISMATCH"):
        validate_approved_artifacts(release, approval)


def test_schema_state_refuses_partial_feature_schema() -> None:
    assert schema_state(set()) == "absent"
    with pytest.raises(RuntimeError, match="FEATURE017_PARTIAL_SCHEMA"):
        schema_state({"form_release"})

