#!/usr/bin/env python3
"""Approve a V2 chunk manifest from an explicit legal-review attestation.

This command is intentionally fail-closed.  It does not edit PostgreSQL,
Chroma, the active pointer, or the draft manifest.  It only writes a new
approved manifest after a reviewer has supplied one review row for every
document in the 12,236-document source snapshot.

The attestation is an input from the legal-review process, not something this
script can infer from database status or a model.  In particular, a missing
official-source URL/checksum, a state change relative to the chunk build, or
an incomplete review blocks approval.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256


DEFAULT_MANIFEST = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-chunk-manifest-v2-draft-passage-v4.json"
DEFAULT_INVENTORY = ROOT / "reports" / "retrieval-release-v2" / "source-inventory-reconciliation-v4.json"
DEFAULT_ATTESTATION = ROOT / "reports" / "retrieval-release-v2" / "legal-review-attestation-v2-v4.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-chunk-manifest-v2-approved-passage-v4.json"
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


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _load_manifest_header(path: Path) -> dict[str, Any]:
    """Read the compact header without materializing a multi-GB chunks array."""

    marker = b',"chunks":['
    buffer = b""
    with path.open("rb") as handle:
        while marker not in buffer:
            block = handle.read(1024 * 1024)
            if not block:
                raise RuntimeError("chunk_manifest_header_missing")
            buffer += block
            if len(buffer) > 16 * 1024 * 1024:
                raise RuntimeError("chunk_manifest_header_too_large")
    header, _separator, _tail = buffer.partition(marker)
    value = json.loads((header + b"}").decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError("chunk_manifest_header_object_required")
    return value


def _official_url(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text.startswith("https://"):
        return False
    host = text.split("/", 3)[2].split(":", 1)[0]
    return host == "haiphong.gov.vn" or any(
        host == item or host.endswith("." + item) for item in OFFICIAL_HOSTS
    )


def _attestation_hash(payload: dict[str, Any]) -> str:
    copy = dict(payload)
    copy.pop("attestation_sha256", None)
    return canonical_sha256(copy)


def _validate(
    *, manifest: dict[str, Any], inventory: dict[str, Any], attestation: dict[str, Any]
) -> dict[str, Any]:
    errors: list[str] = []
    if manifest.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
        errors.append("manifest_schema_version")
    if manifest.get("approved") is True or manifest.get("legal_review_attestation") is True:
        errors.append("manifest_already_approved")
    if int(inventory.get("source_snapshot_document_count") or 0) != 12_236:
        errors.append("inventory_document_count")
    if manifest.get("source_snapshot_sha256") != inventory.get("source_snapshot_sha256"):
        errors.append("source_snapshot_sha256_mismatch")
    if attestation.get("schema_version") != "legal-retrieval-v2-review-attestation-v1":
        errors.append("attestation_schema_version")
    if attestation.get("decision") != "APPROVE":
        errors.append("attestation_decision_not_approve")
    if not str(attestation.get("reviewer_user_id") or "").strip():
        errors.append("reviewer_user_id")
    if not str(attestation.get("reviewed_at") or "").strip():
        errors.append("reviewed_at")
    if attestation.get("release_id") != manifest.get("release_id"):
        errors.append("release_id_mismatch")
    if attestation.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256"):
        errors.append("attestation_source_snapshot_mismatch")
    declared_attestation_sha = str(attestation.get("attestation_sha256") or "")
    if not SHA256_RE.fullmatch(declared_attestation_sha) or declared_attestation_sha != _attestation_hash(attestation):
        errors.append("attestation_sha256")

    inventory_rows = inventory.get("documents") or []
    inventory_by_id = {int(row["document_id"]): row for row in inventory_rows}
    review_rows = attestation.get("document_reviews") or []
    review_by_id: dict[int, dict[str, Any]] = {}
    for row in review_rows:
        try:
            document_id = int(row["document_id"])
        except (KeyError, TypeError, ValueError):
            errors.append("document_review_missing_document_id")
            continue
        if document_id in review_by_id:
            errors.append(f"duplicate_document_review:{document_id}")
        review_by_id[document_id] = row
        expected = inventory_by_id.get(document_id)
        if expected is None:
            errors.append(f"document_not_in_inventory:{document_id}")
            continue
        if row.get("serving_state") != expected.get("serving_state"):
            errors.append(f"state_changed_since_chunk_build:{document_id}")
        state = str(row.get("serving_state") or "")
        if state not in {"current_retrievable", "historical_only", "future_effective", "quarantined"}:
            errors.append(f"invalid_serving_state:{document_id}")
        if state in {"current_retrievable", "historical_only", "future_effective"}:
            if not _official_url(row.get("official_source_url")):
                errors.append(f"official_source_url_required:{document_id}")
            if not SHA256_RE.fullmatch(str(row.get("official_source_sha256") or "")):
                errors.append(f"official_source_sha256_required:{document_id}")
            if not str(row.get("jurisdiction") or "").strip():
                errors.append(f"jurisdiction_required:{document_id}")
        else:
            if not str(row.get("quarantine_reason") or "").strip():
                errors.append(f"quarantine_reason_required:{document_id}")

    missing = sorted(set(inventory_by_id) - set(review_by_id))
    if missing:
        errors.append(f"missing_document_reviews:{len(missing)}")
    if len(review_by_id) != 12_236:
        errors.append("review_count_not_12236")

    counts = {
        "current_retrievable": sum(
            row.get("serving_state") == "current_retrievable" for row in review_rows
        ),
        "historical_only": sum(row.get("serving_state") == "historical_only" for row in review_rows),
        "future_effective": sum(row.get("serving_state") == "future_effective" for row in review_rows),
        "quarantined": sum(row.get("serving_state") == "quarantined" for row in review_rows),
    }
    declared_counts = {
        "current_retrievable": int(manifest.get("current_retrievable_document_count") or 0),
        "historical_only": int(manifest.get("historical_only_document_count") or 0),
        "future_effective": int(manifest.get("future_effective_document_count") or 0),
        "quarantined": int(manifest.get("quarantined_document_count") or 0),
    }
    if counts != declared_counts:
        errors.append("review_state_counts_do_not_match_chunk_manifest")
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "review_count": len(review_by_id),
        "missing_document_review_count": len(missing),
        "state_counts": counts,
        "attestation_sha256": declared_attestation_sha,
    }


def _write_approved_copy(*, draft: Path, output: Path, attestation: dict[str, Any], attestation_file_sha256: str) -> str:
    if draft.resolve() == output.resolve():
        raise RuntimeError("approved_output_must_differ_from_draft")
    marker = (
        b',"approved":false,"legal_review_attestation":false,'
        b'"approval_blocker":"official_source_and_metadata_review_required"'
    )
    # Construct the replacement separately to keep all JSON values quoted.
    replacement = (
        ',"approved":true,"legal_review_attestation":true,"approval_blocker":null'
        f',"legal_review_attestation_sha256":{json.dumps(attestation["attestation_sha256"], ensure_ascii=False)}'
        f',"legal_reviewer_user_id":{json.dumps(str(attestation["reviewer_user_id"]), ensure_ascii=False)}'
        f',"legal_reviewed_at":{json.dumps(str(attestation["reviewed_at"]), ensure_ascii=False)}'
        f',"attestation_file_sha256":{json.dumps(attestation_file_sha256, ensure_ascii=False)}'
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    found = False
    with draft.open("rb") as source, output.with_suffix(output.suffix + ".tmp").open("wb") as target:
        prefix = source.read(1024 * 1024)
        if marker not in prefix:
            raise RuntimeError("draft_approval_marker_not_found")
        prefix = prefix.replace(marker, replacement, 1)
        target.write(prefix)
        shutil.copyfileobj(source, target, length=1024 * 1024)
        found = True
    if not found:
        raise RuntimeError("approved_copy_not_written")
    output.with_suffix(output.suffix + ".tmp").replace(output)
    return file_sha256(output)


def approve(*, manifest_path: Path, inventory_path: Path, attestation_path: Path, output: Path) -> dict[str, Any]:
    manifest = _load_manifest_header(manifest_path)
    inventory = _load(inventory_path)
    attestation = _load(attestation_path)
    validation = _validate(manifest=manifest, inventory=inventory, attestation=attestation)
    report = {
        "schema_version": "legal-retrieval-v2-manifest-approval-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "draft_manifest_path": str(manifest_path.resolve()),
        "draft_manifest_file_sha256": file_sha256(manifest_path),
        "inventory_path": str(inventory_path.resolve()),
        "inventory_file_sha256": file_sha256(inventory_path),
        "attestation_path": str(attestation_path.resolve()),
        "attestation_file_sha256": file_sha256(attestation_path),
        "output_path": str(output.resolve()),
        **validation,
        "database_mutated": False,
        "vector_collections_mutated": False,
        "active_pointer_changed": False,
    }
    if not validation["valid"]:
        report["status"] = "BLOCKED"
        return report
    report["approved_manifest_file_sha256"] = _write_approved_copy(
        draft=manifest_path,
        output=output,
        attestation=attestation,
        attestation_file_sha256=report["attestation_file_sha256"],
    )
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{report['approved_manifest_file_sha256']}  {output.name}\n", encoding="ascii"
    )
    report["status"] = "APPROVED_COPY_WRITTEN"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = approve(
        manifest_path=args.manifest.resolve(),
        inventory_path=args.inventory.resolve(),
        attestation_path=args.attestation.resolve(),
        output=args.output.resolve(),
    )
    report_path = args.output.resolve().with_name(
        args.output.resolve().stem + ".approval-report.json"
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "errors": report["errors"], "output": report["output_path"]}, ensure_ascii=False))
    return 0 if report["status"] == "APPROVED_COPY_WRITTEN" else 2


if __name__ == "__main__":
    raise SystemExit(main())
