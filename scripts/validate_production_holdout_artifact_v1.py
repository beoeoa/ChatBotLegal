#!/usr/bin/env python3
"""Validate public holdout evidence without opening the custody bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_holdout_contracts import (
    public_artifact_forbidden_keys,
    validate_public_holdout_envelope,
)


CONTRACTS = ROOT / "specs" / "018-production-release-readiness" / "contracts"
ENVELOPE_SCHEMA = CONTRACTS / "production-holdout-envelope-v1.schema.json"
RECEIPT_SCHEMA = CONTRACTS / "production-holdout-aggregate-receipt-v1.schema.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _schema_errors(payload: Mapping[str, Any], schema_path: Path) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{'.'.join(str(part) for part in error.absolute_path)}:{error.message}"
        for error in validator.iter_errors(payload)
    ]


def validate_artifacts(
    envelope: Mapping[str, Any], receipt: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    errors = _schema_errors(envelope, ENVELOPE_SCHEMA)
    errors.extend(validate_public_holdout_envelope(envelope))
    if receipt is not None:
        errors.extend(_schema_errors(receipt, RECEIPT_SCHEMA))
        errors.extend(
            f"receipt_forbidden_public_key:{key}"
            for key in sorted(public_artifact_forbidden_keys(receipt))
        )
        bindings = {
            "dataset_version": "dataset_version",
            "holdout_content_sha256": "content_sha256",
            "holdout_case_ids_sha256": "case_ids_sha256",
            "source_snapshot_sha256": "source_snapshot_sha256",
            "manifest_sha256": "manifest_sha256",
            "quota_policy_sha256": "quota_policy_sha256",
            "quota_attestation_sha256": "quota_attestation_sha256",
            "cross_split_leakage_audit_sha256": "cross_split_leakage_audit_sha256",
            "official_source_approval_manifest_sha256": "official_source_approval_manifest_sha256",
        }
        for receipt_key, envelope_key in bindings.items():
            if receipt.get(receipt_key) != envelope.get(envelope_key):
                errors.append(f"receipt_envelope_binding_mismatch:{receipt_key}")
        if receipt.get("holdout_reusable") is not False:
            errors.append("holdout_reuse_forbidden")
        if receipt.get("status") == "FAIL" and receipt.get("next_holdout_version_required") is not True:
            errors.append("failed_holdout_requires_new_version")
    return {
        "schema_version": "production-holdout-public-validation-v1",
        "status": "PASS" if not errors else "FAIL",
        "valid": not errors,
        "errors": sorted(set(errors)),
        "dataset_version": envelope.get("dataset_version"),
        "case_count": envelope.get("case_count"),
        "receipt_present": receipt is not None,
        "hidden_case_content_opened": False,
        "active_pointer_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    envelope = _load(args.envelope.resolve())
    receipt = _load(args.receipt.resolve()) if args.receipt else None
    report = validate_artifacts(envelope, receipt)
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
