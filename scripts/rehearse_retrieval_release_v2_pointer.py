#!/usr/bin/env python3
"""Rehearse V2 pointer switch and rollback in an isolated sandbox.

The live active pointer is read before and after the rehearsal and is never
written.  The candidate pointer must already be an unactivated V3 release
pointer; missing/invalid candidate evidence produces a BLOCKED report.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256


def _read_pointer(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"active_pointer_missing:{path}")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError("active_pointer_empty")
    return value


def _load_candidate(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError("candidate_serving_manifest_missing")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("candidate_serving_manifest_object_required")
    if payload.get("schema_version") != "legal-serving-manifest-v3" or payload.get("kind") != "release_pointer":
        raise RuntimeError("candidate_release_pointer_required")
    if payload.get("activation_performed") is not False or payload.get("active_pointer_changed") is not False:
        raise RuntimeError("candidate_release_pointer_must_be_unactivated")
    collection = str((payload.get("current_collection") or {}).get("path") or "")
    if not collection.startswith("chroma://") or not collection[len("chroma://"):]:
        raise RuntimeError("candidate_current_collection_required")
    return payload


def _atomic_write(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value + "\n", encoding="utf-8")
    os.replace(temporary, path)


def rehearse(*, active_pointer: Path, candidate_manifest: Path, output: Path) -> dict[str, Any]:
    live_before = _read_pointer(active_pointer)
    candidate = _load_candidate(candidate_manifest)
    candidate_collection = str(candidate["current_collection"]["path"])[len("chroma://"):]
    output.parent.mkdir(parents=True, exist_ok=True)
    sandbox_root = Path(tempfile.mkdtemp(prefix="retrieval-v2-pointer-rehearsal-", dir=str(output.parent.resolve())))
    sandbox_pointer = sandbox_root / "active_core_collection.txt"
    switched = False
    rolled_back = False
    try:
        sandbox_pointer.write_text(live_before + "\n", encoding="utf-8")
        _atomic_write(sandbox_pointer, candidate_collection)
        switched = _read_pointer(sandbox_pointer) == candidate_collection
        _atomic_write(sandbox_pointer, live_before)
        rolled_back = _read_pointer(sandbox_pointer) == live_before
    finally:
        shutil.rmtree(sandbox_root, ignore_errors=False)
    live_after = _read_pointer(active_pointer)
    result = {
        "schema_version": "legal-retrieval-v2-pointer-rollback-rehearsal-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if switched and rolled_back and live_before == live_after else "FAIL",
        "active_pointer_path": str(active_pointer.resolve()),
        "active_pointer_before": live_before,
        "active_pointer_after": live_after,
        "live_pointer_unchanged": live_before == live_after,
        "candidate_manifest_path": str(candidate_manifest.resolve()),
        "candidate_manifest_file_sha256": file_sha256(candidate_manifest),
        "candidate_release_id": candidate.get("release_id"),
        "candidate_collection": candidate_collection,
        "sandbox_switch_verified": switched,
        "sandbox_rollback_verified": rolled_back,
        "database_mutated": False,
        "vector_collections_mutated": False,
        "active_pointer_changed": False,
    }
    result["report_sha256"] = canonical_sha256(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--active-pointer", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    try:
        result = rehearse(
            active_pointer=args.active_pointer.resolve(),
            candidate_manifest=args.candidate_manifest.resolve(),
            output=output,
        )
    except Exception as exc:
        result = {
            "schema_version": "legal-retrieval-v2-pointer-rollback-rehearsal-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "BLOCKED",
            "reason": str(exc),
            "active_pointer_path": str(args.active_pointer.resolve()),
            "candidate_manifest_path": str(args.candidate_manifest.resolve()),
            "database_mutated": False,
            "vector_collections_mutated": False,
            "active_pointer_changed": False,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output.with_suffix(output.suffix + ".sha256").write_text(
            f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
        )
    print(json.dumps({"status": result["status"], "output": str(output), "reason": result.get("reason")}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
