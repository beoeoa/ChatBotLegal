from __future__ import annotations

import hashlib
from urllib.parse import parse_qs, urlparse

from api.legal_citation_provenance import (
    citation_can_support_claim,
    enrich_public_citation,
    verify_citation,
)


SOURCE_TEXT = (
    "Điều 10. Thẩm quyền đăng ký hộ tịch\n"
    "1. Ủy ban nhân dân cấp xã thực hiện đăng ký hộ tịch.\n"
    "2. Người yêu cầu nộp hồ sơ tại cơ quan có thẩm quyền."
)
QUOTE = "Ủy ban nhân dân cấp xã thực hiện đăng ký hộ tịch."


SOURCE_ASSET = b"synthetic-pdf-bytes-for-feature016"


def _physical_provenance() -> dict:
    start = SOURCE_TEXT.index(QUOTE)
    return {
        "chunk_id": "legal:chunk-10",
        "span_index": 0,
        "source_asset_sha256": hashlib.sha256(SOURCE_ASSET).hexdigest(),
        "extractor": "pymupdf",
        "extractor_version": "1.0",
        "page_number": 2,
        "char_start": start,
        "char_end": start + len(QUOTE),
        "bounding_box": [72.0, 120.0, 520.0, 146.0],
        "text_hash": hashlib.sha256(QUOTE.encode()).hexdigest(),
        "verification_status": "verified",
    }


def test_physical_span_is_derived_from_verified_offsets_and_viewer_target():
    result = verify_citation(
        source_text=SOURCE_TEXT,
        support_quote=QUOTE,
        provenance=_physical_provenance(),
        source_pages={2: SOURCE_TEXT},
        source_asset_bytes=SOURCE_ASSET,
        internal_url="/legal-documents/doc-10?article=10",
    )
    query = parse_qs(urlparse(result.viewer_url).query)

    assert result.status == "verified"
    assert result.verification_level == "physical_span"
    assert result.page_number == 2
    assert query == {
        "article": ["10"],
        "page": ["2"],
        "char_start": [str(SOURCE_TEXT.index(QUOTE))],
        "char_end": [str(SOURCE_TEXT.index(QUOTE) + len(QUOTE))],
    }
    assert citation_can_support_claim(result) is True


def test_unique_quote_without_coordinates_is_content_quote_only():
    result = verify_citation(
        source_text=SOURCE_TEXT,
        support_quote=QUOTE,
        provenance={"verification_status": "verified"},
        internal_url="/legal-documents/doc-10",
    )

    assert result.verification_level == "content_quote"
    assert result.char_start == SOURCE_TEXT.index(QUOTE)
    assert result.page_number is None
    assert citation_can_support_claim(result) is True


def test_metadata_only_cannot_support_public_legal_claim():
    result = verify_citation(
        source_text=None,
        support_quote=None,
        provenance=None,
        internal_url="/legal-documents/doc-10",
    )

    assert result.status == "unverified"
    assert result.verification_level == "metadata_only"
    assert citation_can_support_claim(result) is False


def test_wrong_quote_offsets_asset_hash_and_ambiguous_quote_are_hard_fail():
    provenance = _physical_provenance()
    forged = verify_citation(
        source_text=SOURCE_TEXT,
        support_quote="Ủy ban nhân dân cấp huyện thực hiện đăng ký hộ tịch.",
        provenance=provenance,
        source_pages={2: SOURCE_TEXT},
        source_asset_bytes=SOURCE_ASSET,
        internal_url="/legal-documents/doc-10",
    )
    wrong_offsets = verify_citation(
        source_text=SOURCE_TEXT,
        support_quote=QUOTE,
        provenance={**provenance, "char_start": 0, "char_end": len(QUOTE)},
        source_pages={2: SOURCE_TEXT},
        source_asset_bytes=SOURCE_ASSET,
        internal_url="/legal-documents/doc-10",
    )
    wrong_asset = verify_citation(
        source_text=SOURCE_TEXT,
        support_quote=QUOTE,
        provenance={**provenance, "source_asset_sha256": "0" * 64},
        source_pages={2: SOURCE_TEXT},
        source_asset_bytes=SOURCE_ASSET,
        internal_url="/legal-documents/doc-10",
    )
    wrong_page = verify_citation(
        source_text=SOURCE_TEXT,
        support_quote=QUOTE,
        provenance={**provenance, "page_number": 3},
        source_pages={2: SOURCE_TEXT, 3: "Trang khac khong chua trich dan."},
        source_asset_bytes=SOURCE_ASSET,
        internal_url="/legal-documents/doc-10",
    )
    ambiguous = verify_citation(
        source_text=f"{QUOTE}\n{QUOTE}",
        support_quote=QUOTE,
        provenance={"verification_status": "verified"},
        internal_url="/legal-documents/doc-10",
    )

    assert forged.reason_code == "physical_span_quote_mismatch"
    assert wrong_offsets.reason_code == "physical_span_quote_mismatch"
    assert wrong_asset.reason_code == "source_asset_hash_mismatch"
    assert wrong_page.reason_code == "physical_span_out_of_bounds"
    assert ambiguous.reason_code == "content_quote_ambiguous"
    assert all(
        item.verification_level == "rejected"
        for item in (forged, wrong_offsets, wrong_asset, wrong_page, ambiguous)
    )


def test_public_projection_exposes_proof_but_not_internal_storage_fields(monkeypatch):
    monkeypatch.setenv("LEGAL_PHYSICAL_CITATIONS_ENABLED", "true")
    public = enrich_public_citation(
        {
            "document_title": "Luật Hộ tịch",
            "law_number": "60/2014/QH13",
            "internal_url": "/legal-documents/doc-10?article=10",
            "support_quote": QUOTE,
            "provenance": _physical_provenance(),
            "source_text": SOURCE_TEXT,
            "source_pages": {2: SOURCE_TEXT},
            "source_asset_bytes": SOURCE_ASSET,
            "source_file": "D:/secret/raw.pdf",
            "chunk_id": "internal-chunk-id",
        }
    )

    assert public["verification_level"] == "physical_span"
    assert public["proof"]["page_number"] == 2
    assert public["proof"]["support_quote"] == QUOTE
    assert "source_file" not in public
    assert "chunk_id" not in public
    assert "secret" not in str(public)


def test_public_physical_proof_rolls_back_to_verified_quote_while_flag_is_off(monkeypatch):
    monkeypatch.setenv("LEGAL_PHYSICAL_CITATIONS_ENABLED", "false")
    public = enrich_public_citation(
        {
            "document_title": "Luật Hộ tịch",
            "internal_url": "/legal-documents/doc-10",
            "support_quote": QUOTE,
            "provenance": _physical_provenance(),
            "source_text": SOURCE_TEXT,
            "source_pages": {2: SOURCE_TEXT},
            "source_asset_bytes": SOURCE_ASSET,
        }
    )

    assert public["verification_status"] == "verified"
    assert public["verification_level"] == "content_quote"
    assert "page_number" not in public["proof"]
