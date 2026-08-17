"""Build a benchmark-only reordered copy of a candidate Chroma collection.

The vector set and metadata are copied byte-for-byte from the existing
candidate.  Only insertion order changes, which can materially affect HNSW
graph topology when document IDs are clustered by source.  The script never
deletes a collection, changes the active pointer, or mutates PostgreSQL.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
from typing import Any

import chromadb

from api.retrieval_release_contracts import require_staging_collection_target


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chroma-path", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260815)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--hnsw-m", type=int, default=0)
    parser.add_argument("--construction-ef", type=int, default=0)
    parser.add_argument("--search-ef", type=int, default=0)
    args = parser.parse_args()

    # Reordering is benchmark-only. Reject an alias of the active pointer
    # before opening or mutating the target collection.
    require_staging_collection_target(args.target, args.chroma_path)

    client = chromadb.PersistentClient(path=str(args.chroma_path.resolve()))
    source = client.get_collection(args.source)
    names = {item.name for item in client.list_collections()}
    target_metadata = {
        **dict(source.metadata or {}),
        "source_collection": args.source,
        "benchmark_only": "true",
        "reordered_hnsw": "true",
        "reorder_seed": str(args.seed),
    }
    if args.hnsw_m > 0:
        target_metadata["hnsw:M"] = int(args.hnsw_m)
    if args.construction_ef > 0:
        target_metadata["hnsw:construction_ef"] = int(args.construction_ef)
    if args.search_ef > 0:
        target_metadata["hnsw:search_ef"] = int(args.search_ef)
    if args.target in names:
        target = client.get_collection(args.target)
        if target.count():
            raise RuntimeError(f"target_collection_exists_nonempty:{args.target}")
    else:
        target = client.create_collection(args.target, metadata=target_metadata)
    ids = [str(value) for value in source.get(include=[]).get("ids") or []]
    if not ids:
        raise RuntimeError("source_collection_empty")
    random.Random(args.seed).shuffle(ids)
    copied = 0
    for start in range(0, len(ids), max(1, args.batch_size)):
        batch_ids = ids[start : start + args.batch_size]
        payload = source.get(ids=batch_ids, include=["embeddings", "metadatas"])
        payload_ids = list(payload.get("ids") or [])
        payload_embeddings = payload.get("embeddings")
        payload_metadatas = list(payload.get("metadatas") or [])
        target.upsert(
            ids=payload_ids,
            embeddings=payload_embeddings,
            metadatas=payload_metadatas,
        )
        copied += len(payload_ids)
    observed = {str(value) for value in target.get(include=[]).get("ids") or []}
    expected = set(ids)
    report: dict[str, Any] = {
        "schema_version": "legal-candidate-chroma-reordered-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_collection": args.source,
        "target_collection": args.target,
        "source_count": len(expected),
        "copied_count": copied,
        "target_count": target.count(),
        "exact_id_set_match": observed == expected and target.count() == len(expected),
        "missing_ids": sorted(expected - observed),
        "orphan_ids": sorted(observed - expected),
        "seed": args.seed,
        "active_pointer_changed": False,
        "baseline_collection_mutated": False,
        "benchmark_only": True,
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["exact_id_set_match"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
