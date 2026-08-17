from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from api.vector_serving_manifest import build_vector_serving_manifest


FINGERPRINTS = {
    "embedding": "embed-v1",
    "splitter": "split-v1",
    "pipeline": "pipeline-v3",
    "validity_snapshot": "a" * 64,
}


def _record(
    chunk_id: int,
    *,
    vector_id: str | None = None,
    fingerprints: dict[str, str] | None = None,
) -> dict:
    return {
        "vector_id": vector_id or f"chunk-{chunk_id}",
        "chunk_id": chunk_id,
        "document_id": f"doc-{chunk_id}",
        "fingerprints": fingerprints or FINGERPRINTS,
    }


def test_manifest_classifies_all_required_serving_states():
    expected = [
        {"chunk_id": 1, "document_id": "doc-1", "serving_action": "allow"},
        {"chunk_id": 2, "document_id": "doc-2", "serving_action": "historical_only"},
        {"chunk_id": 3, "document_id": "doc-3", "document_status": "staging"},
        {"chunk_id": 4, "document_id": "doc-4", "serving_action": "allow"},
        {
            "chunk_id": 5,
            "document_id": "doc-5",
            "serving_action": "allow",
            "eligible": False,
            "canonical_chunk_id": 1,
        },
        {"chunk_id": 6, "document_id": "doc-6", "serving_action": "allow"},
    ]
    collections = [
        {
            "name": "legal-active-v1",
            "role": "active",
            "fingerprints": FINGERPRINTS,
            "records": [
                _record(1),
                _record(2),
                _record(5),
                _record(6, fingerprints={**FINGERPRINTS, "embedding": "embed-old"}),
                _record(99),
            ],
        },
        {
            "name": "legal-staging-v2",
            "role": "staging",
            "fingerprints": FINGERPRINTS,
            "records": [_record(3), _record(4)],
        },
    ]

    manifest = build_vector_serving_manifest(
        expected_chunks=expected,
        collections=collections,
        active_collection="legal-active-v1",
        expected_fingerprints=FINGERPRINTS,
        active_pointer_before="legal-active-v1",
        active_pointer_after="legal-active-v1",
    )

    assert manifest["counts"]["active"] == 1
    assert manifest["counts"]["historical"] == 1
    assert manifest["counts"]["staging"] == 1
    assert manifest["counts"]["missing"] == 1
    assert manifest["counts"]["duplicate"] == 1
    assert manifest["counts"]["fingerprint_mismatch"] == 1
    assert manifest["counts"]["orphan"] == 1
    assert manifest["state_chunk_ids"] == {
        "active": [1],
        "historical": [2],
        "staging": [3],
        "missing": [4],
        "orphan": [99],
        "duplicate": [5],
        "fingerprint_mismatch": [6],
    }
    assert manifest["gate_passed"] is False
    assert manifest["read_only"] is True
    assert manifest["vectors_mutated"] is False
    assert manifest["active_pointer_unchanged"] is True


def test_fingerprint_gate_requires_all_dimensions_and_is_deterministic():
    expected = [
        {"chunk_id": 10, "document_id": "doc-10", "serving_action": "allow"}
    ]
    collection = {
        "name": "active",
        "role": "active",
        "fingerprints": FINGERPRINTS,
        "records": [_record(10)],
    }
    first = build_vector_serving_manifest(
        expected_chunks=expected,
        collections=[collection],
        active_collection="active",
        expected_fingerprints=FINGERPRINTS,
        active_pointer_before="active",
        active_pointer_after="active",
    )
    second = build_vector_serving_manifest(
        expected_chunks=list(reversed(expected)),
        collections=[{**collection, "records": list(reversed(collection["records"]))}],
        active_collection="active",
        expected_fingerprints=dict(reversed(list(FINGERPRINTS.items()))),
        active_pointer_before="active",
        active_pointer_after="active",
    )

    assert first["gate_passed"] is True
    assert first["manifest_fingerprint"] == second["manifest_fingerprint"]
    assert first["inventory_sha256"] == second["inventory_sha256"]

    missing_splitter = build_vector_serving_manifest(
        expected_chunks=expected,
        collections=[
            {
                **collection,
                "fingerprints": {key: value for key, value in FINGERPRINTS.items() if key != "splitter"},
                "records": [{**_record(10), "fingerprints": {}}],
            }
        ],
        active_collection="active",
        expected_fingerprints=FINGERPRINTS,
        active_pointer_before="active",
        active_pointer_after="active",
    )
    assert missing_splitter["state_chunk_ids"]["fingerprint_mismatch"] == [10]
    assert "splitter_missing" in missing_splitter["findings_by_chunk"]["10"]


def test_active_pointer_change_fails_gate_without_reclassifying_inventory():
    manifest = build_vector_serving_manifest(
        expected_chunks=[],
        collections=[],
        active_collection="active-v1",
        expected_fingerprints=FINGERPRINTS,
        active_pointer_before="active-v1",
        active_pointer_after="active-v2",
    )
    assert manifest["active_pointer_unchanged"] is False
    assert manifest["gate_passed"] is False
    assert "active_pointer_changed" in manifest["reason_codes"]


def test_cli_requires_read_only_and_fixture_mode_never_calls_mutation(tmp_path: Path):
    from scripts import build_vector_serving_manifest as command

    inventory = tmp_path / "inventory.json"
    output = tmp_path / "manifest.json"
    inventory.write_text(
        json.dumps(
            {
                "active_collection": "active",
                "active_pointer": "active",
                "expected_fingerprints": FINGERPRINTS,
                "expected_chunks": [
                    {"chunk_id": 1, "document_id": "doc-1", "serving_action": "allow"}
                ],
                "collections": [
                    {
                        "name": "active",
                        "role": "active",
                        "fingerprints": FINGERPRINTS,
                        "records": [_record(1)],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="read_only_flag_required"):
        command.run(output=output, inventory=inventory, read_only=False)
    report = command.run(output=output, inventory=inventory, read_only=True)

    assert report["gate_passed"] is True
    assert report["write_operations_invoked"] == 0
    assert json.loads(output.read_text(encoding="utf-8"))["read_only"] is True


def test_chroma_adapter_uses_immutable_sqlite_and_preserves_store_bytes(tmp_path: Path):
    from scripts.build_vector_serving_manifest import _chroma_inventory

    database = tmp_path / "chroma.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE collection_metadata (
            collection_id TEXT, key TEXT, str_value TEXT, int_value INTEGER,
            float_value REAL, bool_value INTEGER
        );
        CREATE TABLE segments (
            id TEXT PRIMARY KEY, type TEXT, scope TEXT, collection TEXT
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT
        );
        CREATE TABLE embedding_metadata (
            id INTEGER, key TEXT, string_value TEXT, int_value INTEGER,
            float_value REAL, bool_value INTEGER, PRIMARY KEY (id, key)
        );
        INSERT INTO collections VALUES ('collection-1', 'legal-active');
        INSERT INTO collection_metadata VALUES (
            'collection-1', 'embedding_fingerprint', 'embed-v1', NULL, NULL, NULL
        );
        INSERT INTO segments VALUES ('segment-1', 'metadata', 'METADATA', 'collection-1');
        INSERT INTO embeddings VALUES (1, 'segment-1', 'chunk-7');
        INSERT INTO embedding_metadata VALUES (1, 'chunk_id', NULL, 7, NULL, NULL);
        INSERT INTO embedding_metadata VALUES (1, 'document_id', 'doc-7', NULL, NULL, NULL);
        """
    )
    connection.commit()
    connection.close()
    before = (database.read_bytes(), database.stat().st_mtime_ns)

    collections, warnings = _chroma_inventory(
        path=tmp_path, active_collection="legal-active", batch_size=10
    )

    after = (database.read_bytes(), database.stat().st_mtime_ns)
    assert warnings == []
    assert collections[0]["records"][0]["chunk_id"] == 7
    assert collections[0]["fingerprints"]["embedding"] == "embed-v1"
    assert after == before
