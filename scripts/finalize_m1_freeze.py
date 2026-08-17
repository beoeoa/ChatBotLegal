#!/usr/bin/env python3
"""Create and audit the canonical M1 dataset-freeze artifacts.

The baseline embedding fingerprint is a SHA-256 over every persisted vector,
ordered by numeric chunk id. It identifies the actual serving vectors and does
not claim model provenance that cannot be reproduced from the current database.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable

import chromadb
import numpy as np
from sqlalchemy import bindparam, create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_legal_corpus_candidate import _database_url
from scripts.legal_search_server import _model_revision_fingerprint


BASELINE_COLLECTION = "legal_chunks_vnlegal_lal_haiphong_unified_v1"
CANDIDATE_COLLECTION = "legal_chunks_candidate_3000_v1"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_BASELINE_SOURCE = (
    ROOT
    / "reports"
    / "corpus-thinning-remediated-v2"
    / "legal-serving-baseline-7245-v1.json"
)
DEFAULT_CANDIDATE_SOURCE = (
    ROOT
    / "reports"
    / "corpus-thinning-remediated-v2"
    / "legal-serving-candidate-3000-v1.json"
)
DEFAULT_BUILD_REPORT = (
    ROOT / "reports" / "corpus-thinning-remediated-v2" / "candidate-chroma-build-v1.json"
)
DEFAULT_OUTPUT = ROOT / "reports" / "m1-freeze"
DEFAULT_ROLLBACK_REPORT = DEFAULT_OUTPUT / "rollback_rehearsal.json"
DEFAULT_API_LOCK_PROBE = DEFAULT_OUTPUT / "baseline_chroma_api_lock_probe.json"
DEFAULT_MODEL = ROOT / "release-data" / "legal" / "vnlegal-lal-model"
DEFAULT_BACKUP_STORE = (
    ROOT
    / "backups"
    / "candidate-remediation-chroma-prebuild-20260815"
    / "chroma_store"
)
BATCH_SIZE = 1000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _chunk_ids(manifest: dict[str, Any]) -> list[int]:
    return sorted(
        int(chunk_id)
        for document in manifest.get("documents") or []
        for chunk_id in document.get("expected_chunk_ids") or []
    )


def _document_ids(manifest: dict[str, Any]) -> list[int]:
    return sorted(int(item["document_id"]) for item in manifest.get("documents") or [])


def _batched(values: list[int], size: int = BATCH_SIZE) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _collection_schema(connection_path: Path, collection_name: str) -> dict[str, Any]:
    import sqlite3

    connection = sqlite3.connect(
        f"file:{(connection_path / 'chroma.sqlite3').resolve().as_posix()}?mode=ro",
        uri=True,
    )
    try:
        row = connection.execute(
            "SELECT id, dimension, config_json_str, schema_str FROM collections WHERE name = ?",
            (collection_name,),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"collection_not_found:{collection_name}")
        schema = json.loads(row[3] or "{}")
        vector_config = (
            schema.get("keys", {})
            .get("#embedding", {})
            .get("float_list", {})
            .get("vector_index", {})
            .get("config", {})
        )
        segments = connection.execute(
            "SELECT id, scope, type FROM segments WHERE collection = ? ORDER BY scope, id",
            (row[0],),
        ).fetchall()
        return {
            "collection_id": str(row[0]),
            "dimension": int(row[1] or 0),
            "distance_metric": str(vector_config.get("space") or "unknown"),
            "config": json.loads(row[2] or "{}"),
            "segments": [
                {"segment_id": str(item[0]), "scope": str(item[1]), "type": str(item[2])}
                for item in segments
            ],
        }
    finally:
        connection.close()


def _segment_checksums(chroma_path: Path, vector_segment_id: str) -> dict[str, Any]:
    directory = chroma_path / vector_segment_id
    if not directory.is_dir():
        raise RuntimeError(f"vector_segment_directory_missing:{directory}")
    aggregate = hashlib.sha256()
    files = []
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        relative = path.relative_to(directory).as_posix()
        digest = _file_sha256(path)
        size = path.stat().st_size
        aggregate.update(len(relative.encode("utf-8")).to_bytes(4, "big"))
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(size.to_bytes(8, "big"))
        aggregate.update(bytes.fromhex(digest))
        files.append({"path": relative, "size": size, "sha256": digest})
    return {
        "directory": str(directory.resolve()),
        "file_count": len(files),
        "files": files,
        "aggregate_sha256": aggregate.hexdigest(),
    }


def _fingerprint_vectors(
    *, chroma_path: Path, collection_name: str, expected_ids: list[int]
) -> dict[str, Any]:
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(collection_name)
    all_ids = collection.get(include=[]).get("ids") or []
    observed_numeric = sorted(int(str(value).removeprefix("chunk-")) for value in all_ids)
    if len(observed_numeric) != len(set(observed_numeric)):
        raise RuntimeError("duplicate_vector_ids")
    expected_set = set(expected_ids)
    observed_set = set(observed_numeric)

    vector_digest = hashlib.sha256()
    metadata_digest = hashlib.sha256()
    vector_digest.update(b"legal-baseline-vector-content-v1\0")
    metadata_digest.update(b"legal-baseline-vector-metadata-v1\0")
    dimensions: set[int] = set()
    processed = 0
    for batch in _batched(expected_ids):
        requested = [f"chunk-{item}" for item in batch]
        response = collection.get(ids=requested, include=["embeddings", "metadatas"])
        ids = [str(item) for item in response.get("ids") or []]
        vectors = response.get("embeddings")
        metadata_rows = response.get("metadatas") or []
        if vectors is None:
            raise RuntimeError("collection_embeddings_missing")
        by_id = {
            identifier: (vector, metadata)
            for identifier, vector, metadata in zip(ids, vectors, metadata_rows)
        }
        missing_batch = [identifier for identifier in requested if identifier not in by_id]
        if missing_batch:
            raise RuntimeError(f"missing_expected_vectors:{missing_batch[:10]}")
        for identifier in requested:
            vector, metadata = by_id[identifier]
            encoded_id = identifier.encode("utf-8")
            array = np.asarray(vector, dtype="<f4")
            dimensions.add(int(array.size))
            vector_digest.update(len(encoded_id).to_bytes(4, "big"))
            vector_digest.update(encoded_id)
            vector_digest.update(int(array.size).to_bytes(4, "big"))
            vector_digest.update(array.tobytes(order="C"))
            metadata_bytes = json.dumps(
                metadata or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            metadata_digest.update(len(encoded_id).to_bytes(4, "big"))
            metadata_digest.update(encoded_id)
            metadata_digest.update(len(metadata_bytes).to_bytes(8, "big"))
            metadata_digest.update(metadata_bytes)
            processed += 1
        if processed % 20000 < len(batch):
            print(
                json.dumps(
                    {"fingerprinted_vectors": processed, "expected": len(expected_ids)}
                ),
                flush=True,
            )
    return {
        "algorithm": "sha256(id_length_be32 || utf8_id || dimension_be32 || float32_le_bytes)",
        "domain_separator": "legal-baseline-vector-content-v1\\0",
        "embedding_fingerprint": vector_digest.hexdigest(),
        "metadata_fingerprint": metadata_digest.hexdigest(),
        "vector_count": int(collection.count()),
        "fingerprinted_vector_count": processed,
        "dimensions": sorted(dimensions),
        "expected_id_count": len(expected_ids),
        "observed_id_count": len(observed_numeric),
        "missing_chunk_ids": sorted(expected_set - observed_set),
        "orphan_chunk_ids": sorted(observed_set - expected_set),
        "exact_chunk_set_match": expected_set == observed_set,
    }


def prepare(
    *,
    chroma_path: Path,
    baseline_source: Path,
    candidate_source: Path,
    output_dir: Path,
    model_path: Path,
    backup_store: Path,
) -> dict[str, Any]:
    baseline_source_payload = _load_json(baseline_source)
    candidate_source_payload = _load_json(candidate_source)
    if baseline_source_payload.get("collection", {}).get("collection_name") != BASELINE_COLLECTION:
        raise RuntimeError("unexpected_baseline_collection")
    if candidate_source_payload.get("candidate_collection") != CANDIDATE_COLLECTION:
        raise RuntimeError("unexpected_candidate_collection")

    expected_ids = _chunk_ids(baseline_source_payload)
    schema = _collection_schema(chroma_path, BASELINE_COLLECTION)
    vector_segment = next(
        item for item in schema["segments"] if item["scope"] == "VECTOR"
    )
    vector_fingerprint = _fingerprint_vectors(
        chroma_path=chroma_path,
        collection_name=BASELINE_COLLECTION,
        expected_ids=expected_ids,
    )
    if not vector_fingerprint["exact_chunk_set_match"]:
        raise RuntimeError("baseline_vector_set_not_exact")
    live_segment = _segment_checksums(chroma_path, vector_segment["segment_id"])
    backup_segment = _segment_checksums(backup_store, vector_segment["segment_id"])
    model_fingerprint = _model_revision_fingerprint(model_path)
    attestation = {
        "schema_version": "legal-baseline-embedding-attestation-v1",
        "generated_at": _utc_now(),
        "collection": BASELINE_COLLECTION,
        "collection_id": schema["collection_id"],
        "dimension": schema["dimension"],
        "distance_metric": schema["distance_metric"],
        "embedding_fingerprint_type": "persisted_vector_content_sha256",
        "embedding_fingerprint": vector_fingerprint["embedding_fingerprint"],
        "vector_metadata_fingerprint": vector_fingerprint["metadata_fingerprint"],
        "vector_evidence": vector_fingerprint,
        "vector_segment": live_segment,
        "preapply_backup_vector_segment": backup_segment,
        "live_backup_segment_match": (
            live_segment["aggregate_sha256"] == backup_segment["aggregate_sha256"]
        ),
        "model_artifact": {
            "path": str(model_path.resolve()),
            "fingerprint_algorithm": "legal_search_server._model_revision_fingerprint",
            "fingerprint": model_fingerprint,
            "attribution_status": "model_artifact_identified_vector_generation_not_fully_replayable",
            "claim_boundary": (
                "The model artifact is the configured VNLegal-LAL runtime model. "
                "The baseline fingerprint identifies persisted vector bytes; it does not "
                "assert that every legacy vector can be regenerated from current DB text."
            ),
        },
        "checks": {
            "all_manifest_vectors_fingerprinted": (
                vector_fingerprint["fingerprinted_vector_count"] == len(expected_ids)
            ),
            "exact_chunk_set_match": vector_fingerprint["exact_chunk_set_match"],
            "single_expected_dimension": vector_fingerprint["dimensions"]
            == [schema["dimension"]],
            "live_vector_segment_matches_preapply_backup": (
                live_segment["aggregate_sha256"] == backup_segment["aggregate_sha256"]
            ),
            "model_artifact_fingerprint_present": len(model_fingerprint) == 64,
        },
    }
    attestation["passed"] = all(attestation["checks"].values())
    attestation_projection = {
        key: value for key, value in attestation.items() if key not in {"generated_at"}
    }
    attestation["attestation_sha256"] = _canonical_sha256(attestation_projection)
    attestation_path = output_dir / "baseline_embedding_attestation.json"
    _atomic_json(attestation_path, attestation)

    baseline = deepcopy(baseline_source_payload)
    baseline["schema_version"] = "legal-serving-baseline-v2"
    baseline["source_manifest"] = {
        "path": str(baseline_source.resolve()),
        "file_sha256": _file_sha256(baseline_source),
        "manifest_sha256": baseline_source_payload.get("manifest_sha256"),
    }
    baseline["collection"]["embedding_fingerprint"] = attestation[
        "embedding_fingerprint"
    ]
    baseline["collection"]["embedding_fingerprint_type"] = attestation[
        "embedding_fingerprint_type"
    ]
    baseline["collection"]["vector_metadata_fingerprint"] = attestation[
        "vector_metadata_fingerprint"
    ]
    baseline["collection"]["model_artifact_fingerprint"] = model_fingerprint
    baseline["collection"]["distance_metric"] = schema["distance_metric"]
    baseline["collection"]["pipeline_fingerprint"] = "legal-retention-unification-v1"
    baseline["embedding_attestation"] = {
        "path": str(attestation_path.resolve()),
        "file_sha256": _file_sha256(attestation_path),
        "attestation_sha256": attestation["attestation_sha256"],
        "passed": attestation["passed"],
    }
    baseline["manifest_contract"] = {
        "document_id_field": "documents[].document_id",
        "chunk_id_field": "documents[].expected_chunk_ids[]",
        "document_metadata_fields": [
            "law_number",
            "stored_status",
            "domain",
            "scope_reason",
        ],
        "collection_field": "collection.collection_name",
        "embedding_fingerprint_field": "collection.embedding_fingerprint",
        "checksum_field": "manifest_sha256",
    }
    baseline["generated_at"] = _utc_now()
    baseline.pop("manifest_sha256", None)
    baseline["manifest_sha256"] = _canonical_sha256(
        {key: value for key, value in baseline.items() if key != "generated_at"}
    )
    baseline_path = output_dir / "baseline_manifest.json"
    _atomic_json(baseline_path, baseline)

    candidate_path = output_dir / "candidate_manifest.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate_source, candidate_path)
    return {
        "baseline_manifest": str(baseline_path.resolve()),
        "baseline_manifest_sha256": baseline["manifest_sha256"],
        "baseline_manifest_file_sha256": _file_sha256(baseline_path),
        "candidate_manifest": str(candidate_path.resolve()),
        "candidate_manifest_sha256": candidate_source_payload["manifest_sha256"],
        "candidate_manifest_file_sha256": _file_sha256(candidate_path),
        "embedding_attestation": str(attestation_path.resolve()),
        "embedding_fingerprint": attestation["embedding_fingerprint"],
        "passed": attestation["passed"],
    }


def _collection_ids(collection: Any) -> set[int]:
    return {
        int(str(identifier).removeprefix("chunk-"))
        for identifier in (collection.get(include=[]).get("ids") or [])
    }


def _metadata_alignment(
    collection: Any, manifest: dict[str, Any]
) -> dict[str, Any]:
    expected: dict[int, int] = {}
    for document in manifest.get("documents") or []:
        document_id = int(document["document_id"])
        for chunk_id in document.get("expected_chunk_ids") or []:
            numeric_chunk_id = int(chunk_id)
            if numeric_chunk_id in expected:
                raise RuntimeError(f"duplicate_manifest_chunk_id:{numeric_chunk_id}")
            expected[numeric_chunk_id] = document_id
    mismatches: list[dict[str, Any]] = []
    mismatch_count = 0
    observed_documents: set[int] = set()
    processed = 0
    chunk_ids = sorted(expected)
    for batch in _batched(chunk_ids):
        response = collection.get(
            ids=[f"chunk-{chunk_id}" for chunk_id in batch],
            include=["metadatas"],
        )
        for identifier, metadata in zip(
            response.get("ids") or [], response.get("metadatas") or []
        ):
            chunk_id = int(str(identifier).removeprefix("chunk-"))
            observed_chunk_id = int((metadata or {}).get("chunk_id") or -1)
            observed_document_id = int((metadata or {}).get("document_id") or -1)
            if observed_document_id >= 0:
                observed_documents.add(observed_document_id)
            mismatch = (
                observed_chunk_id != chunk_id
                or observed_document_id != expected[chunk_id]
            )
            if mismatch:
                mismatch_count += 1
            if mismatch and len(mismatches) < 20:
                mismatches.append(
                    {
                        "vector_id": str(identifier),
                        "expected_chunk_id": chunk_id,
                        "observed_chunk_id": observed_chunk_id,
                        "expected_document_id": expected[chunk_id],
                        "observed_document_id": observed_document_id,
                    }
                )
            processed += 1
    expected_documents = set(_document_ids(manifest))
    return {
        "processed_vector_count": processed,
        "expected_document_count": len(expected_documents),
        "observed_document_count": len(observed_documents),
        "metadata_mismatch_count": mismatch_count,
        "metadata_mismatch_sample": mismatches,
        "exact_chunk_document_metadata_match": (
            processed == len(expected)
            and mismatch_count == 0
            and observed_documents == expected_documents
        ),
    }


def _database_counts(document_ids: list[int]) -> dict[str, int]:
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    parameter = bindparam("document_ids", expanding=True)
    statements = {
        "documents": text(
            "SELECT COUNT(*) FROM legal_documents WHERE id IN :document_ids"
        ).bindparams(parameter),
        "articles": text(
            "SELECT COUNT(*) FROM legal_articles WHERE document_id IN :document_ids"
        ).bindparams(bindparam("document_ids", expanding=True)),
        "chunks": text(
            """
            SELECT COUNT(*) FROM legal_article_chunks c
            JOIN legal_articles a ON a.id = c.article_id
            WHERE a.document_id IN :document_ids
            """
        ).bindparams(bindparam("document_ids", expanding=True)),
    }
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            return {
                key: int(connection.execute(statement, {"document_ids": document_ids}).scalar_one())
                for key, statement in statements.items()
            }
    finally:
        engine.dispose()


def finalize(
    *,
    chroma_path: Path,
    output_dir: Path,
    lock_report_path: Path,
    api_lock_probe_path: Path,
    build_report_path: Path,
    rollback_report_path: Path,
) -> dict[str, Any]:
    baseline_path = output_dir / "baseline_manifest.json"
    candidate_path = output_dir / "candidate_manifest.json"
    attestation_path = output_dir / "baseline_embedding_attestation.json"
    baseline = _load_json(baseline_path)
    candidate = _load_json(candidate_path)
    attestation = _load_json(attestation_path)
    lock_report = _load_json(lock_report_path)
    api_lock_probe = _load_json(api_lock_probe_path)
    build_report = _load_json(build_report_path)
    rollback_report = _load_json(rollback_report_path)

    baseline_ids = _chunk_ids(baseline)
    candidate_ids = _chunk_ids(candidate)
    client = chromadb.PersistentClient(path=str(chroma_path))
    baseline_collection = client.get_collection(BASELINE_COLLECTION)
    candidate_collection = client.get_collection(CANDIDATE_COLLECTION)
    observed_baseline_ids = _collection_ids(baseline_collection)
    observed_candidate_ids = _collection_ids(candidate_collection)
    baseline_expected = set(baseline_ids)
    candidate_expected = set(candidate_ids)
    baseline_metadata_alignment = _metadata_alignment(baseline_collection, baseline)
    candidate_metadata_alignment = _metadata_alignment(candidate_collection, candidate)
    baseline_db = _database_counts(_document_ids(baseline))
    candidate_db = _database_counts(_document_ids(candidate))
    active_pointer_file = chroma_path / "active_core_collection.txt"
    active_pointer = active_pointer_file.read_text(encoding="utf-8-sig").strip()

    required_candidate_fields = {
        "document_id",
        "law_number",
        "status",
        "domain",
        "reason_kept",
        "score_components",
        "required_procedure_mappings",
        "expected_chunk_ids",
    }
    candidate_fields_complete = all(
        required_candidate_fields <= set(document)
        for document in candidate.get("documents") or []
    )
    checks = {
        "baseline_document_count_7245": baseline_db["documents"] == 7245,
        "baseline_article_count_71849": baseline_db["articles"] == 71849,
        "baseline_chunk_count_168155": baseline_db["chunks"] == 168155,
        "baseline_vector_count_168155": baseline_collection.count() == 168155,
        "baseline_exact_chunk_set": observed_baseline_ids == baseline_expected,
        "baseline_chunk_document_metadata_match": baseline_metadata_alignment[
            "exact_chunk_document_metadata_match"
        ],
        "baseline_embedding_attestation_passed": attestation.get("passed") is True,
        "baseline_embedding_fingerprint_present": len(
            str(baseline.get("collection", {}).get("embedding_fingerprint") or "")
        )
        == 64,
        "baseline_manifest_checksum_present": len(str(baseline.get("manifest_sha256") or ""))
        == 64,
        "candidate_document_count_3000": candidate_db["documents"] == 3000,
        "candidate_article_count_36260": candidate_db["articles"] == 36260,
        "candidate_chunk_count_88209": candidate_db["chunks"] == 88209,
        "candidate_vector_count_88209": candidate_collection.count() == 88209,
        "candidate_exact_chunk_set": observed_candidate_ids == candidate_expected,
        "candidate_chunk_document_metadata_match": candidate_metadata_alignment[
            "exact_chunk_document_metadata_match"
        ],
        "candidate_required_fields_complete": candidate_fields_complete,
        "candidate_build_report_passed": build_report.get("exact_chunk_set_match") is True,
        "baseline_write_lock_passed": lock_report.get("passed") is True,
        "baseline_chroma_api_write_rejected": api_lock_probe.get("passed") is True,
        "active_pointer_still_baseline": active_pointer == BASELINE_COLLECTION,
        "rollback_rehearsal_passed": rollback_report.get("passed") is True,
        "required_artifact_names_present": False,
    }
    report = {
        "schema_version": "legal-m1-baseline-report-v1",
        "generated_at": _utc_now(),
        "status": "pass" if all(checks.values()) else "fail",
        "scope": "M1 dataset freeze and baseline lock only; not a production activation decision",
        "collections": {
            "baseline": {
                "name": BASELINE_COLLECTION,
                "documents": baseline_db["documents"],
                "articles": baseline_db["articles"],
                "chunks": baseline_db["chunks"],
                "vectors": baseline_collection.count(),
                "missing_chunk_count": len(baseline_expected - observed_baseline_ids),
                "orphan_chunk_count": len(observed_baseline_ids - baseline_expected),
                "embedding_fingerprint": baseline["collection"]["embedding_fingerprint"],
                "metadata_alignment": baseline_metadata_alignment,
            },
            "candidate": {
                "name": CANDIDATE_COLLECTION,
                "documents": candidate_db["documents"],
                "articles": candidate_db["articles"],
                "chunks": candidate_db["chunks"],
                "vectors": candidate_collection.count(),
                "missing_chunk_count": len(candidate_expected - observed_candidate_ids),
                "orphan_chunk_count": len(observed_candidate_ids - candidate_expected),
                "metadata_alignment": candidate_metadata_alignment,
            },
        },
        "active_pointer": active_pointer,
        "artifacts": {
            "baseline_manifest.json": {
                "path": str(baseline_path.resolve()),
                "file_sha256": _file_sha256(baseline_path),
                "manifest_sha256": baseline["manifest_sha256"],
            },
            "candidate_manifest.json": {
                "path": str(candidate_path.resolve()),
                "file_sha256": _file_sha256(candidate_path),
                "manifest_sha256": candidate["manifest_sha256"],
            },
            "baseline_embedding_attestation.json": {
                "path": str(attestation_path.resolve()),
                "file_sha256": _file_sha256(attestation_path),
                "attestation_sha256": attestation["attestation_sha256"],
            },
            "baseline_write_lock.json": {
                "path": str(lock_report_path.resolve()),
                "file_sha256": _file_sha256(lock_report_path),
            },
            "baseline_chroma_api_lock_probe.json": {
                "path": str(api_lock_probe_path.resolve()),
                "file_sha256": _file_sha256(api_lock_probe_path),
            },
            "rollback_rehearsal-v1.json": {
                "path": str(rollback_report_path.resolve()),
                "file_sha256": _file_sha256(rollback_report_path),
            },
        },
        "checks": checks,
        "passed": all(checks.values()),
        "known_non_m1_release_gate": (
            "Candidate activation remains separately governed by Golden/answer quality gates."
        ),
    }
    report["report_sha256"] = _canonical_sha256(
        {key: value for key, value in report.items() if key != "generated_at"}
    )
    report_path = output_dir / "baseline_report.json"
    _atomic_json(report_path, report)
    report["checks"]["required_artifact_names_present"] = all(
        (output_dir / name).is_file()
        for name in (
            "baseline_report.json",
            "baseline_manifest.json",
            "candidate_manifest.json",
        )
    )
    report["passed"] = all(report["checks"].values())
    report["status"] = "pass" if report["passed"] else "fail"
    report["report_sha256"] = _canonical_sha256(
        {
            key: value
            for key, value in report.items()
            if key not in {"generated_at", "report_sha256"}
        }
    )
    _atomic_json(report_path, report)

    checksum_paths = [
        report_path,
        baseline_path,
        candidate_path,
        attestation_path,
        lock_report_path,
        api_lock_probe_path,
        rollback_report_path,
        build_report_path,
    ]
    checksums = {
        "schema_version": "legal-m1-artifact-checksums-v1",
        "generated_at": _utc_now(),
        "artifacts": [
            {
                "path": str(path.resolve()),
                "size": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
            for path in checksum_paths
        ],
    }
    checksums["checksum_manifest_sha256"] = _canonical_sha256(
        {key: value for key, value in checksums.items() if key != "generated_at"}
    )
    _atomic_json(output_dir / "SHA256SUMS.json", checksums)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    prepare_parser.add_argument("--baseline-source", type=Path, default=DEFAULT_BASELINE_SOURCE)
    prepare_parser.add_argument("--candidate-source", type=Path, default=DEFAULT_CANDIDATE_SOURCE)
    prepare_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    prepare_parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL)
    prepare_parser.add_argument("--backup-store", type=Path, default=DEFAULT_BACKUP_STORE)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    finalize_parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    finalize_parser.add_argument(
        "--lock-report", type=Path, default=DEFAULT_OUTPUT / "baseline_write_lock.json"
    )
    finalize_parser.add_argument(
        "--api-lock-probe", type=Path, default=DEFAULT_API_LOCK_PROBE
    )
    finalize_parser.add_argument("--build-report", type=Path, default=DEFAULT_BUILD_REPORT)
    finalize_parser.add_argument("--rollback-report", type=Path, default=DEFAULT_ROLLBACK_REPORT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.operation == "prepare":
        report = prepare(
            chroma_path=args.chroma_path,
            baseline_source=args.baseline_source,
            candidate_source=args.candidate_source,
            output_dir=args.output_dir,
            model_path=args.model_path,
            backup_store=args.backup_store,
        )
    else:
        report = finalize(
            chroma_path=args.chroma_path,
            output_dir=args.output_dir,
            lock_report_path=args.lock_report,
            api_lock_probe_path=args.api_lock_probe,
            build_report_path=args.build_report,
            rollback_report_path=args.rollback_report,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
