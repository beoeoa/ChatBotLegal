#!/usr/bin/env python3
"""Create release-scoped SHA-256 manifests for pinned local rerankers.

This is an artifact-preparation command only.  It never downloads a model,
changes a live pointer, changes a collection, or changes the historical model
manifests.  The output is consumed by the V2 M6 runner, which verifies every
listed file again immediately before loading the model.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _expected(value: Any) -> tuple[str, int | None]:
    if isinstance(value, Mapping):
        size = value.get("size_bytes")
        return str(value.get("sha256") or ""), int(size) if size is not None else None
    return str(value or ""), None


def _files(root: Path, declared: Mapping[str, Any]) -> dict[str, Any]:
    if not root.is_dir():
        raise RuntimeError(f"model_path_missing:{root}")
    result: dict[str, Any] = {}
    for relative, expected_value in declared.items():
        path = root / str(relative)
        if not path.is_file():
            raise RuntimeError(f"model_file_missing:{root}:{relative}")
        expected, expected_size = _expected(expected_value)
        actual_size = path.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            raise RuntimeError(f"model_file_size_mismatch:{root}:{relative}")
        actual = _sha(path)
        if expected and expected.casefold() != actual.casefold():
            raise RuntimeError(f"model_file_checksum_mismatch:{root}:{relative}")
        result[str(relative)] = {
            "size_bytes": actual_size,
            "sha256": actual,
        }
    if not result:
        raise RuntimeError(f"model_files_required:{root}")
    return result


def _manifest(
    *,
    source_manifest_path: Path,
    model_path: Path,
    custom_code_path: Path | None,
    output_path: Path,
) -> dict[str, Any]:
    source = _load(source_manifest_path)
    model_id = str(source.get("model_id") or "").strip()
    revision = str(source.get("revision") or "").strip()
    if not model_id or not revision:
        raise RuntimeError("pinned_model_id_and_revision_required")
    model_files = _files(model_path, dict(source.get("files") or {}))
    source_custom = dict(source.get("custom_code") or {})
    declared_custom_path = custom_code_path
    if declared_custom_path is None and source_custom.get("local_path"):
        declared_custom_path = Path(str(source_custom["local_path"]))
    custom_code: dict[str, Any] | None = None
    if declared_custom_path is not None or source_custom:
        if declared_custom_path is None:
            raise RuntimeError("custom_code_path_required")
        custom_files = _files(
            declared_custom_path,
            dict(source_custom.get("files") or {}),
        )
        if not {"configuration.py", "modeling.py"}.issubset(custom_files):
            raise RuntimeError("custom_code_loader_files_required")
        custom_code = {
            "repository": source_custom.get("repository"),
            "revision": source_custom.get("revision"),
            "local_path": str(declared_custom_path.resolve()),
            "runtime_remote_code_download_allowed": False,
            "files": custom_files,
        }
    return {
        "schema_version": "legal-retrieval-v2-reranker-manifest-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_scope": "retrieval-release-v2",
        "model_id": model_id,
        "revision": revision,
        "license": source.get("license"),
        "local_path": str(model_path.resolve()),
        "download_policy": "pinned_revision_local_only_runtime",
        "runtime_download_allowed": False,
        "files": model_files,
        "custom_code": custom_code,
        "benchmark_contract": {
            "max_length": 512,
            "batch_size": 8,
            "top_n": [20, 30, 50, 100],
            "cuda_required": True,
            "candidate_manifest": "retrieval-eval-suite-v1",
        },
        "source_manifest_sha256": _sha(source_manifest_path),
        "benchmark_only": True,
        "active_pointer_changed": False,
        "live_configuration_changed": False,
        "output_path": str(output_path.resolve()),
    }


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{_sha(path)}  {path.name}\n", encoding="ascii"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bge-path", type=Path, required=True)
    parser.add_argument("--bge-source-manifest", type=Path, required=True)
    parser.add_argument("--gte-path", type=Path, required=True)
    parser.add_argument("--gte-source-manifest", type=Path, required=True)
    parser.add_argument("--gte-code-path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "retrieval-release-v2")
    args = parser.parse_args(argv)
    output_dir = args.output_dir.resolve()
    entries = (
        (
            args.bge_path.resolve(),
            args.bge_source_manifest.resolve(),
            None,
            output_dir / "reranker-bge-reranker-v2-m3-manifest.json",
        ),
        (
            args.gte_path.resolve(),
            args.gte_source_manifest.resolve(),
            args.gte_code_path.resolve() if args.gte_code_path else None,
            output_dir / "reranker-gte-multilingual-reranker-base-manifest.json",
        ),
    )
    for model_path, source_path, code_path, output_path in entries:
        _write(
            output_path,
            _manifest(
                source_manifest_path=source_path,
                model_path=model_path,
                custom_code_path=code_path,
                output_path=output_path,
            ),
        )
    print(
        json.dumps(
            {
                "status": "PASS",
                "manifests": [str(entry[3]) for entry in entries],
                "active_pointer_changed": False,
                "live_configuration_changed": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
