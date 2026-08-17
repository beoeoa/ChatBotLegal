"""Freeze an explicitly user-approved Feature 017 Golden V3 dataset.

This command creates immutable approval artifacts beside the proposed dataset.
It never mutates a legal corpus, release pointer, PostgreSQL, SurrealDB, or a
vector index.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_feature017_golden_v3 import CATEGORIES, DOMAINS, canonical_hash


DATASET_SCHEMA = "feature017-golden-v3-dataset-v1"
APPROVAL_SCHEMA = "feature017-golden-v3-approval-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _truth_projection(case: Mapping[str, Any]) -> dict[str, Any]:
    """Return all case fields except the mutable review marker."""

    return {key: copy.deepcopy(value) for key, value in case.items() if key != "review_status"}


def approved_checksum(dataset: Mapping[str, Any]) -> str:
    """Checksum the exact approved case payload, excluding timestamp/receipt metadata."""

    return canonical_hash(
        {
            "schema_version": DATASET_SCHEMA,
            "review_status": "approved",
            "cases": dataset.get("cases") or [],
        }
    )


def approve_dataset(
    proposed: Mapping[str, Any],
    release_manifest: Mapping[str, Any],
    *,
    approved_by: str,
    approved_at: str,
    approval_statement: str,
) -> dict[str, Any]:
    if proposed.get("schema_version") != DATASET_SCHEMA:
        raise ValueError("FEATURE017_GOLDEN_DATASET_INVALID")
    if proposed.get("review_status") != "proposed":
        raise ValueError("FEATURE017_GOLDEN_NOT_PROPOSED")
    if proposed.get("approved_checksum") is not None:
        raise ValueError("FEATURE017_GOLDEN_ALREADY_FROZEN")
    if not str(approved_by).strip() or not str(approval_statement).strip():
        raise ValueError("FEATURE017_GOLDEN_APPROVAL_IDENTITY_REQUIRED")

    cases = list(proposed.get("cases") or [])
    if len(cases) != 1000:
        raise ValueError("FEATURE017_GOLDEN_CASE_COUNT_INVALID")
    if any(item.get("review_status") != "proposed" for item in cases):
        raise ValueError("FEATURE017_GOLDEN_CASE_STATUS_INVALID")
    if Counter(item.get("domain") for item in cases) != Counter({domain: 200 for domain in DOMAINS}):
        raise ValueError("FEATURE017_GOLDEN_DOMAIN_DISTRIBUTION_INVALID")
    if Counter(item.get("category") for item in cases) != Counter(dict(CATEGORIES)):
        raise ValueError("FEATURE017_GOLDEN_CATEGORY_DISTRIBUTION_INVALID")

    release_hash = canonical_hash(release_manifest)
    if any(item.get("release_manifest_sha256") != release_hash for item in cases):
        raise ValueError("FEATURE017_GOLDEN_RELEASE_MISMATCH")

    approved_cases = copy.deepcopy(cases)
    for item in approved_cases:
        item["review_status"] = "approved"

    before_truth = [_truth_projection(item) for item in cases]
    after_truth = [_truth_projection(item) for item in approved_cases]
    if before_truth != after_truth:
        raise AssertionError("FEATURE017_GOLDEN_TRUTH_MUTATED")

    approved = {
        "schema_version": DATASET_SCHEMA,
        "review_status": "approved",
        "approved_checksum": None,
        "approved_at": approved_at,
        "approval": {
            "schema_version": APPROVAL_SCHEMA,
            "decision": "approve_all",
            "actor": str(approved_by).strip(),
            "channel": "codex_task",
            "case_count": 1000,
            "statement_sha256": hashlib.sha256(
                approval_statement.strip().encode("utf-8")
            ).hexdigest(),
        },
        "release_id": release_manifest.get("release_id"),
        "release_manifest_sha256": release_hash,
        "cases": approved_cases,
    }
    approved["approved_checksum"] = approved_checksum(approved)
    return approved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--approval-statement", required=True)
    parser.add_argument(
        "--confirm-all-1000",
        action="store_true",
        help="Required guard confirming the user approved every case.",
    )
    args = parser.parse_args()
    if not args.confirm_all_1000:
        raise SystemExit("approval refused: --confirm-all-1000 is required")

    proposed = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
    release_manifest = json.loads(
        args.release_manifest.read_text(encoding="utf-8-sig")
    )
    approved_at = datetime.now(timezone.utc).isoformat()
    approved = approve_dataset(
        proposed,
        release_manifest,
        approved_by=args.approved_by,
        approved_at=approved_at,
        approval_statement=args.approval_statement,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    approved_path = args.output_dir / "golden-v3-1000-approved.json"
    receipt_path = args.output_dir / "approval-receipt.json"
    manifest_path = args.output_dir / "approved-manifest.json"
    _atomic_json(approved_path, approved)

    receipt_core = {
        "schema_version": APPROVAL_SCHEMA,
        "status": "approved",
        "approved_at": approved_at,
        "approved_by": args.approved_by,
        "case_count": 1000,
        "approved_checksum": approved["approved_checksum"],
        "release_manifest_sha256": approved["release_manifest_sha256"],
        "source_proposed_sha256": _sha256(args.dataset),
        "approved_dataset_sha256": _sha256(approved_path),
        "production_database_mutated": False,
        "production_vectors_mutated": False,
        "public_release_activated": False,
    }
    receipt_core["entry_hash"] = canonical_hash(receipt_core)
    _atomic_json(receipt_path, receipt_core)

    manifest = {
        "schema_version": "feature017-golden-v3-approved-manifest-v1",
        "status": "approved",
        "case_count": 1000,
        "approved_at": approved_at,
        "approved_checksum": approved["approved_checksum"],
        "release_id": approved["release_id"],
        "release_manifest_sha256": approved["release_manifest_sha256"],
        "source_proposed_sha256": _sha256(args.dataset),
        "artifacts": {
            "approved_dataset": {
                "path": approved_path.name,
                "sha256": _sha256(approved_path),
            },
            "approval_receipt": {
                "path": receipt_path.name,
                "sha256": _sha256(receipt_path),
            },
        },
        "automation_promoted": False,
        "public_release_activated": False,
    }
    _atomic_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "status": "approved",
                "cases": 1000,
                "approved_checksum": approved["approved_checksum"],
                "approved_dataset": str(approved_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
