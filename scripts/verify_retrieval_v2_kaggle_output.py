#!/usr/bin/env python3
"""Verify checksum-bound vector shards returned by the Kaggle worker."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import file_sha256
from scripts.export_retrieval_v2_kaggle_shards import FINGERPRINT_KEYS


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _input_ids(input_manifest: dict[str, Any], root: Path) -> list[str]:
    identifiers: list[str] = []
    for shard in input_manifest.get("shards") or []:
        path = root / str(shard["path"])
        if file_sha256(path) != str(shard["sha256"]):
            raise RuntimeError(f"input_shard_checksum_mismatch:{path}")
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                identifiers.append(str(json.loads(line)["chunk_revision_id"]))
    return identifiers


def verify_kaggle_output(
    *,
    input_manifest_path: Path,
    output_manifest_path: Path,
    expected_dimension: int = 1024,
    output_report_path: Path | None = None,
) -> dict[str, Any]:
    input_manifest = _load(input_manifest_path)
    output_manifest = _load(output_manifest_path)
    input_schema = str(input_manifest.get("schema_version") or "")
    output_schema = str(output_manifest.get("schema_version") or "")
    if input_schema not in {
        "legal-retrieval-kaggle-input-v1",
        "legal-retrieval-kaggle-input-v2",
    }:
        raise RuntimeError("kaggle_input_manifest_required")
    if output_schema not in {
        "legal-retrieval-kaggle-output-v1",
        "legal-retrieval-kaggle-output-v2",
    }:
        raise RuntimeError("kaggle_output_manifest_required")
    output_input_sha = str(output_manifest.get("input_manifest_sha256") or "")
    if output_input_sha and output_input_sha != file_sha256(input_manifest_path):
        raise RuntimeError("kaggle_output_input_manifest_checksum_mismatch")
    for key in ("release_id", "source_snapshot_sha256", *FINGERPRINT_KEYS):
        if str(output_manifest.get(key) or "") != str(input_manifest.get(key) or ""):
            raise RuntimeError(f"kaggle_output_contract_mismatch:{key}")
    if input_schema.endswith("-v2"):
        if output_schema != "legal-retrieval-kaggle-output-v2":
            raise RuntimeError("kaggle_output_v2_required_for_v2_input")
        for key in (
            "embedding_job_id",
            "worker_fingerprint",
            "kaggle_kernel_version",
            "accelerator",
        ):
            if not str(input_manifest.get(key) or "").strip():
                raise RuntimeError(f"kaggle_input_v2_field_required:{key}")
            if str(output_manifest.get(key) or "") != str(input_manifest.get(key) or ""):
                raise RuntimeError(f"kaggle_output_contract_mismatch:{key}")
        if output_manifest.get("internet_enabled") is not False:
            raise RuntimeError("kaggle_internet_must_be_disabled")
        if output_manifest.get("worker_fingerprint") != input_manifest.get("worker_fingerprint"):
            raise RuntimeError("kaggle_worker_fingerprint_mismatch")
        runtime = output_manifest.get("runtime")
        if not isinstance(runtime, dict) or not str(runtime.get("gpu") or "").strip():
            raise RuntimeError("kaggle_runtime_gpu_evidence_required")
    if int(output_manifest.get("embedding_dimension") or 0) != int(expected_dimension):
        raise RuntimeError("kaggle_output_dimension_contract_mismatch")

    expected_ids = _input_ids(input_manifest, input_manifest_path.parent)
    expected_set = set(expected_ids)
    if len(expected_set) != len(expected_ids):
        raise RuntimeError("duplicate_input_chunk_id")

    observed_ids: list[str] = []
    non_finite = 0
    zero_vectors = 0
    norm_out_of_range = 0
    vector_digest = hashlib.sha256()
    vector_count = 0
    output_root = output_manifest_path.parent
    for shard in output_manifest.get("shards") or []:
        path = output_root / str(shard["path"])
        if file_sha256(path) != str(shard["sha256"]):
            raise RuntimeError(f"output_shard_checksum_mismatch:{path}")
        with np.load(path, allow_pickle=False) as payload:
            ids = [str(value) for value in payload["ids"].tolist()]
            vectors = np.asarray(payload["vectors"])
        if vectors.dtype != np.float32:
            raise RuntimeError(f"output_vector_dtype_mismatch:{path}:{vectors.dtype}")
        if vectors.ndim != 2 or vectors.shape != (len(ids), int(expected_dimension)):
            raise RuntimeError(f"output_vector_shape_mismatch:{path}:{vectors.shape}")
        if int(shard.get("count") or 0) != len(ids):
            raise RuntimeError(f"output_shard_count_mismatch:{path}")
        finite_rows = np.isfinite(vectors).all(axis=1)
        non_finite += int((~finite_rows).sum())
        norms = np.linalg.norm(vectors, axis=1)
        zero_vectors += int((norms == 0).sum())
        norm_out_of_range += int(((norms < 0.999) | (norms > 1.001)).sum())
        for identifier, vector in zip(ids, vectors):
            vector_digest.update(identifier.encode("utf-8"))
            vector_digest.update(b"\0")
            vector_digest.update(np.asarray(vector, dtype="<f4").tobytes(order="C"))
        observed_ids.extend(ids)
        vector_count += len(ids)

    observed_counts = Counter(observed_ids)
    observed_set = set(observed_counts)
    duplicate_ids = sorted(
        identifier for identifier, count in observed_counts.items() if count > 1
    )
    missing_ids = sorted(expected_set - observed_set)
    extra_ids = sorted(observed_set - expected_set)
    valid = (
        vector_count == len(expected_ids)
        and not duplicate_ids
        and not missing_ids
        and not extra_ids
        and non_finite == 0
        and zero_vectors == 0
        and norm_out_of_range == 0
    )
    declared_vector_count = output_manifest.get("vector_count")
    if declared_vector_count is not None and int(declared_vector_count) != vector_count:
        raise RuntimeError("kaggle_output_vector_count_mismatch")
    declared_vector_sha = str(output_manifest.get("vector_content_sha256") or "")
    if declared_vector_sha and declared_vector_sha != vector_digest.hexdigest():
        raise RuntimeError("kaggle_output_vector_content_checksum_mismatch")
    if input_schema.endswith("-v2") and len(declared_vector_sha) != 64:
        raise RuntimeError("kaggle_output_vector_content_checksum_required")
    report = {
        "schema_version": "legal-retrieval-kaggle-output-verification-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_id": input_manifest["release_id"],
        "input_manifest_file_sha256": file_sha256(input_manifest_path),
        "output_manifest_file_sha256": file_sha256(output_manifest_path),
        "expected_vector_count": len(expected_ids),
        "vector_count": vector_count,
        "embedding_dimension": int(expected_dimension),
        "missing_ids": missing_ids,
        "extra_ids": extra_ids,
        "duplicate_ids": duplicate_ids,
        "non_finite_vectors": non_finite,
        "zero_vectors": zero_vectors,
        "norm_out_of_range": norm_out_of_range,
        "vector_content_sha256": vector_digest.hexdigest(),
        "provisional_staging": bool(input_manifest.get("provisional_staging")),
        "release_eligible": bool(input_manifest.get("release_eligible")) and valid,
        "active_pointer_changed": False,
        "chroma_mutated": False,
        "valid": valid,
    }
    if output_report_path is not None:
        output_report_path.parent.mkdir(parents=True, exist_ok=True)
        output_report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        output_report_path.with_suffix(output_report_path.suffix + ".sha256").write_text(
            f"{file_sha256(output_report_path)}  {output_report_path.name}\n",
            encoding="ascii",
        )
    if not valid:
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--expected-dimension", type=int, default=1024)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    report = verify_kaggle_output(
        input_manifest_path=args.input_manifest.resolve(),
        output_manifest_path=args.output_manifest.resolve(),
        expected_dimension=args.expected_dimension,
        output_report_path=args.report.resolve(),
    )
    print(json.dumps({"status": "PASS", "report": str(args.report.resolve()), **report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
