#!/usr/bin/env python3
"""Verify a Retrieval V2 Chroma shadow through its read-only SQLite store.

The Stage C vector shards are already structurally and numerically verified.
This verifier checks the persisted Chroma IDs and metadata without loading a
million 1,024-dimensional vectors into Python.  It never writes to Chroma,
PostgreSQL, or the active pointer.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import file_sha256


REQUIRED_METADATA_KEYS = (
    "chunk_revision_id",
    "document_id",
    "article_id",
    "release_id",
    "source_snapshot_sha256",
    "document_serving_state",
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return payload


def _expected_ids(input_manifest_path: Path, document_state: str) -> set[str]:
    manifest = _load(input_manifest_path)
    expected: set[str] = set()
    for shard in manifest.get("shards") or []:
        shard_path = input_manifest_path.parent / str(shard["path"])
        with shard_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                row = json.loads(line)
                state = str(row.get("document_serving_state") or "")
                if document_state == "all" or state == document_state:
                    identifier = str(row["chunk_revision_id"])
                    if identifier in expected:
                        raise RuntimeError(f"duplicate_expected_chunk:{identifier}")
                    expected.add(identifier)
    return expected


def _collection_id(connection: sqlite3.Connection, name: str) -> str:
    row = connection.execute("SELECT id FROM collections WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise RuntimeError(f"collection_missing:{name}")
    return str(row[0])


def _metadata_value(row: tuple[Any, ...]) -> Any:
    _, string_value, int_value, float_value, bool_value = row
    if string_value is not None:
        return string_value
    if int_value is not None:
        return int_value
    if float_value is not None:
        return float_value
    return bool_value


def verify(
    *,
    input_manifest_path: Path,
    chroma_path: Path,
    collection_name: str,
    document_state: str,
    vector_content_sha256: str,
    output: Path,
) -> dict[str, Any]:
    if document_state not in {"all", "current_retrievable", "historical_only"}:
        raise ValueError("invalid_document_state")
    expected = _expected_ids(input_manifest_path, document_state)
    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else ""
    db_path = chroma_path / "chroma.sqlite3"
    connection = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=30)
    try:
        collection_id = _collection_id(connection, collection_name)
        # Chroma's ``embeddings`` table is keyed by the metadata segment ID;
        # the vector HNSW segment stores the persisted index separately.
        segment = connection.execute(
            "SELECT id FROM segments WHERE collection = ? AND scope = 'METADATA'",
            (collection_id,),
        ).fetchone()
        if segment is None:
            raise RuntimeError(f"vector_segment_missing:{collection_name}")
        segment_id = str(segment[0])
        actual = {
            str(row[0])
            for row in connection.execute(
                "SELECT embedding_id FROM embeddings WHERE segment_id = ?",
                (segment_id,),
            )
        }
        missing = sorted(expected - actual)
        orphan = sorted(actual - expected)

        metadata_failures: dict[str, int] = {}
        for key in REQUIRED_METADATA_KEYS:
            missing_count = int(connection.execute(
                """
                SELECT COUNT(*) FROM embeddings e
                WHERE e.segment_id = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM embedding_metadata m
                      WHERE m.id = e.id AND m.key = ?
                  )
                """,
                (segment_id, key),
            ).fetchone()[0])
            if missing_count:
                metadata_failures[key] = missing_count

        collection_metadata = {
            str(row[0]): _metadata_value(row)
            for row in connection.execute(
                "SELECT key,str_value,int_value,float_value,bool_value FROM collection_metadata WHERE collection_id = ?",
                (collection_id,),
            )
        }
        expected_input = _load(input_manifest_path)
        metadata_contract_failures = {
            "release_id": collection_metadata.get("release_id") != expected_input.get("release_id"),
            "source_snapshot_sha256": collection_metadata.get("source_snapshot_sha256") != expected_input.get("source_snapshot_sha256"),
            "document_state": document_state == "current_retrievable"
            and collection_metadata.get("document_state") != "current_retrievable",
        }
        metadata_contract_failures = {
            key: value for key, value in metadata_contract_failures.items() if value
        }
        pointer_after = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else ""
        report = {
            "schema_version": "legal-retrieval-v2-chroma-shadow-sqlite-verification-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "input_manifest_path": str(input_manifest_path.resolve()),
            "input_manifest_file_sha256": file_sha256(input_manifest_path),
            # Bind the shadow collection to the approved chunk manifest as
            # well as to the Kaggle input wrapper.  Manifest V3 consumes this
            # logical hash; the file hash above remains useful for replay.
            "manifest_sha256": expected_input.get("source_manifest_sha256"),
            "manifest_file_sha256": expected_input.get("source_manifest_file_sha256"),
            "release_id": expected_input.get("release_id"),
            "source_snapshot_sha256": expected_input.get("source_snapshot_sha256"),
            "target_collection": collection_name,
            "collection_id": collection_id,
            "segment_id": segment_id,
            "document_state": document_state,
            "expected_vector_count": len(expected),
            "actual_vector_count": len(actual),
            "missing_ids": missing,
            "orphan_ids": orphan,
            "metadata_failures": metadata_failures,
            "metadata_contract_failures": metadata_contract_failures,
            "hydration_contract": {
                "required_metadata_keys": list(REQUIRED_METADATA_KEYS),
                "missing_key_counts": metadata_failures,
                "passed": not metadata_failures,
            },
            "collection_metadata": collection_metadata,
            "vector_content_sha256": vector_content_sha256,
            "persisted_vector_binding": "stage-c-verified-shards-imported_without_reencoding",
            "active_pointer_before": pointer_before,
            "active_pointer_after": pointer_after,
            "active_pointer_unchanged": pointer_before == pointer_after,
            "valid": (
                expected == actual
                and not metadata_failures
                and not metadata_contract_failures
                and pointer_before == pointer_after
                and len(actual) == len(expected)
            ),
            "mutation": {
                "collection_mutated": False,
                "active_pointer_changed": pointer_before != pointer_after,
                "live_sql_mutated": False,
            },
        }
    finally:
        connection.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--document-state", choices=("all", "current_retrievable", "historical_only"), required=True)
    parser.add_argument("--vector-content-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(
        input_manifest_path=args.input_manifest.resolve(),
        chroma_path=args.chroma_path.resolve(),
        collection_name=args.collection,
        document_state=args.document_state,
        vector_content_sha256=args.vector_content_sha256,
        output=args.output.resolve(),
    )
    print(json.dumps({"status": "PASS" if report["valid"] else "FAIL", "expected": report["expected_vector_count"], "actual": report["actual_vector_count"], "missing": len(report["missing_ids"]), "orphan": len(report["orphan_ids"])}, ensure_ascii=False))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
