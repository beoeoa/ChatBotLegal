#!/usr/bin/env python3
"""Create and verify a narrow, recoverable form-catalog backup.

This command never reads or copies the legal corpus or vector collections.  It
backs up only the JSON catalogs touched by authenticated form attestation and
can also emit the checksum-bound Feature 006 baseline report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = ROOT / "notebook_data" / "forms"
DEFAULT_MANIFEST = ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-29.json"
DEFAULT_SOURCE_SNAPSHOT = ROOT / "data" / "source_cache" / "form_requirements" / "dvc-form-requirements-2026-07-29.json"
DEFAULT_BASELINE = ROOT / "reports" / "feature006" / "form-completion-baseline.json"
BACKUP_FILES = (
    FORMS_DIR / "canonical_forms_catalog_v1.json",
    FORMS_DIR / "procedure_form_bindings_v1.json",
    FORMS_DIR / "legal_review_attestations_v1.json",
    FORMS_DIR / "haiphong_official_form_index.json",
    FORMS_DIR / "canonical_form_checksums_v1.json",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build_baseline(
    *,
    requirement_manifest: Path = DEFAULT_MANIFEST,
    source_snapshot: Path = DEFAULT_SOURCE_SNAPSHOT,
) -> dict[str, Any]:
    requirement_manifest = requirement_manifest.resolve()
    source_snapshot = source_snapshot.resolve()
    manifest = _read(requirement_manifest)
    summary = manifest.get("summary") or {}
    catalog = _read(FORMS_DIR / "canonical_forms_catalog_v1.json")
    runtime_approved = sum(
        item.get("approved") is True and item.get("runtime_eligible") is True
        for item in catalog.get("forms") or []
    )
    counts = {
        "procedures": int(summary.get("scoped_procedure_count") or 0),
        "identities": int(summary.get("required_form_identity_planning_count") or 0),
        "paper_or_file": int((summary.get("deliverable_type_counts") or {}).get("paper_or_file") or 0),
        "interactive_eform": int((summary.get("deliverable_type_counts") or {}).get("interactive_eform") or 0),
        "runtime_approved": runtime_approved,
        "pending": sum(
            item.get("release_status") != "APPROVED_RUNTIME"
            for item in manifest.get("form_identities") or []
        ),
    }
    release_counts = summary.get("release_status_counts") or {}
    expected = {
        "procedures": 418,
        "identities": 278,
        "paper_or_file": 254,
        "interactive_eform": 24,
        "runtime_approved": int(release_counts.get("APPROVED_RUNTIME") or 0),
        "pending": int(release_counts.get("PENDING_LEGAL_REVIEW") or 0),
    }
    if counts != expected:
        raise ValueError(f"FORM_COMPLETION_BASELINE_DRIFT:{counts!r}")
    paths = [requirement_manifest, source_snapshot, *BACKUP_FILES]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"FORM_COMPLETION_BASELINE_FILE_MISSING:{missing!r}")
    return {
        "schema_version": "form-completion-baseline-v1",
        "generated_at": _utcnow(),
        "legal_as_of": manifest.get("legal_as_of"),
        "counts": counts,
        "expected_counts": expected,
        "checksums": {
            str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path)
            for path in paths
        },
        "requirement_manifest_sha256": _sha256(requirement_manifest),
        "source_snapshot_sha256": _sha256(source_snapshot),
        "active_collection": os.getenv(
            "LEGAL_ACTIVE_COLLECTION", "legal_chunks_lechan_primary_v20260723"
        ),
        "feature_flag_enabled": os.getenv(
            "LEGAL_SECTION_GROUNDING_ENABLED", "false"
        ).strip().casefold()
        in {"1", "true", "yes", "on"},
        "runtime_catalog_mutated": False,
        "automated_approval_count": 0,
    }


def create_backup(output_dir: Path, paths: Iterable[Path] = BACKUP_FILES) -> dict[str, Any]:
    resolved_output = output_dir.resolve()
    resolved_output.mkdir(parents=True, exist_ok=False)
    records: list[dict[str, Any]] = []
    for source in paths:
        resolved_source = source.resolve()
        if not resolved_source.is_file() or FORMS_DIR.resolve() not in resolved_source.parents:
            raise ValueError(f"FORM_COMPLETION_BACKUP_SOURCE_INVALID:{source}")
        destination = resolved_output / resolved_source.name
        shutil.copy2(resolved_source, destination)
        records.append(
            {
                "source": str(resolved_source.relative_to(ROOT)).replace("\\", "/"),
                "backup": destination.name,
                "sha256": _sha256(destination),
                "size_bytes": destination.stat().st_size,
            }
        )
    manifest = {
        "schema_version": "form-completion-backup-v1",
        "created_at": _utcnow(),
        "records": records,
        "corpus_included": False,
        "vector_collection_included": False,
    }
    _write_atomic(resolved_output / "backup-manifest.json", manifest)
    return manifest


def verify_backup(manifest_path: Path) -> dict[str, Any]:
    manifest = _read(manifest_path)
    backup_dir = manifest_path.resolve().parent
    failures: list[str] = []
    for record in manifest.get("records") or []:
        backup_path = backup_dir / str(record.get("backup") or "")
        if not backup_path.is_file():
            failures.append(f"MISSING:{record.get('backup')}")
        elif _sha256(backup_path) != record.get("sha256"):
            failures.append(f"CHECKSUM_MISMATCH:{record.get('backup')}")
    return {
        "status": "PASS" if not failures else "FAIL",
        "record_count": len(manifest.get("records") or []),
        "failures": failures,
    }


def verify_restore_drill(
    manifest_path: Path,
    restore_dir: Path,
) -> dict[str, Any]:
    """Restore a backup into a fresh non-runtime directory and verify bytes."""

    manifest_path = manifest_path.resolve()
    backup_dir = manifest_path.parent
    resolved_restore = restore_dir.resolve()
    protected = FORMS_DIR.resolve()
    if (
        resolved_restore == protected
        or protected in resolved_restore.parents
        or resolved_restore in protected.parents
        or resolved_restore.exists()
    ):
        raise ValueError(
            f"FORM_COMPLETION_RESTORE_TARGET_INVALID:{resolved_restore}"
        )

    manifest = _read(manifest_path)
    resolved_restore.mkdir(parents=True, exist_ok=False)
    failures: list[str] = []
    restored: list[dict[str, Any]] = []
    for record in manifest.get("records") or []:
        backup_name = str(record.get("backup") or "")
        backup_path = (backup_dir / backup_name).resolve()
        if (
            not backup_name
            or Path(backup_name).name != backup_name
            or backup_dir not in backup_path.parents
            or not backup_path.is_file()
        ):
            failures.append(f"BACKUP_INVALID:{backup_name}")
            continue
        destination = resolved_restore / backup_name
        shutil.copy2(backup_path, destination)
        checksum = _sha256(destination)
        expected_checksum = str(record.get("sha256") or "")
        if checksum != expected_checksum:
            failures.append(f"CHECKSUM_MISMATCH:{backup_name}")
        restored.append(
            {
                "file": backup_name,
                "sha256": checksum,
                "size_bytes": destination.stat().st_size,
            }
        )

    result = {
        "schema_version": "form-completion-restore-drill-v1",
        "verified_at": _utcnow(),
        "status": "PASS" if not failures else "FAIL",
        "record_count": len(manifest.get("records") or []),
        "restored": restored,
        "failures": failures,
        "runtime_catalog_mutated": False,
        "corpus_included": False,
        "vector_collection_included": False,
    }
    _write_atomic(
        resolved_restore / "restore-verification.json",
        result,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument(
        "--backup-only",
        action="store_true",
        help=(
            "Back up the current catalog sidecars even when the generated "
            "requirement manifest is temporarily stale during reconciliation."
        ),
    )
    parser.add_argument("--baseline-output", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--source-snapshot", type=Path, default=DEFAULT_SOURCE_SNAPSHOT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--restore-check-dir", type=Path)
    args = parser.parse_args()
    if args.restore_check_dir and not args.verify:
        parser.error("--restore-check-dir requires --verify")
    if args.verify:
        result = verify_backup(args.verify)
        if result["status"] == "PASS" and args.restore_check_dir:
            restore_result = verify_restore_drill(
                args.verify,
                args.restore_check_dir,
            )
            result["restore_drill"] = restore_result
            if restore_result["status"] != "PASS":
                result["status"] = "FAIL"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "PASS" else 1
    if args.backup_only:
        if args.baseline_only:
            parser.error("--backup-only cannot be combined with --baseline-only")
        output_dir = args.output_dir or (
            ROOT
            / "backups"
            / "form_completion"
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        )
        result = create_backup(output_dir)
        print(
            json.dumps(
                {"backup": result, "output_dir": str(output_dir)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    baseline = build_baseline(
        requirement_manifest=args.manifest,
        source_snapshot=args.source_snapshot,
    )
    _write_atomic(args.baseline_output, baseline)
    if args.baseline_only:
        print(json.dumps(baseline, ensure_ascii=False, indent=2))
        return 0
    output_dir = args.output_dir or (
        ROOT
        / "backups"
        / "form_completion"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    result = create_backup(output_dir)
    print(
        json.dumps(
            {"baseline": baseline, "backup": result, "output_dir": str(output_dir)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
