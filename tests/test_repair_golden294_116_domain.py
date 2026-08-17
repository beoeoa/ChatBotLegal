import pytest

from scripts.repair_golden294_116_domain import (
    DOCUMENT_ID,
    DOMAIN,
    FIELD_ID,
    FIELD_NAME,
    LAW_NUMBER,
    corrected_metadata,
)


def test_corrected_metadata_preserves_provenance_and_repairs_serving_fields():
    before = {
        "document_id": DOCUMENT_ID,
        "law_number": LAW_NUMBER,
        "chunk_id": 751393,
        "source_url": "https://vbpl.vn/example",
        "field_id": 7,
        "field_name": "Hộ tịch - Chứng thực",
        "domain_slug": "ho_tich_chung_thuc",
        "status": "staging",
        "doc_status": "staging",
    }
    after = corrected_metadata(before)
    assert after["field_id"] == FIELD_ID
    assert after["field_name"] == FIELD_NAME
    assert after["domain_slug"] == DOMAIN
    assert after["status"] == after["doc_status"] == "active"
    assert after["chunk_id"] == before["chunk_id"]
    assert after["source_url"] == before["source_url"]


def test_corrected_metadata_rejects_another_document():
    with pytest.raises(ValueError, match="DOCUMENT_MISMATCH"):
        corrected_metadata({"document_id": 1, "law_number": LAW_NUMBER})
