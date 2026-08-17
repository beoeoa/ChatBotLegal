#!/usr/bin/env python3
"""Bridge the fixed Feature 006 requirement manifest into campaign inputs.

The bridge is deterministic and read-only with respect to the canonical form
catalog.  Every emitted item remains unapproved and runtime-ineligible.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "notebook_data" / "forms" / "canonical_forms_catalog_v1.json"
DEFAULT_ATTESTATIONS = (
    ROOT / "notebook_data" / "forms" / "legal_review_attestations_v1.json"
)
DEFAULT_OUTPUT = (
    ROOT / "notebook_data" / "forms" / "feature006_form_completion_inventory_v1.json"
)
DEFAULT_REPORT = (
    ROOT / "reports" / "feature006" / "form-completion-bridge-verification.json"
)

TERMINAL_CATALOG_STATUSES = {
    "rejected",
    "superseded",
    "sync_failed",
    "sync-failed",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"INVALID_JSON_OBJECT:{path.name}")
    return value


def sha256_path(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"REQUIRED_FILE_MISSING:{path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _candidate_id(identity_id: str, procedure_id: str) -> str:
    digest = hashlib.sha256(f"{identity_id}\0{procedure_id}".encode()).hexdigest()
    return f"feature006-{digest[:24]}"


def _catalog_state(
    identity: Mapping[str, Any], catalog_by_id: Mapping[str, Mapping[str, Any]]
) -> tuple[str, list[str]]:
    form_ids = sorted(
        {
            _text(item)
            for item in identity.get("approved_catalog_form_ids") or []
            if _text(item)
        }
    )
    records = [catalog_by_id[item] for item in form_ids if item in catalog_by_id]
    if any(
        item.get("approved") is True and item.get("runtime_eligible") is True
        for item in records
    ):
        return "APPROVED_RUNTIME", form_ids
    statuses = {
        _text(item.get("legal_review_status") or item.get("review_status")).casefold()
        for item in records
    }
    terminal = sorted(statuses & TERMINAL_CATALOG_STATUSES)
    if terminal:
        return f"CATALOG_{terminal[0].upper().replace('-', '_')}", form_ids
    return "PENDING_LEGAL_REVIEW", form_ids


def _occurrence(
    identity: Mapping[str, Any], procedure_id: str
) -> dict[str, Any]:
    titles = [
        _text(item) for item in identity.get("canonical_titles") or [] if _text(item)
    ]
    domains = [_text(item) for item in identity.get("domains") or [] if _text(item)]
    source_pages = [
        _text(item)
        for item in identity.get("official_source_pages") or []
        if _text(item)
    ]
    identity_id = _text(identity.get("identity_id"))
    return {
        "candidate_id": _candidate_id(identity_id, procedure_id),
        "requirement_identity_id": identity_id,
        "procedure_id": procedure_id,
        "component_kind": "applicant_form",
        "identity_status": _text(identity.get("identity_status")),
        "form_code": _text(identity.get("form_code")) or None,
        "issuing_instrument": _text(identity.get("issuing_instrument")) or None,
        "canonical_name": titles[0] if titles else None,
        "form_name": titles[0] if titles else None,
        "canonical_titles": titles,
        "domain": domains[0] if domains else None,
        "source_tier": "central",
        "executing_level": "commune",
        "issuing_instruments": (
            [_text(identity.get("issuing_instrument"))]
            if _text(identity.get("issuing_instrument"))
            else []
        ),
        "official_source_pages": source_pages,
        "source_page_url": source_pages[0] if source_pages else None,
        "distribution_variants": list(identity.get("distribution_variants") or []),
        "prior_approval_status": False,
        "approved": False,
        "runtime_eligible": False,
        "candidate_only": True,
        "human_attestation_required": True,
    }


def bridge_manifest(
    *,
    manifest_path: Path,
    source_snapshot_path: Path,
    catalog_path: Path,
    attestations_path: Path,
    legal_as_of: str,
    expected_manifest_sha256: str,
    expected_source_snapshot_sha256: str,
) -> dict[str, Any]:
    manifest_sha = sha256_path(manifest_path)
    source_sha = sha256_path(source_snapshot_path)
    if manifest_sha != _text(expected_manifest_sha256):
        raise ValueError("MANIFEST_CHECKSUM_DRIFT")
    if source_sha != _text(expected_source_snapshot_sha256):
        raise ValueError("SOURCE_SNAPSHOT_CHECKSUM_DRIFT")

    manifest = _load(manifest_path)
    catalog = _load(catalog_path)
    attestations = _load(attestations_path)
    if _text(manifest.get("legal_as_of")) != _text(legal_as_of):
        raise ValueError("LEGAL_AS_OF_DRIFT")
    identities = [
        item for item in manifest.get("form_identities") or [] if isinstance(item, dict)
    ]
    declared_target = int(
        (manifest.get("summary") or {}).get(
            "required_form_identity_planning_count", -1
        )
    )
    if declared_target != len(identities):
        raise ValueError("FORM_IDENTITY_COUNT_DRIFT")
    identity_ids = [_text(item.get("identity_id")) for item in identities]
    if not all(identity_ids) or len(set(identity_ids)) != len(identity_ids):
        raise ValueError("FORM_IDENTITY_KEY_INVALID")

    catalog_by_id = {
        _text(item.get("form_id")): item
        for item in catalog.get("forms") or []
        if isinstance(item, dict) and _text(item.get("form_id"))
    }
    paper_rows: list[dict[str, Any]] = []
    eform_rows: list[dict[str, Any]] = []
    terminal_identities: list[dict[str, Any]] = []
    approved_identities: list[dict[str, Any]] = []
    pending_identity_ids: set[str] = set()

    for identity in sorted(identities, key=lambda item: _text(item.get("identity_id"))):
        identity_id = _text(identity.get("identity_id"))
        state, catalog_form_ids = _catalog_state(identity, catalog_by_id)
        if state == "APPROVED_RUNTIME":
            approved_identities.append(
                {
                    "requirement_identity_id": identity_id,
                    "catalog_form_ids": catalog_form_ids,
                    "terminal_reason": state,
                }
            )
            continue
        if state.startswith("CATALOG_"):
            terminal_identities.append(
                {
                    "requirement_identity_id": identity_id,
                    "catalog_form_ids": catalog_form_ids,
                    "terminal_reason": state,
                }
            )
            continue

        pending_identity_ids.add(identity_id)
        procedure_ids = sorted(
            {_text(item) for item in identity.get("procedure_ids") or [] if _text(item)}
        )
        if not procedure_ids:
            terminal_identities.append(
                {
                    "requirement_identity_id": identity_id,
                    "catalog_form_ids": [],
                    "terminal_reason": "MISSING_PROCEDURE_ID",
                }
            )
            continue
        target = (
            eform_rows
            if "interactive_eform" in (identity.get("distribution_variants") or [])
            else paper_rows
        )
        target.extend(_occurrence(identity, procedure_id) for procedure_id in procedure_ids)

    paper_rows.sort(key=lambda item: (item["requirement_identity_id"], item["procedure_id"]))
    eform_rows.sort(key=lambda item: (item["requirement_identity_id"], item["procedure_id"]))
    campaign_inventory = {
        "schema_version": "feature006-form-completion-inventory-v1",
        "legal_as_of": _text(legal_as_of),
        "manifest_sha256": manifest_sha,
        "source_snapshot_sha256": source_sha,
        "canonical_forms": [],
        "verified_data_gaps": paper_rows,
        "eform_requirements": eform_rows,
        "approved_identities": approved_identities,
        "terminal_identities": terminal_identities,
        "candidate_only": True,
        "automated_approval": False,
    }
    result = {
        "schema_version": "feature006-form-completion-bridge-v1",
        "legal_as_of": _text(legal_as_of),
        "checksums": {
            "requirement_manifest": manifest_sha,
            "source_snapshot": source_sha,
            "catalog": sha256_path(catalog_path),
            "attestations": sha256_path(attestations_path),
        },
        "summary": {
            "target_identities": len(identities),
            "approved_identities": len(approved_identities),
            "pending_identities": len(pending_identity_ids),
            "pending_procedure_bindings": len(paper_rows) + len(eform_rows),
            "paper_or_file_pending": len(
                {item["requirement_identity_id"] for item in paper_rows}
            ),
            "interactive_eform_pending": len(
                {item["requirement_identity_id"] for item in eform_rows}
            ),
        },
        "campaign_inventory": campaign_inventory,
        "attestation_record_count": len(attestations.get("attestations") or []),
        "candidate_only": True,
        "automated_approval": False,
    }
    result["bridge_payload_sha256"] = _stable_digest(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--source-snapshot", type=Path)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--attestations", type=Path, default=DEFAULT_ATTESTATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--legal-as-of", default=date.today().isoformat())
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--source-snapshot-sha256", required=True)
    args = parser.parse_args()

    legal_as_of = date.fromisoformat(args.legal_as_of).isoformat()
    manifest_path = args.manifest or (
        ROOT / "reports" / "feature006" / f"form-requirement-manifest-{legal_as_of}.json"
    )
    source_snapshot_path = args.source_snapshot or (
        ROOT
        / "data"
        / "source_cache"
        / "form_requirements"
        / f"dvc-form-requirements-{legal_as_of}.json"
    )

    first = bridge_manifest(
        manifest_path=manifest_path,
        source_snapshot_path=source_snapshot_path,
        catalog_path=args.catalog,
        attestations_path=args.attestations,
        legal_as_of=legal_as_of,
        expected_manifest_sha256=args.manifest_sha256,
        expected_source_snapshot_sha256=args.source_snapshot_sha256,
    )
    second = bridge_manifest(
        manifest_path=manifest_path,
        source_snapshot_path=source_snapshot_path,
        catalog_path=args.catalog,
        attestations_path=args.attestations,
        legal_as_of=legal_as_of,
        expected_manifest_sha256=args.manifest_sha256,
        expected_source_snapshot_sha256=args.source_snapshot_sha256,
    )
    if first["bridge_payload_sha256"] != second["bridge_payload_sha256"]:
        raise ValueError("BRIDGE_NOT_IDEMPOTENT")
    summary = first["summary"]
    if summary["target_identities"] != 278:
        raise ValueError("FORM_COMPLETION_BASELINE_DRIFT")
    if (
        summary["approved_identities"]
        + summary["pending_identities"]
        + len(first["campaign_inventory"]["terminal_identities"])
        != summary["target_identities"]
    ):
        raise ValueError("FORM_COMPLETION_RECONCILIATION_DRIFT")
    _write_atomic(args.output, first["campaign_inventory"])
    report = {
        **{key: value for key, value in first.items() if key != "campaign_inventory"},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output_path": str(args.output),
        "output_sha256": sha256_path(args.output),
        "idempotency": "PASS",
        "catalog_mutated": False,
        "attestations_mutated": False,
        "corpus_mutated": False,
        "vectors_mutated": False,
        "active_collection_changed": False,
        "feature_flag_enabled": False,
    }
    _write_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
