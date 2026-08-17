#!/usr/bin/env python3
"""Build a checksum-bound quality report for a V2 chunk-release draft.

This report deliberately does not replace the read-only legacy-table quality
assessment.  It evaluates the actual additive V2 release artifacts: the
12,236-document inventory, the streamed chunk builder report and the structural
manifest verification.  Legal approval remains a separate release gate.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256, validate_inventory_partition


DEFAULT_DIR = ROOT / "reports" / "retrieval-release-v2"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def build(*, inventory_path: Path, build_report_path: Path, verification_path: Path, output: Path) -> dict[str, Any]:
    inventory = _read(inventory_path)
    build_report = _read(build_report_path)
    verification = _read(verification_path)
    documents = inventory.get("documents") or []
    inventory_count = int(inventory.get("source_snapshot_document_count") or 0)
    partition_errors = validate_inventory_partition(documents)
    counts = build_report.get("counts") or {}
    stats = build_report.get("stats") or {}
    quality_checks = {
        "inventory_exact_12236": inventory_count == 12_236 and len(documents) == 12_236,
        "inventory_partition_exact": not partition_errors,
        "structural_manifest_verified": verification.get("structural_gate_passed") is True,
        "chunk_count_matches_vector_count": int(counts.get("chunk_count") or 0) == int(counts.get("vector_count") or 0),
        "chunk_count_matches_verification": int(counts.get("chunk_count") or 0) == int(verification.get("chunk_count") or 0),
        "no_empty_eligible_chunks": int(stats.get("empty_chunk") or 0) == 0,
        "no_oversized_or_invalid_chunks": int(stats.get("oversized_or_invalid_chunk") or 0) == 0,
        "no_orphan_or_unverified_chunks": int(verification.get("invalid_chunk_count") or 0) == 0,
        "current_and_historical_documents_have_chunks": (
            int(counts.get("included_document_count") or 0)
            >= int(counts.get("current_retrievable_document_count") or 0)
            + int(counts.get("historical_only_document_count") or 0)
        ),
        "database_unchanged": build_report.get("database_mutated") is False,
        "active_pointer_unchanged": build_report.get("active_pointer_changed") is False,
    }
    quality_gate_passed = all(quality_checks.values())
    report = {
        "schema_version": "legal-quality-policy-v2-release-report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_id": str(build_report.get("release_id") or ""),
        "inventory": {
            "path": str(inventory_path.resolve()),
            "sha256": file_sha256(inventory_path),
            "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
            "document_count": inventory_count,
            "state_counts": inventory.get("inventory_counts") or {},
        },
        "chunk_release": {
            "build_report_path": str(build_report_path.resolve()),
            "build_report_sha256": file_sha256(build_report_path),
            "verification_path": str(verification_path.resolve()),
            "verification_sha256": file_sha256(verification_path),
            "counts": counts,
            "stats": stats,
            "max_tokens": 512,
            "overlap_tokens": 64,
            "quality_policy_version": "legal-chunk-quality-v2",
        },
        "quality_checks": quality_checks,
        "quality_gate_passed": quality_gate_passed,
        "legal_release_approved": False,
        "release_gate_passed": False,
        "approval_blocker": "legal_metadata_and_official_source_attestation_required",
        "partition_errors": partition_errors[:100],
        "mutation": {
            "database_mutated": False,
            "vector_collections_mutated": False,
            "active_pointer_changed": False,
        },
    }
    report["report_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_DIR / "source-inventory-reconciliation.json")
    parser.add_argument("--build-report", type=Path, default=DEFAULT_DIR / "legal-retrieval-chunk-manifest-v2-draft-passage-v3.report.json")
    parser.add_argument("--verification", type=Path, default=DEFAULT_DIR / "legal-retrieval-chunk-manifest-v2-verification-passage-v3.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_DIR / "quality-policy-v2-release-passage-v3.json")
    args = parser.parse_args(argv)
    report = build(
        inventory_path=args.inventory.resolve(),
        build_report_path=args.build_report.resolve(),
        verification_path=args.verification.resolve(),
        output=args.output.resolve(),
    )
    print(json.dumps({
        "status": "PASS" if report["quality_gate_passed"] else "BLOCKED",
        "quality_gate_passed": report["quality_gate_passed"],
        "release_gate_passed": report["release_gate_passed"],
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0 if report["quality_gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
