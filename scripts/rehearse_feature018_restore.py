"""Rehearse a checksum-bound multi-store restore in an isolated directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMPONENTS = ("postgres", "objects", "vectors", "surreal")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def filesystem_scan_root(root: Path) -> Path:
    resolved = root.resolve()
    if os.name == "nt" and not str(resolved).startswith("\\\\?\\"):
        return Path(f"\\\\?\\{resolved}")
    return resolved


def inventory(root: Path) -> list[dict[str, Any]]:
    scan_root = filesystem_scan_root(root)
    return [
        {
            "path": path.relative_to(scan_root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in sorted(scan_root.rglob("*"))
        if path.is_file()
    ]


def stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def component_inventory(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for component in COMPONENTS:
        for item in inventory(root / component):
            rows.append({**item, "path": f"{component}/{item['path']}"})
    return rows


def copy_component(source: Path, target: Path) -> None:
    if os.name != "nt":
        shutil.copytree(source, target, copy_function=shutil.copy2)
        return
    target.mkdir(parents=True)
    result = subprocess.run(
        [
            "robocopy",
            str(source),
            str(target),
            "/E",
            "/COPY:DAT",
            "/DCOPY:DAT",
            "/R:1",
            "/W:1",
            "/NFL",
            "/NDL",
            "/NJH",
            "/NJS",
            "/NP",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode > 7:
        raise RuntimeError(
            f"restore_component_copy_failed:{source.name}:{result.returncode}"
        )


def rehearse(
    source: Path,
    target: Path,
    release_manifest: Path,
    *,
    verify_existing_target: bool = False,
) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    source = source.resolve()
    target = target.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"restore_source_missing:{source}")
    missing = [name for name in COMPONENTS if not (source / name).is_dir()]
    if missing:
        raise ValueError(f"restore_components_missing:{','.join(missing)}")
    if target.exists() and not verify_existing_target:
        raise FileExistsError(f"restore_target_exists:{target}")
    if verify_existing_target and not target.is_dir():
        raise FileNotFoundError(f"restore_target_missing:{target}")
    manifest = json.loads(release_manifest.read_text(encoding="utf-8-sig"))
    expected_fingerprint = (
        str(manifest.get("release_fingerprint") or "")
        if isinstance(manifest, dict)
        else sha256(release_manifest)
    )
    if len(expected_fingerprint) != 64:
        raise ValueError("release_fingerprint_missing_or_invalid")

    backup_inventory = component_inventory(source)
    if not verify_existing_target:
        target.mkdir(parents=True)
        for component in COMPONENTS:
            copy_component(source / component, target / component)
    restored_inventory = component_inventory(target)
    inventory_match = backup_inventory == restored_inventory
    source_fingerprint = stable_hash(backup_inventory)
    restored_fingerprint = stable_hash(restored_inventory)
    passed = inventory_match and source_fingerprint == restored_fingerprint
    finished = datetime.now(timezone.utc)
    return {
        "schema_version": "feature018-isolated-restore-rehearsal-v1",
        "started_at": started.isoformat(),
        "generated_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "scope": "isolated_fixture_or_operator_supplied_backup",
        "production_activation_authorized": False,
        "copy_performed": not verify_existing_target,
        "passed": passed,
        "components": list(COMPONENTS),
        "file_count": len(backup_inventory),
        "inventory_match": inventory_match,
        "backup_inventory_sha256": source_fingerprint,
        "restored_inventory_sha256": restored_fingerprint,
        "release_fingerprint": expected_fingerprint,
        "release_manifest_sha256": sha256(release_manifest),
        "release_fingerprint_reconciled": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-existing-target", action="store_true")
    args = parser.parse_args()
    result = rehearse(
        args.source,
        args.target,
        args.release_manifest,
        verify_existing_target=args.verify_existing_target,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
