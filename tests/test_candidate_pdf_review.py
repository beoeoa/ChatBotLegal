from pathlib import Path

import pytest

from api.crawlers import ocr_extractor
from api.crawlers.pdf_extractor import HAS_FITZ, extract_pdf_bytes_for_review
from api.legal_crawl_service import LegalCrawlService

pytestmark = pytest.mark.skipif(not HAS_FITZ, reason="PyMuPDF is required for PDF fixtures")
FIXTURES = Path(__file__).parent / "fixtures" / "pdf_review"


def _text_pdf() -> bytes:
    return (FIXTURES / "text-based.pdf").read_bytes()


def _scan_like_pdf() -> bytes:
    return (FIXTURES / "scan-like.pdf").read_bytes()


def test_text_pdf_fixture_uses_pymupdf_and_persists_review_evidence():
    result = extract_pdf_bytes_for_review(_text_pdf(), filename="text-based.pdf")

    assert result["status"] == "ok"
    assert result["pdf_kind"] == "text_based"
    assert result["ocr_status"] == "not_required"
    assert result["page_count"] == 1
    assert result["text"]
    assert result["preview"] == result["text"][:2000]
    assert result["text_fingerprint"]
    assert result["file_fingerprint"]


def test_scan_pdf_fixture_calls_ocr_adapter_and_keeps_text_when_success(monkeypatch):
    def fake_ocr(_data: bytes, max_pages: int):
        assert max_pages == 50
        return {
            "status": "ok",
            "text": "Van ban scan da OCR duoc noi dung.",
            "page_count": 1,
            "ocr_confidence": 88.5,
            "language": "vie+eng",
            "reason": "",
        }

    monkeypatch.setattr(ocr_extractor, "extract_ocr_from_pdf_bytes", fake_ocr)
    result = extract_pdf_bytes_for_review(_scan_like_pdf(), filename="scan-like.pdf")

    assert result["status"] == "ok"
    assert result["pdf_kind"] == "scan"
    assert result["ocr_status"] == "ok"
    assert result["ocr_confidence"] == 88.5
    assert result["text"] == "Van ban scan da OCR duoc noi dung."
    assert result["text_fingerprint"]


def test_scan_pdf_ocr_unavailable_is_soft_failure_without_fabricated_text(monkeypatch):
    monkeypatch.setattr(ocr_extractor, "HAS_OCR", False)
    result = extract_pdf_bytes_for_review(_scan_like_pdf(), filename="scan-like.pdf")

    assert result["status"] == "review_required"
    assert result["pdf_kind"] == "scan"
    assert result["ocr_status"] == "unavailable"
    assert result["text"] == ""
    assert result["reason"]


def test_ocr_candidate_requires_manual_completion_before_import():
    errors = LegalCrawlService.validate_candidate_for_import({
        "title": "Van ban thu nghiem",
        "law_number": "01/2026/ND-CP",
        "source_url": "https://example.gov.vn/document.pdf",
        "scope": "central",
        "content": "Noi dung du dai de kiem tra. " * 20,
        "raw_metadata": {"confirmed_official_source": True, "effective_date": "2026-01-01"},
        "extraction_result": {"ocr_status": "unavailable"},
    })

    assert any("OCR/trích xuất" in error for error in errors)
