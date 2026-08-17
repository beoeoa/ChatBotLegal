#!/usr/bin/env python3
"""Fail closed on approved form artifacts that are technically not forms.

This command preserves every human legal-review decision and binding.  It only
removes technical runtime eligibility from matching canonical/index/candidate
records, writes atomically, and emits checksum-bound evidence for rollback and
release reporting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = ROOT / "notebook_data" / "forms"
TARGETS = (
    (FORMS_DIR / "canonical_forms_catalog_v1.json", "forms"),
    (FORMS_DIR / "haiphong_official_form_index.json", "forms"),
    (FORMS_DIR / "official_forms_candidates_classified.json", "records"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"QUARANTINE_PAYLOAD_INVALID:{path}")
    return payload


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _record_hash(record: dict[str, Any]) -> str:
    return str(record.get("sha256") or record.get("source_sha256") or "").casefold()


def quarantine_payload(
    payload: dict[str, Any],
    *,
    collection_key: str,
    artifact_hashes: set[str],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = payload.get(collection_key)
    if not isinstance(rows, list):
        raise ValueError(f"QUARANTINE_COLLECTION_INVALID:{collection_key}")
    changed: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or _record_hash(row) not in artifact_hashes:
            continue
        before = {
            "review_status": row.get("review_status"),
            "legal_review_status": row.get("legal_review_status"),
            "approved": row.get("approved"),
            "is_approved": row.get("is_approved"),
            "runtime_eligible": row.get("runtime_eligible"),
            "is_quarantined": row.get("is_quarantined"),
            "catalog_status": row.get("catalog_status"),
        }
        row["runtime_eligible"] = False
        row["is_quarantined"] = True
        row["catalog_status"] = "quarantined"
        after = {key: row.get(key) for key in before}
        if before != after:
            changed.append(
                {
                    "record_id": row.get("form_id") or row.get("id"),
                    "procedure_id": row.get("procedure_id"),
                    "procedure_ids": row.get("procedure_ids") or [],
                    "form_code": row.get("form_code"),
                    "sha256": _record_hash(row),
                    "before": before,
                    "after": after,
                    "legal_decision_preserved": (
                        before.get("review_status") == after.get("review_status")
                        and before.get("legal_review_status")
                        == after.get("legal_review_status")
                        and before.get("approved") == after.get("approved")
                        and before.get("is_approved") == after.get("is_approved")
                    ),
                }
            )
    return payload, changed


def quarantine_files(
    *,
    artifact_hashes: set[str],
    reason_code: str,
    report_path: Path,
    write: bool,
) -> dict[str, Any]:
    normalized = {value.strip().casefold() for value in artifact_hashes if value.strip()}
    if not normalized or any(len(value) != 64 for value in normalized):
        raise ValueError("QUARANTINE_SHA256_INVALID")
    evidence: list[dict[str, Any]] = []
    matched_hashes: set[str] = set()
    for path, collection_key in TARGETS:
        before_sha = _sha256(path)
        payload, changed = quarantine_payload(
            _read(path),
            collection_key=collection_key,
            artifact_hashes=normalized,
        )
        matched_hashes.update(item["sha256"] for item in changed)
        if write and changed:
            _write_atomic(path, payload)
        evidence.append(
            {
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "before_sha256": before_sha,
                "after_sha256": _sha256(path) if write else before_sha,
                "changed_count": len(changed),
                "records": changed,
            }
        )
    missing = sorted(normalized - matched_hashes)
    legal_decisions_preserved = all(
        item["legal_decision_preserved"]
        for file_evidence in evidence
        for item in file_evidence["records"]
    )
    result = {
        "schema_version": "invalid-form-artifact-quarantine-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reason_code": reason_code,
        "write_applied": write,
        "requested_hashes": sorted(normalized),
        "matched_hashes": sorted(matched_hashes),
        "missing_hashes": missing,
        "changed_record_count": sum(item["changed_count"] for item in evidence),
        "legal_decisions_preserved": legal_decisions_preserved,
        "files": evidence,
        "status": (
            "PASS"
            if not missing and legal_decisions_preserved
            else "FAIL"
        ),
    }
    if write:
        _write_atomic(report_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha256", action="append", required=True)
    parser.add_argument("--reason-code", required=True)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "reports" / "feature006" / "invalid-form-artifact-quarantine.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = quarantine_files(
        artifact_hashes=set(args.sha256),
        reason_code=str(args.reason_code).strip(),
        report_path=args.report.resolve(),
        write=not args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
