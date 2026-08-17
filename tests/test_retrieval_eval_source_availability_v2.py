from __future__ import annotations

import json

from api.retrieval_release_contracts import canonical_sha256
from scripts.audit_retrieval_eval_source_availability_v2 import audit


def _suite():
    # The complete quota validator is intentionally bypassed in this focused
    # unit test by replacing it at module scope below; the audit logic itself
    # is tested against one answer-required case.
    return {
        "schema_version": "retrieval-eval-suite-v1",
        "source_snapshot_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "cases": [{
            "case_id": "case-1",
            "answer_required": True,
            "temporal_scope": "current",
            "positive_source_groups": [{"sources": [{"law_number": "01/2026", "article": "Điều 2"}]}],
        }],
    }


def test_source_availability_detects_article_and_state(tmp_path, monkeypatch):
    import scripts.audit_retrieval_eval_source_availability_v2 as module

    monkeypatch.setattr(module, "validate_suite", lambda suite, require_complete: {"valid": True})
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_suite()), encoding="utf-8")
    manifest = {
        "schema_version": "legal-retrieval-chunk-manifest-v2",
        "source_snapshot_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "approved": True,
        "legal_review_attestation": True,
        "chunks": [{
            "document_id": 7,
            "law_number": "01/2026",
            "article_number": "2",
            "content": "Điều 2. Nội dung",
            "document_serving_state": "current_retrievable",
        }],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = audit(
        suite_path=suite_path,
        manifest_path=manifest_path,
        output=tmp_path / "report.json",
    )
    assert report["status"] == "PASS"
    assert report["availability_rate"] == 1.0


def test_source_availability_reports_source_absence(tmp_path, monkeypatch):
    import scripts.audit_retrieval_eval_source_availability_v2 as module

    monkeypatch.setattr(module, "validate_suite", lambda suite, require_complete: {"valid": True})
    suite = _suite()
    suite["manifest_sha256"] = "b" * 64
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(suite), encoding="utf-8")
    manifest = {
        "schema_version": "legal-retrieval-chunk-manifest-v2",
        "source_snapshot_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "approved": True,
        "legal_review_attestation": True,
        "chunks": [],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = audit(
        suite_path=suite_path,
        manifest_path=manifest_path,
        output=tmp_path / "report.json",
    )
    assert report["status"] == "BLOCKED"
    assert report["status_counts"] == {"source_absent": 1}


def test_source_availability_distinguishes_article_chunk_gap(tmp_path, monkeypatch):
    import scripts.audit_retrieval_eval_source_availability_v2 as module

    monkeypatch.setattr(module, "validate_suite", lambda suite, require_complete: {"valid": True})
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(json.dumps(_suite()), encoding="utf-8")
    manifest = {
        "schema_version": "legal-retrieval-chunk-manifest-v2",
        "source_snapshot_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "approved": True,
        "legal_review_attestation": True,
        "chunks": [{
            "document_id": 7,
            "law_number": "01/2026",
            "article_number": "3",
            "structural_path": "Điều 3",
            "document_serving_state": "current_retrievable",
        }],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report = audit(
        suite_path=suite_path,
        manifest_path=manifest_path,
        output=tmp_path / "report.json",
    )
    assert report["status_counts"] == {"article_chunk_absent": 1}
