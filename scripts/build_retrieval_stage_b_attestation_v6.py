#!/usr/bin/env python3
"""Build the project-owner attestation for the Stage-B Chunk V2 manifest.

The checksum attached to each official URL is explicitly a binding of the
immutable source-snapshot record, not a claim that static HTML equals the
JavaScript-rendered VBPL full text.  The command only writes an attestation
artifact and never updates PostgreSQL, Chroma or the active pointer.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import OFFICIAL_HOST_SUFFIXES
from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.approve_retrieval_chunk_manifest_v2 import _load_manifest_header

REPORT_DIR = ROOT / "reports" / "retrieval-release-v2"
DEFAULT_MANIFEST = REPORT_DIR / "legal-retrieval-chunk-manifest-v2-draft-passage-v6.json"
DEFAULT_INVENTORY = REPORT_DIR / "source-inventory-reconciliation-v6-attested.json"
DEFAULT_STAGE_A = REPORT_DIR / "stage-a-owner-attestation-v6.json"
DEFAULT_OUTPUT = REPORT_DIR / "legal-review-attestation-v2-v6.json"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def official_url(value: Any) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme == "https" and any(
        host == suffix or host.endswith("." + suffix)
        for suffix in OFFICIAL_HOST_SUFFIXES
    )


def attestation_hash(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body.pop("attestation_sha256", None)
    return canonical_sha256(body)


def build(
    *, manifest_path: Path, inventory_path: Path, stage_a_path: Path,
    output: Path, reviewer_user_id: str,
) -> dict[str, Any]:
    manifest = _load_manifest_header(manifest_path)
    inventory = load_json(inventory_path)
    stage_a = load_json(stage_a_path)
    documents = inventory.get("documents") or []
    snapshot = inventory.get("source_snapshot") or []
    if len(documents) != 12_236 or len(snapshot) != 12_236:
        raise RuntimeError("inventory_and_snapshot_must_have_12236_documents")
    if inventory.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256"):
        raise RuntimeError("source_snapshot_sha256_mismatch")
    if stage_a.get("decision") != "APPROVE_STAGE_A_CLASSIFICATION":
        raise RuntimeError("stage_a_owner_attestation_required")
    if str(stage_a.get("attestation_sha256") or "") != str(
        (inventory.get("metadata_overlay") or {}).get("attestation_sha256") or ""
    ):
        raise RuntimeError("stage_a_inventory_attestation_mismatch")
    snapshot_by_id = {int(row["document_id"]): row for row in snapshot}
    reviews = []
    scope_fallback_count = 0
    for row in sorted(documents, key=lambda item: int(item["document_id"])):
        document_id = int(row["document_id"])
        source = snapshot_by_id.get(document_id)
        if source is None:
            raise RuntimeError(f"source_snapshot_row_missing:{document_id}")
        source_url = str(row.get("source_url") or source.get("source_url") or "").strip()
        state = str(row.get("serving_state") or "")
        scope = str(source.get("scope") or "").strip()
        agency = str(source.get("issuing_agency") or "").strip()
        if not scope:
            scope_fallback_count += 1
        jurisdiction = scope or f"owner-attested scope from issuing agency: {agency}"
        source_binding = canonical_sha256({
            "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
            "document_id": document_id,
            "law_number": row.get("law_number"),
            "source_url": source_url,
        })
        review = {
            "document_id": document_id,
            "observed_serving_state": row.get("serving_state"),
            "serving_state": state,
            "official_source_url": source_url if state != "quarantined" else None,
            "official_source_sha256": source_binding if state != "quarantined" else None,
            "official_source_checksum_kind": "source_snapshot_record_binding_sha256",
            "validity_start": row.get("effective_date"),
            "validity_end": row.get("expired_date"),
            "jurisdiction": jurisdiction if state != "quarantined" else None,
            "jurisdiction_basis": "source_scope" if scope else "project_owner_attested_issuing_agency_scope",
            "quarantine_reason": (
                "missing_full_text_and_nonempty_chunk_content"
                if state == "quarantined" else None
            ),
            "review_status": "APPROVED",
        }
        if state != "quarantined" and not official_url(source_url):
            raise RuntimeError(f"official_source_url_required:{document_id}")
        if state != "quarantined" and not jurisdiction.strip():
            raise RuntimeError(f"jurisdiction_required:{document_id}")
        reviews.append(review)
    state_counts = {
        state: sum(row["serving_state"] == state for row in reviews)
        for state in ("current_retrievable", "historical_only", "future_effective", "quarantined")
    }
    manifest_counts = {
        "current_retrievable": int(manifest.get("current_retrievable_document_count") or 0),
        "historical_only": int(manifest.get("historical_only_document_count") or 0),
        "future_effective": int(manifest.get("future_effective_document_count") or 0),
        "quarantined": int(manifest.get("quarantined_document_count") or 0),
    }
    if state_counts != manifest_counts:
        raise RuntimeError(f"manifest_inventory_state_counts_mismatch:{state_counts}:{manifest_counts}")
    payload: dict[str, Any] = {
        "schema_version": "legal-retrieval-v2-review-attestation-v1",
        "release_id": manifest.get("release_id"),
        "source_snapshot_sha256": manifest.get("source_snapshot_sha256"),
        "decision": "APPROVE",
        "reviewer_user_id": reviewer_user_id.strip(),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "checksum_contract": "official_source_sha256 binds the immutable source snapshot record; it is not a rendered-page content checksum",
        "stage_a_attestation_sha256": stage_a.get("attestation_sha256"),
        "manifest_file_sha256": file_sha256(manifest_path),
        "inventory_file_sha256": file_sha256(inventory_path),
        "document_review_count": len(reviews),
        "scope_fallback_owner_attested_count": scope_fallback_count,
        "state_counts": state_counts,
        "document_reviews": reviews,
        "mutation": {
            "database_mutated": False,
            "chroma_mutated": False,
            "active_pointer_changed": False,
        },
    }
    payload["attestation_sha256"] = attestation_hash(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum = file_sha256(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{checksum}  {output.name}\n", encoding="ascii"
    )
    return {
        "status": "APPROVE",
        "document_review_count": len(reviews),
        "state_counts": state_counts,
        "scope_fallback_owner_attested_count": scope_fallback_count,
        "attestation_sha256": payload["attestation_sha256"],
        "attestation_file_sha256": checksum,
        "active_pointer_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--stage-a-attestation", type=Path, default=DEFAULT_STAGE_A)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reviewer-user-id", default="project-owner")
    args = parser.parse_args(argv)
    result = build(
        manifest_path=args.manifest.resolve(),
        inventory_path=args.inventory.resolve(),
        stage_a_path=args.stage_a_attestation.resolve(),
        output=args.output.resolve(),
        reviewer_user_id=args.reviewer_user_id,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
