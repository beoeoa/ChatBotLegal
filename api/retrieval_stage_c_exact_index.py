"""Read-only exact vector search over the immutable Stage C shard set.

The Kaggle M5 benchmark computes cosine similarity by multiplying normalized
FP16 query and passage tensors.  Chroma HNSW is suitable for ordinary serving,
but its approximate tail can change min/max weighted fusion.  This adapter
replays the frozen M5 vector branch without mutating Chroma, the Stage C shards,
or any serving pointer.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from pathlib import Path
from threading import RLock, Semaphore
from time import perf_counter
from typing import Any

import numpy as np


class StageCExactIndexError(RuntimeError):
    """Raised when the immutable exact-vector contract cannot be verified."""


def _search_gate_from_environment() -> Semaphore:
    """Return a bounded read gate for exact matrix searches.

    Exact search is a large, read-only matrix multiplication.  The previous
    implementation used one process-wide ``RLock`` and therefore turned a
    burst of independent requests into a serial queue.  Keep the safe,
    deterministic default of one reader; staging can opt into a bounded
    value after measuring the host (for example, two readers on a four-core
    CPU) without changing ranking or lifecycle semantics.
    """

    try:
        configured = int(os.getenv("LEGAL_RETRIEVAL_EXACT_MAX_CONCURRENCY") or "1")
    except (TypeError, ValueError):
        configured = 1
    return Semaphore(max(1, min(configured, 16)))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ordinal(value: Any, fallback: str) -> int:
    return date.fromisoformat(str(value or fallback)[:10]).toordinal()


class StageCExactVectorIndex:
    """Checksum-bound FP16 matrix index compatible with the frozen M5 worker."""

    def __init__(
        self,
        *,
        manifest_path: str | Path,
        sqlite_connection: Any,
        expected_release_id: str,
        expected_source_snapshot_sha256: str,
        expected_model_fingerprint: str,
        expected_embedding_recipe_fingerprint: str,
        device: str = "cuda",
    ) -> None:
        import torch

        self.manifest_path = Path(manifest_path).resolve()
        if not self.manifest_path.is_file():
            raise StageCExactIndexError("stage_c_vector_manifest_missing")
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))
        if payload.get("schema_version") != "legal-retrieval-kaggle-output-v1":
            raise StageCExactIndexError("stage_c_vector_manifest_schema_invalid")
        checks = (
            ("release_id", expected_release_id),
            ("source_snapshot_sha256", expected_source_snapshot_sha256),
            ("model_artifact_fingerprint", expected_model_fingerprint),
            (
                "embedding_recipe_fingerprint",
                expected_embedding_recipe_fingerprint,
            ),
        )
        for key, expected in checks:
            if not expected or str(payload.get(key) or "") != str(expected):
                raise StageCExactIndexError(f"stage_c_vector_{key}_mismatch")

        requested_device = str(device or "cuda").strip().casefold()
        if requested_device not in {"cuda", "cpu"}:
            raise StageCExactIndexError("stage_c_exact_device_invalid")
        if requested_device == "cuda" and not torch.cuda.is_available():
            raise StageCExactIndexError("stage_c_exact_cuda_unavailable")
        self.device = torch.device(requested_device)
        self.dtype = torch.float16
        self.embedding_dimension = int(payload.get("embedding_dimension") or 0)
        self.vector_count = int(
            payload.get("vector_count") or payload.get("chunk_count") or 0
        )
        shards = list(payload.get("shards") or [])
        if self.embedding_dimension <= 0 or self.vector_count <= 0 or not shards:
            raise StageCExactIndexError("stage_c_vector_manifest_incomplete")
        if sum(int(item.get("count") or 0) for item in shards) != self.vector_count:
            raise StageCExactIndexError("stage_c_vector_shard_count_mismatch")

        metadata: dict[str, tuple[int, int, int]] = {}
        metadata_identifiers: list[str] = []
        for row in sqlite_connection.execute(
            "SELECT chunk_revision_id,document_serving_state,effective_from,effective_to FROM chunks"
        ):
            identifier = str(row["chunk_revision_id"])
            if identifier in metadata:
                raise StageCExactIndexError("stage_c_vector_duplicate_metadata_id")
            state = (
                1
                if str(row["document_serving_state"]) == "current_retrievable"
                else 2
            )
            metadata[identifier] = (
                state,
                _ordinal(row["effective_from"], "0001-01-01"),
                _ordinal(row["effective_to"], "9999-12-31"),
            )
            metadata_identifiers.append(identifier)
        if len(metadata) != self.vector_count:
            raise StageCExactIndexError("stage_c_vector_metadata_count_mismatch")

        started = perf_counter()
        corpus = torch.empty(
            (self.vector_count, self.embedding_dimension),
            dtype=self.dtype,
            device=self.device,
        )
        states = np.empty(self.vector_count, dtype=np.int8)
        starts = np.empty(self.vector_count, dtype=np.int32)
        ends = np.empty(self.vector_count, dtype=np.int32)
        identifiers: list[str] = []
        seen: set[str] = set()
        offset = 0
        for shard in shards:
            name = str(shard.get("path") or "")
            path = (self.manifest_path.parent / name).resolve()
            if path.parent != self.manifest_path.parent or not path.is_file():
                raise StageCExactIndexError(f"stage_c_vector_shard_missing:{name}")
            if _file_sha256(path) != str(shard.get("sha256") or ""):
                raise StageCExactIndexError(f"stage_c_vector_shard_checksum_mismatch:{name}")
            with np.load(path, allow_pickle=False) as part:
                shard_ids = part["ids"].astype(str).tolist()
                vectors = part["vectors"].astype(np.float32, copy=False)
            count = len(shard_ids)
            if (
                count != int(shard.get("count") or 0)
                or vectors.shape != (count, self.embedding_dimension)
                or offset + count > self.vector_count
            ):
                raise StageCExactIndexError(f"stage_c_vector_shard_shape_mismatch:{name}")
            if any(identifier in seen for identifier in shard_ids):
                raise StageCExactIndexError("stage_c_vector_duplicate_id")
            missing = [identifier for identifier in shard_ids if identifier not in metadata]
            if missing:
                raise StageCExactIndexError(
                    f"stage_c_vector_hydration_mismatch:{missing[0]}"
                )
            seen.update(shard_ids)
            identifiers.extend(shard_ids)
            corpus[offset : offset + count].copy_(
                torch.from_numpy(vectors).to(self.device, dtype=self.dtype)
            )
            for local_index, identifier in enumerate(shard_ids):
                state, start, end = metadata[identifier]
                target = offset + local_index
                states[target] = state
                starts[target] = start
                ends[target] = end
            offset += count
        if offset != self.vector_count or len(seen) != self.vector_count:
            raise StageCExactIndexError("stage_c_vector_count_mismatch")

        self._identifiers = identifiers
        self._corpus = corpus
        self._states = torch.from_numpy(states).to(self.device)
        self._starts = torch.from_numpy(starts).to(self.device)
        self._ends = torch.from_numpy(ends).to(self.device)
        self._lock = RLock()
        self._search_gate = _search_gate_from_environment()
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        self.load_ms = round((perf_counter() - started) * 1000.0, 3)

    def search(
        self,
        query_vector: np.ndarray,
        *,
        top_k: int,
        temporal_scope: str,
        as_of: str,
    ) -> list[tuple[str, float]]:
        import torch

        if temporal_scope not in {"current", "historical"}:
            return []
        ordinal = date.fromisoformat(str(as_of)[:10]).toordinal()
        requested = min(max(1, int(top_k)), self.vector_count)
        # The corpus and lifecycle arrays are immutable after startup.  A
        # configurable, bounded read gate prevents the old process-wide
        # mutex from serializing every request while still protecting small
        # CPU hosts from unbounded concurrent matrix multiplies.
        with self._search_gate, torch.inference_mode():
            query = torch.from_numpy(
                np.asarray(query_vector, dtype=np.float32).reshape(1, -1)
            ).to(self.device, dtype=self.dtype)
            if query.shape[1] != self.embedding_dimension:
                raise StageCExactIndexError("stage_c_query_dimension_mismatch")
            scores = query @ self._corpus.T
            state_ok = self._states == 1 if temporal_scope == "current" else self._states >= 1
            valid = state_ok & (self._starts <= ordinal) & (self._ends > ordinal)
            scores.masked_fill_(~valid.reshape(1, -1), float("-inf"))
            values, indices = torch.topk(scores, k=requested, dim=1)
            if self.device.type == "cuda":
                torch.cuda.synchronize(self.device)
            value_list = values[0].float().cpu().tolist()
            index_list = indices[0].cpu().tolist()
        return [
            (self._identifiers[int(index)], float(score))
            for score, index in zip(value_list, index_list)
            if np.isfinite(score)
        ]

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "stage_c_exact",
            "device": str(self.device),
            "dtype": str(self.dtype).replace("torch.", ""),
            "vector_count": self.vector_count,
            "embedding_dimension": self.embedding_dimension,
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": _file_sha256(self.manifest_path),
            "load_ms": self.load_ms,
        }

    def close(self) -> None:
        import torch

        self._corpus = None
        self._states = None
        self._starts = None
        self._ends = None
        if self.device.type == "cuda":
            torch.cuda.empty_cache()


class MmapExactVectorIndex:
    """Load an immutable offline exact artifact without Chroma hydration.

    The artifact consists of NumPy ``.npy`` arrays.  NumPy maps the arrays
    directly from disk; only the one-time device transfer (when CUDA is used)
    is performed by this class.  This removes the very expensive Chroma
    ``collection.get`` startup loop while preserving the release/fingerprint
    and SQLite chunk-identity checks.
    """

    def __init__(
        self,
        *,
        manifest_path: str | Path,
        sqlite_connection: Any,
        expected_release_id: str,
        expected_source_snapshot_sha256: str,
        expected_model_fingerprint: str,
        expected_embedding_recipe_fingerprint: str,
        device: str = "cuda",
    ) -> None:
        import torch

        self.manifest_path = Path(manifest_path).resolve()
        if not self.manifest_path.is_file():
            raise StageCExactIndexError("offline_mmap_manifest_missing")
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))
        if payload.get("schema_version") != "legal-retrieval-offline-mmap-v1":
            raise StageCExactIndexError("offline_mmap_manifest_schema_invalid")
        for key, expected in (
            ("release_id", expected_release_id),
            ("source_snapshot_sha256", expected_source_snapshot_sha256),
            ("model_artifact_fingerprint", expected_model_fingerprint),
            ("embedding_recipe_fingerprint", expected_embedding_recipe_fingerprint),
        ):
            if not expected or str(payload.get(key) or "") != str(expected):
                raise StageCExactIndexError(f"offline_mmap_{key}_mismatch")
        requested_device = str(device or "cuda").strip().casefold()
        if requested_device not in {"cuda", "cpu"}:
            raise StageCExactIndexError("stage_c_exact_device_invalid")
        if requested_device == "cuda" and not torch.cuda.is_available():
            raise StageCExactIndexError("stage_c_exact_cuda_unavailable")
        self.device = torch.device(requested_device)
        self.dtype = torch.float16
        self.vector_count = int(payload.get("vector_count") or 0)
        self.embedding_dimension = int(payload.get("embedding_dimension") or 0)
        if self.vector_count <= 0 or self.embedding_dimension <= 0:
            raise StageCExactIndexError("offline_mmap_manifest_incomplete")

        def _array(name: str, dtype: Any) -> np.ndarray:
            spec = payload.get(name) or {}
            relative = str(spec.get("path") or "")
            path = (self.manifest_path.parent / relative).resolve()
            if path.parent != self.manifest_path.parent or not path.is_file():
                raise StageCExactIndexError(f"offline_mmap_array_missing:{name}")
            if _file_sha256(path) != str(spec.get("sha256") or ""):
                raise StageCExactIndexError(f"offline_mmap_array_checksum_mismatch:{name}")
            try:
                values = np.load(path, mmap_mode="r", allow_pickle=False)
            except Exception as exc:
                raise StageCExactIndexError(f"offline_mmap_array_invalid:{name}") from exc
            if values.dtype != np.dtype(dtype):
                raise StageCExactIndexError(f"offline_mmap_array_dtype_mismatch:{name}")
            return values

        vectors = _array("vectors", np.float16)
        identifiers = _array("ids", np.dtype(str(payload.get("id_dtype") or "U1")))
        states = _array("states", np.int8)
        starts = _array("starts", np.int32)
        ends = _array("ends", np.int32)
        if vectors.shape != (self.vector_count, self.embedding_dimension):
            raise StageCExactIndexError("offline_mmap_vector_shape_mismatch")
        if any(array.shape != (self.vector_count,) for array in (identifiers, states, starts, ends)):
            raise StageCExactIndexError("offline_mmap_metadata_shape_mismatch")
        id_list = [str(value) for value in identifiers.tolist()]
        if not all(id_list) or len(set(id_list)) != self.vector_count:
            raise StageCExactIndexError("offline_mmap_duplicate_or_empty_id")
        sqlite_ids = {str(row[0]) for row in sqlite_connection.execute("SELECT chunk_revision_id FROM chunks")}
        if sqlite_ids != set(id_list):
            missing = next(iter(sqlite_ids - set(id_list)), "")
            raise StageCExactIndexError(f"offline_mmap_sqlite_identity_mismatch:{missing}")
        if not np.isfinite(vectors).all():
            raise StageCExactIndexError("offline_mmap_non_finite_vector")
        if np.any(states < 1) or np.any(states > 2):
            raise StageCExactIndexError("offline_mmap_invalid_serving_state")

        started = perf_counter()
        # The CPU path keeps the memory-mapped array; CUDA performs one
        # deterministic transfer to the device used by the exact matmul.
        # NumPy correctly exposes the file-backed array as read-only.  The
        # exact path never writes to it; suppress PyTorch's advisory warning
        # rather than copying the full mmap on CPU startup.
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="The given NumPy array is not writable.*",
                category=UserWarning,
            )
            self._corpus = torch.from_numpy(vectors)
        if self.device.type == "cuda":
            self._corpus = self._corpus.to(self.device, dtype=self.dtype)
        self._states = torch.from_numpy(states)
        self._starts = torch.from_numpy(starts)
        self._ends = torch.from_numpy(ends)
        if self.device.type == "cuda":
            self._states = self._states.to(self.device)
            self._starts = self._starts.to(self.device)
            self._ends = self._ends.to(self.device)
        self._identifiers = id_list
        self._lock = RLock()
        self._search_gate = _search_gate_from_environment()
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        self.load_ms = round((perf_counter() - started) * 1000.0, 3)

    def search(self, query_vector: np.ndarray, *, top_k: int, temporal_scope: str, as_of: str) -> list[tuple[str, float]]:
        import torch

        if temporal_scope not in {"current", "historical"}:
            return []
        ordinal = date.fromisoformat(str(as_of)[:10]).toordinal()
        requested = min(max(1, int(top_k)), self.vector_count)
        # Mmap arrays are read-only after validation; use the bounded read
        # gate rather than serializing all requests behind one mutex.
        with self._search_gate, torch.inference_mode():
            query = torch.from_numpy(np.asarray(query_vector, dtype=np.float32).reshape(1, -1)).to(self.device, dtype=self.dtype)
            if query.shape[1] != self.embedding_dimension:
                raise StageCExactIndexError("stage_c_query_dimension_mismatch")
            scores = query @ self._corpus.T
            state_ok = self._states == 1 if temporal_scope == "current" else self._states >= 1
            valid = state_ok & (self._starts <= ordinal) & (self._ends > ordinal)
            scores.masked_fill_(~valid.reshape(1, -1), float("-inf"))
            values, indices = torch.topk(scores, k=requested, dim=1)
            if self.device.type == "cuda":
                torch.cuda.synchronize(self.device)
            value_list = values[0].float().cpu().tolist()
            index_list = indices[0].cpu().tolist()
        return [(self._identifiers[int(index)], float(score)) for score, index in zip(value_list, index_list) if np.isfinite(score)]

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "offline_mmap_exact",
            "device": str(self.device),
            "dtype": str(self.dtype).replace("torch.", ""),
            "vector_count": self.vector_count,
            "embedding_dimension": self.embedding_dimension,
            "manifest_path": str(self.manifest_path),
            "manifest_sha256": _file_sha256(self.manifest_path),
            "load_ms": self.load_ms,
        }

    def close(self) -> None:
        import torch

        self._corpus = None
        self._states = None
        self._starts = None
        self._ends = None
        if self.device.type == "cuda":
            torch.cuda.empty_cache()


class VerifiedChromaExactVectorIndex(StageCExactVectorIndex):
    """Exact matrix view of a manifest-verified immutable Chroma collection."""

    def __init__(
        self,
        *,
        collection: Any,
        sqlite_connection: Any,
        expected_release_id: str,
        expected_source_snapshot_sha256: str,
        expected_vector_count: int,
        device: str = "cuda",
        batch_size: int = 10_000,
    ) -> None:
        import torch

        values = dict(collection.metadata or {})
        if str(values.get("release_id") or "") != expected_release_id:
            raise StageCExactIndexError("exact_chroma_release_id_mismatch")
        if (
            str(values.get("source_snapshot_sha256") or "")
            != expected_source_snapshot_sha256
        ):
            raise StageCExactIndexError("exact_chroma_source_snapshot_mismatch")
        observed_count = int(collection.count())
        if observed_count != int(expected_vector_count):
            raise StageCExactIndexError("exact_chroma_vector_count_mismatch")
        requested_device = str(device or "cuda").strip().casefold()
        if requested_device not in {"cuda", "cpu"}:
            raise StageCExactIndexError("stage_c_exact_device_invalid")
        if requested_device == "cuda" and not torch.cuda.is_available():
            raise StageCExactIndexError("stage_c_exact_cuda_unavailable")
        self.device = torch.device(requested_device)
        self.dtype = torch.float16
        self.vector_count = observed_count
        self.manifest_path = None
        self.collection_name = str(collection.name)

        metadata: dict[str, tuple[int, int, int]] = {}
        metadata_identifiers: list[str] = []
        for row in sqlite_connection.execute(
            "SELECT chunk_revision_id,document_serving_state,effective_from,effective_to FROM chunks"
        ):
            identifier = str(row["chunk_revision_id"])
            if identifier in metadata:
                raise StageCExactIndexError("stage_c_vector_duplicate_metadata_id")
            state = (
                1
                if str(row["document_serving_state"]) == "current_retrievable"
                else 2
            )
            metadata[identifier] = (
                state,
                _ordinal(row["effective_from"], "0001-01-01"),
                _ordinal(row["effective_to"], "9999-12-31"),
            )
            metadata_identifiers.append(identifier)
        if len(metadata) != self.vector_count:
            raise StageCExactIndexError("stage_c_vector_metadata_count_mismatch")

        started = perf_counter()
        corpus = None
        states = np.empty(self.vector_count, dtype=np.int8)
        starts = np.empty(self.vector_count, dtype=np.int32)
        ends = np.empty(self.vector_count, dtype=np.int32)
        identifiers: list[str] = []
        seen: set[str] = set()
        offset = 0
        while offset < self.vector_count:
            requested_ids = metadata_identifiers[
                offset : offset
                + min(max(1, int(batch_size)), self.vector_count - offset)
            ]
            response = collection.get(
                ids=requested_ids,
                include=["embeddings"],
            )
            shard_ids = [str(value) for value in (response.get("ids") or [])]
            raw_vectors = response.get("embeddings")
            vectors = np.asarray(raw_vectors, dtype=np.float32)
            count = len(shard_ids)
            if (
                count != len(requested_ids)
                or vectors.ndim != 2
                or vectors.shape[0] != count
            ):
                raise StageCExactIndexError("exact_chroma_batch_invalid")
            if len(set(shard_ids)) != count:
                raise StageCExactIndexError("stage_c_vector_duplicate_id")
            returned = {
                identifier: vectors[index]
                for index, identifier in enumerate(shard_ids)
            }
            if set(returned) != set(requested_ids):
                missing = next(
                    (identifier for identifier in requested_ids if identifier not in returned),
                    None,
                )
                raise StageCExactIndexError(
                    f"stage_c_vector_hydration_mismatch:{missing or shard_ids[0]}"
                )
            vectors = np.stack(
                [returned[identifier] for identifier in requested_ids], axis=0
            )
            shard_ids = requested_ids
            if corpus is None:
                self.embedding_dimension = int(vectors.shape[1])
                corpus = torch.empty(
                    (self.vector_count, self.embedding_dimension),
                    dtype=self.dtype,
                    device=self.device,
                )
            if vectors.shape[1] != self.embedding_dimension:
                raise StageCExactIndexError("exact_chroma_dimension_mismatch")
            if any(identifier in seen for identifier in shard_ids):
                raise StageCExactIndexError("stage_c_vector_duplicate_id")
            seen.update(shard_ids)
            identifiers.extend(shard_ids)
            corpus[offset : offset + count].copy_(
                torch.from_numpy(vectors).to(self.device, dtype=self.dtype)
            )
            for local_index, identifier in enumerate(shard_ids):
                state, start, end = metadata[identifier]
                target = offset + local_index
                states[target] = state
                starts[target] = start
                ends[target] = end
            offset += count
        if corpus is None or offset != self.vector_count or len(seen) != self.vector_count:
            raise StageCExactIndexError("stage_c_vector_count_mismatch")
        self._identifiers = identifiers
        self._corpus = corpus
        self._states = torch.from_numpy(states).to(self.device)
        self._starts = torch.from_numpy(starts).to(self.device)
        self._ends = torch.from_numpy(ends).to(self.device)
        self._lock = RLock()
        self._search_gate = _search_gate_from_environment()
        if self.device.type == "cuda":
            torch.cuda.synchronize(self.device)
        self.load_ms = round((perf_counter() - started) * 1000.0, 3)

    def stats(self) -> dict[str, Any]:
        return {
            "backend": "verified_chroma_exact",
            "device": str(self.device),
            "dtype": str(self.dtype).replace("torch.", ""),
            "vector_count": self.vector_count,
            "embedding_dimension": self.embedding_dimension,
            "collection": self.collection_name,
            "load_ms": self.load_ms,
        }
