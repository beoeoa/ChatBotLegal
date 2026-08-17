#!/usr/bin/env python3
"""Persist a validated Feature 017 release in an explicitly isolated database.

This rehearsal never activates a release pointer and refuses protected/live
database names through the shared Feature 017 isolation guard.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_governance_models import canonical_sha256  # noqa: E402
from api.form_governance_release_validator import validate_release_manifest  # noqa: E402
from api.form_governance_repository import PostgresFormGovernanceRepository  # noqa: E402
from scripts.manage_feature017_form_schema import assert_isolated_database  # noqa: E402


DEFAULT_CANDIDATE = (
    ROOT
    / "outputs"
    / "feature017-full-release-candidate-20260812-packaged"
    / "release-candidate.json"
)
DEFAULT_REPORT = ROOT / "reports" / "feature017" / "postgres-full-release-rehearsal.json"


def validate_candidate(payload: Mapping[str, Any]) -> dict[str, Any]:
    manifest = dict(payload.get("manifest") or {})
    expected_hash = str(payload.get("manifest_sha256") or "")
    if payload.get("activation_allowed") is not False:
        raise ValueError("FEATURE017_REHEARSAL_ACTIVATION_MUST_BE_FALSE")
    if expected_hash != canonical_sha256(manifest):
        raise ValueError("FEATURE017_REHEARSAL_MANIFEST_HASH_MISMATCH")
    gate = validate_release_manifest(
        manifest,
        expected_manifest_sha256=expected_hash,
    )
    if gate.get("passed") is not True:
        raise ValueError("FEATURE017_REHEARSAL_RELEASE_GATE_FAILED")
    return {"manifest": manifest, "manifest_sha256": expected_hash, "gate_report": gate}


def rehearse(candidate: Mapping[str, Any]) -> dict[str, Any]:
    database_url = assert_isolated_database(confirmed=True)
    validated = validate_candidate(candidate)
    manifest = validated["manifest"]
    repository = PostgresFormGovernanceRepository(database_url)
    release_id = str(manifest["release_id"])
    try:
        if repository.active_release() is not None:
            raise RuntimeError("FEATURE017_REHEARSAL_ACTIVE_POINTER_NOT_EMPTY")
        if repository.get_release(release_id) is not None:
            raise RuntimeError("FEATURE017_REHEARSAL_RELEASE_ALREADY_EXISTS")
        stored = repository.save_release(
            {
                "release_id": release_id,
                "version": int(manifest.get("version") or 1),
                "legal_as_of": manifest["legal_as_of"],
                "status": "candidate",
                "source_snapshot_sha256": manifest["source_snapshot_sha256"],
                "manifest": manifest,
                "manifest_sha256": validated["manifest_sha256"],
                "previous_release_id": manifest.get("previous_release_id"),
                "created_by": "admin",
            }
        )
        stored["status"] = "validated"
        stored["gate_report"] = validated["gate_report"]
        repository.update_release(stored)
        persisted = repository.get_release(release_id)
        with repository.engine.connect() as connection:
            counts = {
                table: int(
                    connection.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar_one()
                )
                for table in (
                    "form_release",
                    "form_active_release",
                    "legal_procedure",
                    "legal_form_asset",
                    "procedure_form_binding",
                    "procedure_question_alias",
                )
            }
        result = {
            "schema_version": "feature017-full-release-postgres-rehearsal-v1",
            "status": "passed",
            "database": "isolated_rehearsal",
            "live_apply": False,
            "release_id": release_id,
            "release_status": (persisted or {}).get("status"),
            "manifest_sha256": validated["manifest_sha256"],
            "gate_passed": (persisted or {}).get("gate_report", {}).get("passed") is True,
            "coverage": manifest["coverage"],
            "counts": counts,
            "active_pointer_changed": False,
            "runtime_catalog_materialized": False,
            "public_activation_allowed": False,
        }
        if not all(
            (
                result["release_status"] == "validated",
                result["gate_passed"],
                counts["form_release"] == 1,
                counts["form_active_release"] == 0,
                counts["legal_procedure"] == 0,
                counts["legal_form_asset"] == 0,
                counts["procedure_form_binding"] == 0,
                counts["procedure_question_alias"] == 0,
            )
        ):
            raise RuntimeError(f"FEATURE017_FULL_RELEASE_REHEARSAL_FAILED:{result}")
        return result
    finally:
        repository.engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--confirm-isolated", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_isolated:
        raise RuntimeError("isolated_database_confirmation_required")
    candidate = json.loads(args.candidate.read_text(encoding="utf-8-sig"))
    result = rehearse(candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
