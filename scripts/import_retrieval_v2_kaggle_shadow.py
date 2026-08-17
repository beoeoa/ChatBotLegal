#!/usr/bin/env python3
"""Verify and import Kaggle Retrieval V2 vectors into isolated Chroma shadows.

The command is a dry-run unless ``--apply`` is supplied.  It has no activation
path and rejects the live collection name.  Provisional input can only be
written to targets whose names explicitly contain both ``kaggle`` and
``provisional``.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Iterable

import chromadb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import (
    file_sha256,
    read_active_collection_pointer,
)
from scripts.export_retrieval_v2_kaggle_shards import FINGERPRINT_KEYS
from scripts.verify_retrieval_v2_kaggle_output import verify_kaggle_output


DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_CURRENT = "legal_chunks_retrieval_v2_current_kaggle_provisional"
DEFAULT_TEMPORAL = "legal_chunks_retrieval_v2_temporal_kaggle_provisional"
# Chroma shadow import is I/O-bound; keep the batch large enough to avoid
# thousands of tiny SQLite transactions while remaining safe for the 1,024-D
# float32 vectors and metadata payloads used by this release.
WRITE_BATCH_SIZE = 5000
# A sync threshold above the final collection size leaves the persisted HNSW
# segment empty and makes the first production query replay the entire vector
# log.  Keep ingestion batches large, but persist often enough that a fresh
# process only has a bounded tail to replay.
HNSW_BATCH_SIZE = 10000
HNSW_SYNC_THRESHOLD = 50000


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _metadata_scalar(value: Any) -> str | int | float | bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_target_names(
    *, current: str, temporal: str, active: str | None, provisional: bool
) -> None:
    """Require two explicit Kaggle staging targets that cannot alias live."""

    normalized = [str(current).strip(), str(temporal).strip()]
    if not all(normalized) or normalized[0] == normalized[1]:
        raise RuntimeError("kaggle_staging_target_required:distinct_targets")
    active_name = str(active or "").strip()
    for target in normalized:
        folded = target.casefold()
        if target == active_name:
            raise RuntimeError("kaggle_staging_target_required:active_alias")
        if "kaggle" not in folded:
            raise RuntimeError("kaggle_staging_target_required:kaggle_marker")
        if provisional and "provisional" not in folded:
            raise RuntimeError("kaggle_staging_target_required:provisional_marker")
        if not provisional and not any(marker in folded for marker in ("shadow", "staging")):
            raise RuntimeError("kaggle_staging_target_required:shadow_marker")


def build_collection_metadata(
    source: dict[str, Any], *, document_state: str
) -> dict[str, Any]:
    provisional = bool(source.get("provisional_staging"))
    values: dict[str, Any] = {
        "schema_version": "legal-retrieval-shadow-v2",
        "release_id": source.get("release_id"),
        "source_snapshot_sha256": source.get("source_snapshot_sha256"),
        **{key: source.get(key) for key in FINGERPRINT_KEYS},
        "quality_policy_version": source.get("quality_policy_version")
        or "legal-chunk-quality-v2",
        "hnsw:space": "cosine",
        "hnsw:batch_size": HNSW_BATCH_SIZE,
        "hnsw:sync_threshold": HNSW_SYNC_THRESHOLD,
        "benchmark_only": "true",
        "provisional_staging": "true" if provisional else "false",
        "release_eligible": "true"
        if source.get("release_eligible") is True and not provisional
        else "false",
        "embedding_max_length": str(source.get("embedding_max_length") or 512),
        "embedding_source": "kaggle_gpu_verified_shards",
        "document_state": document_state,
    }
    missing = [key for key, value in values.items() if value in (None, "")]
    if missing:
        raise RuntimeError(f"collection_metadata_missing:{','.join(missing)}")
    return {
        str(key): value if key in {"hnsw:batch_size", "hnsw:sync_threshold"} else str(value)
        for key, value in values.items()
    }


def build_vector_metadata(row: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    source_metadata = dict(row.get("metadata") or {})
    values: dict[str, Any] = dict(source_metadata)
    values.update(
        {
            "chunk_revision_id": row.get("chunk_revision_id"),
            "document_id": int(row["document_id"]),
            "article_id": int(row["article_id"]),
            "release_id": source.get("release_id"),
            "source_snapshot_sha256": source.get("source_snapshot_sha256"),
            "quality_policy_version": source.get("quality_policy_version")
            or "legal-chunk-quality-v2",
            "serving_state": "retrievable",
            "document_serving_state": row.get("document_serving_state"),
            "effective_from": source_metadata.get("article_effective_from")
            or source_metadata.get("effective_date"),
            "effective_to": source_metadata.get("article_effective_to")
            or source_metadata.get("expired_date"),
        }
    )
    required = (
        "chunk_revision_id",
        "document_id",
        "article_id",
        "release_id",
        "source_snapshot_sha256",
        "document_serving_state",
    )
    missing = [key for key in required if values.get(key) in (None, "")]
    if missing:
        raise RuntimeError(
            f"vector_metadata_missing:{row.get('chunk_revision_id')}:{','.join(missing)}"
        )
    return {
        str(key): scalar
        for key, value in values.items()
        if (scalar := _metadata_scalar(value)) is not None
    }


def _read_rows(path: Path, expected_sha256: str) -> list[dict[str, Any]]:
    if file_sha256(path) != expected_sha256:
        raise RuntimeError(f"input_shard_checksum_mismatch:{path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                rows.append(dict(json.loads(line)))
    return rows


def _blocks(values: list[Any], size: int = WRITE_BATCH_SIZE) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _existing_ids(collection: Any, ids: list[str], *, batch_size: int = 5000) -> set[str]:
    """Return IDs already committed in a resumable shadow collection."""

    found: set[str] = set()
    for block in _blocks(ids, batch_size):
        response = collection.get(ids=block, include=[])
        found.update(str(value) for value in response.get("ids") or [])
    return found


def _validate_resume_metadata(
    observed: dict[str, Any],
    expected: dict[str, Any],
    *,
    preserve_existing_hnsw_config: bool = False,
) -> None:
    for key, value in expected.items():
        if preserve_existing_hnsw_config and key in {
            "hnsw:batch_size",
            "hnsw:sync_threshold",
        }:
            continue
        if str(observed.get(key) or "") != str(value):
            raise RuntimeError(f"kaggle_shadow_resume_contract_mismatch:{key}")


def _open_target(
    client: Any,
    *,
    name: str,
    metadata: dict[str, Any],
    resume: bool,
    preserve_existing_hnsw_config: bool = False,
) -> Any:
    names = {item.name for item in client.list_collections()}
    if name in names:
        if not resume:
            raise RuntimeError(f"kaggle_shadow_exists_use_resume:{name}")
        collection = client.get_collection(name)
        _validate_resume_metadata(
            dict(collection.metadata or {}),
            metadata,
            preserve_existing_hnsw_config=preserve_existing_hnsw_config,
        )
        return collection
    return client.create_collection(name=name, metadata=metadata)


def _upsert_rows(collection: Any, rows: list[dict[str, Any]], vectors: np.ndarray, source: dict[str, Any]) -> int:
    written = 0
    for indices in _blocks(list(range(len(rows)))):
        block_rows = [rows[index] for index in indices]
        collection.upsert(
            ids=[str(row["chunk_revision_id"]) for row in block_rows],
            embeddings=np.asarray(vectors[indices], dtype=np.float32).tolist(),
            metadatas=[build_vector_metadata(row, source) for row in block_rows],
        )
        written += len(block_rows)
    return written


def import_kaggle_shadow(
    *,
    input_manifest_path: Path,
    output_manifest_path: Path,
    chroma_path: Path,
    current_target: str,
    temporal_target: str,
    report_path: Path,
    verification_report_path: Path | None = None,
    apply: bool = False,
    resume: bool = False,
    reuse_verified_report: bool = False,
    preserve_existing_hnsw_config: bool = False,
) -> dict[str, Any]:
    if preserve_existing_hnsw_config and not resume:
        raise RuntimeError("preserve_existing_hnsw_config_requires_resume")
    input_manifest = _load(input_manifest_path)
    output_manifest = _load(output_manifest_path)
    expected_dimension = int(input_manifest.get("embedding_dimension") or 0)
    verification_path = (
        verification_report_path.resolve()
        if verification_report_path is not None
        else report_path.with_name(report_path.stem + "-verification.json")
    )
    if reuse_verified_report and verification_path.is_file():
        verification = _load(verification_path)
        if (
            verification.get("valid") is not True
            or verification.get("input_manifest_file_sha256") != file_sha256(input_manifest_path)
            or verification.get("output_manifest_file_sha256") != file_sha256(output_manifest_path)
            or int(verification.get("vector_count") or 0) != int(input_manifest.get("chunk_count") or 0)
        ):
            raise RuntimeError("verified_report_contract_mismatch")
    else:
        verification = verify_kaggle_output(
            input_manifest_path=input_manifest_path,
            output_manifest_path=output_manifest_path,
            expected_dimension=expected_dimension,
            output_report_path=verification_path,
        )
    active_before = read_active_collection_pointer(chroma_path)
    provisional = bool(input_manifest.get("provisional_staging"))
    validate_target_names(
        current=current_target,
        temporal=temporal_target,
        active=active_before,
        provisional=provisional,
    )
    expected_temporal = int(input_manifest.get("chunk_count") or 0)
    expected_current = int(
        (input_manifest.get("document_state_counts") or {}).get(
            "current_retrievable", 0
        )
    )
    base_report: dict[str, Any] = {
        "schema_version": "legal-retrieval-kaggle-shadow-import-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PROVISIONAL_DRY_RUN" if provisional else "DRY_RUN",
        "release_id": input_manifest.get("release_id"),
        "input_manifest_file_sha256": file_sha256(input_manifest_path),
        "output_manifest_file_sha256": file_sha256(output_manifest_path),
        "verification_report": str(verification_path.resolve()),
        "verification_valid": verification.get("valid") is True,
        "current_target": current_target,
        "temporal_target": temporal_target,
        "expected_current_vectors": expected_current,
        "expected_temporal_vectors": expected_temporal,
        "provisional_staging": provisional,
        "release_eligible": bool(input_manifest.get("release_eligible")) and not provisional,
        "hnsw_config_mode": (
            "preserve_existing_immutable_config"
            if preserve_existing_hnsw_config
            else "new_collection_bounded_sync"
        ),
        "active_pointer_before": active_before,
        "active_pointer_after": active_before,
        "active_pointer_unchanged": True,
        "mutation": {
            "collection_mutated": False,
            "baseline_mutated": False,
            "active_pointer_changed": False,
        },
    }
    if not apply:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(base_report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report_path.with_suffix(report_path.suffix + ".sha256").write_text(
            f"{file_sha256(report_path)}  {report_path.name}\n", encoding="ascii"
        )
        return base_report

    client = chromadb.PersistentClient(path=str(chroma_path))
    current = _open_target(
        client,
        name=current_target,
        metadata=build_collection_metadata(input_manifest, document_state="current_retrievable"),
        resume=resume,
        preserve_existing_hnsw_config=preserve_existing_hnsw_config,
    )
    temporal = _open_target(
        client,
        name=temporal_target,
        metadata=build_collection_metadata(input_manifest, document_state="all"),
        resume=resume,
        preserve_existing_hnsw_config=preserve_existing_hnsw_config,
    )
    input_shards = {
        str(item["path"]): item for item in input_manifest.get("shards") or []
    }
    written_current = 0
    written_temporal = 0
    for output_shard in output_manifest.get("shards") or []:
        input_name = str(output_shard.get("input_path") or "")
        input_shard = input_shards.get(input_name)
        if input_shard is None:
            raise RuntimeError(f"output_input_shard_binding_missing:{input_name}")
        rows = _read_rows(
            input_manifest_path.parent / input_name, str(input_shard["sha256"])
        )
        with np.load(
            output_manifest_path.parent / str(output_shard["path"]),
            allow_pickle=False,
        ) as payload:
            ids = [str(value) for value in payload["ids"].tolist()]
            vectors = np.asarray(payload["vectors"], dtype=np.float32)
        by_id = {str(row["chunk_revision_id"]): row for row in rows}
        if set(ids) != set(by_id):
            raise RuntimeError(f"output_input_chunk_binding_mismatch:{input_name}")
        ordered_rows = [by_id[identifier] for identifier in ids]
        ordered_ids = [str(row["chunk_revision_id"]) for row in ordered_rows]
        temporal_existing = _existing_ids(temporal, ordered_ids)
        temporal_missing_indices = [
            index for index, identifier in enumerate(ordered_ids)
            if identifier not in temporal_existing
        ]
        if temporal_missing_indices:
            temporal_rows = [ordered_rows[index] for index in temporal_missing_indices]
            temporal_vectors = np.asarray(vectors[temporal_missing_indices], dtype=np.float32)
            written_temporal += _upsert_rows(temporal, temporal_rows, temporal_vectors, input_manifest)
        current_indices = [
            index
            for index, row in enumerate(ordered_rows)
            if row.get("document_serving_state") == "current_retrievable"
        ]
        if current_indices:
            current_rows = [ordered_rows[index] for index in current_indices]
            current_ids = [str(row["chunk_revision_id"]) for row in current_rows]
            current_existing = _existing_ids(current, current_ids)
            current_missing_indices = [
                index for index, identifier in enumerate(current_ids)
                if identifier not in current_existing
            ]
            if current_missing_indices:
                missing_rows = [current_rows[index] for index in current_missing_indices]
                missing_vectors = np.asarray(
                    vectors[[current_indices[index] for index in current_missing_indices]],
                    dtype=np.float32,
                )
                written_current += _upsert_rows(
                    current, missing_rows, missing_vectors, input_manifest
                )

    current_ids = {str(value) for value in (current.get(include=[]).get("ids") or [])}
    temporal_ids = {str(value) for value in (temporal.get(include=[]).get("ids") or [])}
    active_after = read_active_collection_pointer(chroma_path)
    valid = (
        len(current_ids) == expected_current
        and len(temporal_ids) == expected_temporal
        and current_ids.issubset(temporal_ids)
        and active_after == active_before
    )
    report = {
        **base_report,
        "status": "PROVISIONAL_STAGING_PASS" if provisional and valid else "PASS" if valid else "FAIL",
        "actual_current_vectors": len(current_ids),
        "actual_temporal_vectors": len(temporal_ids),
        "written_current_vectors": written_current,
        "written_temporal_vectors": written_temporal,
        "active_pointer_after": active_after,
        "active_pointer_unchanged": active_after == active_before,
        "valid": valid,
        "mutation": {
            "collection_mutated": True,
            "baseline_mutated": False,
            "active_pointer_changed": False,
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report_path.with_suffix(report_path.suffix + ".sha256").write_text(
        f"{file_sha256(report_path)}  {report_path.name}\n", encoding="ascii"
    )
    if not valid:
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--current-target", default=DEFAULT_CURRENT)
    parser.add_argument("--temporal-target", default=DEFAULT_TEMPORAL)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--verification-report",
        type=Path,
        help="Existing Stage C/D verification report to reuse after checksum validation.",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--preserve-existing-hnsw-config",
        action="store_true",
        help="Resume an existing compatible shadow without rewriting its immutable HNSW settings.",
    )
    parser.add_argument(
        "--reuse-verified-report",
        action="store_true",
        help="Reuse a prior PASS report only when its input/output SHA-256 and vector count match exactly.",
    )
    args = parser.parse_args(argv)
    report = import_kaggle_shadow(
        input_manifest_path=args.input_manifest.resolve(),
        output_manifest_path=args.output_manifest.resolve(),
        chroma_path=args.chroma_path.resolve(),
        current_target=args.current_target,
        temporal_target=args.temporal_target,
        report_path=args.report.resolve(),
        verification_report_path=(
            args.verification_report.resolve() if args.verification_report else None
        ),
        apply=args.apply,
        resume=args.resume,
        reuse_verified_report=args.reuse_verified_report,
        preserve_existing_hnsw_config=args.preserve_existing_hnsw_config,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
