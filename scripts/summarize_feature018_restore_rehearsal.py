"""Summarize real four-layer restore evidence for the Feature 018 release."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping


def summarize(
    *,
    release_fingerprint: str,
    copy_report: Mapping[str, object],
    retrieval_manifest: Mapping[str, object],
    postgres_report: Mapping[str, object],
    vector_report: Mapping[str, object],
    surreal_report: Mapping[str, object],
    surreal_live_drift_report: Mapping[str, object] | None = None,
) -> dict[str, object]:
    postgres_manifest = retrieval_manifest.get("postgres") or {}
    fingerprints = [
        copy_report.get("release_fingerprint"),
        postgres_report.get("release_fingerprint"),
        vector_report.get("release_fingerprint"),
    ]
    checks = {
        "release_fingerprint_reconciled": all(
            value == release_fingerprint for value in fingerprints
        ),
        "four_layer_inventory_match": bool(
            copy_report.get("passed")
            and copy_report.get("inventory_match")
            and set(copy_report.get("components") or [])
            == {"postgres", "objects", "vectors", "surreal"}
        ),
        "postgres_restore_passed": postgres_report.get("status") == "PASS",
        "postgres_dump_checksum_match": (
            postgres_report.get("dump_sha256")
            == postgres_manifest.get("sha256")
        ),
        "vector_restore_passed": vector_report.get("status") == "PASS",
        "surreal_snapshot_restore_passed": surreal_report.get("status") == "PASS",
        "surreal_schema_match": bool(
            (surreal_report.get("comparison") or {}).get("schema_match")
        ),
        "surreal_raw_copy_match": bool(
            (surreal_report.get("raw_copy") or {}).get("match")
        ),
    }
    passed = all(checks.values())
    live_mismatches = (
        ((surreal_live_drift_report or {}).get("comparison") or {}).get(
            "count_mismatches"
        )
        or {}
    )
    durations = [
        float(report.get("duration_seconds") or 0)
        for report in (copy_report, postgres_report, vector_report, surreal_report)
    ]
    return {
        "schema_version": "feature018-real-restore-rehearsal-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "release_id": "feature018-candidate-20260813",
        "release_fingerprint": release_fingerprint,
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "production_activation_authorized": False,
        "backup": {
            "status": "PASS" if checks["four_layer_inventory_match"] else "FAIL",
            "component_count": 4,
            "file_count": copy_report.get("file_count"),
            "inventory_sha256": copy_report.get("backup_inventory_sha256"),
            "postgres_dump_sha256": postgres_manifest.get("sha256"),
            "vector_collections": (
                retrieval_manifest.get("chroma") or {}
            ).get("collections"),
        },
        "restore": {
            "status": "PASS" if passed else "FAIL",
            "inventory_sha256": copy_report.get("restored_inventory_sha256"),
            "postgres_table_count": postgres_report.get("restored_table_count"),
            "postgres_total_rows": postgres_report.get("restored_total_rows"),
            "vector_collections": vector_report.get("restored_collection_counts"),
            "surreal_table_count": (
                surreal_report.get("restored") or {}
            ).get("inventory", {}).get("table_count"),
            "surreal_total_records": (
                surreal_report.get("restored") or {}
            ).get("inventory", {}).get("total_records"),
        },
        "checks": checks,
        "rto_evidence_seconds": round(sum(durations), 3),
        "rpo": {
            "live_source_drift_disclosed": bool(live_mismatches),
            "live_source_count_mismatches": live_mismatches,
            "snapshot_point_in_time_match": checks["surreal_snapshot_restore_passed"],
        },
    }


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--copy-report", type=Path, required=True)
    parser.add_argument("--retrieval-manifest", type=Path, required=True)
    parser.add_argument("--postgres-report", type=Path, required=True)
    parser.add_argument("--vector-report", type=Path, required=True)
    parser.add_argument("--surreal-report", type=Path, required=True)
    parser.add_argument("--surreal-live-drift-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(
        release_fingerprint=hashlib.sha256(
            args.release_manifest.read_bytes()
        ).hexdigest(),
        copy_report=_load(args.copy_report),
        retrieval_manifest=_load(args.retrieval_manifest),
        postgres_report=_load(args.postgres_report),
        vector_report=_load(args.vector_report),
        surreal_report=_load(args.surreal_report),
        surreal_live_drift_report=(
            _load(args.surreal_live_drift_report)
            if args.surreal_live_drift_report
            else None
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": report["checks"],
                "rto_evidence_seconds": report["rto_evidence_seconds"],
                "live_drift_disclosed": report["rpo"][
                    "live_source_drift_disclosed"
                ],
            }
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
