from __future__ import annotations

import pytest

from scripts.verify_retrieval_release_v2_vectors import (
    EMBEDDING_MAX_LENGTH,
    _encode_with_device,
    _validate_collection_metadata,
)
from scripts.build_retrieval_release_v2_shadow import _vector_content_sha_stream


class _MetadataCollection:
    def __init__(self, rows):
        self.rows = {str(identifier): dict(metadata) for identifier, metadata in rows}

    def get(self, *, ids=None, include=None):
        selected = [str(identifier) for identifier in (ids or self.rows)]
        return {
            "ids": selected,
            "metadatas": [self.rows[identifier] for identifier in selected],
        }


def test_all_persisted_metadata_must_bind_to_release_and_snapshot():
    collection = _MetadataCollection([
        ("r-1", {
            "release_id": "release-1",
            "source_snapshot_sha256": "a" * 64,
            "serving_state": "retrievable",
        }),
        ("r-2", {
            "release_id": "release-1",
            "source_snapshot_sha256": "a" * 64,
            "serving_state": "retrievable",
        }),
    ])
    _validate_collection_metadata(
        collection,
        ["r-1", "r-2"],
        release_id="release-1",
        source_snapshot_sha256="a" * 64,
        batch_size=1,
    )


def test_persisted_metadata_mismatch_is_rejected():
    collection = _MetadataCollection([
        ("r-1", {
            "release_id": "wrong-release",
            "source_snapshot_sha256": "a" * 64,
            "serving_state": "retrievable",
        }),
    ])
    with pytest.raises(RuntimeError, match="vector_metadata_release_mismatch"):
        _validate_collection_metadata(
            collection,
            ["r-1"],
            release_id="release-1",
            source_snapshot_sha256="a" * 64,
        )


def test_provisional_metadata_requires_explicit_marker():
    collection = _MetadataCollection([("r-1", {
        "release_id": "release-1",
        "source_snapshot_sha256": "a" * 64,
        "serving_state": "retrievable",
    })])
    with pytest.raises(RuntimeError, match="vector_metadata_provisional_marker_mismatch"):
        _validate_collection_metadata(
            collection,
            ["r-1"],
            release_id="release-1",
            source_snapshot_sha256="a" * 64,
            provisional_staging=True,
        )


def test_replay_encoder_uses_v2_max_length(monkeypatch):
    calls = []

    class _Retriever:
        def __init__(self):
            pass

        def encode_passages(self, passages, *, batch_size, max_length):
            calls.append({"batch_size": batch_size, "max_length": max_length})
            return [[1.0, 0.0] for _ in passages]

    import scripts.legal_search_server as server

    monkeypatch.setattr(server, "LegalRetriever", _Retriever)
    vectors = _encode_with_device(
        [{"chunk_revision_id": "r-1", "content": "sample"}],
        device="cpu",
        batch_size=8,
    )
    assert vectors == [[1.0, 0.0]]
    assert calls == [{"batch_size": 8, "max_length": EMBEDDING_MAX_LENGTH}]
    assert EMBEDDING_MAX_LENGTH == 512


class _VectorCollection:
    def __init__(self, vectors):
        self.vectors = {str(identifier): list(vector) for identifier, vector in vectors}

    def get(self, *, ids=None, include=None):
        selected = sorted(self.vectors) if ids is None else [str(identifier) for identifier in ids]
        return {
            "ids": selected,
            "embeddings": [self.vectors[identifier] for identifier in selected],
        }


def test_persisted_vector_hash_can_enforce_fixed_dimension():
    collection = _VectorCollection([("r-1", [1.0, 0.0])])
    with pytest.raises(RuntimeError, match="persisted_vector_dimension_mismatch"):
        _vector_content_sha_stream(collection, expected_dimension=1024)
