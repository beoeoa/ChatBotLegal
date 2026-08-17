from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from api.legal_vector_release import (
    LegalChunkRelease,
    LegalChunkRevision,
    LegalMetadataReviewCase,
    canonical_payload_sha256,
)
from scripts.validate_feature018_contracts import validate


ROOT = Path(__file__).resolve().parents[1]
FEATURE = ROOT / "specs" / "018-production-release-readiness"
SCHEMA = FEATURE / "contracts" / "legal-serving-manifest-v3.schema.json"
RUNNER = ROOT / "scripts" / "manage_vector_integrity_v3_schema.py"
HEX64 = "a" * 64


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "manage_vector_integrity_v3_schema", RUNNER
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _release(**overrides):
    values = {
        "release_id": "legal-vector-v3-test",
        "source_manifest_sha256": HEX64,
        "source_snapshot_sha256": "b" * 64,
        "model_artifact_fingerprint": "c" * 64,
        "tokenizer_fingerprint": "d" * 64,
        "embedding_recipe_fingerprint": "e" * 64,
        "passage_recipe_fingerprint": "f" * 64,
        "splitter_fingerprint": "1" * 64,
        "quality_policy_version": "legal-chunk-quality-v2",
        "dependency_lock_fingerprint": "2" * 64,
        "build_state": "complete",
        "inventory_document_count": 7245,
        "retrievable_document_count": 6000,
        "quarantined_document_count": 1245,
        "chunk_count": 120000,
        "vector_count": 120000,
    }
    values.update(overrides)
    return LegalChunkRelease.model_validate(values)


def test_manifest_v3_contract_separates_provenance_and_quarantine():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    required = set(schema["required"])
    assert {
        "model_artifact_fingerprint",
        "tokenizer_fingerprint",
        "embedding_recipe_fingerprint",
        "passage_recipe_fingerprint",
        "splitter_fingerprint",
        "source_snapshot_fingerprint",
        "dependency_lock_fingerprint",
        "vector_content_fingerprint",
        "quality_policy_version",
        "inventory_document_count",
        "retrievable_document_count",
        "quarantined_document_count",
    } <= required
    assert "embedding_fingerprint" not in required
    assert schema["additionalProperties"] is False
    document = schema["properties"]["documents"]["items"]
    assert set(document["properties"]["serving_state"]["enum"]) == {
        "retrievable",
        "quarantined",
    }
    assert any("chunk_ids" in branch.get("then", {}).get("properties", {}) for branch in document["allOf"])


def test_feature018_validator_includes_manifest_v3_contract():
    result = validate()
    assert "legal-serving-manifest-v3.schema.json" in result["contracts"]


def test_chunk_release_counts_are_fail_closed_and_models_are_immutable():
    release = _release()
    assert release.inventory_document_count == (
        release.retrievable_document_count + release.quarantined_document_count
    )
    with pytest.raises(ValidationError, match="inventory_document_count"):
        _release(quarantined_document_count=1200)
    with pytest.raises(ValidationError, match="vector_count"):
        _release(vector_count=119999)
    with pytest.raises(ValidationError):
        release.build_state = "building"


def test_chunk_revision_rejects_unassessed_empty_and_oversized_passages():
    base = {
        "release_id": "legal-vector-v3-test",
        "document_id": 10,
        "article_id": 20,
        "chunk_index": 0,
        "structural_path": "Điều 1 > Khoản 1",
        "child_kind": "clause",
        "heading": "Điều 1 > Khoản 1",
        "content": "Nội dung đã xác minh",
        "source_content_sha256": HEX64,
        "passage_sha256": "b" * 64,
        "token_count": 120,
        "quality_policy_version": "legal-chunk-quality-v2",
        "quality_assessed": True,
        "eligible": True,
        "serving_state": "retrievable",
        "quality_reasons": (),
    }
    revision = LegalChunkRevision.model_validate(base)
    assert revision.eligible is True
    for update, error in (
        ({"quality_assessed": False}, "quality_assessed"),
        ({"content": ""}, "content"),
        ({"token_count": 513}, "token_count"),
        ({"serving_state": "quarantined"}, "serving_state"),
    ):
        with pytest.raises(ValidationError, match=error):
            LegalChunkRevision.model_validate({**base, **update})


def test_metadata_review_never_approves_without_official_evidence_and_reviewer():
    base = {
        "case_id": "metadata-review-1",
        "document_id": 10,
        "field_name": "effective_date",
        "old_value": None,
        "proposed_value": "2026-01-01",
        "evidence_url": "https://example.gov.vn/document/10",
        "evidence_sha256": HEX64,
        "state": "approved",
        "reviewer_user_id": "admin-1",
    }
    approved = LegalMetadataReviewCase.model_validate(base)
    assert approved.state == "approved"
    for missing in ("evidence_url", "evidence_sha256", "reviewer_user_id"):
        invalid = dict(base)
        invalid[missing] = None
        with pytest.raises(ValidationError, match=missing):
            LegalMetadataReviewCase.model_validate(invalid)


def test_canonical_payload_hash_is_order_independent_and_excludes_self_hash():
    left = {"b": 2, "a": 1, "manifest_sha256": "old"}
    right = {"a": 1, "b": 2}
    assert canonical_payload_sha256(left) == canonical_payload_sha256(right)


def test_vector_integrity_schema_is_additive_and_runner_refuses_live(monkeypatch):
    runner = _runner_module()
    up = runner.migration_sql("up")
    down = runner.migration_sql("down")
    for table in (
        "legal_chunk_release",
        "legal_chunk_revision",
        "legal_metadata_review_case",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in up
        assert f"DROP TABLE IF EXISTS {table}" in down
    assert "DROP TABLE IF EXISTS legal_documents" not in down
    assert "DROP TABLE IF EXISTS legal_article_chunks" not in down
    assert "ON DELETE CASCADE" not in up
    assert "FEATURE018_VECTOR_RELEASE_IMMUTABLE" in up

    monkeypatch.setenv(
        "FEATURE018_VECTOR_DATABASE_URL",
        "postgresql+psycopg2://user:secret@127.0.0.1:5432/legal_chatbot",
    )
    with pytest.raises(RuntimeError, match="isolated_database_required"):
        runner.assert_isolated_database(confirmed=True)
    plan = runner.plan_payload()
    assert "secret" not in str(plan)
    assert plan["active_pointer_change"] is False
    assert plan["vector_mutation"] is False
