from __future__ import annotations

import json
import stat

import pytest
import yaml
from fastapi import Request

from api.legal_serving_scope import canonical_manifest_sha256
from scripts import build_m2_serving_manifest as builder


def _baseline():
    return {
        "legal_as_of": "2026-08-15",
        "manifest_sha256": "b" * 64,
        "collection": {
            "collection_name": "baseline",
            "embedding_fingerprint": "e" * 64,
        },
        "documents": [{
            "document_id": 1, "law_number": "01/2026", "stored_status": "active",
            "domain": "ho_tich", "scope_reason": "reviewed", "expected_chunk_ids": [10, 11],
        }],
    }


def test_builder_preserves_unknown_provenance_without_fabrication(monkeypatch):
    monkeypatch.setattr(builder, "_source_ids", lambda _ids: {1: None})
    payload = builder.build_manifest(_baseline(), {"documents": []}, dataset_version="v1")
    row = payload["documents"][0]
    assert row["source_id"] is None
    assert row["validity_confidence"] is None
    assert payload["manifest_sha256"] == canonical_manifest_sha256(payload)


def test_versioned_manifest_cannot_be_rewritten(tmp_path):
    path = tmp_path / "v1.json"
    original = {"dataset_version": "v1", "generated_at": "first", "value": 1}
    builder._write_immutable(path, original)
    assert not path.stat().st_mode & stat.S_IWRITE
    same_version = {"dataset_version": "v1", "generated_at": "second", "value": 1}
    builder._write_immutable(path, same_version)
    changed = {"dataset_version": "v1", "generated_at": "third", "value": 2}
    with pytest.raises(RuntimeError, match="version_already_exists"):
        builder._write_immutable(path, changed)


def test_release_retrieval_requires_runtime_manifest():
    compose = yaml.safe_load(builder.ROOT.joinpath("docker-compose.release.yml").read_text(encoding="utf-8"))
    environment = compose["services"]["legal_retrieval"]["environment"]
    assert environment["LEGAL_SERVING_MANIFEST_REQUIRED"] == "true"
    assert environment["LEGAL_SERVING_MANIFEST_POINTER"].endswith(
        "/serving_manifests/active_serving_manifest.json"
    )


@pytest.mark.asyncio
async def test_api_derives_audience_from_authenticated_backend_role(monkeypatch):
    from api.routers import legal_search

    captured = {}

    async def internal_request(method, path, **kwargs):
        captured.update(kwargs["json"])
        return {"results": []}

    request = Request({"type": "http", "method": "POST", "path": "/legal/search", "headers": []})
    request.state.user_role = "officer"
    monkeypatch.setattr(legal_search, "_request", internal_request)
    await legal_search.legal_search(
        legal_search.LegalSearchRequest(query="đăng ký khai sinh"), request
    )
    assert captured["audience"] == "officer"
