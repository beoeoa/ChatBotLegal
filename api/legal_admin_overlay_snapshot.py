"""Atomic exact-vector snapshot for the mutable Admin Chroma overlay.

Chroma can expose a stale in-memory HNSW graph when one process writes an
incremental collection and another long-running process serves it.  The
overlay is intentionally small, so the writer also publishes a compact,
checksum-validated NumPy snapshot. Retrieval V2 reloads it by generation and
ranks the overlay exactly without reopening the immutable release indexes.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


class OverlaySnapshotCollection:
    """Read-only Chroma-compatible view of the atomic Admin overlay snapshot.

    Local Chroma collection handles can retain a stale segment when another
    process performs an approved import.  Retrieval uses this small exact view
    so restarts and cross-process reads observe the writer's committed snapshot.
    PostgreSQL still decides whether each document is allowed to be served.
    """

    def __init__(
        self,
        path: Path,
        *,
        collection_name: str,
        fallback: Any | None = None,
    ) -> None:
        self.path = Path(path)
        self.name = str(collection_name)
        self.fallback = fallback
        self._generation_ns: int | None = None
        self._snapshot: dict[str, Any] | None = None

    def _read(self) -> dict[str, Any] | None:
        try:
            generation = self.path.stat().st_mtime_ns
            if self._snapshot is None or generation != self._generation_ns:
                self._snapshot = load_overlay_snapshot(
                    self.path, expected_collection=self.name
                )
                self._generation_ns = generation
            return self._snapshot
        except Exception:
            return None

    def count(self) -> int:
        snapshot = self._read()
        if snapshot is not None:
            return int(snapshot.get("count") or 0)
        return int(self.fallback.count()) if self.fallback is not None else 0

    def get(
        self,
        *,
        ids: Sequence[str] | None = None,
        include: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        snapshot = self._read()
        if snapshot is None:
            if self.fallback is not None:
                return self.fallback.get(ids=ids, include=list(include or []), **kwargs)
            return {"ids": [], "metadatas": [], "embeddings": None}
        wanted = None if ids is None else {str(value) for value in ids}
        indexes = [
            index
            for index, value in enumerate(snapshot["ids"])
            if wanted is None or str(value) in wanted
        ]
        requested = set(include or [])
        result: dict[str, Any] = {
            "ids": [snapshot["ids"][index] for index in indexes]
        }
        if "metadatas" in requested:
            result["metadatas"] = [snapshot["metadatas"][index] for index in indexes]
        if "embeddings" in requested:
            result["embeddings"] = snapshot["embeddings"][indexes]
        return result

    def query(
        self,
        *,
        query_embeddings: Sequence[Sequence[float]],
        n_results: int,
        include: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, list[list[Any]]]:
        snapshot = self._read()
        if snapshot is None:
            if self.fallback is not None:
                return self.fallback.query(
                    query_embeddings=query_embeddings,
                    n_results=n_results,
                    include=list(include or []),
                    **kwargs,
                )
            return {"ids": [[]], "metadatas": [[]], "distances": [[]]}
        matrix = np.asarray(snapshot["embeddings"], dtype=np.float32)
        queries = np.asarray(query_embeddings, dtype=np.float32)
        if matrix.ndim != 2 or queries.ndim != 2 or not len(matrix):
            return {
                "ids": [[] for _ in queries],
                "metadatas": [[] for _ in queries],
                "distances": [[] for _ in queries],
            }
        matrix_norms = np.maximum(np.linalg.norm(matrix, axis=1), 1e-12)
        ids_rows: list[list[Any]] = []
        metadata_rows: list[list[Any]] = []
        distance_rows: list[list[Any]] = []
        for query in queries:
            denominator = matrix_norms * max(float(np.linalg.norm(query)), 1e-12)
            similarities = (matrix @ query) / denominator
            order = np.argsort(-similarities, kind="stable")[: max(1, int(n_results))]
            ids_rows.append([snapshot["ids"][int(index)] for index in order])
            metadata_rows.append(
                [snapshot["metadatas"][int(index)] for index in order]
            )
            distance_rows.append(
                [float(1.0 - similarities[int(index)]) for index in order]
            )
        return {
            "ids": ids_rows,
            "metadatas": metadata_rows,
            "distances": distance_rows,
        }


def reconcile_collection_from_snapshot(
    collection: Any,
    *,
    path: Path,
    collection_name: str,
) -> dict[str, Any]:
    """Restore snapshot-only vectors before the writer mutates the overlay."""

    try:
        snapshot = load_overlay_snapshot(path, expected_collection=collection_name)
    except FileNotFoundError:
        return {"status": "snapshot_missing", "restored": 0}
    ids = [str(value) for value in snapshot.get("ids") or []]
    if not ids:
        return {"status": "already_aligned", "restored": 0}
    present_payload = collection.get(ids=ids, include=[])
    present = {str(value) for value in (present_payload.get("ids") or [])}
    missing_indexes = [index for index, value in enumerate(ids) if value not in present]
    if not missing_indexes:
        return {"status": "already_aligned", "restored": 0}
    collection.upsert(
        ids=[ids[index] for index in missing_indexes],
        embeddings=np.asarray(snapshot["embeddings"], dtype=np.float32)[
            missing_indexes
        ].tolist(),
        metadatas=[snapshot["metadatas"][index] for index in missing_indexes],
    )
    return {"status": "restored", "restored": len(missing_indexes)}


def default_overlay_snapshot_path(chroma_path: Path) -> Path:
    configured = str(os.getenv("LEGAL_ADMIN_OVERLAY_SNAPSHOT") or "").strip()
    if configured:
        return Path(configured).resolve()
    return chroma_path.resolve().parent / "admin-overlay-exact-v1.npz"


def write_overlay_snapshot(
    collection: Any,
    *,
    path: Path,
    collection_name: str,
) -> dict[str, Any]:
    """Publish IDs, metadata and vectors from the writer's fresh Chroma view."""

    payload = collection.get(include=["embeddings", "metadatas"])
    ids = [str(value) for value in (payload.get("ids") or [])]
    metadatas = [dict(value or {}) for value in (payload.get("metadatas") or [])]
    raw_embeddings = payload.get("embeddings")
    matrix = (
        np.asarray(raw_embeddings, dtype=np.float32)
        if raw_embeddings is not None
        else np.empty((0, 0), dtype=np.float32)
    )
    # Chroma returns [] (a one-dimensional array) for an empty collection.
    # Publish an empty snapshot so deleting the last overlay document also
    # clears any previous snapshot in the serving process.
    if not ids and not metadatas and matrix.size == 0:
        matrix = np.empty((0, 0), dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != len(ids) or len(metadatas) != len(ids):
        raise RuntimeError("admin_overlay_snapshot_alignment_failed")
    if len(ids) != len(set(ids)):
        raise RuntimeError("admin_overlay_snapshot_duplicate_ids")

    metadata_json = [
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for value in metadatas
    ]
    fingerprint = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        np.savez_compressed(
            handle,
            schema_version=np.asarray(["legal-admin-overlay-exact-v1"]),
            collection_name=np.asarray([str(collection_name)]),
            ids=np.asarray(ids, dtype=np.str_),
            metadatas=np.asarray(metadata_json, dtype=np.str_),
            embeddings=matrix,
            ids_sha256=np.asarray([fingerprint]),
        )
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return {
        "status": "published",
        "path": str(path),
        "collection": str(collection_name),
        "count": len(ids),
        "dimension": int(matrix.shape[1]) if matrix.size else 0,
        "ids_sha256": fingerprint,
        "generation_ns": path.stat().st_mtime_ns,
    }


def load_overlay_snapshot(
    path: Path,
    *,
    expected_collection: str,
) -> dict[str, Any]:
    """Load and validate a published overlay snapshot without pickle."""

    with np.load(path, allow_pickle=False) as payload:
        schema = str(payload["schema_version"][0])
        collection = str(payload["collection_name"][0])
        ids = [str(value) for value in payload["ids"].tolist()]
        metadata_json = [str(value) for value in payload["metadatas"].tolist()]
        matrix = np.asarray(payload["embeddings"], dtype=np.float32)
        expected_hash = str(payload["ids_sha256"][0])
    if schema != "legal-admin-overlay-exact-v1":
        raise RuntimeError("admin_overlay_snapshot_schema_mismatch")
    if collection != str(expected_collection):
        raise RuntimeError("admin_overlay_snapshot_collection_mismatch")
    if matrix.ndim != 2 or matrix.shape[0] != len(ids) or len(metadata_json) != len(ids):
        raise RuntimeError("admin_overlay_snapshot_alignment_failed")
    observed_hash = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    if observed_hash != expected_hash:
        raise RuntimeError("admin_overlay_snapshot_checksum_mismatch")
    return {
        "ids": ids,
        "metadatas": [json.loads(value) for value in metadata_json],
        "embeddings": matrix,
        "count": len(ids),
        "dimension": int(matrix.shape[1]) if matrix.size else 0,
        "ids_sha256": observed_hash,
        "generation_ns": path.stat().st_mtime_ns,
    }
