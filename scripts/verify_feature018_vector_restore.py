"""Open an isolated restored Chroma store and reconcile collection counts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping


def compare_collection_counts(
    expected: Mapping[str, int | None],
    restored: Mapping[str, int | None],
) -> dict[str, object]:
    mismatches = {
        name: {"expected": expected.get(name), "restored": restored.get(name)}
        for name in sorted(set(expected) | set(restored))
        if expected.get(name) != restored.get(name)
    }
    return {"passed": not mismatches, "count_mismatches": mismatches}


def verify(
    *,
    backup_manifest: Path,
    restored_chroma_path: Path,
    release_manifest: Path,
) -> dict[str, object]:
    started = datetime.now(UTC)
    backup = json.loads(backup_manifest.read_text(encoding="utf-8"))
    expected = {
        str(name): None if count is None else int(count)
        for name, count in (backup.get("chroma", {}).get("collections") or {}).items()
    }
    if not expected or any(value is None for value in expected.values()):
        raise ValueError("vector_backup_collection_counts_missing")
    restored_path = restored_chroma_path.resolve()
    if not restored_path.is_dir():
        raise FileNotFoundError(f"restored_chroma_missing:{restored_path}")
    import chromadb

    client = chromadb.PersistentClient(path=str(restored_path))
    restored: dict[str, int | None] = {}
    for name in expected:
        try:
            restored[name] = int(client.get_collection(name).count())
        except Exception:
            restored[name] = None
    comparison = compare_collection_counts(expected, restored)
    finished = datetime.now(UTC)
    return {
        "schema_version": "feature018-vector-real-restore-v1",
        "status": "PASS" if comparison["passed"] else "FAIL",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "isolated": True,
        "read_only_verification": True,
        "production_pointer_changed": False,
        "release_fingerprint": hashlib.sha256(
            release_manifest.read_bytes()
        ).hexdigest(),
        "expected_collection_counts": expected,
        "restored_collection_counts": restored,
        "comparison": comparison,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--restored-chroma-path", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(
        backup_manifest=args.backup_manifest,
        restored_chroma_path=args.restored_chroma_path,
        release_manifest=args.release_manifest,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "collections": report["restored_collection_counts"],
                "mismatch_count": len(report["comparison"]["count_mismatches"]),
            }
        )
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
