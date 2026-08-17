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

    # A form candidate must never be recommended for the legal-document import
    # lane. This is advisory only; the Admin still owns the actual decision.
    assert recommendation["action"] == "recommended_rejection"
    assert recommendation["scores"]["trich_xuat_noi_dung"] >= 80
    assert recommendation["scores"]["nguon_chinh_thuc"] == 0
    assert recommendation["passed_hard_gates"] is False
    assert any("biểu mẫu/thủ tục" in item for item in recommendation["hard_gate_failures"])
    assert recommendation["evidence"]


def test_ocr_processes_every_pdf_page_without_default_truncation(monkeypatch):
    loaded_pages = []

    class FakeDocument:
        page_count = 94

        def close(self):
            return None

    class FakeTesseract:
        class Output:
            DICT = "dict"

        @staticmethod
        def image_to_string(_image, **_kwargs):
            return "Nghị định 16/2022/NĐ-CP Điều 16"

        @staticmethod
        def image_to_data(_image, **_kwargs):
            return {"conf": ["90"]}

    monkeypatch.setattr(ocr_extractor, "HAS_OCR", True)
    monkeypatch.setattr(ocr_extractor, "_ocr_runtime_readiness", lambda: (True, ""))
    monkeypatch.setattr(ocr_extractor.fitz, "open", lambda **_kwargs: FakeDocument())
    monkeypatch.setattr(
        ocr_extractor,
        "_render_page_image",
        lambda _document, index, _dpi: loaded_pages.append(index) or object(),
    )
    monkeypatch.setattr(ocr_extractor, "pytesseract", FakeTesseract)

    result = ocr_extractor.extract_ocr_from_pdf_bytes(b"%PDF-test")

    assert result["status"] == "ok"
    assert result["processed_pages"] == 94
    assert result["total_pages"] == 94
    assert result["failed_pages"] == []
    assert result["complete"] is True
    assert result["truncated"] is False
    assert loaded_pages == list(range(94))


def test_form_boundary_ocr_can_skip_second_confidence_pass(monkeypatch):
    class FakeDocument:
        page_count = 1

        def close(self):
            return None

    class FakeTesseract:
        class Output:
            DICT = "dict"

        @staticmethod
        def image_to_string(_image, **_kwargs):
            return "Mẫu số 01"

        @staticmethod
        def image_to_data(_image, **_kwargs):
            raise AssertionError("confidence pass must be skipped")

    monkeypatch.setattr(ocr_extractor, "HAS_OCR", True)
    monkeypatch.setattr(ocr_extractor, "_ocr_runtime_readiness", lambda: (True, ""))
    monkeypatch.setattr(ocr_extractor.fitz, "open", lambda **_kwargs: FakeDocument())
    monkeypatch.setattr(ocr_extractor, "_render_page_image", lambda *_args: object())
    monkeypatch.setattr(ocr_extractor, "pytesseract", FakeTesseract)

    result = ocr_extractor.extract_ocr_from_pdf_bytes(
        b"%PDF-test",
        collect_confidence=False,
    )

    assert result["complete"] is True
    assert result["ocr_confidence"] is None


def test_bounded_ocr_is_partial_and_cannot_pass_import_gate(monkeypatch):
    class FakeDocument:
        page_count = 94

        def close(self):
            return None

    class FakeTesseract:
        class Output:
            DICT = "dict"

        @staticmethod
        def image_to_string(_image, **_kwargs):
            return "Nội dung OCR"

        @staticmethod
        def image_to_data(_image, **_kwargs):
            return {"conf": ["85"]}

    monkeypatch.setattr(ocr_extractor, "HAS_OCR", True)
    monkeypatch.setattr(ocr_extractor, "_ocr_runtime_readiness", lambda: (True, ""))
    monkeypatch.setattr(ocr_extractor.fitz, "open", lambda **_kwargs: FakeDocument())
    monkeypatch.setattr(ocr_extractor, "_render_page_image", lambda *_args: object())
    monkeypatch.setattr(ocr_extractor, "pytesseract", FakeTesseract)

    result = ocr_extractor.extract_ocr_from_pdf_bytes(b"%PDF-test", max_pages=50)

    assert result["status"] == "partial"
    assert result["processed_pages"] == 50
    assert result["total_pages"] == 94
    assert result["complete"] is False
    assert result["truncated"] is True


def test_page_ocr_failure_never_becomes_candidate_text(monkeypatch):
    class FakeDocument:
        page_count = 1

        def close(self):
            return None

    class FakeTesseract:
        class Output:
            DICT = "dict"

        @staticmethod
        def image_to_string(_image, **_kwargs):
            raise RuntimeError("sensitive binary error detail")

    monkeypatch.setattr(ocr_extractor, "HAS_OCR", True)
    monkeypatch.setattr(ocr_extractor, "_ocr_runtime_readiness", lambda: (True, ""))
    monkeypatch.setattr(ocr_extractor.fitz, "open", lambda **_kwargs: FakeDocument())
    monkeypatch.setattr(ocr_extractor, "_render_page_image", lambda *_args: object())
    monkeypatch.setattr(ocr_extractor, "pytesseract", FakeTesseract)

    result = ocr_extractor.extract_ocr_from_pdf_bytes(b"%PDF-test")

    assert result["status"] == "failed"
    assert result["text"] == ""
    assert result["failed_pages"] == [1]
    assert "sensitive binary error detail" not in result["reason"]


def test_ocr_batches_merge_only_when_every_page_succeeds():
    merged = ocr_extractor.merge_ocr_batches(
        [
            {
                "text": "--- Trang 1 ---\nNội dung 1",
                "processed_pages": 1,
                "failed_pages": [],
                "ocr_confidence": 90.0,
            },
            {
                "text": "--- Trang 2 ---\nNội dung 2",
                "processed_pages": 1,
                "failed_pages": [],
                "ocr_confidence": 80.0,
            },
        ],
        total_pages=2,
    )

    assert merged["status"] == "ok"
    assert merged["complete"] is True
    assert merged["processed_pages"] == merged["total_pages"] == 2
    assert merged["ocr_confidence"] == 85.0
