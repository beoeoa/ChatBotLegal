#!/usr/bin/env python3
"""Create a review-only queue from the V2 inventory audit.

The queue contains proposals only.  It never writes legal_documents and never
promotes a machine classification to approved legal metadata.
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


DEFAULT_INVENTORY = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation-v4.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "metadata-review-queue-v4.json"


def build(*, inventory_path: Path) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    documents = inventory.get("documents") or []
    proposals: list[dict[str, Any]] = []
    for row in documents:
        state = str(row.get("serving_state") or "quarantined")
        reasons = str(row.get("classification_basis") or "unresolved").split(";")
        fields = [
            "status_observed",
            "effective_date",
            "expired_date",
            "source_url",
            "scope_included_observed",
        ]
        if "missing_law_number" in reasons:
            fields.insert(0, "law_number")
        if "missing_articles" in reasons or "missing_chunks" in reasons:
            fields.extend(["content", "article_structure"])
        proposals.append(
            {
                "case_id": f"retrieval-v2-metadata-{int(row['document_id']):06d}",
                "document_id": int(row["document_id"]),
                "observed_serving_state": state,
                "fields_to_review": sorted(set(fields)),
                "reason_codes": sorted(set(item for item in reasons if item)),
                # Preserve the exact pre-review values so a later attestation
                # can prove an explicit before/after change.  These values
                # are observations, not approved legal metadata.
                "observed_values": {
                    "law_number": row.get("law_number"),
                    "status_observed": row.get("status_observed"),
                    "effective_date": row.get("effective_date"),
                    "expired_date": row.get("expired_date"),
                    "source_url": row.get("source_url"),
                    "scope_included_observed": row.get("scope_included_observed"),
                    "serving_state": state,
                    "classification_basis": row.get("classification_basis"),
                    "article_count": row.get("article_count"),
                    "chunk_count": row.get("chunk_count"),
                },
                "state": "proposed",
                "official_evidence_url": None,
                "official_evidence_sha256": None,
                "reviewer_user_id": None,
                "approved": False,
            }
        )
    report = {
        "schema_version": "legal-metadata-review-queue-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inventory_report": {
            "path": str(inventory_path.resolve()),
            "sha256": file_sha256(inventory_path),
            "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
        },
        "proposal_count": len(proposals),
        "approved_count": 0,
        "queue_is_proposal_only": True,
        "database_mutated": False,
        "active_pointer_changed": False,
        "proposals": proposals,
    }
    report["queue_sha256"] = canonical_sha256(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build(inventory_path=args.inventory.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": "PROPOSAL_ONLY",
        "proposal_count": report["proposal_count"],
        "approved_count": report["approved_count"],
        "queue_sha256": report["queue_sha256"],
        "output": str(output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
