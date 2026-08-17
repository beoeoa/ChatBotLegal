#!/usr/bin/env python3
"""Build the isolated 3,000-document Chroma collection from a gated manifest.

Baseline vectors are copied byte-for-byte from the active collection.  Only
chunks absent from that immutable collection (currently the verified staged
source) are embedded with the runtime VNLegal-LAL model.  The active pointer is
never changed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import chromadb
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_legal_corpus_candidate import _database_url
from scripts.rebuild_quality_chroma import _metadata, _passage
from scripts.legal_search_server import retriever
from api.retrieval_release_contracts import require_staging_collection_target


DEFAULT_MANIFEST = ROOT / "reports" / "corpus-thinning-remediated-v2" / "legal-serving-candidate-3000-v1.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_TARGET = "legal_chunks_candidate_3000_v1"
BATCH_SIZE = 1000


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("status") != "ready_for_candidate_build":
        raise RuntimeError("candidate_manifest_not_ready")
    gate = payload.get("prebuild_gate") or {}
    if gate.get("allowed") is not True or gate.get("blockers"):
        raise RuntimeError(f"candidate_prebuild_gate_blocked:{gate}")
    if payload.get("required_source_blockers"):
        raise RuntimeError("candidate_required_sources_blocked")
    if int(payload.get("selected_document_count") or 0) != 3000:
        raise RuntimeError("candidate_document_count_not_3000")
    return payload


def _stage_rows(chunk_ids: list[int]) -> list[dict[str, Any]]:
    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    statement = text(
        """
        SELECT c.id AS chunk_id, c.chunk_index, c.heading AS chunk_heading, c.content,
               a.id AS article_id, a.article_number, a.title AS article_title,
               d.id AS document_id, d.title AS document_title, d.law_number,
               d.document_type, d.issuing_agency, d.scope, d.sector, d.field_id,
               f.name AS field_name, d.source_url, d.effective_date,
               'dat_dai_xay_dung' AS domain_slug
        FROM legal_article_chunks c
        JOIN legal_articles a ON a.id = c.article_id
        JOIN legal_documents d ON d.id = a.document_id
        LEFT JOIN legal_fields f ON f.id = d.field_id
        WHERE c.id = ANY(:chunk_ids)
        ORDER BY c.id
        """
    )
    try:
        with engine.connect() as connection:
            return [dict(row) for row in connection.execute(statement, {"chunk_ids": chunk_ids}).mappings()]
    finally:
        engine.dispose()


def _existing_ids(collection: Any, chunk_ids: list[int]) -> set[str]:
    observed: set[str] = set()
    for start in range(0, len(chunk_ids), BATCH_SIZE):
        response = collection.get(
            ids=[f"chunk-{item}" for item in chunk_ids[start : start + BATCH_SIZE]],
            include=[],
        )
        observed.update(str(item) for item in (response.get("ids") or []))
    return observed


def build(*, manifest_path: Path, chroma_path: Path, target_name: str, output: Path, resume: bool) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    active_pointer = require_staging_collection_target(target_name, chroma_path) or ""
    expected_ids = sorted(
        int(chunk_id)
        for document in manifest["documents"]
        for chunk_id in document["expected_chunk_ids"]
    )
    if len(expected_ids) != len(set(expected_ids)):
        raise RuntimeError("candidate_manifest_duplicate_chunk_ids")
    client = chromadb.PersistentClient(path=str(chroma_path))
    source_name = str(manifest["baseline_collection"])
    source = client.get_collection(source_name)
    names = {item.name for item in client.list_collections()}
    if target_name in names and not resume:
        raise RuntimeError(f"candidate_collection_exists:{target_name}")
    target = client.get_collection(target_name) if target_name in names else client.create_collection(
        target_name,
        metadata={
            "schema_version": "legal-candidate-chroma-v1",
            "source_collection": source_name,
            "baseline_manifest_sha256": manifest["baseline_manifest_sha256"],
            "candidate_manifest_sha256": manifest["manifest_sha256"],
            "embedding_fingerprint": str(getattr(retriever, "_model_fingerprint", "")),
            "pipeline_fingerprint": "legal-corpus-candidate-v2",
            "distance_metric": str((source.metadata or {}).get("hnsw:space") or "cosine"),
            "benchmark_only": "true",
        },
    )
    existing = _existing_ids(target, expected_ids) if resume else set()
    baseline_present = _existing_ids(source, expected_ids)
    pending_ids = [chunk_id for chunk_id in expected_ids if f"chunk-{chunk_id}" not in existing]
    copied = 0
    embedded = 0
    missing_from_source: list[int] = []
    for start in range(0, len(pending_ids), BATCH_SIZE):
        batch_ids = pending_ids[start : start + BATCH_SIZE]
        response = source.get(ids=[f"chunk-{item}" for item in batch_ids], include=["embeddings", "metadatas"])
        found_ids = list(response.get("ids") or [])
        found = set(found_ids)
        if found_ids:
            target.upsert(ids=found_ids, embeddings=response["embeddings"], metadatas=response["metadatas"])
            copied += len(found_ids)
        missing_from_source.extend(item for item in batch_ids if f"chunk-{item}" not in found)

    if missing_from_source:
        rows = _stage_rows(missing_from_source)
        by_id = {int(row["chunk_id"]): row for row in rows}
        if set(missing_from_source) != set(by_id):
            raise RuntimeError("candidate_missing_chunk_rows")
        passages = [_passage(by_id[item]) for item in missing_from_source]
        embeddings = retriever.encode_passages(passages, batch_size=32)
        metadata = []
        for item in missing_from_source:
            row = by_id[item]
            values = _metadata(row)
            values.update({"status": "active", "doc_status": "staging", "candidate_only": True})
            metadata.append(values)
        target.upsert(ids=[f"chunk-{item}" for item in missing_from_source], embeddings=embeddings, metadatas=metadata)
        embedded = len(missing_from_source)

    observed = _existing_ids(target, expected_ids)
    expected_vector_ids = {f"chunk-{item}" for item in expected_ids}
    missing = sorted(expected_vector_ids - observed)
    orphan = sorted(observed - expected_vector_ids)
    report = {
        "schema_version": "legal-candidate-chroma-build-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path.resolve()),
        "manifest_file_sha256": _file_sha256(manifest_path),
        "baseline_collection": source_name,
        "candidate_collection": target_name,
        "expected_vector_count": len(expected_vector_ids),
        "actual_vector_count": target.count(),
        "copied_from_baseline": copied,
        "preexisting_target_vectors": len(existing),
        "baseline_vectors_in_candidate_total": len(baseline_present),
        "copied_from_baseline_total": len(baseline_present),
        "embedded_staged_chunks": embedded,
        "missing_vector_ids": [int(item.removeprefix("chunk-")) for item in sorted(missing)],
        "orphan_vector_ids": [int(item.removeprefix("chunk-")) for item in sorted(orphan)],
        "exact_chunk_set_match": not missing and not orphan and target.count() == len(expected_vector_ids),
        "embedding_fingerprint": str(getattr(retriever, "_model_fingerprint", "")),
        "active_pointer_changed": False,
        "active_pointer_before": active_pointer,
        "active_pointer_after": active_pointer,
        "baseline_collection_mutated": False,
        "benchmark_only": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["exact_chunk_set_match"]:
        raise RuntimeError(json.dumps(report, ensure_ascii=False))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    report = build(
        manifest_path=args.manifest.resolve(),
        chroma_path=args.chroma_path.resolve(),
        target_name=args.target,
        output=args.output.resolve(),
        resume=args.resume,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
