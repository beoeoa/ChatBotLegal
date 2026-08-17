#!/usr/bin/env python3
"""Replay Kaggle vectors locally without importing them into Chroma.

This verifier is the final Stage C parity gate.  It deterministically samples
approved Kaggle input rows, reads the corresponding persisted vectors directly
from the NPZ output shards, re-embeds the passages with the pinned local model,
and optionally compares CUDA with CPU inference.  PostgreSQL, Chroma and the
active collection pointer are read-only throughout the command.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.kaggle_retrieval_v2_worker import model_revision_fingerprint


EMBEDDING_DIMENSION = 1024
EMBEDDING_MAX_LENGTH = 512
REPLAY_THRESHOLD = 0.999
PARITY_THRESHOLD = 0.995


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _directory_fingerprint(path: Path) -> str:
    rows = []
    for item in sorted(path.rglob("*")):
        if item.is_file():
            rows.append(
                {
                    "path": item.relative_to(path).as_posix(),
                    "sha256": file_sha256(item),
                    "size": item.stat().st_size,
                }
            )
    return canonical_sha256(rows)


def _cosine(left: Iterable[float], right: Iterable[float]) -> float:
    lhs = np.asarray(list(left), dtype=np.float32)
    rhs = np.asarray(list(right), dtype=np.float32)
    if lhs.shape != rhs.shape or lhs.ndim != 1 or not lhs.size:
        raise RuntimeError("vector_dimension_mismatch")
    left_norm = float(np.linalg.norm(lhs))
    right_norm = float(np.linalg.norm(rhs))
    if left_norm == 0.0 or right_norm == 0.0:
        raise RuntimeError("zero_vector")
    return float(np.dot(lhs, rhs) / (left_norm * right_norm))


def _deterministic_sample(
    input_manifest: dict[str, Any], input_root: Path, sample_size: int
) -> list[dict[str, Any]]:
    """Return rows with the smallest SHA-256 scores using bounded memory."""

    if sample_size < 1:
        raise ValueError("sample_size_must_be_positive")
    heap: list[tuple[int, str, dict[str, Any]]] = []
    seen: set[str] = set()
    for shard in input_manifest.get("shards") or []:
        path = input_root / str(shard["path"])
        if file_sha256(path) != str(shard.get("sha256") or ""):
            raise RuntimeError(f"input_shard_checksum_mismatch:{path}")
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                identifier = str(row.get("chunk_revision_id") or "")
                if not identifier or identifier in seen:
                    raise RuntimeError(f"invalid_or_duplicate_chunk_id:{identifier}")
                seen.add(identifier)
                text = str(row.get("embedding_text") or "")
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != str(
                    row.get("embedding_text_sha256") or ""
                ):
                    raise RuntimeError(
                        f"embedding_text_checksum_mismatch:{identifier}"
                    )
                score = int.from_bytes(
                    hashlib.sha256(identifier.encode("utf-8")).digest(), "big"
                )
                candidate = (-score, identifier, dict(row))
                if len(heap) < sample_size:
                    heapq.heappush(heap, candidate)
                elif candidate > heap[0]:
                    heapq.heapreplace(heap, candidate)
    expected_count = int(input_manifest.get("chunk_count") or 0)
    if len(seen) != expected_count:
        raise RuntimeError(
            f"input_chunk_count_mismatch:expected={expected_count}:actual={len(seen)}"
        )
    if len(heap) < sample_size:
        raise RuntimeError(f"eligible_sample_too_small:{len(heap)}<{sample_size}")
    return [item[2] for item in sorted(heap, key=lambda item: (-item[0], item[1]))]


def _load_persisted_sample(
    output_manifest: dict[str, Any], output_root: Path, identifiers: set[str]
) -> dict[str, np.ndarray]:
    observed: dict[str, np.ndarray] = {}
    for shard in output_manifest.get("shards") or []:
        path = output_root / str(shard["path"])
        if file_sha256(path) != str(shard.get("sha256") or ""):
            raise RuntimeError(f"output_shard_checksum_mismatch:{path}")
        with np.load(path, allow_pickle=False) as payload:
            ids = [str(value) for value in payload["ids"].tolist()]
            vectors = np.asarray(payload["vectors"])
        if vectors.dtype != np.float32:
            raise RuntimeError(f"output_vector_dtype_mismatch:{path}:{vectors.dtype}")
        if vectors.ndim != 2 or vectors.shape != (len(ids), EMBEDDING_DIMENSION):
            raise RuntimeError(f"output_vector_shape_mismatch:{path}:{vectors.shape}")
        for identifier, vector in zip(ids, vectors):
            if identifier in identifiers:
                if identifier in observed:
                    raise RuntimeError(f"duplicate_output_sample_id:{identifier}")
                observed[identifier] = np.asarray(vector, dtype=np.float32)
        del vectors
        gc.collect()
    missing = sorted(identifiers - set(observed))
    if missing:
        raise RuntimeError(f"output_sample_ids_missing:{missing[:5]}")
    return observed


def _encode(
    rows: list[dict[str, Any]],
    *,
    model_path: Path,
    device_name: str,
    batch_size: int,
) -> dict[str, np.ndarray]:
    import torch
    import torch.nn.functional as functional
    from transformers import AutoConfig, AutoModel, AutoTokenizer

    if device_name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("cuda_required_for_replay")
    device = torch.device(device_name)
    dtype = torch.float16 if device.type == "cuda" else torch.float32
    config = AutoConfig.from_pretrained(model_path, local_files_only=True)
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    try:
        model = AutoModel.from_pretrained(
            model_path,
            config=config,
            local_files_only=True,
            torch_dtype=dtype,
        )
    except TypeError:
        model = AutoModel.from_pretrained(
            model_path,
            config=config,
            local_files_only=True,
            dtype=dtype,
        )
    model.to(device)
    model.eval()
    ordered = sorted(
        rows,
        key=lambda row: (int(row.get("token_count") or 0), row["chunk_revision_id"]),
    )
    result: dict[str, np.ndarray] = {}
    with torch.inference_mode():
        for start in range(0, len(ordered), max(1, int(batch_size))):
            batch = ordered[start : start + max(1, int(batch_size))]
            inputs = tokenizer(
                [str(row["embedding_text"]) for row in batch],
                padding=True,
                truncation=True,
                max_length=EMBEDDING_MAX_LENGTH,
                return_tensors="pt",
            )
            inputs = {key: value.to(device) for key, value in inputs.items()}
            output = model(**inputs)
            last_token = inputs["attention_mask"].sum(dim=1) - 1
            vectors = output.last_hidden_state[
                torch.arange(len(batch), device=device), last_token
            ]
            vectors = functional.normalize(vectors, p=2, dim=1)
            vectors_np = vectors.cpu().numpy().astype(np.float32)
            for row, vector in zip(batch, vectors_np):
                result[str(row["chunk_revision_id"])] = vector
    del model, tokenizer
    gc.collect()
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return result


def verify_parity(
    *,
    input_manifest_path: Path,
    output_manifest_path: Path,
    model_path: Path,
    pointer_path: Path,
    report_path: Path,
    sample_size: int = 500,
    parity_sample_size: int = 100,
    gpu_batch_size: int = 8,
    cpu_batch_size: int = 4,
) -> dict[str, Any]:
    input_manifest = _load(input_manifest_path)
    output_manifest = _load(output_manifest_path)
    if input_manifest.get("schema_version") != "legal-retrieval-kaggle-input-v2":
        raise RuntimeError("approved_kaggle_input_v2_required")
    if output_manifest.get("schema_version") != "legal-retrieval-kaggle-output-v2":
        raise RuntimeError("kaggle_output_v2_required")
    if input_manifest.get("release_eligible") is not True:
        raise RuntimeError("release_eligible_input_required")
    if output_manifest.get("release_eligible") is not True:
        raise RuntimeError("release_eligible_output_required")
    if int(output_manifest.get("vector_count") or 0) != int(
        input_manifest.get("chunk_count") or 0
    ):
        raise RuntimeError("kaggle_vector_count_contract_mismatch")
    if output_manifest.get("input_manifest_sha256") != file_sha256(
        input_manifest_path
    ):
        raise RuntimeError("kaggle_output_input_manifest_checksum_mismatch")
    if model_revision_fingerprint(model_path) != str(
        input_manifest.get("model_artifact_fingerprint") or ""
    ):
        raise RuntimeError("local_model_artifact_fingerprint_mismatch")
    if _directory_fingerprint(model_path) != str(
        input_manifest.get("tokenizer_fingerprint") or ""
    ):
        raise RuntimeError("local_tokenizer_fingerprint_mismatch")
    pointer_before = (
        pointer_path.read_text(encoding="utf-8").strip()
        if pointer_path.is_file()
        else ""
    )
    rows = _deterministic_sample(
        input_manifest, input_manifest_path.parent, sample_size
    )
    expected_ids = {str(row["chunk_revision_id"]) for row in rows}
    persisted = _load_persisted_sample(
        output_manifest, output_manifest_path.parent, expected_ids
    )
    gpu = _encode(
        rows,
        model_path=model_path,
        device_name="cuda",
        batch_size=gpu_batch_size,
    )
    replay_values = [
        _cosine(gpu[str(row["chunk_revision_id"])], persisted[str(row["chunk_revision_id"])])
        for row in rows
    ]
    parity_rows = rows[: min(len(rows), max(1, int(parity_sample_size)))]
    cpu = _encode(
        parity_rows,
        model_path=model_path,
        device_name="cpu",
        batch_size=cpu_batch_size,
    )
    parity_values = [
        _cosine(gpu[str(row["chunk_revision_id"])], cpu[str(row["chunk_revision_id"])])
        for row in parity_rows
    ]
    pointer_after = (
        pointer_path.read_text(encoding="utf-8").strip()
        if pointer_path.is_file()
        else ""
    )
    replay_passed = min(replay_values) >= REPLAY_THRESHOLD
    parity_passed = min(parity_values) >= PARITY_THRESHOLD
    result: dict[str, Any] = {
        "schema_version": "legal-retrieval-kaggle-parity-verification-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_id": input_manifest.get("release_id"),
        "embedding_job_id": input_manifest.get("embedding_job_id"),
        "input_manifest_file_sha256": file_sha256(input_manifest_path),
        "output_manifest_file_sha256": file_sha256(output_manifest_path),
        "model_artifact_fingerprint": input_manifest.get(
            "model_artifact_fingerprint"
        ),
        "tokenizer_fingerprint": input_manifest.get("tokenizer_fingerprint"),
        "embedding_dimension": EMBEDDING_DIMENSION,
        "embedding_max_length": EMBEDDING_MAX_LENGTH,
        "replay_cosine": {
            "sample_count": len(replay_values),
            "minimum": min(replay_values),
            "median": float(np.median(np.asarray(replay_values))),
            "threshold": REPLAY_THRESHOLD,
            "passed": replay_passed,
        },
        "cpu_gpu_parity": {
            "sample_count": len(parity_values),
            "minimum_cosine": min(parity_values),
            "median_cosine": float(np.median(np.asarray(parity_values))),
            "threshold": PARITY_THRESHOLD,
            "passed": parity_passed,
        },
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_changed": pointer_before != pointer_after,
        "database_mutated": False,
        "vector_collections_mutated": False,
    }
    result["status"] = (
        "PASS"
        if replay_passed and parity_passed and pointer_before == pointer_after
        else "FAIL"
    )
    result["report_sha256"] = canonical_sha256(result)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_path.with_suffix(report_path.suffix + ".sha256").write_text(
        f"{file_sha256(report_path)}  {report_path.name}\n", encoding="ascii"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument(
        "--pointer-path",
        type=Path,
        default=ROOT
        / "release-data"
        / "legal"
        / "chroma_store"
        / "active_core_collection.txt",
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=500)
    parser.add_argument("--parity-sample-size", type=int, default=100)
    parser.add_argument("--gpu-batch-size", type=int, default=8)
    parser.add_argument("--cpu-batch-size", type=int, default=4)
    args = parser.parse_args(argv)
    result = verify_parity(
        input_manifest_path=args.input_manifest.resolve(),
        output_manifest_path=args.output_manifest.resolve(),
        model_path=args.model_path.resolve(),
        pointer_path=args.pointer_path.resolve(),
        report_path=args.report.resolve(),
        sample_size=args.sample_size,
        parity_sample_size=args.parity_sample_size,
        gpu_batch_size=args.gpu_batch_size,
        cpu_batch_size=args.cpu_batch_size,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "report": str(args.report.resolve()),
                "replay_cosine": result["replay_cosine"],
                "cpu_gpu_parity": result["cpu_gpu_parity"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
