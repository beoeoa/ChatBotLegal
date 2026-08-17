from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.build_retrieval_release_v2_shadow import (
    _load_manifest,
    _vector_content_sha,
    _vector_content_sha_stream,
    _validate_resume_metadata,
    _validate_chunks,
    build,
)


def _chunk(**overrides):
    content = "Nội dung pháp lý"
    passage = "Văn bản\n01/2026/NĐ-CP\nĐiều 1\n" + content
    row = {
        "chunk_revision_id": "r-1",
        "release_id": "r1",
        "document_id": 1,
        "article_id": 1,
        "structural_path": "Điều 1",
        "content": content,
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "embedding_text": passage,
        "embedding_text_sha256": hashlib.sha256(passage.encode()).hexdigest(),
        "source_content_sha256": "a" * 64,
        "passage_sha256": hashlib.sha256(passage.encode()).hexdigest(),
        "token_count": 20,
        "eligible": True,
        "serving_state": "retrievable",
        "document_serving_state": "current_retrievable",
    }
    row.update(overrides)
    return row


def _manifest(tmp_path: Path, **overrides) -> Path:
    payload = {
        "schema_version": "legal-retrieval-chunk-manifest-v2",
        "approved": True,
        "legal_review_attestation": True,
        "source_snapshot_sha256": "b" * 64,
        "model_artifact_fingerprint": "c" * 64,
        "tokenizer_fingerprint": "d" * 64,
        "embedding_recipe_fingerprint": "e" * 64,
        "passage_recipe_fingerprint": "f" * 64,
        "splitter_fingerprint": "1" * 64,
        "dependency_lock_fingerprint": "2" * 64,
        "chunks": [_chunk()],
    }
    payload.update(overrides)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_shadow_dry_run_never_copies_legacy_vectors(tmp_path: Path):
    result = build(
        manifest_path=_manifest(tmp_path),
        chroma_path=tmp_path / "chroma",
        target_name="shadow",
        output=tmp_path / "report.json",
        apply=False,
        resume=False,
        batch_size=32,
        document_state="current_retrievable",
    )
    assert result["status"] == "DRY_RUN"
    assert result["fresh_embedding_required"] is True
    assert result["legacy_vector_copy"] is False


def test_shadow_dry_run_writes_no_collection_and_preserves_pointer(tmp_path: Path):
    chroma_path = tmp_path / "chroma"
    result = build(
        manifest_path=_manifest(tmp_path),
        chroma_path=chroma_path,
        target_name="shadow",
        output=tmp_path / "report.json",
        apply=False,
        resume=False,
        batch_size=32,
        document_state="all",
    )
    assert result["status"] == "DRY_RUN"
    assert not chroma_path.exists()


class _FakeCollection:
    def __init__(self, rows):
        self.rows = {str(identifier): [float(value) for value in vector] for identifier, vector in rows}

    def get(self, *, ids=None, include=None):
        if ids is None:
            return {"ids": list(self.rows)}
        selected = [str(identifier) for identifier in ids]
        return {"ids": selected, "embeddings": [self.rows[identifier] for identifier in selected]}


def test_streaming_vector_checksum_matches_canonical_checksum():
    rows = [("b", [0.6, 0.8]), ("a", [0.8, 0.6])]
    collection = _FakeCollection(rows)
    streamed, count = _vector_content_sha_stream(collection, batch_size=1)
    expected = _vector_content_sha(
        [identifier for identifier, _ in rows],
        [vector for _, vector in rows],
    )
    assert count == 2
    assert streamed == expected


def test_shadow_loader_rejects_unapproved_manifest(tmp_path: Path):
    path = _manifest(tmp_path, approved=False)
    with pytest.raises(RuntimeError, match="approved_v2_chunk_manifest_required"):
        _load_manifest(path)


def test_shadow_loader_allows_only_explicit_provisional_staging(tmp_path: Path):
    path = _manifest(
        tmp_path,
        approved=False,
        legal_review_attestation=False,
        approval_blocker="official_source_and_metadata_review_required",
    )
    payload = _load_manifest(path, allow_provisional_staging=True)
    assert payload["provisional_staging"] is True
    with pytest.raises(RuntimeError, match="provisional_target_name_required"):
        build(
            manifest_path=path,
            chroma_path=tmp_path / "chroma",
            target_name="shadow",
            output=tmp_path / "report.json",
            apply=False,
            resume=False,
            batch_size=32,
            document_state="all",
            allow_provisional_staging=True,
        )


def test_provisional_shadow_dry_run_is_not_release_eligible(tmp_path: Path):
    path = _manifest(
        tmp_path,
        approved=False,
        legal_review_attestation=False,
        approval_blocker="official_source_and_metadata_review_required",
    )
    result = build(
        manifest_path=path,
        chroma_path=tmp_path / "chroma",
        target_name="shadow-provisional",
        output=tmp_path / "report.json",
        apply=False,
        resume=False,
        batch_size=32,
        document_state="all",
        allow_provisional_staging=True,
    )
    assert result["status"] == "PROVISIONAL_DRY_RUN"
    assert result["release_eligible"] is False


def test_shadow_validation_rejects_checksum_and_budget_errors():
    with pytest.raises(RuntimeError, match="content_checksum_mismatch"):
        _validate_chunks({"chunks": [_chunk(content_sha256="c" * 64)]}, document_state="all")
    with pytest.raises(RuntimeError, match="chunk_token_budget"):
        _validate_chunks({"chunks": [_chunk(token_count=513)]}, document_state="all")


def test_shadow_resume_rejects_mixed_fingerprint_or_non_benchmark_collection():
    expected = {"release_id": "r1", "model_artifact_fingerprint": "a" * 64,
                "benchmark_only": "true"}
    with pytest.raises(RuntimeError, match="fingerprint_mismatch:model_artifact_fingerprint"):
        _validate_resume_metadata({"release_id": "r1", "model_artifact_fingerprint": "b" * 64,
                                   "benchmark_only": "true"}, expected)
    with pytest.raises(RuntimeError, match="benchmark_only_contract_missing"):
        _validate_resume_metadata({"release_id": "r1", "model_artifact_fingerprint": "a" * 64,
                                   "benchmark_only": "false"}, expected)
