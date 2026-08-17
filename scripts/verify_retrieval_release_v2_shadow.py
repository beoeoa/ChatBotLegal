#!/usr/bin/env python3
"""Verify a V2 shadow collection against its approved chunk manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any

import chromadb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256


def _vectors(collection: Any, ids: list[str], batch_size: int = 1024) -> tuple[list[str], list[list[float]]]:
    observed_ids: list[str] = []
    observed_vectors: list[list[float]] = []
    for start in range(0, len(ids), batch_size):
        response = collection.get(ids=ids[start:start + batch_size], include=["embeddings", "metadatas"])
        observed_ids.extend(str(item) for item in response.get("ids") or [])
        observed_vectors.extend(
            [[float(value) for value in vector] for vector in response.get("embeddings") or []]
        )
    return observed_ids, observed_vectors


def _metadata(collection: Any, ids: list[str], batch_size: int = 5000) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), batch_size):
        batch_ids = ids[start:start + batch_size]
        response = collection.get(ids=batch_ids, include=["metadatas"])
        for identifier, metadata in zip(response.get("ids") or [], response.get("metadatas") or []):
            values[str(identifier)] = dict(metadata or {})
    return values


def verify(*, manifest_path: Path, chroma_path: Path, collection_name: str, output: Path, document_state: str) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("schema_version") != "legal-retrieval-chunk-manifest-v2" or manifest.get("approved") is not True:
        raise RuntimeError("approved_v2_chunk_manifest_required")
    if document_state not in {"all", "current_retrievable", "historical_only"}:
        raise ValueError("invalid_document_state")
    expected_rows = [
        row for row in manifest.get("chunks") or []
        if row.get("eligible") is True
        and (
            document_state == "all"
            or str(row.get("document_serving_state") or "") == document_state
        )
    ]
    expected_ids = sorted(str(row["chunk_revision_id"]) for row in expected_rows)
    expected_set = set(expected_ids)
    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else ""
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(collection_name)
    observed_all = sorted(str(item) for item in collection.get(include=[]).get("ids") or [])
    observed_set = set(observed_all)
    ids, vectors = _vectors(collection, expected_ids)
    missing = sorted(expected_set - observed_set)
    orphan = sorted(observed_set - expected_set)
    invalid = []
    norms = []
    for identifier, vector in zip(ids, vectors):
        if not vector or not all(math.isfinite(value) for value in vector):
            invalid.append(identifier)
            continue
        norm = math.sqrt(sum(value * value for value in vector))
        norms.append(norm)
        if not 0.999 <= norm <= 1.001:
            invalid.append(identifier)
    canonical_vectors = [
        {"id": identifier, "vector": vector}
        for identifier, vector in sorted(zip(ids, vectors), key=lambda item: item[0])
    ]
    metadata_by_id = _metadata(collection, expected_ids)
    metadata_failures = []
    for identifier in expected_ids:
        values = metadata_by_id.get(identifier) or {}
        if values.get("release_id") != manifest.get("release_id") or values.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256"):
            metadata_failures.append(identifier)
    pointer_after = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else ""
    pointer_changed = pointer_before != pointer_after
    report = {
        "schema_version": "legal-retrieval-shadow-verification-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_file_sha256": file_sha256(manifest_path),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "release_id": manifest.get("release_id"),
        "source_snapshot_sha256": manifest.get("source_snapshot_sha256"),
        "collection_name": collection_name,
        "document_state": document_state,
        "expected_count": len(expected_ids),
        "actual_count": len(observed_all),
        "expected_vector_count": len(expected_ids),
        "actual_vector_count": len(observed_all),
        "missing_ids": missing,
        "orphan_ids": orphan,
        "invalid_or_bad_norm_ids": sorted(set(invalid)),
        "metadata_failures": sorted(set(metadata_failures)),
        "norm_min": min(norms) if norms else None,
        "norm_max": max(norms) if norms else None,
        "vector_content_sha256": canonical_sha256(canonical_vectors),
        "valid": (
            not missing and not orphan and not invalid and not metadata_failures
            and len(observed_all) == len(expected_ids)
            and not pointer_changed
        ),
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_unchanged": not pointer_changed,
        "mutation": {"collection_mutated": False, "active_pointer_changed": pointer_changed},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument(
        "--document-state",
        choices=("all", "current_retrievable", "historical_only"),
        default="all",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(
        manifest_path=args.manifest.resolve(),
        chroma_path=args.chroma_path.resolve(),
        collection_name=args.collection,
        output=args.output.resolve(),
        document_state=args.document_state,
    )
    print(json.dumps({"status": "PASS" if report["valid"] else "FAIL", **{key: report[key] for key in ("expected_count", "actual_count", "vector_content_sha256")}}, ensure_ascii=False))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
