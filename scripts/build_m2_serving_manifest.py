#!/usr/bin/env python3
"""Build an immutable M2 runtime serving manifest from the frozen M1 baseline."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy import ARRAY, Integer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_serving_scope import canonical_manifest_sha256, file_sha256
from scripts.build_legal_corpus_candidate import _database_url

DEFAULT_BASELINE = ROOT / "reports" / "m1-freeze" / "baseline_manifest.json"
DEFAULT_CANDIDATE = ROOT / "reports" / "m1-freeze" / "candidate_manifest.json"
DEFAULT_DIRECTORY = ROOT / "release-data" / "legal" / "serving_manifests"
DEFAULT_POINTER = DEFAULT_DIRECTORY / "active_serving_manifest.json"
DEFAULT_REPORT = ROOT / "reports" / "m2-serving-manifest" / "m2_report.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _write_immutable(path: Path, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists():
        existing = _load(path)
        comparable_existing = dict(existing)
        comparable_payload = dict(payload)
        for value in (comparable_existing, comparable_payload):
            value.pop("generated_at", None)
            value.pop("manifest_sha256", None)
        if comparable_existing != comparable_payload:
            raise RuntimeError("serving_manifest_version_already_exists")
        payload.clear()
        payload.update(existing)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, path)
    path.chmod(stat.S_IREAD)
    return file_sha256(path)


def _source_ids(document_ids: list[int]) -> dict[int, Any]:
    statement = text(
        "SELECT id, source_id FROM legal_documents WHERE id = ANY(:document_ids)"
    ).bindparams(bindparam("document_ids", type_=ARRAY(Integer)))
    engine = create_engine(_database_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                statement, {"document_ids": document_ids}
            ).mappings()
            return {int(row["id"]): row.get("source_id") for row in rows}
    finally:
        engine.dispose()


def build_manifest(
    baseline: dict[str, Any], candidate: dict[str, Any], *, dataset_version: str
) -> dict[str, Any]:
    baseline_documents = list(baseline.get("documents") or [])
    candidate_by_id = {
        int(item["document_id"]): item for item in candidate.get("documents") or []
    }
    ids = [int(item["document_id"]) for item in baseline_documents]
    source_ids = _source_ids(ids)
    if len(source_ids) != len(ids):
        missing = sorted(set(ids) - set(source_ids))
        raise RuntimeError(f"serving_manifest_database_documents_missing:{missing[:10]}")

    documents: list[dict[str, Any]] = []
    all_chunks: set[int] = set()
    for baseline_row in baseline_documents:
        document_id = int(baseline_row["document_id"])
        candidate_row = candidate_by_id.get(document_id) or {}
        chunk_ids = sorted(int(value) for value in baseline_row["expected_chunk_ids"])
        overlap = all_chunks.intersection(chunk_ids)
        if overlap:
            raise RuntimeError(f"serving_manifest_duplicate_chunks:{sorted(overlap)[:10]}")
        all_chunks.update(chunk_ids)
        score_components = dict(candidate_row.get("score_components") or {})
        documents.append(
            {
                "document_id": document_id,
                "law_number": str(baseline_row.get("law_number") or ""),
                "status": str(baseline_row.get("stored_status") or "active"),
                "domain": str(baseline_row.get("domain") or ""),
                "visibility": "public",
                "reason_kept": list(candidate_row.get("reason_kept") or [
                    str(baseline_row.get("scope_reason") or "m1_frozen_baseline")
                ]),
                "score_components": score_components,
                "required_procedures": list(
                    candidate_row.get("required_procedure_mappings") or []
                ),
                "chunk_ids": chunk_ids,
                # Preserve the official database value. Null means provenance
                # is unavailable and must not be replaced with a model guess.
                "source_id": source_ids[document_id],
                "validity_confidence": score_components.get("validity_confidence"),
            }
        )

    collection = baseline.get("collection") or {}
    payload: dict[str, Any] = {
        "schema_version": "legal-serving-manifest-v2",
        "manifest_version": "m2-v1",
        "dataset_version": dataset_version,
        "legal_as_of": str(baseline.get("legal_as_of") or ""),
        "collection_name": str(collection.get("collection_name") or ""),
        "embedding_fingerprint": str(collection.get("embedding_fingerprint") or ""),
        "document_count": len(documents),
        "chunk_count": len(all_chunks),
        "visibility_policy": "backend-role-v1",
        "source_baseline_manifest_sha256": str(baseline.get("manifest_sha256") or ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents": documents,
    }
    if not payload["collection_name"] or not payload["embedding_fingerprint"]:
        raise RuntimeError("serving_manifest_baseline_attestation_missing")
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--pointer", type=Path, default=DEFAULT_POINTER)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--dataset-version", default="legal-baseline-7245-m2-v1")
    args = parser.parse_args()

    payload = build_manifest(
        _load(args.baseline), _load(args.candidate),
        dataset_version=args.dataset_version,
    )
    target = args.directory / f"{args.dataset_version}.json"
    file_hash = _write_immutable(target, payload)
    pointer = {
        "schema_version": "legal-serving-pointer-v1",
        "manifest_path": target.relative_to(args.pointer.parent).as_posix(),
        "file_sha256": file_hash,
        "manifest_sha256": payload["manifest_sha256"],
        "dataset_version": payload["dataset_version"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(args.pointer, pointer)
    report = {
        "schema_version": "legal-m2-report-v1",
        "status": "pass",
        "manifest_path": str(target.resolve()),
        "pointer_path": str(args.pointer.resolve()),
        "file_sha256": file_hash,
        "manifest_sha256": payload["manifest_sha256"],
        "document_count": payload["document_count"],
        "chunk_count": payload["chunk_count"],
        "collection_name": payload["collection_name"],
        "embedding_fingerprint": payload["embedding_fingerprint"],
        "gates": {
            "immutable_versioned_manifest": "pass",
            "pointer_checksum_bound": "pass",
            "required_schema_fields": "pass",
            "document_and_chunk_uniqueness": "pass",
            "official_source_id_not_fabricated": "pass",
        },
    }
    _atomic_json(args.report, report)
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
