#!/usr/bin/env python3
"""Prepare a reviewer-fillable V2 attestation template.

The output contains no inferred official URLs, checksums or legal decisions.
It is a work queue/template only; the approval command rejects it until every
field is completed and a reviewer signs the exact release snapshot.
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

from api.retrieval_release_contracts import canonical_sha256, file_sha256


DEFAULT_MANIFEST = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-chunk-manifest-v2-draft-passage-v4.json"
DEFAULT_INVENTORY = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation-v4.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "legal-review-attestation-v2-template-v4.json"
DEFAULT_OBSERVATIONS = ROOT / "reports" / "retrieval-release-v2" / "official-source-observations-v2.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def build(*, manifest_path: Path, inventory_path: Path, output: Path, observations_path: Path | None = None) -> dict[str, Any]:
    manifest = _load(manifest_path)
    inventory = _load(inventory_path)
    documents = inventory.get("documents") or []
    observations: dict[str, dict[str, Any]] = {}
    if observations_path and observations_path.is_file():
        observed = _load(observations_path)
        for item in observed.get("records") or []:
            key = str(item.get("law_number") or "").strip().casefold()
            if key:
                observations[key] = item
    if int(inventory.get("source_snapshot_document_count") or 0) != 12_236 or len(documents) != 12_236:
        raise RuntimeError("inventory_must_contain_12236_documents")
    reviews = [
        {
            "document_id": int(row["document_id"]),
            "observed_serving_state": row.get("serving_state"),
            "serving_state": row.get("serving_state"),
            "official_source_url": None,
            "official_source_sha256": None,
            "validity_start": None,
            "validity_end": None,
            "jurisdiction": None,
            "quarantine_reason": None,
            "suggested_official_source_url": observations.get(str(row.get("law_number") or "").strip().casefold(), {}).get("official_source_url"),
            "source_observation_status": "evidence_only_suggested" if observations.get(str(row.get("law_number") or "").strip().casefold(), {}).get("official_source_url") else "not_observed",
            "review_status": "PENDING",
        }
        for row in documents
    ]
    report = {
        "schema_version": "legal-retrieval-v2-review-attestation-v1",
        "release_id": manifest.get("release_id"),
        "source_snapshot_sha256": manifest.get("source_snapshot_sha256"),
        "decision": "PENDING",
        "reviewer_user_id": None,
        "reviewed_at": None,
        "attestation_sha256": None,
        "template_only": True,
        "manifest_file_sha256": file_sha256(manifest_path),
        "inventory_file_sha256": file_sha256(inventory_path),
        "observations_file_sha256": file_sha256(observations_path) if observations_path and observations_path.is_file() else None,
        "document_review_count": len(reviews),
        "document_reviews": reviews,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    report["template_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{file_sha256(output)}  {output.name}\n", encoding="ascii")
    return {
        "status": "REVIEW_TEMPLATE_ONLY",
        "output": str(output.resolve()),
        "document_review_count": len(reviews),
        "approval_possible": False,
        "database_mutated": False,
        "active_pointer_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--official-observations", type=Path, default=DEFAULT_OBSERVATIONS)
    args = parser.parse_args(argv)
    report = build(manifest_path=args.manifest.resolve(), inventory_path=args.inventory.resolve(), output=args.output.resolve(), observations_path=args.official_observations.resolve())
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
