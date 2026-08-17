from __future__ import annotations

import json

import pytest

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from api.retrieval_serving_manifest_v3 import (
    V3ServingManifestError,
    load_v3_serving_manifest,
)


def _pointer(tmp_path, **overrides):
    lexical = tmp_path / "index.sqlite3"
    lexical.write_bytes(b"staging-index")
    documents = [
        {
            "document_id": 1,
            "serving_state": "current_retrievable",
            "chunk_count": 1,
            "chunk_ids_sha256": "a" * 64,
        },
        {
            "document_id": 2,
            "serving_state": "historical_only",
            "chunk_count": 1,
            "chunk_ids_sha256": "b" * 64,
        },
        {
            "document_id": 3,
            "serving_state": "future_effective",
            "chunk_count": 0,
            "chunk_ids_sha256": "c" * 64,
        },
    ]
    documents.extend(
        {
            "document_id": number,
            "serving_state": "quarantined",
            "chunk_count": 0,
            "chunk_ids_sha256": "d" * 64,
        }
        for number in range(4, 12_237)
    )
    payload = {
        "schema_version": "legal-serving-manifest-v3",
        "kind": "release_pointer",
        "manifest_version": "manifest-v3",
        "dataset_version": "dataset-v2",
        "release_id": "release-v2",
        "source_snapshot_sha256": "d" * 64,
        "chunk_manifest_sha256": "e" * 64,
        "chunk_manifest_file_sha256": "f" * 64,
        "legal_as_of": "2026-08-16",
        "inventory_document_count": 12_236,
        "document_state_counts": {
            "current_retrievable": 1,
            "historical_only": 1,
            "future_effective": 1,
            "quarantined": 12_233,
        },
        "current_collection": {
            "kind": "chroma_collection",
            "path": "chroma://current-v2",
            "sha256": "1" * 64,
            "release_id": "release-v2",
            "source_snapshot_sha256": "d" * 64,
            "count": 1,
        },
        "temporal_collection": {
            "kind": "chroma_collection",
            "path": "chroma://temporal-v2",
            "sha256": "2" * 64,
            "release_id": "release-v2",
            "source_snapshot_sha256": "d" * 64,
            "count": 2,
        },
        "exact_lexical_index": {
            "kind": "sqlite_exact_fts5",
            "path": lexical.relative_to(tmp_path).as_posix(),
            "sha256": file_sha256(lexical),
            "release_id": "release-v2",
            "source_snapshot_sha256": "d" * 64,
            "count": 2,
        },
        "provenance": {
            "model_artifact_fingerprint": "3" * 64,
            "tokenizer_fingerprint": "4" * 64,
            "embedding_recipe_fingerprint": "5" * 64,
            "passage_recipe_fingerprint": "6" * 64,
            "splitter_fingerprint": "7" * 64,
            "dependency_lock_fingerprint": "8" * 64,
            "quality_policy_version": "legal-chunk-quality-v2",
        },
        "quality_policy_version": "legal-chunk-quality-v2",
        "documents": documents,
        "generated_at": "2026-08-16T00:00:00+00:00",
        "activation_performed": False,
        "active_pointer_changed": False,
    }
    payload.update(overrides)
    payload["manifest_sha256"] = canonical_sha256(payload)
    path = tmp_path / "serving-manifest-v3.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path, payload, lexical


def test_v3_loader_validates_atomic_pointer_and_file_artifact(tmp_path):
    path, payload, lexical = _pointer(tmp_path)
    loaded = load_v3_serving_manifest(
        path,
        expected_file_sha256=file_sha256(path),
        project_root=tmp_path,
        verify_file_artifacts=True,
    )
    assert loaded.release_id == "release-v2"
    assert loaded.current_collection == "current-v2"
    assert loaded.temporal_collection == "temporal-v2"
    assert loaded.artifact_path("exact_lexical_index", project_root=tmp_path) == lexical.resolve()


def test_v3_loader_rejects_mutated_pointer_checksum(tmp_path):
    path, payload, _ = _pointer(tmp_path)
    payload["dataset_version"] = "mutated"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(V3ServingManifestError, match="content_checksum_mismatch"):
        load_v3_serving_manifest(path)


def test_v3_loader_rejects_quarantined_document_with_chunks(tmp_path):
    path, payload, _ = _pointer(tmp_path)
    payload["documents"][3]["chunk_count"] = 1
    payload["document_state_counts"]["current_retrievable"] = 2
    payload["document_state_counts"]["quarantined"] = 12_232
    payload["manifest_sha256"] = canonical_sha256({key: value for key, value in payload.items() if key != "manifest_sha256"})
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(V3ServingManifestError, match="quarantined_document_has_chunks"):
        load_v3_serving_manifest(path)


def test_v3_loader_rejects_missing_provenance(tmp_path):
    path, payload, _ = _pointer(tmp_path)
    payload["provenance"].pop("splitter_fingerprint")
    payload["manifest_sha256"] = canonical_sha256({key: value for key, value in payload.items() if key != "manifest_sha256"})
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(V3ServingManifestError, match="provenance_splitter_fingerprint"):
        load_v3_serving_manifest(path)


def test_v3_loader_rejects_provisional_pointer_by_default_but_allows_explicit_staging(tmp_path):
    path, payload, _ = _pointer(
        tmp_path,
        provisional_staging=True,
        release_eligible=False,
    )
    with pytest.raises(V3ServingManifestError, match="not_release_eligible"):
        load_v3_serving_manifest(path)
    loaded = load_v3_serving_manifest(path, allow_provisional_staging=True)
    assert loaded.payload["provisional_staging"] is True
    assert loaded.payload["release_eligible"] is False


def test_v1_scope_loader_rejects_v3_pointer(tmp_path):
    from api.legal_serving_scope import load_serving_manifest_scope

    path, _, _ = _pointer(tmp_path)
    with pytest.raises(RuntimeError, match="v3_release_pointer_requires_v2_runtime"):
        load_serving_manifest_scope(path)
