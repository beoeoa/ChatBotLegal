#!/usr/bin/env python3
"""Standalone Kaggle GPU worker for checksum-bound Retrieval V2 passages.

This file is copied into a private Kaggle Kernel bundle.  It discovers the
input manifest and pinned model from ``/kaggle/input``, embeds all shards, and
writes immutable NumPy vector shards under ``/kaggle/working``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
from typing import Any
import zipfile

import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoConfig, AutoModel, AutoTokenizer


INPUT_ROOT = Path(os.getenv("KAGGLE_INPUT_ROOT", "/kaggle/input"))
OUTPUT_ROOT = Path(os.getenv("KAGGLE_OUTPUT_ROOT", "/kaggle/working"))
MAX_LENGTH = 512
EMBEDDING_DIMENSION = 1024
DEFAULT_BATCHES = (128, 96, 64, 32, 16, 8, 4, 1)
FINGERPRINT_KEYS = (
    "model_artifact_fingerprint",
    "tokenizer_fingerprint",
    "embedding_recipe_fingerprint",
    "passage_recipe_fingerprint",
    "splitter_fingerprint",
    "dependency_lock_fingerprint",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def model_revision_fingerprint(model_path: Path) -> str:
    digest = hashlib.sha256()
    patterns = ("*.json", "*.safetensors", "*.bin", "*.model", "*.txt")
    files = sorted(
        {
            path
            for pattern in patterns
            for path in model_path.rglob(pattern)
            if path.is_file()
        },
        key=lambda path: path.relative_to(model_path).as_posix(),
    )
    if not files:
        digest.update(b"model-artifacts-missing")
    for path in files:
        relative = path.relative_to(model_path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def discover_input_manifest(root: Path) -> Path:
    matches = sorted(root.rglob("embedding-input-manifest.json"))
    if len(matches) != 1:
        raise RuntimeError(f"single_input_manifest_required:found={len(matches)}")
    return matches[0]


def _safe_extract_model_zip(archive_path: Path, extraction_root: Path) -> Path:
    destination = extraction_root / file_sha256(archive_path)[:16]
    if destination.is_dir() and any(destination.rglob("config.json")):
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > 10_000:
            raise RuntimeError(f"model_zip_too_many_members:{archive_path}")
        total_size = sum(int(member.file_size) for member in members)
        if total_size > 10 * 1024**3:
            raise RuntimeError(f"model_zip_uncompressed_size_limit:{archive_path}")
        resolved_destination = destination.resolve()
        for member in members:
            member_path = (destination / member.filename).resolve()
            if not member_path.is_relative_to(resolved_destination):
                raise RuntimeError(f"model_zip_path_traversal:{member.filename}")
        archive.extractall(destination)
    return destination


def discover_model(
    root: Path,
    expected_fingerprint: str,
    *,
    extraction_root: Path | None = None,
) -> Path:
    search_roots = [root]
    candidates = sorted({path.parent for path in root.rglob("config.json")})
    fingerprints = {path: model_revision_fingerprint(path) for path in candidates}
    if not any(value == expected_fingerprint for value in fingerprints.values()):
        cache_root = extraction_root or Path(
            os.getenv("KAGGLE_MODEL_CACHE_ROOT", "/kaggle/temp/legal-v2-model")
        )
        for archive_path in sorted(root.rglob("model.zip")):
            search_roots.append(_safe_extract_model_zip(archive_path, cache_root))
        candidates = sorted(
            {
                path.parent
                for search_root in search_roots
                for path in search_root.rglob("config.json")
            }
        )
        fingerprints = {path: model_revision_fingerprint(path) for path in candidates}
    matches = [
        path
        for path in candidates
        if fingerprints[path] == expected_fingerprint
    ]
    if len(matches) != 1:
        raise RuntimeError(f"single_pinned_model_required:found={len(matches)}")
    return matches[0]


def _read_input_shard(path: Path, expected_sha256: str) -> list[dict[str, Any]]:
    if file_sha256(path) != expected_sha256:
        raise RuntimeError(f"input_shard_checksum_mismatch:{path}")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
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
                raise RuntimeError(f"embedding_text_checksum_mismatch:{identifier}")
            token_count = int(row.get("token_count") or 0)
            if token_count < 1 or token_count > MAX_LENGTH:
                raise RuntimeError(f"embedding_token_budget:{identifier}:{token_count}")
            rows.append(dict(row))
    return rows


class Encoder:
    def __init__(self, model_path: Path):
        if not torch.cuda.is_available():
            raise RuntimeError("kaggle_cuda_required")
        self.device = torch.device("cuda")
        config = AutoConfig.from_pretrained(model_path, local_files_only=True)
        self.tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        try:
            self.model = AutoModel.from_pretrained(
                model_path,
                config=config,
                local_files_only=True,
                torch_dtype=torch.float16,
            )
        except TypeError:
            self.model = AutoModel.from_pretrained(
                model_path,
                config=config,
                local_files_only=True,
                dtype=torch.float16,
            )
        self.model.to(self.device)
        self.model.eval()

    def _encode_batch(self, passages: list[str]) -> np.ndarray:
        inputs = self.tokenizer(
            passages,
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
            return_tensors="pt",
        )
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        output = self.model(**inputs)
        last_token = inputs["attention_mask"].sum(dim=1) - 1
        embeddings = output.last_hidden_state[
            torch.arange(len(inputs["input_ids"]), device=self.device), last_token
        ]
        embeddings = F.normalize(embeddings, p=2, dim=1)
        return embeddings.cpu().numpy().astype(np.float32)

    def encode(self, passages: list[str], requested_batch: int) -> tuple[np.ndarray, int]:
        last_error: Exception | None = None
        for batch_size in DEFAULT_BATCHES:
            if batch_size > requested_batch:
                continue
            try:
                vectors: list[np.ndarray] = []
                with torch.inference_mode():
                    for start in range(0, len(passages), batch_size):
                        vectors.append(self._encode_batch(passages[start : start + batch_size]))
                return np.concatenate(vectors, axis=0), batch_size
            except torch.cuda.OutOfMemoryError as exc:
                last_error = exc
                torch.cuda.empty_cache()
        raise RuntimeError("embedding_oom_after_batch_fallback") from last_error


def run() -> dict[str, Any]:
    input_manifest_path = discover_input_manifest(INPUT_ROOT)
    input_manifest = _load_json(input_manifest_path)
    if input_manifest.get("schema_version") not in {
        "legal-retrieval-kaggle-input-v1",
        "legal-retrieval-kaggle-input-v2",
    }:
        raise RuntimeError("kaggle_input_manifest_required")
    worker_fingerprint = file_sha256(Path(__file__))
    if input_manifest.get("schema_version") == "legal-retrieval-kaggle-input-v2":
        if str(input_manifest.get("worker_fingerprint") or "") != worker_fingerprint:
            raise RuntimeError("kaggle_worker_fingerprint_mismatch")
        if input_manifest.get("internet_enabled") is not False:
            raise RuntimeError("kaggle_internet_must_be_disabled")
    if int(input_manifest.get("embedding_max_length") or 0) != MAX_LENGTH:
        raise RuntimeError("embedding_max_length_contract_mismatch")
    if int(input_manifest.get("embedding_dimension") or 0) != EMBEDDING_DIMENSION:
        raise RuntimeError("embedding_dimension_contract_mismatch")
    model_path = discover_model(
        INPUT_ROOT, str(input_manifest["model_artifact_fingerprint"])
    )
    requested_batch = int(os.getenv("KAGGLE_EMBED_BATCH_SIZE", "128"))
    encoder = Encoder(model_path)
    encoder.encode(["warm up passage"] * min(8, requested_batch), requested_batch)

    output_shards: list[dict[str, Any]] = []
    vector_digest = hashlib.sha256()
    total_vectors = 0
    started = perf_counter()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    checkpoint_path = OUTPUT_ROOT / "embedding-checkpoint.json"
    resume_requested = os.getenv("KAGGLE_EMBED_RESUME", "").casefold() in {
        "1",
        "true",
        "yes",
    }
    input_manifest_sha256 = file_sha256(input_manifest_path)
    checkpoint: dict[str, Any] = {}
    if checkpoint_path.is_file():
        if not resume_requested:
            raise RuntimeError("embedding_checkpoint_exists_use_resume")
        checkpoint = _load_json(checkpoint_path)
        if checkpoint.get("input_manifest_sha256") != input_manifest_sha256:
            raise RuntimeError("embedding_checkpoint_input_manifest_mismatch")
        if checkpoint.get("embedding_job_id") != input_manifest.get("embedding_job_id"):
            raise RuntimeError("embedding_checkpoint_job_id_mismatch")
        if checkpoint.get("worker_fingerprint") != worker_fingerprint:
            raise RuntimeError("embedding_checkpoint_worker_mismatch")
        if checkpoint.get("release_id") != input_manifest.get("release_id"):
            raise RuntimeError("embedding_checkpoint_release_mismatch")
        output_shards = [dict(row) for row in checkpoint.get("output_shards") or []]
        total_vectors = sum(int(row.get("count") or 0) for row in output_shards)
        for row in output_shards:
            output_path = OUTPUT_ROOT / str(row["path"])
            if file_sha256(output_path) != str(row.get("sha256") or ""):
                raise RuntimeError(f"embedding_checkpoint_output_checksum_mismatch:{output_path}")
            with np.load(output_path, allow_pickle=False) as payload:
                ids = [str(value) for value in payload["ids"].tolist()]
                vectors = np.asarray(payload["vectors"], dtype=np.float32)
            for identifier, vector in zip(ids, vectors):
                vector_digest.update(identifier.encode("utf-8"))
                vector_digest.update(b"\0")
                vector_digest.update(np.asarray(vector, dtype="<f4").tobytes(order="C"))
    completed_indices = {
        int(str(row["path"]).rsplit("-", 1)[-1].split(".", 1)[0])
        for row in output_shards
    }
    for shard_index, shard in enumerate(input_manifest.get("shards") or []):
        input_path = input_manifest_path.parent / str(shard["path"])
        rows = _read_input_shard(input_path, str(shard["sha256"]))
        if len(rows) != int(shard.get("count") or 0):
            raise RuntimeError(f"input_shard_count_mismatch:{input_path}")
        if shard_index in completed_indices:
            print(
                json.dumps(
                    {
                        "stage": "embedding_resume",
                        "shard_index": shard_index,
                        "persisted_vectors": total_vectors,
                        "expected_vectors": input_manifest["chunk_count"],
                    }
                ),
                flush=True,
            )
            continue
        rows.sort(key=lambda row: (int(row["token_count"]), row["chunk_revision_id"]))
        vectors, used_batch = encoder.encode(
            [str(row["embedding_text"]) for row in rows], requested_batch
        )
        if vectors.shape != (len(rows), EMBEDDING_DIMENSION):
            raise RuntimeError(f"embedding_shape_mismatch:{vectors.shape}")
        norms = np.linalg.norm(vectors, axis=1)
        if not np.isfinite(vectors).all() or not ((norms >= 0.999) & (norms <= 1.001)).all():
            raise RuntimeError("embedding_integrity_failure")
        order = np.argsort(np.asarray([row["chunk_revision_id"] for row in rows]))
        ids = np.asarray([rows[index]["chunk_revision_id"] for index in order])
        vectors = np.asarray(vectors[order], dtype=np.float32)
        for identifier, vector in zip(ids.tolist(), vectors):
            vector_digest.update(str(identifier).encode("utf-8"))
            vector_digest.update(b"\0")
            vector_digest.update(np.asarray(vector, dtype="<f4").tobytes(order="C"))
        output_path = OUTPUT_ROOT / f"vectors-{shard_index:05d}.npz"
        np.savez(output_path, ids=ids, vectors=vectors)
        output_shards.append(
            {
                "path": output_path.name,
                "count": len(ids),
                "sha256": file_sha256(output_path),
                "input_path": str(shard["path"]),
                "input_sha256": str(shard["sha256"]),
                "batch_size_used": used_batch,
                "min_norm": float(norms.min()),
                "max_norm": float(norms.max()),
            }
        )
        total_vectors += len(ids)
        checkpoint = {
            "schema_version": "legal-retrieval-kaggle-checkpoint-v2",
            "embedding_job_id": input_manifest.get("embedding_job_id"),
            "release_id": input_manifest.get("release_id"),
            "input_manifest_sha256": input_manifest_sha256,
            "worker_fingerprint": worker_fingerprint,
            "completed_shards": shard_index + 1,
            "output_shards": output_shards,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        checkpoint_path.write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "stage": "embedding",
                    "shard_index": shard_index,
                    "shard_count": len(input_manifest.get("shards") or []),
                    "persisted_vectors": total_vectors,
                    "expected_vectors": input_manifest["chunk_count"],
                    "batch_size_used": used_batch,
                    "elapsed_seconds": round(perf_counter() - started, 3),
                }
            ),
            flush=True,
        )

    if total_vectors != int(input_manifest.get("chunk_count") or 0):
        raise RuntimeError("full_embedding_count_mismatch")
    runtime = {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0),
        "kernel_version_observed": (
            os.getenv("KAGGLE_KERNEL_VERSION")
            or os.getenv("KAGGLE_KERNEL_RUN_ID")
            or os.getenv("KAGGLE_KERNEL_ID")
            or "kaggle-runtime-unreported"
        ),
    }
    output_schema = (
        "legal-retrieval-kaggle-output-v2"
        if input_manifest.get("schema_version") == "legal-retrieval-kaggle-input-v2"
        else "legal-retrieval-kaggle-output-v1"
    )
    report = {
        "schema_version": output_schema,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_id": input_manifest["release_id"],
        "source_snapshot_sha256": input_manifest["source_snapshot_sha256"],
        **{key: input_manifest[key] for key in FINGERPRINT_KEYS},
        "input_manifest_sha256": input_manifest_sha256,
        "embedding_job_id": input_manifest.get("embedding_job_id"),
        "worker_fingerprint": worker_fingerprint,
        "kaggle_kernel_version": input_manifest.get("kaggle_kernel_version"),
        "accelerator": input_manifest.get("accelerator") or "NvidiaTeslaT4",
        "internet_enabled": False,
        "embedding_dimension": EMBEDDING_DIMENSION,
        "embedding_max_length": MAX_LENGTH,
        "embedding_dtype": "float32",
        "vector_count": total_vectors,
        "shards": output_shards,
        "runtime": runtime,
        "vector_content_sha256": vector_digest.hexdigest(),
        "elapsed_seconds": round(perf_counter() - started, 3),
        "provisional_staging": bool(input_manifest.get("provisional_staging")),
        "release_eligible": bool(input_manifest.get("release_eligible")),
        "active_pointer_changed": False,
        "chroma_mutated": False,
    }
    output_manifest_path = OUTPUT_ROOT / "embedding-output-manifest.json"
    output_manifest_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT_ROOT / "embedding-output-manifest.json.sha256").write_text(
        f"{file_sha256(output_manifest_path)}  {output_manifest_path.name}\n",
        encoding="ascii",
    )
    return report


if __name__ == "__main__":
    try:
        result = run()
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "reason": str(exc)}), file=sys.stderr)
        raise
    print(json.dumps({"status": "PASS", "vector_count": result["vector_count"]}))
