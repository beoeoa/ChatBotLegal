from __future__ import annotations

import hashlib

import pytest

from scripts.benchmark_feature016_embedding_shadow import (
    ShadowIsolationError,
    build_shadow_manifest,
    evaluate_rankings,
)


def test_shadow_manifest_never_reuses_or_activates_active_collection(tmp_path):
    pointer = tmp_path / "active_core_collection.txt"
    pointer.write_text("legal_chunks_active\n", encoding="utf-8")
    before = hashlib.sha256(pointer.read_bytes()).hexdigest()

    manifest = build_shadow_manifest(
        active_collection="legal_chunks_active",
        model_id="BAAI/bge-m3",
        model_fingerprint="a" * 64,
        dataset_sha256="b" * 64,
        model_path=tmp_path / "missing-bge-m3",
    )

    assert manifest["shadow_collection"] != "legal_chunks_active"
    assert manifest["activation_requested"] is False
    assert manifest["active_collection_before"] == manifest["active_collection_after"]
    assert manifest["status"] == "disabled"
    assert manifest["reason_code"] == "model_path_missing"
    assert hashlib.sha256(pointer.read_bytes()).hexdigest() == before


def test_shadow_manifest_rejects_active_collection_name_collision(tmp_path):
    with pytest.raises(ShadowIsolationError, match="shadow_collection_must_differ"):
        build_shadow_manifest(
            active_collection="feature016-shadow-bge-m3-aaaaaaaaaaaa",
            shadow_collection="feature016-shadow-bge-m3-aaaaaaaaaaaa",
            model_id="BAAI/bge-m3",
            model_fingerprint="a" * 64,
            dataset_sha256="b" * 64,
            model_path=tmp_path,
        )


def test_shadow_quality_metrics_are_reproducible_on_isolated_rankings():
    expected = {"q1": {"d1"}, "q2": {"d4"}}
    active = {"q1": ["d2", "d1"], "q2": ["d3", "d4"]}
    shadow = {"q1": ["d1", "d2"], "q2": ["d4", "d3"]}

    result = evaluate_rankings(expected, active, shadow)

    assert result["active"]["recall_at_10"] == 1.0
    assert result["shadow"]["recall_at_10"] == 1.0
    assert result["shadow"]["mrr"] > result["active"]["mrr"]
    assert result["safety_regression_count"] == 0
    assert result["activation_eligible"] is True

