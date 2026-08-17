#!/usr/bin/env python3
"""Apply separately attested metadata corrections to a staging overlay.

This command is deliberately not a PostgreSQL writer.  It verifies that each
correction is anchored to the immutable review queue, carries official-source
evidence and has an explicit reviewer attestation, then writes a new
before/after overlay for a later release build.  The original inventory and
queue are never overwritten.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256


SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
OFFICIAL_HOSTS = (
    "vbpl.vn",
    "vanban.chinhphu.vn",
    "chinhphu.vn",
    "haiphong.gov.vn",
    "bocongan.gov.vn",
    "moj.gov.vn",
    "moc.gov.vn",
    "moh.gov.vn",
    "moet.gov.vn",
)
ALLOWED_STATES = {"current_retrievable", "historical_only", "future_effective", "quarantined"}
ALLOWED_FIELDS = {
    "law_number",
    "status_observed",
    "effective_date",
    "expired_date",
    "source_url",
    "scope_included_observed",
    "serving_state",
}


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required:{path}")
    return payload


def _official_url(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text.startswith("https://"):
        return False
    host = text.split("/", 3)[2].split(":", 1)[0]
    return host == "haiphong.gov.vn" or any(
        host == item or host.endswith("." + item) for item in OFFICIAL_HOSTS
    )


def _attestation_hash(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body.pop("attestation_sha256", None)
    return canonical_sha256(body)


def _json_equal(left: Any, right: Any) -> bool:
    return canonical_sha256(left) == canonical_sha256(right)


def _validate(
    *,
    queue: dict[str, Any],
    inventory: dict[str, Any],
    attestation: dict[str, Any],
    queue_file_sha256: str,
    inventory_file_sha256: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    if queue.get("schema_version") != "legal-metadata-review-queue-v2":
        errors.append("queue_schema_version")
    if str(queue.get("queue_sha256") or "") != canonical_sha256({
        key: value for key, value in queue.items() if key != "queue_sha256"
    }):
        errors.append("queue_sha256_mismatch")
    if int(inventory.get("source_snapshot_document_count") or 0) != 12_236:
        errors.append("inventory_document_count")
    if attestation.get("schema_version") != "legal-retrieval-metadata-attestation-v1":
        errors.append("attestation_schema_version")
    if attestation.get("decision") != "APPROVE_CORRECTIONS":
        errors.append("attestation_decision_not_approve_corrections")
    if not str(attestation.get("reviewer_user_id") or "").strip():
        errors.append("reviewer_user_id")
    if str(attestation.get("reviewer_user_id") or "").strip().casefold() in {
        "automated", "system", "model", "ai",
    }:
        errors.append("real_reviewer_required")
    if not str(attestation.get("reviewed_at") or "").strip():
        errors.append("reviewed_at")
    if attestation.get("queue_file_sha256") != queue_file_sha256:
        errors.append("queue_file_sha256_mismatch")
    if attestation.get("inventory_file_sha256") != inventory_file_sha256:
        errors.append("inventory_file_sha256_mismatch")
    declared_hash = str(attestation.get("attestation_sha256") or "")
    if not SHA256_RE.fullmatch(declared_hash) or declared_hash != _attestation_hash(attestation):
        errors.append("attestation_sha256")

    inventory_by_id = {
        int(row["document_id"]): row for row in inventory.get("documents") or []
    }
    queue_by_id = {
        int(row["document_id"]): row for row in queue.get("proposals") or []
    }
    corrections = attestation.get("corrections") or []
    if not isinstance(corrections, list) or not corrections:
        errors.append("corrections_required")
        corrections = []
    before_after: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for index, correction in enumerate(corrections):
        prefix = f"correction[{index}]"
        if not isinstance(correction, dict):
            errors.append(f"{prefix}:object_required")
            continue
        try:
            document_id = int(correction.get("document_id"))
        except (TypeError, ValueError):
            errors.append(f"{prefix}:document_id")
            continue
        field_name = str(correction.get("field_name") or "")
        key = (document_id, field_name)
        if key in seen:
            errors.append(f"{prefix}:duplicate_correction")
        seen.add(key)
        if field_name not in ALLOWED_FIELDS:
            errors.append(f"{prefix}:field_not_allowed")
        inventory_row = inventory_by_id.get(document_id)
        queue_row = queue_by_id.get(document_id)
        if inventory_row is None:
            errors.append(f"{prefix}:document_not_in_inventory")
            continue
        if queue_row is None:
            errors.append(f"{prefix}:document_not_in_queue")
            continue
        if field_name not in set(queue_row.get("fields_to_review") or []):
            errors.append(f"{prefix}:field_not_in_review_scope")
        observed_values = queue_row.get("observed_values") or {}
        before = observed_values.get(field_name)
        if not _json_equal(correction.get("old_value"), before):
            errors.append(f"{prefix}:old_value_mismatch")
        proposed = correction.get("proposed_value")
        if _json_equal(proposed, before):
            errors.append(f"{prefix}:no_change")
        if field_name == "serving_state" and proposed not in ALLOWED_STATES:
            errors.append(f"{prefix}:invalid_serving_state")
        if field_name == "source_url" and not _official_url(proposed):
            errors.append(f"{prefix}:official_source_url_required")
        evidence_url = correction.get("evidence_url")
        if not _official_url(evidence_url):
            errors.append(f"{prefix}:evidence_url_required")
        if not SHA256_RE.fullmatch(str(correction.get("evidence_sha256") or "")):
            errors.append(f"{prefix}:evidence_sha256_required")
        if not str(correction.get("review_note") or "").strip():
            errors.append(f"{prefix}:review_note_required")
        before_after.append({
            "document_id": document_id,
            "field_name": field_name,
            "before": before,
            "after": proposed,
            "evidence_url": evidence_url,
            "evidence_sha256": correction.get("evidence_sha256"),
            "review_note": correction.get("review_note"),
            "reviewer_user_id": attestation.get("reviewer_user_id"),
        })
    return sorted(set(errors)), before_after


def apply_attestation(
    *,
    queue_path: Path,
    inventory_path: Path,
    attestation_path: Path,
    output_audit: Path,
    output_inventory: Path | None,
) -> dict[str, Any]:
    queue = _load(queue_path)
    inventory = _load(inventory_path)
    attestation = _load(attestation_path)
    errors, before_after = _validate(
        queue=queue,
        inventory=inventory,
        attestation=attestation,
        queue_file_sha256=file_sha256(queue_path),
        inventory_file_sha256=file_sha256(inventory_path),
    )
    report: dict[str, Any] = {
        "schema_version": "legal-retrieval-metadata-attestation-report-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "BLOCKED" if errors else "ATTESTED_STAGING",
        "errors": errors,
        "queue_path": str(queue_path.resolve()),
        "queue_file_sha256": file_sha256(queue_path),
        "inventory_path": str(inventory_path.resolve()),
        "inventory_file_sha256": file_sha256(inventory_path),
        "attestation_path": str(attestation_path.resolve()),
        "attestation_file_sha256": file_sha256(attestation_path),
        "attestation_sha256": attestation.get("attestation_sha256"),
        "correction_count": len(before_after),
        "before_after": before_after,
        "corrected_inventory_path": str(output_inventory.resolve()) if output_inventory else None,
        "database_mutated": False,
        "source_inventory_mutated": False,
        "active_pointer_changed": False,
        "vector_collections_mutated": False,
    }
    if errors:
        return report
    if output_inventory is not None:
        corrected = deepcopy(inventory)
        rows_by_id = {int(row["document_id"]): row for row in corrected.get("documents") or []}
        field_map = {
            "law_number": "law_number",
            "status_observed": "status_observed",
            "effective_date": "effective_date",
            "expired_date": "expired_date",
            "source_url": "source_url",
            "scope_included_observed": "scope_included_observed",
            "serving_state": "serving_state",
        }
        for change in before_after:
            rows_by_id[int(change["document_id"])][field_map[change["field_name"]]] = change["after"]
            rows_by_id[int(change["document_id"])]["legal_review_required"] = False
            rows_by_id[int(change["document_id"])]["metadata_attestation_sha256"] = attestation["attestation_sha256"]
            rows_by_id[int(change["document_id"])]["metadata_evidence_url"] = change["evidence_url"]
            rows_by_id[int(change["document_id"])]["metadata_evidence_sha256"] = change["evidence_sha256"]
            rows_by_id[int(change["document_id"])]["metadata_reviewed_by"] = attestation["reviewer_user_id"]
            rows_by_id[int(change["document_id"])]["metadata_reviewed_at"] = attestation["reviewed_at"]
        corrected["metadata_overlay"] = {
            "schema_version": "legal-retrieval-metadata-overlay-v1",
            "attestation_sha256": attestation["attestation_sha256"],
            "reviewer_user_id": attestation["reviewer_user_id"],
            "reviewed_at": attestation["reviewed_at"],
            "correction_count": len(before_after),
            "source_inventory_unchanged": True,
        }
        output_inventory.parent.mkdir(parents=True, exist_ok=True)
        output_inventory.write_text(json.dumps(corrected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["corrected_inventory_file_sha256"] = file_sha256(output_inventory)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--output-audit", type=Path, required=True)
    parser.add_argument("--output-inventory", type=Path)
    args = parser.parse_args(argv)
    report = apply_attestation(
        queue_path=args.queue.resolve(),
        inventory_path=args.inventory.resolve(),
        attestation_path=args.attestation.resolve(),
        output_audit=args.output_audit.resolve(),
        output_inventory=args.output_inventory.resolve() if args.output_inventory else None,
    )
    output = args.output_audit.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({"status": report["status"], "errors": report["errors"], "output": str(output)}, ensure_ascii=False))
    return 0 if report["status"] == "ATTESTED_STAGING" else 2


if __name__ == "__main__":
    raise SystemExit(main())
