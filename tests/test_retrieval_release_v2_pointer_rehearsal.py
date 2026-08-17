from __future__ import annotations

import json
from pathlib import Path

from scripts.rehearse_retrieval_release_v2_pointer import rehearse


def _candidate(path: Path) -> Path:
    payload = {
        "schema_version": "legal-serving-manifest-v3",
        "kind": "release_pointer",
        "release_id": "release-v2",
        "activation_performed": False,
        "active_pointer_changed": False,
        "current_collection": {"path": "chroma://v2-current"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_pointer_switch_and_rollback_are_verified_in_sandbox(tmp_path: Path):
    pointer = tmp_path / "active_core_collection.txt"
    pointer.write_text("baseline", encoding="utf-8")
    result = rehearse(
        active_pointer=pointer,
        candidate_manifest=_candidate(tmp_path / "candidate.json"),
        output=tmp_path / "rehearsal.json",
    )
    assert result["status"] == "PASS"
    assert result["sandbox_switch_verified"] is True
    assert result["sandbox_rollback_verified"] is True
    assert result["live_pointer_unchanged"] is True
    assert pointer.read_text(encoding="utf-8") == "baseline"
