#!/usr/bin/env python3
"""Verify fresh V2 vectors, deterministic replay and optional CPU/GPU parity.

The command is read-only with respect to Chroma and PostgreSQL.  It samples
deterministic eligible chunks, re-encodes their passages with the pinned
VNLegal-LAL recipe, and compares the result with persisted vectors.  A vector
build report is accepted only when the collection and manifest fingerprints
match and all sampled IDs are present.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Iterable

import chromadb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.build_retrieval_release_v2_shadow import (
    EMBEDDING_DIMENSION,
    _vector_content_sha_stream,
)

DEFAULT_MANIFEST = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-chunk-manifest-v2-approved-passage-v3.json"
DEFAULT_REPORT = ROOT / "reports" / "retrieval-release-v2" / "legal-retrieval-v2-current-shadow.report.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "current-vector-verification.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
EMBEDDING_MAX_LENGTH = 512


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def _cosine(left: Iterable[float], right: Iterable[float]) -> float:
    lhs = [float(value) for value in left]
    rhs = [float(value) for value in right]
    if len(lhs) != len(rhs) or not lhs:
        raise RuntimeError("vector_dimension_mismatch")
    dot = sum(a * b for a, b in zip(lhs, rhs))
    left_norm = math.sqrt(sum(value * value for value in lhs))
    right_norm = math.sqrt(sum(value * value for value in rhs))
    if left_norm == 0 or right_norm == 0:
        raise RuntimeError("zero_vector")
    return dot / (left_norm * right_norm)


def _sample_rows(payload: dict[str, Any], *, state: str, limit: int) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in payload.get("chunks") or []
        if row.get("eligible") is True
        and row.get("serving_state") == "retrievable"
        and (state == "all" or row.get("document_serving_state") == state)
    ]
    rows.sort(key=lambda row: str(row.get("chunk_revision_id") or ""))
    return rows[: max(1, int(limit))]


def _encode_with_device(
    rows: list[dict[str, Any]],
    *,
    device: str,
    batch_size: int,
    max_length: int = EMBEDDING_MAX_LENGTH,
) -> list[list[float]]:
    os.environ["LEGAL_EMBED_DEVICE"] = device
    from scripts.legal_search_server import LegalRetriever

    retriever = LegalRetriever()
    vectors = retriever.encode_passages(
        [str(row.get("embedding_text") or row["content"]) for row in rows],
        batch_size=batch_size,
        max_length=max_length,
    )
    result = [[float(value) for value in vector] for vector in vectors]
    del retriever
    gc.collect()
    if device == "cuda":
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
    return result


def _validate_collection_metadata(
    collection: Any,
    identifiers: list[str],
    *,
    release_id: str,
    source_snapshot_sha256: str,
    provisional_staging: bool = False,
    batch_size: int = 512,
) -> None:
    """Validate release metadata for every persisted vector in bounded batches."""

    for start in range(0, len(identifiers), max(1, int(batch_size))):
        requested = identifiers[start:start + max(1, int(batch_size))]
        response = collection.get(ids=requested, include=["metadatas"])
        observed_ids = [str(item) for item in (response.get("ids") or [])]
        metadatas = response.get("metadatas") or []
        by_id = {
            identifier: (metadata or {})
            for identifier, metadata in zip(observed_ids, metadatas)
        }
        if set(by_id) != set(requested) or len(metadatas) != len(requested):
            raise RuntimeError("vector_metadata_identity_mismatch")
        for identifier in requested:
            metadata = by_id[identifier]
            if str(metadata.get("release_id") or "") != release_id:
                raise RuntimeError(f"vector_metadata_release_mismatch:{identifier}")
            if str(metadata.get("source_snapshot_sha256") or "") != source_snapshot_sha256:
                raise RuntimeError(f"vector_metadata_snapshot_mismatch:{identifier}")
            if str(metadata.get("serving_state") or "") != "retrievable":
                raise RuntimeError(f"vector_metadata_serving_state_mismatch:{identifier}")
            if provisional_staging and str(metadata.get("provisional_staging") or "") != "true":
                raise RuntimeError(f"vector_metadata_provisional_marker_mismatch:{identifier}")


def verify(
    *,
    manifest_path: Path,
    vector_report_path: Path,
    chroma_path: Path,
    collection_name: str,
    output: Path,
    state: str,
    sample_size: int,
    batch_size: int,
    run_cpu_gpu_parity: bool,
    allow_provisional_staging: bool = False,
) -> dict[str, Any]:
    manifest = _load(manifest_path)
    if manifest.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
        raise RuntimeError("v2_manifest_required")
    provisional = manifest.get("approved") is not True
    if provisional and not allow_provisional_staging:
        raise RuntimeError("approved_v2_manifest_required")
    if provisional and (
        manifest.get("legal_review_attestation") is not False
        or not str(manifest.get("approval_blocker") or "").strip()
    ):
        raise RuntimeError("provisional_manifest_review_marker_required")
    report = _load(vector_report_path)
    if report.get("valid") is not True:
        raise RuntimeError("vector_build_report_not_valid")
    if report.get("manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("vector_report_manifest_mismatch")
    if report.get("release_id") != manifest.get("release_id"):
        raise RuntimeError("vector_report_release_mismatch")
    if bool(report.get("provisional_staging")) != provisional:
        raise RuntimeError("vector_report_provisional_marker_mismatch")
    expected_rows = _sample_rows(manifest, state=state, limit=sample_size)
    if len(expected_rows) < sample_size:
        raise RuntimeError(f"eligible_sample_too_small:{len(expected_rows)}<{sample_size}")

    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else ""
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(collection_name)
    expected_ids = sorted(
        str(row["chunk_revision_id"])
        for row in manifest.get("chunks") or []
        if row.get("eligible") is True
        and row.get("serving_state") == "retrievable"
        and (state == "all" or row.get("document_serving_state") == state)
    )
    observed_ids = sorted(str(item) for item in (collection.get(include=[]).get("ids") or []))
    missing_ids = sorted(set(expected_ids) - set(observed_ids))
    orphan_ids = sorted(set(observed_ids) - set(expected_ids))
    if missing_ids or orphan_ids or collection.count() != len(expected_ids):
        raise RuntimeError(
            "vector_collection_manifest_mismatch:"
            f"expected={len(expected_ids)}:actual={collection.count()}:"
            f"missing={missing_ids[:3]}:orphan={orphan_ids[:3]}"
        )
    _validate_collection_metadata(
        collection,
        expected_ids,
        release_id=str(manifest.get("release_id") or ""),
        source_snapshot_sha256=str(manifest.get("source_snapshot_sha256") or ""),
        provisional_staging=provisional,
    )
    persisted_vector_sha, persisted_vector_count = _vector_content_sha_stream(
        collection,
        expected_dimension=EMBEDDING_DIMENSION,
    )
    expected_vector_sha = str(report.get("vector_content_sha256") or "")
    if not expected_vector_sha:
        raise RuntimeError("vector_report_content_sha_missing")
    if persisted_vector_sha != expected_vector_sha:
        raise RuntimeError("vector_content_sha_mismatch")
    if persisted_vector_count != len(expected_ids):
        raise RuntimeError("vector_content_count_mismatch")
    observed = collection.get(
        ids=[str(row["chunk_revision_id"]) for row in expected_rows],
        include=["embeddings", "metadatas"],
    )
    observed_by_id = {
        str(identifier): (vector, metadata or {})
        for identifier, vector, metadata in zip(
            observed.get("ids") or [],
            observed.get("embeddings") or [],
            observed.get("metadatas") or [],
        )
    }
    missing = [str(row["chunk_revision_id"]) for row in expected_rows if str(row["chunk_revision_id"]) not in observed_by_id]
    if missing:
        raise RuntimeError(f"vector_sample_ids_missing:{missing[:5]}")
    replayed = _encode_with_device(
        expected_rows,
        device="cuda",
        batch_size=batch_size,
        max_length=EMBEDDING_MAX_LENGTH,
    )
    cosine_values = [
        _cosine(replayed[index], observed_by_id[str(row["chunk_revision_id"])][0])
        for index, row in enumerate(expected_rows)
    ]
    parity = None
    if run_cpu_gpu_parity:
        cpu_vectors = _encode_with_device(
            expected_rows,
            device="cpu",
            batch_size=batch_size,
            max_length=EMBEDDING_MAX_LENGTH,
        )
        parity_values = [_cosine(replayed[index], cpu_vectors[index]) for index in range(len(expected_rows))]
        parity = {
            "sample_count": len(parity_values),
            "minimum_cosine": min(parity_values),
            "median_cosine": sorted(parity_values)[len(parity_values) // 2],
            "threshold": 0.995,
            "passed": min(parity_values) >= 0.995,
        }
    pointer_after = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else ""
    result: dict[str, Any] = {
        "schema_version": "legal-retrieval-vector-verification-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_file_sha256": file_sha256(manifest_path),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "vector_report_file_sha256": file_sha256(vector_report_path),
        "release_id": manifest.get("release_id"),
        "collection_name": collection_name,
        "state": state,
        "embedding_max_length": EMBEDDING_MAX_LENGTH,
        "embedding_dimension": EMBEDDING_DIMENSION,
        "collection_count": collection.count(),
        "expected_vector_count": len(expected_ids),
        "missing_vector_ids": missing_ids,
        "orphan_vector_ids": orphan_ids,
        "persisted_vector_count_hashed": persisted_vector_count,
        "persisted_vector_content_sha256": persisted_vector_sha,
        "sample_count": len(expected_rows),
        "replay_cosine": {
            "minimum": min(cosine_values),
            "median": sorted(cosine_values)[len(cosine_values) // 2],
            "threshold": 0.999,
            "passed": min(cosine_values) >= 0.999,
        },
        "cpu_gpu_parity": parity,
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_changed": pointer_before != pointer_after,
        "database_mutated": False,
        "vector_collections_mutated": False,
    }
    result["status"] = (
        "PROVISIONAL_PASS"
        if provisional and result["replay_cosine"]["passed"] and not result["active_pointer_changed"] and (parity is None or parity["passed"])
        else "PASS"
        if result["replay_cosine"]["passed"] and not result["active_pointer_changed"] and (parity is None or parity["passed"])
        else "FAIL"
    )
    result["provisional_staging"] = provisional
    result["release_eligible"] = not provisional
    result["report_sha256"] = canonical_sha256(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{file_sha256(output)}  {output.name}\n", encoding="ascii")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--vector-report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--state", choices=("all", "current_retrievable", "historical_only"), default="current_retrievable")
    parser.add_argument("--sample-size", type=int, default=500)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--run-cpu-gpu-parity", action="store_true")
    parser.add_argument("--allow-provisional-staging", action="store_true")
    args = parser.parse_args(argv)
    manifest_path = args.manifest.resolve()
    vector_report_path = args.vector_report.resolve()
    output_path = args.output.resolve()
    try:
        result = verify(
            manifest_path=manifest_path,
            vector_report_path=vector_report_path,
            chroma_path=args.chroma_path.resolve(),
            collection_name=args.collection,
            output=output_path,
            state=args.state,
            sample_size=args.sample_size,
            batch_size=args.batch_size,
            run_cpu_gpu_parity=args.run_cpu_gpu_parity,
            allow_provisional_staging=args.allow_provisional_staging,
        )
    except Exception as exc:
        # A missing approval, collection or integrity report is a release
        # blocker, not a reason to emit an unverifiable PASS/traceback only.
        result = {
            "schema_version": "legal-retrieval-vector-verification-v2",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "BLOCKED",
            "reason": str(exc),
            "manifest_path": str(manifest_path),
            "manifest_file_sha256": file_sha256(manifest_path) if manifest_path.is_file() else None,
            "vector_report_path": str(vector_report_path),
            "collection_name": args.collection,
            "state": args.state,
            "database_mutated": False,
            "vector_collections_mutated": False,
            "active_pointer_changed": False,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output_path.with_suffix(output_path.suffix + ".sha256").write_text(
            f"{file_sha256(output_path)}  {output_path.name}\n", encoding="ascii"
        )
        print(json.dumps({"status": result["status"], "output": str(output_path), "reason": result["reason"]}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "output": str(args.output.resolve()), "replay_cosine": result["replay_cosine"]}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
