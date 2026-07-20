from api.crawlers import ocr_extractor
from api.legal_crawl_service import LegalCrawlService


def test_ocr_unavailable_is_structured_soft_failure(monkeypatch):
    monkeypatch.setattr(ocr_extractor, "HAS_OCR", False)
    result = ocr_extractor.extract_ocr_from_pdf_bytes(b"not-a-pdf")

    assert result["status"] == "unavailable"
    assert result["text"] == ""
    assert result["ocr_confidence"] is None
    assert result["reason"]


def test_candidate_recommendation_is_review_only_and_explainable():
    recommendation = LegalCrawlService.build_review_recommendation({
        "domain": "ho_tich_chung_thuc",
        "scope": "haiphong",
        "source_type": "form",
        "source_url": "https://example.gov.vn/form.pdf",
        "content": "Nội dung đã trích xuất đầy đủ." * 30,
        "extraction_result": {"characters": 900, "ocr_status": "not_required"},
        "duplicate_candidates": [],
    })

    assert recommendation["action"] == "manual_review_required"
    assert recommendation["scores"]["domain_fit"] == 90
    assert recommendation["scores"]["ward_haiphong_relevance"] == 90
    assert recommendation["scores"]["form_relevance"] == 90
    assert recommendation["scores"]["extraction_quality"] >= 80
    assert recommendation["evidence"]
