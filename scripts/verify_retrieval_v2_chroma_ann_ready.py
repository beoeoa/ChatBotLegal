"""Verify that Retrieval V2 shadow collections have usable persisted ANN indexes.

Count-only Chroma checks are insufficient: records can exist in SQLite while
the HNSW segment has never been synchronized.  This verifier checks the
persisted segment, bounds the replay tail, opens the collections in a fresh
process, and executes cold/warm vector queries.  It is read-only and also
asserts that the active pointer remains unchanged.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import pickle
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

import chromadb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import file_sha256, read_active_collection_pointer


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * q)))
    return float(ordered[index])


def _segment_id(chroma_path: Path, collection_name: str) -> str:
    import sqlite3

    db_path = chroma_path / "chroma.sqlite3"
    connection = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT s.id FROM segments s JOIN collections c ON c.id=s.collection "
            "WHERE c.name=? AND s.scope='VECTOR'",
            [collection_name],
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(f"vector_segment_missing:{collection_name}")
    return str(row[0])


def _persisted_count(metadata_path: Path) -> int:
    if not metadata_path.is_file():
        return 0
    with metadata_path.open("rb") as handle:
        value = pickle.load(handle)  # trusted local Chroma artifact
    mapping = getattr(value, "id_to_label", None)
    if isinstance(mapping, dict):
        return len(mapping)
    if isinstance(value, dict):
        for key in ("id_to_label", "label_to_id"):
            if isinstance(value.get(key), dict):
                return len(value[key])
    total = getattr(value, "total_elements_added", None)
    return int(total or 0)


def _collection_check(
    *,
    client: Any,
    chroma_path: Path,
    collection_name: str,
    expected_count: int,
    release_id: str,
    source_snapshot_sha256: str,
    query_vectors: np.ndarray,
    allowed_unpersisted_tail: int,
    warm_runs: int,
    temporal: bool,
) -> dict[str, Any]:
    collection = client.get_collection(collection_name)
    actual_count = int(collection.count())
    metadata = dict(collection.metadata or {})
    segment_id = _segment_id(chroma_path, collection_name)
    segment_path = chroma_path / segment_id
    files = {
        name: (segment_path / name).stat().st_size if (segment_path / name).is_file() else 0
        for name in (
            "header.bin",
            "data_level0.bin",
            "length.bin",
            "link_lists.bin",
            "index_metadata.pickle",
        )
    }
    persisted_count = _persisted_count(segment_path / "index_metadata.pickle")
    replay_tail = max(0, actual_count - persisted_count)
    latencies: list[float] = []
    returned_ids: list[str] = []
    returned_metadata: list[dict[str, Any]] = []
    cold_started = perf_counter()
    cold = collection.query(
        query_embeddings=[query_vectors[0].tolist()],
        n_results=10,
        include=["metadatas", "distances"],
    )
    cold_ms = (perf_counter() - cold_started) * 1000
    returned_ids.extend(str(value) for value in (cold.get("ids") or [[]])[0])
    returned_metadata.extend(dict(value or {}) for value in (cold.get("metadatas") or [[]])[0])
    for run in range(max(1, int(warm_runs))):
        for vector in query_vectors:
            started = perf_counter()
            result = collection.query(
                query_embeddings=[vector.tolist()],
                n_results=10,
                include=["metadatas", "distances"],
            )
            latencies.append((perf_counter() - started) * 1000)
            returned_ids.extend(str(value) for value in (result.get("ids") or [[]])[0])
            returned_metadata.extend(dict(value or {}) for value in (result.get("metadatas") or [[]])[0])
    allowed_states = (
        {"current_retrievable", "historical_only"}
        if temporal
        else {"current_retrievable"}
    )
    invalid_returned_metadata = sum(
        1
        for item in returned_metadata
        if str(item.get("release_id") or "") != release_id
        or str(item.get("serving_state") or "") != "retrievable"
        or str(item.get("document_serving_state") or "") not in allowed_states
    )
    checks = {
        "count_matches": actual_count == expected_count,
        "release_id_matches": str(metadata.get("release_id") or "") == release_id,
        "source_snapshot_matches": str(metadata.get("source_snapshot_sha256") or "")
        == source_snapshot_sha256,
        "cosine_metric": str(metadata.get("hnsw:space") or "") == "cosine",
        "persisted_metadata_present": files["index_metadata.pickle"] > 0,
        "persisted_count_positive": persisted_count > 0,
        "persisted_data_sized": files["data_level0.bin"] >= persisted_count * 4096,
        "bounded_replay_tail": replay_tail <= allowed_unpersisted_tail,
        "query_returned_results": bool(returned_ids),
        "returned_metadata_release_scope_valid": invalid_returned_metadata == 0,
    }
    return {
        "collection": collection_name,
        "expected_count": expected_count,
        "actual_count": actual_count,
        "segment_id": segment_id,
        "segment_path": str(segment_path),
        "segment_files": files,
        "persisted_count": persisted_count,
        "unpersisted_replay_tail": replay_tail,
        "allowed_unpersisted_tail": allowed_unpersisted_tail,
        "cold_query_ms": round(cold_ms, 3),
        "warm_query_ms": {
            "sample_count": len(latencies),
            "p50": round(_quantile(latencies, 0.50), 3),
            "p95": round(_quantile(latencies, 0.95), 3),
            "max": round(max(latencies) if latencies else 0.0, 3),
        },
        "returned_id_sample": returned_ids[:10],
        "invalid_returned_metadata_count": invalid_returned_metadata,
        "checks": checks,
        "valid": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chroma-path", type=Path, required=True)
    parser.add_argument("--current-collection", required=True)
    parser.add_argument("--temporal-collection", required=True)
    parser.add_argument("--expected-current", type=int, required=True)
    parser.add_argument("--expected-temporal", type=int, required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--source-snapshot-sha256", required=True)
    parser.add_argument("--query-vector-shard", type=Path, required=True)
    parser.add_argument("--query-count", type=int, default=5)
    parser.add_argument("--warm-runs", type=int, default=3)
    parser.add_argument("--allowed-unpersisted-tail", type=int, default=5000)
    parser.add_argument("--expected-active-pointer", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    chroma_path = args.chroma_path.resolve()
    with np.load(args.query_vector_shard.resolve(), allow_pickle=False) as payload:
        vectors = np.asarray(payload["vectors"][: max(1, int(args.query_count))], dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[1] != 1024:
        raise SystemExit("query_vector_dimension_mismatch")
    pointer_before = read_active_collection_pointer(chroma_path)
    client = chromadb.PersistentClient(path=str(chroma_path))
    current = _collection_check(
        client=client,
        chroma_path=chroma_path,
        collection_name=args.current_collection,
        expected_count=args.expected_current,
        release_id=args.release_id,
        source_snapshot_sha256=args.source_snapshot_sha256,
        query_vectors=vectors,
        allowed_unpersisted_tail=args.allowed_unpersisted_tail,
        warm_runs=args.warm_runs,
        temporal=False,
    )
    temporal = _collection_check(
        client=client,
        chroma_path=chroma_path,
        collection_name=args.temporal_collection,
        expected_count=args.expected_temporal,
        release_id=args.release_id,
        source_snapshot_sha256=args.source_snapshot_sha256,
        query_vectors=vectors,
        allowed_unpersisted_tail=args.allowed_unpersisted_tail,
        warm_runs=args.warm_runs,
        temporal=True,
    )
    pointer_after = read_active_collection_pointer(chroma_path)
    pointer_ok = (
        pointer_before == args.expected_active_pointer
        and pointer_after == args.expected_active_pointer
    )
    valid = current["valid"] and temporal["valid"] and pointer_ok
    report = {
        "schema_version": "legal-retrieval-v2-chroma-ann-readiness-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if valid else "FAIL",
        "release_id": args.release_id,
        "source_snapshot_sha256": args.source_snapshot_sha256,
        "current": current,
        "temporal": temporal,
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_unchanged": pointer_ok,
        "activation_performed": False,
        "valid": valid,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
