from __future__ import annotations

import json

import pytest

from scripts.build_lechan_serving_collections import (
    _hydrated_metadata,
    _load_ledger,
    _validate_hydrated_eligibility,
)


def test_ledger_domain_is_used_for_hydrated_collection_metadata(tmp_path):
    (tmp_path / "documents.jsonl").write_text(
        json.dumps(
            {
                "document_id": 10,
                "tier": "primary",
                "eligible": True,
                "domain_code": "tu_phap_ho_tich",
                "hard_gate": {
                    "official_status": True,
                    "provenance": True,
                    "effectivity": True,
                    "scope": True,
                    "domain": True,
                    "hierarchy": True,
                    "article_chunk": True,
                    "canonical_identity": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "chunks.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": 100,
                "document_id": 10,
                "tier": "primary",
                "eligible": True,
                "canonical_chunk_id": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    tiers, chunks, document_domains = _load_ledger(tmp_path)
    row = {
        "chunk_id": 100,
        "article_id": 20,
        "document_id": 10,
        "source_url": "https://vbpl.vn/example",
        "document_status": "active",
        "article_status": "active",
        "domain_slug": None,
    }

    metadata = _hydrated_metadata(
        row,
        tier="primary",
        scope_version="scope-v2",
        manifest_domain=document_domains[10],
    )

    assert tiers["primary"] == {"100"}
    assert chunks["100"]["document_id"] == 10
    assert metadata["domain_slug"] == "tu_phap_ho_tich"


def test_hydrated_serving_rows_reject_test_source_provenance():
    with pytest.raises(ValueError, match="unapproved source provenance"):
        _validate_hydrated_eligibility(
            {
                "chunk_id": 100,
                "document_id": 10,
                "source_url": "http://test.local/9999-b20",
                "document_status": "active",
                "article_status": "active",
                "domain_slug": "hanh_chinh_cong",
            },
            legal_as_of="2026-07-24",
        )

