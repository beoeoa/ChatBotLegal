from __future__ import annotations

import hashlib
import json
from pathlib import Path

from api.retrieval_release_contracts import canonical_sha256, file_sha256
from scripts.apply_retrieval_metadata_attestation_v2 import apply_attestation
from scripts.build_retrieval_metadata_review_queue_v2 import build
from scripts.build_retrieval_chunk_manifest_v2 import _load_metadata_overlay


def _inventory() -> dict:
    base = {
        "law_number": "01/2026/QH15",
        "status_observed": "active",
        "serving_state": "current_retrievable",
        "classification_basis": "scope_and_effectivity_observed",
        "legal_review_required": True,
        "scope_included_observed": True,
        "effective_date": "2026-01-01",
        "expired_date": None,
        "article_count": 1,
        "chunk_count": 1,
        "source_url": "https://vbpl.vn/old",
    }
    return {
        "source_snapshot_document_count": 12_236,
        "source_snapshot_sha256": "a" * 64,
        "documents": [{**base, "document_id": index} for index in range(1, 12_237)],
    }


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _attestation(queue_path: Path, inventory_path: Path) -> dict:
    payload = {
        "schema_version": "legal-retrieval-metadata-attestation-v1",
        "queue_file_sha256": file_sha256(queue_path),
        "inventory_file_sha256": file_sha256(inventory_path),
        "decision": "APPROVE_CORRECTIONS",
        "reviewer_user_id": "legal-reviewer-1",
        "reviewed_at": "2026-08-16T00:00:00Z",
        "corrections": [{
            "document_id": 1,
            "field_name": "source_url",
            "old_value": "https://vbpl.vn/old",
            "proposed_value": "https://vbpl.vn/new",
            "evidence_url": "https://vbpl.vn/new",
            "evidence_sha256": "b" * 64,
            "review_note": "Official source checked by legal reviewer.",
        }],
    }
    payload["attestation_sha256"] = canonical_sha256(payload)
    return payload


def test_attested_correction_creates_staging_overlay_without_mutating_inputs(tmp_path: Path):
    inventory = _inventory()
    inventory_path = _write(tmp_path / "inventory.json", inventory)
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(build(inventory_path=inventory_path), ensure_ascii=False), encoding="utf-8")
    attestation_path = _write(tmp_path / "attestation.json", _attestation(queue_path, inventory_path))
    audit_path = tmp_path / "audit.json"
    corrected_path = tmp_path / "corrected.json"
    original_inventory_sha = file_sha256(inventory_path)
    report = apply_attestation(
        queue_path=queue_path,
        inventory_path=inventory_path,
        attestation_path=attestation_path,
        output_audit=audit_path,
        output_inventory=corrected_path,
    )
    assert report["status"] == "ATTESTED_STAGING"
    assert report["database_mutated"] is False
    assert report["before_after"][0]["before"] == "https://vbpl.vn/old"
    assert report["before_after"][0]["after"] == "https://vbpl.vn/new"
    assert file_sha256(inventory_path) == original_inventory_sha
    corrected = json.loads(corrected_path.read_text(encoding="utf-8"))
    assert corrected["documents"][0]["source_url"] == "https://vbpl.vn/new"


def test_attestation_rejects_stale_before_value(tmp_path: Path):
    inventory = _inventory()
    inventory_path = _write(tmp_path / "inventory.json", inventory)
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps(build(inventory_path=inventory_path), ensure_ascii=False), encoding="utf-8")
    attestation = _attestation(queue_path, inventory_path)
    attestation["corrections"][0]["old_value"] = "tampered"
    attestation["attestation_sha256"] = canonical_sha256({k: v for k, v in attestation.items() if k != "attestation_sha256"})
    attestation_path = _write(tmp_path / "attestation.json", attestation)
    report = apply_attestation(
        queue_path=queue_path,
        inventory_path=inventory_path,
        attestation_path=attestation_path,
        output_audit=tmp_path / "audit.json",
        output_inventory=None,
    )
    assert report["status"] == "BLOCKED"
    assert "correction[0]:old_value_mismatch" in report["errors"]


def test_chunk_builder_accepts_only_attested_overlay_rows():
    inventory = _inventory()
    inventory["metadata_overlay"] = {
        "schema_version": "legal-retrieval-metadata-overlay-v1",
        "attestation_sha256": "c" * 64,
        "source_inventory_unchanged": True,
    }
    inventory["documents"][0]["metadata_attestation_sha256"] = "c" * 64
    rows = _load_metadata_overlay(inventory)
    assert list(rows) == [1]
