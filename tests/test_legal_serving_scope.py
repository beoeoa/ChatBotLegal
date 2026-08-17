from __future__ import annotations

import hashlib
import json

import pytest

from api.legal_serving_scope import (
    benchmark_scope_from_environment,
    canonical_manifest_sha256,
    file_sha256,
    load_serving_manifest_pointer,
    load_serving_manifest_scope,
    serving_scope_from_environment,
)


def _write_candidate(tmp_path, **overrides):
    payload = {
        "schema_version": "legal-serving-candidate-v1",
        "legal_as_of": "2026-08-14",
        "candidate_collection": "legal_chunks_candidate_3000_v1",
        "selected_document_count": 2,
        "expected_vector_count": 3,
        "manifest_sha256": "a" * 64,
        "documents": [
            {"document_id": 1, "expected_chunk_ids": [10, 11]},
            {"document_id": 2, "expected_chunk_ids": [20]},
        ],
    }
    payload.update(overrides)
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_runtime(tmp_path, **overrides):
    payload = {
        "schema_version": "legal-serving-manifest-v2",
        "manifest_version": "m2-v1",
        "dataset_version": "fixture-v1",
        "legal_as_of": "2026-08-15",
        "collection_name": "baseline",
        "document_count": 2,
        "chunk_count": 3,
        "documents": [
            {
                "document_id": 1, "law_number": "01/2026", "status": "active",
                "domain": "ho_tich", "visibility": "public",
                "reason_kept": ["required"], "score_components": {},
                "required_procedures": [], "chunk_ids": [10, 11],
                "source_id": "official-1", "validity_confidence": None,
            },
            {
                "document_id": 2, "law_number": "02/2026", "status": "active",
                "domain": "noi_vu", "visibility": "officer",
                "reason_kept": ["required"], "score_components": {},
                "required_procedures": [], "chunk_ids": [20],
                "source_id": None, "validity_confidence": 100,
            },
        ],
    }
    payload.update(overrides)
    payload["manifest_sha256"] = canonical_manifest_sha256(payload)
    path = tmp_path / "fixture-v1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload


def test_load_scope_binds_exact_document_and_chunk_sets(tmp_path):
    path = _write_candidate(tmp_path)
    scope = load_serving_manifest_scope(
        path, configured_collection="legal_chunks_candidate_3000_v1"
    )
    assert scope.document_ids == frozenset({1, 2})
    assert scope.chunk_ids == frozenset({10, 11, 20})
    assert scope.public_status()["benchmark_only"] is True


def test_load_scope_refuses_collection_mismatch(tmp_path):
    path = _write_candidate(tmp_path)
    with pytest.raises(RuntimeError, match="collection_mismatch"):
        load_serving_manifest_scope(path, configured_collection="baseline")


def test_load_scope_refuses_duplicate_chunk(tmp_path):
    path = _write_candidate(
        tmp_path,
        documents=[
            {"document_id": 1, "expected_chunk_ids": [10, 11]},
            {"document_id": 2, "expected_chunk_ids": [11]},
        ],
    )
    with pytest.raises(RuntimeError, match="duplicate_chunk"):
        load_serving_manifest_scope(path)


def test_load_scope_refuses_checksum_mismatch(tmp_path):
    path = _write_candidate(tmp_path)
    with pytest.raises(RuntimeError, match="checksum_mismatch"):
        load_serving_manifest_scope(path, expected_file_sha256="0" * 64)


def test_environment_loader_requires_explicit_benchmark_mode(tmp_path, monkeypatch):
    path = _write_candidate(tmp_path)
    monkeypatch.setenv("LEGAL_BENCHMARK_SERVING_MANIFEST", str(path))
    monkeypatch.delenv("LEGAL_BENCHMARK_MODE", raising=False)
    with pytest.raises(RuntimeError, match="requires_benchmark_mode"):
        benchmark_scope_from_environment(
            configured_collection="legal_chunks_candidate_3000_v1"
        )


def test_environment_loader_verifies_file_checksum(tmp_path, monkeypatch):
    path = _write_candidate(tmp_path)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setenv("LEGAL_BENCHMARK_MODE", "1")
    monkeypatch.setenv("LEGAL_BENCHMARK_SERVING_MANIFEST", str(path))
    monkeypatch.setenv("LEGAL_BENCHMARK_SERVING_MANIFEST_FILE_SHA256", checksum)
    scope = benchmark_scope_from_environment(
        configured_collection="legal_chunks_candidate_3000_v1"
    )
    assert scope is not None
    assert scope.file_sha256 == checksum


def test_runtime_scope_enforces_backend_visibility(tmp_path):
    path, _ = _write_runtime(tmp_path)
    scope = load_serving_manifest_scope(path, configured_collection="baseline")
    assert scope.benchmark_only is False
    assert scope.document_ids_for("citizen") == frozenset({1})
    assert scope.chunk_ids_for("citizen") == frozenset({10, 11})
    assert scope.document_ids_for("officer") == frozenset({1, 2})
    assert scope.chunk_ids_for("admin") == frozenset({10, 11, 20})


def test_pointer_is_checksum_bound_and_detects_manifest_mutation(tmp_path):
    path, payload = _write_runtime(tmp_path)
    pointer = tmp_path / "active_serving_manifest.json"
    pointer.write_text(json.dumps({
        "schema_version": "legal-serving-pointer-v1",
        "manifest_path": path.name,
        "file_sha256": file_sha256(path),
        "manifest_sha256": payload["manifest_sha256"],
    }), encoding="utf-8")
    assert load_serving_manifest_pointer(pointer).dataset_version == "fixture-v1"
    payload["documents"][0]["law_number"] = "MUTATED"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="file_checksum_mismatch"):
        load_serving_manifest_pointer(pointer)


def test_runtime_required_fails_closed_without_pointer(monkeypatch):
    monkeypatch.delenv("LEGAL_BENCHMARK_MODE", raising=False)
    monkeypatch.delenv("LEGAL_BENCHMARK_SERVING_MANIFEST", raising=False)
    monkeypatch.delenv("LEGAL_SERVING_MANIFEST_POINTER", raising=False)
    monkeypatch.delenv("LEGAL_SERVING_MANIFEST", raising=False)
    monkeypatch.setenv("LEGAL_SERVING_MANIFEST_REQUIRED", "true")
    with pytest.raises(RuntimeError, match="serving_manifest_required"):
        serving_scope_from_environment(configured_collection="baseline")
