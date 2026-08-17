"""Verify and seal the Golden-294 live backup without exposing credentials."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_seal(root: Path) -> dict[str, Any]:
    root = root.resolve()
    baseline_path = root / "pre-change-baseline.json"
    surreal_path = root / "surreal-open_notebook.surql"
    retrieval_manifest_path = root / "retrieval" / "manifest.json"
    postgres_path = root / "retrieval" / "postgres.dump"
    required = (
        baseline_path,
        surreal_path,
        retrieval_manifest_path,
        postgres_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"GOLDEN294_BACKUP_FILE_MISSING:{missing!r}")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    retrieval_manifest = json.loads(
        retrieval_manifest_path.read_text(encoding="utf-8")
    )
    expected_postgres_hash = str(
        retrieval_manifest.get("postgres", {}).get("sha256") or ""
    )
    actual_postgres_hash = sha256(postgres_path)
    checks = {
        "baseline_read_only": baseline.get("mutation_performed") is False,
        "postgres_hash_matches": bool(expected_postgres_hash)
        and actual_postgres_hash == expected_postgres_hash,
        "chroma_file_manifest_present": int(
            retrieval_manifest.get("chroma", {}).get("file_count") or 0
        )
        > 0,
        "primary_collection_count_present": int(
            retrieval_manifest.get("chroma", {})
            .get("collections", {})
            .get("legal_chunks_vnlegal_lal_haiphong")
            or 0
        )
        > 0,
        "source_collection_count_present": int(
            retrieval_manifest.get("chroma", {})
            .get("collections", {})
            .get("legal_chunks_vnlegal_lal")
            or 0
        )
        > 0,
        "surreal_export_nonempty": surreal_path.stat().st_size > 0,
    }
    return {
        "schema_version": "golden-294-live-backup-seal-v1",
        "sealed_at": datetime.now(timezone.utc).isoformat(),
        "backup_root": str(root),
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "artifacts": {
            "pre_change_baseline": {
                "path": str(baseline_path),
                "bytes": baseline_path.stat().st_size,
                "sha256": sha256(baseline_path),
            },
            "surreal_export": {
                "path": str(surreal_path),
                "bytes": surreal_path.stat().st_size,
                "sha256": sha256(surreal_path),
            },
            "retrieval_manifest": {
                "path": str(retrieval_manifest_path),
                "bytes": retrieval_manifest_path.stat().st_size,
                "sha256": sha256(retrieval_manifest_path),
            },
            "postgres_dump": {
                "path": str(postgres_path),
                "bytes": postgres_path.stat().st_size,
                "sha256": actual_postgres_hash,
            },
        },
        "collections": retrieval_manifest["chroma"]["collections"],
        "chroma_files": retrieval_manifest["chroma"]["file_count"],
        "chroma_bytes": retrieval_manifest["chroma"]["total_bytes"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-root", required=True, type=Path)
    args = parser.parse_args()
    seal = build_seal(args.backup_root)
    output = args.backup_root.resolve() / "backup-seal.json"
    output.write_text(
        json.dumps(seal, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": seal["status"],
                "output": str(output),
                "collections": seal["collections"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if seal["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
