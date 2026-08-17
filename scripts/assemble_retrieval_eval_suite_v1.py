#!/usr/bin/env python3
"""Assemble three already-reviewed split files into the locked 2,000-case suite.

This command does not author questions or legal mappings.  It accepts only
canonical retrieval-eval-suite-v1 split payloads, checks exact quotas and
shared fingerprints, requires a sealed holdout plus its checksum sidecar, and
writes a new immutable combined artifact.  It is intentionally not run in
this workspace because the sealed holdout is not present here.
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

from api.retrieval_release_contracts import (
    EVAL_SUITE_SCHEMA_VERSION,
    SPLIT_COUNTS,
    canonical_sha256,
    file_sha256,
)
from scripts.validate_retrieval_eval_suite_v1 import validate_suite


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _sidecar_digest(path: Path) -> str | None:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.is_file():
        return None
    first = sidecar.read_text(encoding="ascii").strip().split()
    return first[0].lower() if first and len(first[0]) == 64 else None


def _validate_split(payload: dict[str, Any], *, path: Path, split: str, require_sealed: bool) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != EVAL_SUITE_SCHEMA_VERSION:
        errors.append(f"{split}:schema_version_mismatch")
    cases = [item for item in payload.get("cases") or [] if isinstance(item, dict)]
    expected = int(SPLIT_COUNTS[split])
    if len(cases) != expected:
        errors.append(f"{split}:case_count:{len(cases)}!={expected}")
    wrong_split = sum(item.get("split") != split for item in cases)
    if wrong_split:
        errors.append(f"{split}:wrong_split_cases:{wrong_split}")
    policy = payload.get("review_policy") or {}
    if not bool(policy.get("golden_development_allowed")) and split == "golden-regression":
        errors.append("golden-regression:development_policy_missing")
    if not bool(policy.get("hard_negative_development_allowed")) and split == "hard-negative":
        errors.append("hard-negative:development_policy_missing")
    if require_sealed:
        if policy.get("holdout_sealed") is not True:
            errors.append("production-holdout:holdout_not_sealed")
        digest = _sidecar_digest(path)
        if digest != file_sha256(path):
            errors.append("production-holdout:file_checksum_sidecar_missing_or_mismatch")
    return errors


def assemble(*, golden: Path, hard_negative: Path, holdout: Path, output: Path) -> dict[str, Any]:
    inputs = [
        ("golden-regression", golden.resolve(), False),
        ("hard-negative", hard_negative.resolve(), False),
        ("production-holdout", holdout.resolve(), True),
    ]
    payloads: list[tuple[str, Path, dict[str, Any]]] = []
    errors: list[str] = []
    for split, path, require_sealed in inputs:
        if not path.is_file():
            errors.append(f"{split}:file_missing")
            continue
        payload = _load(path)
        errors.extend(_validate_split(payload, path=path, split=split, require_sealed=require_sealed))
        payloads.append((split, path, payload))
    versions = {str(payload.get("dataset_version") or "") for _, _, payload in payloads}
    source_snapshots = {str(payload.get("source_snapshot_sha256") or "") for _, _, payload in payloads}
    manifests = {str(payload.get("manifest_sha256") or "") for _, _, payload in payloads}
    if len(versions) != 1 or "" in versions:
        errors.append("dataset_version_mismatch")
    if len(source_snapshots) != 1 or "" in source_snapshots:
        errors.append("source_snapshot_sha256_mismatch")
    if len(manifests) != 1 or "" in manifests:
        errors.append("manifest_sha256_mismatch")
    if errors:
        raise ValueError(";".join(sorted(set(errors))))

    cases = [case for _, _, payload in payloads for case in payload.get("cases") or []]
    combined: dict[str, Any] = {
        "schema_version": EVAL_SUITE_SCHEMA_VERSION,
        "dataset_version": next(iter(versions)),
        "source_snapshot_sha256": next(iter(source_snapshots)),
        "manifest_sha256": next(iter(manifests)),
        "review_policy": {
            "golden_development_allowed": True,
            "hard_negative_development_allowed": True,
            "holdout_sealed": True,
        },
        "cases": cases,
    }
    report = validate_suite(combined, require_complete=True)
    if not report["valid"]:
        raise ValueError("combined_suite_validation_failed:" + ";".join(report["errors"][:20]))
    combined["suite_sha256"] = canonical_sha256(combined)
    if output.exists():
        raise FileExistsError(f"refusing_to_overwrite_existing_suite:{output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    return {
        "schema_version": "retrieval-eval-suite-assembly-report-v1",
        "status": "PASS",
        "suite_path": str(output.resolve()),
        "suite_sha256": combined["suite_sha256"],
        "suite_file_sha256": file_sha256(output),
        "case_count": len(cases),
        "split_counts": {split: sum(case.get("split") == split for case in cases) for split in SPLIT_COUNTS},
        "holdout_sealed": True,
        "mutation": {"input_datasets_mutated": False, "active_pointer_changed": False},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--hard-negative", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = assemble(
            golden=args.golden,
            hard_negative=args.hard_negative,
            holdout=args.holdout,
            output=args.output,
        )
    except (FileNotFoundError, ValueError) as exc:
        # Keep the missing/invalid split evidence auditable without creating a
        # partial suite. In particular, a missing sealed holdout must not be
        # silently replaced by a legacy development set.
        output_path = args.output.resolve()
        report = {
            "schema_version": "retrieval-eval-suite-assembly-report-v1",
            "status": "BLOCKED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": str(exc),
            "requested_output": str(output_path),
            "input_paths": {
                "golden": str(args.golden.resolve()),
                "hard_negative": str(args.hard_negative.resolve()),
                "production_holdout": str(args.holdout.resolve()),
            },
            "partial_suite_written": False,
            "mutation": {
                "input_datasets_mutated": False,
                "active_pointer_changed": False,
            },
        }
        report_path = output_path.with_suffix(output_path.suffix + ".assembly-blocked.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_path.with_suffix(report_path.suffix + ".sha256").write_text(
            f"{file_sha256(report_path)}  {report_path.name}\n", encoding="ascii"
        )
        print(json.dumps(report, ensure_ascii=True))
        return 2
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
