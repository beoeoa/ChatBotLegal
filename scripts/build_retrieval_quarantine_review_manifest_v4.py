#!/usr/bin/env python3
"""Build an evidence-bound review manifest for the 4,994 non-serving records.

This command joins the immutable v4 inventory with the read-only source-content
audit and quarantine transport observations.  It proposes a legal-review
disposition; it never changes PostgreSQL, Chroma, a serving manifest or the
active pointer.  In particular, an HTTP 200 or an identity match is evidence,
not legal approval.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256


DEFAULT_DIR = ROOT / "reports" / "retrieval-release-v2"
DEFAULT_INVENTORY = DEFAULT_DIR / "source-inventory-reconciliation-v4.json"
DEFAULT_CONTENT_AUDIT = DEFAULT_DIR / "source-content-audit-12236-v4.json"
DEFAULT_QUARANTINE_AUDIT = DEFAULT_DIR / "quarantine-audit-v4.json"
DEFAULT_OUTPUT = DEFAULT_DIR / "quarantine-review-manifest-v4.json"
REMAINING_COUNT = 4_994


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _transport(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("transport_observation")
    return dict(value) if isinstance(value, Mapping) else {}


def _source_evidence(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "verification_status": "missing_audit_record",
            "identity_verified": False,
            "transport_status": None,
            "status_code": None,
            "official_source_url_allowlisted": False,
            "content_reasons": ["source_audit_record_missing"],
        }
    identity = row.get("identity_evidence")
    identity = dict(identity) if isinstance(identity, Mapping) else {}
    transport = _transport(row)
    return {
        "verification_status": str(row.get("verification_status") or "unknown"),
        "identity_verified": bool(row.get("identity_verified")),
        "transport_status": transport.get("transport_status"),
        "status_code": transport.get("status_code"),
        "final_url": transport.get("final_url"),
        "official_source_url_allowlisted": bool(row.get("official_source_url_allowlisted")),
        "law_number_match": identity.get("law_number_match"),
        "title_overlap": identity.get("title_overlap"),
        "issuing_agency_overlap": identity.get("issuing_agency_overlap"),
        "legal_content_detected": identity.get("legal_content_detected"),
        "content_reasons": list(identity.get("content_reasons") or []),
        "content_sha256": (row.get("source_content_evidence") or {}).get("content_sha256"),
    }


def _candidate(row: Mapping[str, Any], *, source: Mapping[str, Any]) -> tuple[str, str, list[str]]:
    state = str(row.get("serving_state") or "quarantined")
    status = str(row.get("status_observed") or "").casefold()
    verified = bool(source.get("identity_verified"))
    if state == "future_effective":
        return (
            "future_effective",
            "hold_until_effective_date",
            ["confirm_official_identity", "confirm_effective_date", "keep_out_of_current_and_temporal_collections"],
        )
    if status == "expired" and str(row.get("classification_basis") or "") == "outside_search_scope":
        return (
            "historical_only_candidate" if verified else "historical_only_review_required",
            "review_historical_serving_scope",
            ["confirm_official_identity", "confirm_validity_interval", "confirm_jurisdiction", "approve_historical_query_scope"],
        )
    if status == "staging":
        return (
            "quarantined",
            "staging_record_requires_resolution",
            ["resolve_staging_provenance", "confirm_official_identity", "confirm_validity_interval"],
        )
    return (
        "quarantined",
        "unresolved_legal_metadata_or_source",
        ["confirm_official_identity", "confirm_status", "confirm_validity_interval", "confirm_jurisdiction", "decide_serving_scope"],
    )


def build(*, inventory_path: Path, content_audit_path: Path, quarantine_audit_path: Path) -> dict[str, Any]:
    inventory = _load(inventory_path)
    content = _load(content_audit_path)
    quarantine = _load(quarantine_audit_path) if quarantine_audit_path.is_file() else {"records": []}
    documents = [
        row for row in inventory.get("documents") or []
        if str(row.get("serving_state") or "") in {"future_effective", "quarantined"}
    ]
    if len(documents) != REMAINING_COUNT:
        raise RuntimeError(f"remaining_document_count:{len(documents)}!={REMAINING_COUNT}")
    content_by_id = {int(row["document_id"]): row for row in content.get("records") or []}
    quarantine_by_id = {int(row["document_id"]): row for row in quarantine.get("records") or []}
    records: list[dict[str, Any]] = []
    for row in sorted(documents, key=lambda item: int(item["document_id"])):
        document_id = int(row["document_id"])
        source = _source_evidence(content_by_id.get(document_id))
        proposed_state, action, required = _candidate(row, source=source)
        quarantine_transport = _transport(quarantine_by_id.get(document_id) or {})
        records.append({
            "case_id": f"retrieval-v2-quarantine-{document_id:06d}",
            "document_id": document_id,
            "law_number": row.get("law_number"),
            "status_observed": row.get("status_observed"),
            "serving_state_observed": row.get("serving_state"),
            "classification_basis_observed": row.get("classification_basis"),
            "effective_date": row.get("effective_date"),
            "expired_date": row.get("expired_date"),
            "source_url": row.get("source_url"),
            "article_count": int(row.get("article_count") or 0),
            "chunk_count_observed": int(row.get("chunk_count") or 0),
            "source_content_evidence": source,
            "quarantine_transport_evidence": quarantine_transport,
            "provisional_candidate_state": proposed_state,
            "required_action": action,
            "required_legal_decisions": required,
            "legal_review_status": "required",
            "approved_for_serving": False,
            "metadata_mutated": False,
            "vectors_mutated": False,
        })
    proposal_counts = Counter(str(row["provisional_candidate_state"]) for row in records)
    source_counts = Counter(str(row["source_content_evidence"]["verification_status"]) for row in records)
    payload: dict[str, Any] = {
        "schema_version": "legal-retrieval-quarantine-review-manifest-v4",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inventory_file_sha256": file_sha256(inventory_path),
        "source_content_audit_file_sha256": file_sha256(content_audit_path),
        "quarantine_audit_file_sha256": file_sha256(quarantine_audit_path) if quarantine_audit_path.is_file() else None,
        "source_snapshot_sha256": inventory.get("source_snapshot_sha256"),
        "inventory_document_count": int(inventory.get("source_snapshot_document_count") or 0),
        "remaining_document_count": len(records),
        "provisional_candidate_state_counts": dict(sorted(proposal_counts.items())),
        "source_content_verification_counts": dict(sorted(source_counts.items())),
        "legal_review_required": True,
        "approved_for_serving": False,
        "records": records,
        "mutation": {
            "database_mutated": False,
            "chroma_mutated": False,
            "active_pointer_changed": False,
        },
    }
    payload["manifest_sha256"] = canonical_sha256(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--content-audit", type=Path, default=DEFAULT_CONTENT_AUDIT)
    parser.add_argument("--quarantine-audit", type=Path, default=DEFAULT_QUARANTINE_AUDIT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build(
        inventory_path=args.inventory.resolve(),
        content_audit_path=args.content_audit.resolve(),
        quarantine_audit_path=args.quarantine_audit.resolve(),
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": "LEGAL_REVIEW_REQUIRED",
        "remaining_document_count": report["remaining_document_count"],
        "provisional_candidate_state_counts": report["provisional_candidate_state_counts"],
        "source_content_verification_counts": report["source_content_verification_counts"],
        "manifest_sha256": report["manifest_sha256"],
        "output": str(output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
