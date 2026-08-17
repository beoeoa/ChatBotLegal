#!/usr/bin/env python3
"""Build a fresh V2 Chroma shadow collection from an approved chunk manifest.

This writer is deliberately separate from the legacy candidate builder: it
never reads embeddings from another collection and has no pointer-activation
path.  The chunk manifest must be produced by the approved V2 parser and legal
review workflow; a provisional inventory or a legacy chunk list is rejected.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import math
import sys
from time import perf_counter
from typing import Any

import chromadb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import (
    canonical_sha256,
    file_sha256,
    require_staging_collection_target,
)


DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_TARGET = "legal_chunks_retrieval_release_v2_shadow"
MAX_TOKENS = 512
EMBEDDING_MAX_LENGTH = 512
EMBEDDING_DIMENSION = 1024
ALLOWED_BATCHES = (32, 16, 8, 4, 1)


def _load_manifest(path: Path, *, allow_provisional_staging: bool = False) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("chunk_manifest_object_required")
    if payload.get("schema_version") != "legal-retrieval-chunk-manifest-v2":
        raise RuntimeError("v2_chunk_manifest_required")
    provisional = payload.get("approved") is not True
    if provisional and not allow_provisional_staging:
        raise RuntimeError("approved_v2_chunk_manifest_required")
    if provisional and (
        payload.get("legal_review_attestation") is not False
        or not str(payload.get("approval_blocker") or "").strip()
    ):
        raise RuntimeError("provisional_manifest_review_marker_required")
    if payload.get("source_snapshot_sha256") in (None, ""):
        raise RuntimeError("source_snapshot_fingerprint_required")
    if not provisional and payload.get("legal_review_attestation") is not True:
        raise RuntimeError("legal_review_attestation_required")
    for key in (
        "model_artifact_fingerprint", "tokenizer_fingerprint",
        "embedding_recipe_fingerprint", "passage_recipe_fingerprint",
        "splitter_fingerprint", "dependency_lock_fingerprint",
    ):
        if payload.get(key) in (None, ""):
            raise RuntimeError(f"manifest_fingerprint_required:{key}")
    chunks = payload.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise RuntimeError("v2_chunks_required")
    payload["provisional_staging"] = provisional
    return payload


def _validate_chunks(payload: dict[str, Any], *, document_state: str) -> list[dict[str, Any]]:
    if document_state not in {"all", "current_retrievable", "historical_only"}:
        raise ValueError("invalid_document_state")
    chunks = [
        dict(row)
        for row in payload["chunks"]
        if document_state == "all"
        or str(row.get("document_serving_state") or "") == document_state
    ]
    ids = [str(row.get("chunk_revision_id") or "") for row in chunks]
    if not all(ids):
        raise RuntimeError("chunk_revision_id_required")
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate_chunk_revision_id")
    for row in chunks:
        if row.get("eligible") is not True or row.get("serving_state") != "retrievable":
            raise RuntimeError(f"ineligible_chunk_in_build:{row.get('chunk_revision_id')}")
        document_serving_state = str(row.get("document_serving_state") or "")
        if document_serving_state not in {"current_retrievable", "historical_only"}:
            raise RuntimeError(
                f"invalid_document_serving_state_in_build:{row.get('chunk_revision_id')}"
            )
        if document_state == "current_retrievable" and document_serving_state != document_state:
            raise RuntimeError(f"current_collection_state_mismatch:{row.get('chunk_revision_id')}")
        content = str(row.get("content") or "").strip()
        if not content:
            raise RuntimeError(f"empty_chunk_in_build:{row.get('chunk_revision_id')}")
        token_count = int(row.get("token_count") or 0)
        if token_count <= 0 or token_count > MAX_TOKENS:
            raise RuntimeError(f"chunk_token_budget:{row.get('chunk_revision_id')}:{token_count}")
        required = (
            "document_id", "article_id", "passage_sha256", "source_content_sha256",
            "content_sha256", "embedding_text", "embedding_text_sha256",
            "release_id", "structural_path",
        )
        missing = [key for key in required if row.get(key) in (None, "")]
        if missing:
            raise RuntimeError(f"chunk_provenance_missing:{row.get('chunk_revision_id')}:{','.join(missing)}")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != str(row.get("content_sha256") or hashlib.sha256(content.encode("utf-8")).hexdigest()):
            raise RuntimeError(f"chunk_content_checksum_mismatch:{row.get('chunk_revision_id')}")
        embedding_text = str(row.get("embedding_text") or "")
        embedding_text_sha = str(row.get("embedding_text_sha256") or "")
        if hashlib.sha256(embedding_text.encode("utf-8")).hexdigest() != embedding_text_sha:
            raise RuntimeError(f"chunk_embedding_text_checksum_mismatch:{row.get('chunk_revision_id')}")
    return chunks


def _metadata(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    values = dict(row.get("metadata") or {})
    source_metadata = dict(row.get("metadata") or {})
    values.update({
        "chunk_revision_id": row["chunk_revision_id"],
        "document_id": int(row["document_id"]),
        "article_id": int(row["article_id"]),
        "release_id": row["release_id"],
        "source_snapshot_sha256": payload["source_snapshot_sha256"],
        "quality_policy_version": "legal-chunk-quality-v2",
        "serving_state": "retrievable",
        "document_serving_state": row.get("document_serving_state"),
        "effective_from": (
            source_metadata.get("article_effective_from")
            or source_metadata.get("effective_date")
        ),
        "effective_to": (
            source_metadata.get("article_effective_to")
            or source_metadata.get("expired_date")
        ),
    })
    return {str(key): value for key, value in values.items() if value not in (None, "")}


def _vector_content_sha(ids: list[str], vectors: list[list[float]]) -> str:
    pairs = []
    for identifier, vector in sorted(zip(ids, vectors), key=lambda item: item[0]):
        pairs.append({"id": identifier, "vector": [float(value) for value in vector]})
    return canonical_sha256(pairs)


def _vector_content_sha_stream(
    collection: Any,
    *,
    batch_size: int = 256,
    expected_dimension: int | None = None,
) -> tuple[str, int]:
    """Hash persisted vectors without materialising the whole collection.

    ``canonical_sha256([{"id": ..., "vector": ...}, ...])`` is defined by
    the release contract.  JSON arrays can be serialized incrementally, so we
    preserve that exact canonical representation while reading Chroma in
    bounded batches.  This is required for the 392k-chunk release: loading all
    Python float lists at once can exceed the GTX 1660 host memory budget.
    """

    identifiers = sorted(str(item) for item in (collection.get(include=[]).get("ids") or []))
    digest = hashlib.sha256()
    digest.update(b"[")
    written = 0
    for start in range(0, len(identifiers), max(1, int(batch_size))):
        requested = identifiers[start:start + max(1, int(batch_size))]
        response = collection.get(ids=requested, include=["embeddings"])
        observed_ids = [str(item) for item in (response.get("ids") or [])]
        embeddings = response.get("embeddings") or []
        by_id = {
            identifier: [float(value) for value in vector]
            for identifier, vector in zip(observed_ids, embeddings)
        }
        if set(by_id) != set(requested) or len(embeddings) != len(requested):
            missing = sorted(set(requested) - set(by_id))
            extra = sorted(set(by_id) - set(requested))
            raise RuntimeError(f"vector_batch_identity_mismatch:missing={missing[:3]}:extra={extra[:3]}")
        for identifier in requested:
            vector = by_id[identifier]
            if not vector or not all(math.isfinite(value) for value in vector):
                raise RuntimeError(f"non_finite_persisted_vector:{identifier}")
            if expected_dimension is not None and len(vector) != int(expected_dimension):
                raise RuntimeError(
                    f"persisted_vector_dimension_mismatch:{identifier}:{len(vector)}!={int(expected_dimension)}"
                )
            norm = math.sqrt(sum(value * value for value in vector))
            if norm == 0 or not 0.999 <= norm <= 1.001:
                raise RuntimeError(f"persisted_vector_norm_out_of_range:{identifier}:{norm}")
            if written:
                digest.update(b",")
            digest.update(json.dumps(
                {"id": identifier, "vector": vector},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8"))
            written += 1
    digest.update(b"]")
    return digest.hexdigest(), written


def _existing_ids(collection: Any, ids: list[str], *, batch_size: int = 5000) -> set[str]:
    existing: set[str] = set()
    for start in range(0, len(ids), batch_size):
        response = collection.get(ids=ids[start:start + batch_size], include=[])
        existing.update(str(item) for item in response.get("ids") or [])
    return existing


def _encode(
    retriever: Any,
    passages: list[str],
    batch_size: int,
    *,
    max_length: int = EMBEDDING_MAX_LENGTH,
) -> tuple[list[list[float]], int]:
    last_error: Exception | None = None
    for current in ALLOWED_BATCHES:
        if current > batch_size:
            continue
        try:
            vectors = retriever.encode_passages(
                passages,
                batch_size=current,
                max_length=max_length,
            )
            return [[float(value) for value in row] for row in vectors], current
        except (RuntimeError, MemoryError) as exc:
            last_error = exc
            continue
    raise RuntimeError("embedding_oom_or_failure_after_batch_fallback") from last_error


def _validate_resume_metadata(observed: dict[str, Any], expected: dict[str, Any]) -> None:
    if str(observed.get("benchmark_only") or "") != "true":
        raise RuntimeError("shadow_resume_benchmark_only_contract_missing")
    for key, value in expected.items():
        if str(observed.get(key) or "") != str(value):
            raise RuntimeError(f"shadow_resume_fingerprint_mismatch:{key}")


def build(*, manifest_path: Path, chroma_path: Path, target_name: str, output: Path, apply: bool, resume: bool, batch_size: int, document_state: str, allow_provisional_staging: bool = False) -> dict[str, Any]:
    payload = _load_manifest(
        manifest_path,
        allow_provisional_staging=allow_provisional_staging,
    )
    provisional = bool(payload.get("provisional_staging"))
    if provisional and "provisional" not in str(target_name).casefold():
        raise RuntimeError("provisional_target_name_required")
    chunks = _validate_chunks(payload, document_state=document_state)
    active_before = require_staging_collection_target(target_name, chroma_path) or ""
    pointer_path = chroma_path / "active_core_collection.txt"
    if not apply:
        return {
            "status": "PROVISIONAL_DRY_RUN" if provisional else "DRY_RUN",
            "target_collection": target_name,
            "expected_vector_count": len(chunks),
            "document_state": document_state,
            "active_pointer": active_before,
            "fresh_embedding_required": True,
            "legacy_vector_copy": False,
            "embedding_max_length": EMBEDDING_MAX_LENGTH,
            "provisional_staging": provisional,
            "release_eligible": not provisional,
        }

    from scripts.legal_search_server import retriever

    actual_model = str(getattr(retriever, "_model_fingerprint", "") or "")
    expected_model = str(payload.get("model_artifact_fingerprint") or "")
    if not expected_model or actual_model != expected_model:
        raise RuntimeError("model_artifact_fingerprint_mismatch")
    client = chromadb.PersistentClient(path=str(chroma_path))
    names = {item.name for item in client.list_collections()}
    if target_name in names and not resume:
        raise RuntimeError("shadow_collection_exists_use_resume_or_new_release")
    metadata = {
        "schema_version": "legal-retrieval-shadow-v2",
        "release_id": payload["release_id"],
        "source_snapshot_sha256": payload["source_snapshot_sha256"],
        "model_artifact_fingerprint": expected_model,
        "tokenizer_fingerprint": payload["tokenizer_fingerprint"],
        "embedding_recipe_fingerprint": payload["embedding_recipe_fingerprint"],
        "passage_recipe_fingerprint": payload["passage_recipe_fingerprint"],
        "splitter_fingerprint": payload["splitter_fingerprint"],
        "quality_policy_version": "legal-chunk-quality-v2",
        "hnsw:space": "cosine",
        "benchmark_only": "true",
        "provisional_staging": "true" if provisional else "false",
        "release_eligible": "false" if provisional else "true",
        "embedding_max_length": str(EMBEDDING_MAX_LENGTH),
        "document_state": document_state,
    }
    if target_name in names:
        target = client.get_collection(target_name)
        observed_metadata = {str(key): str(value) for key, value in (target.metadata or {}).items()}
        _validate_resume_metadata(observed_metadata, metadata)
    else:
        target = client.create_collection(target_name, metadata=metadata)
    expected_ids_list = [str(row["chunk_revision_id"]) for row in chunks]
    existing = _existing_ids(target, expected_ids_list) if resume else set()
    pending = [row for row in chunks if str(row["chunk_revision_id"]) not in existing]
    # Group similarly sized passages so padding does not make every batch pay
    # for a 512-token outlier. This changes only execution order, not IDs,
    # vector values, checksums, or the persisted collection contract.
    pending.sort(
        key=lambda row: (
            int(row.get("token_count") or 0),
            str(row.get("chunk_revision_id") or ""),
        )
    )
    embedded = 0
    used_batches: list[int] = []
    embedding_started = perf_counter()
    for batch_index, start in enumerate(range(0, len(pending), 256), start=1):
        block = pending[start:start + 256]
        vectors, used = _encode(
            retriever,
            [str(row.get("embedding_text") or row["content"]) for row in block],
            batch_size,
            max_length=EMBEDDING_MAX_LENGTH,
        )
        if len(vectors) != len(block):
            raise RuntimeError("embedding_count_mismatch")
        for vector in vectors:
            if not vector or not all(math.isfinite(value) for value in vector):
                raise RuntimeError("non_finite_vector")
            norm = math.sqrt(sum(value * value for value in vector))
            if norm == 0 or not 0.999 <= norm <= 1.001:
                raise RuntimeError("vector_norm_out_of_range")
        ids = [str(row["chunk_revision_id"]) for row in block]
        target.upsert(ids=ids, embeddings=vectors, metadatas=[_metadata(row, payload) for row in block])
        embedded += len(block)
        used_batches.append(used)
        print(json.dumps({
            "stage": "embedding",
            "target_collection": target_name,
            "document_state": document_state,
            "resumed_existing": len(existing),
            "embedded_this_run": embedded,
            "pending_total": len(pending),
            "persisted_estimate": len(existing) + embedded,
            "expected_total": len(expected_ids_list),
            "batch_index": batch_index,
            "batch_size_requested": batch_size,
            "batch_size_used": used,
            "elapsed_seconds": round(perf_counter() - embedding_started, 3),
        }, ensure_ascii=False), flush=True)
    actual = int(target.count())
    expected = len(chunks)
    observed = set(target.get(include=[]).get("ids") or [])
    expected_ids = {str(row["chunk_revision_id"]) for row in chunks}
    missing = sorted(expected_ids - observed)
    orphan = sorted(observed - expected_ids)
    pointer_after = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else ""
    vector_content_sha, persisted_vector_count = _vector_content_sha_stream(
        target,
        expected_dimension=EMBEDDING_DIMENSION,
    )
    report = {
        "schema_version": "legal-retrieval-shadow-build-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(manifest_path.resolve()),
        "manifest_file_sha256": file_sha256(manifest_path),
        "manifest_sha256": payload.get("manifest_sha256"),
        "target_collection": target_name,
        "document_state": document_state,
        "release_id": payload["release_id"],
        "expected_vector_count": expected,
        "actual_vector_count": actual,
        "embedded_fresh_vectors": embedded,
        "resumed_existing_vectors": len(existing),
        "embedding_elapsed_seconds": round(perf_counter() - embedding_started, 3),
        "legacy_vectors_copied": 0,
        "missing_ids": missing,
        "orphan_ids": orphan,
        "vector_content_sha256": vector_content_sha if persisted_vector_count else None,
        "persisted_vector_count_hashed": persisted_vector_count,
        "batch_sizes_used": sorted(set(used_batches)),
        "embedding_max_length": EMBEDDING_MAX_LENGTH,
        "embedding_dimension": EMBEDDING_DIMENSION,
        "active_pointer_before": active_before,
        "active_pointer_after": pointer_after,
        "active_pointer_unchanged": active_before == pointer_after,
        "valid": actual == expected and not missing and not orphan and active_before == pointer_after,
        "status": "PROVISIONAL_STAGING_PASS" if provisional else "PASS",
        "provisional_staging": provisional,
        "release_eligible": not provisional,
        "mutation": {"active_pointer_changed": False, "baseline_mutated": False},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    if not report["valid"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=32, choices=ALLOWED_BATCHES)
    parser.add_argument(
        "--document-state",
        choices=("all", "current_retrievable", "historical_only"),
        default="all",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--allow-provisional-staging",
        action="store_true",
        help="Allow a draft manifest only for a target explicitly named *provisional; never release-eligible.",
    )
    args = parser.parse_args(argv)
    manifest_path = args.manifest.resolve()
    output_path = args.output.resolve()
    try:
        report = build(
            manifest_path=manifest_path,
            chroma_path=args.chroma_path.resolve(),
            target_name=args.target,
            output=output_path,
            apply=args.apply,
            resume=args.resume,
            batch_size=args.batch_size,
            document_state=args.document_state,
            allow_provisional_staging=args.allow_provisional_staging,
        )
    except Exception as exc:
        # Preserve a machine-readable, immutable blocker report even when the
        # input is a draft or otherwise fails before a collection is opened.
        # This makes the legal-review gate auditable without weakening it.
        report = {
            "schema_version": "legal-retrieval-shadow-build-v2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "BLOCKED",
            "reason": str(exc),
            "manifest_path": str(manifest_path),
            "manifest_file_sha256": file_sha256(manifest_path) if manifest_path.is_file() else None,
            "target_collection": args.target,
            "document_state": args.document_state,
            "fresh_embedding_required": True,
            "legacy_vectors_copied": 0,
            "provisional_staging": bool(args.allow_provisional_staging),
            "release_eligible": False,
            "mutation": {
                "collection_created": False,
                "collection_mutated": False,
                "baseline_mutated": False,
                "active_pointer_changed": False,
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output_path.with_suffix(output_path.suffix + ".sha256").write_text(
            f"{file_sha256(output_path)}  {output_path.name}\n", encoding="ascii"
        )
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report.get("status") in {"DRY_RUN", "PROVISIONAL_DRY_RUN"} or report.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
