from __future__ import annotations

import hashlib

from api.crawlers import ocr_extractor
from api.crawlers.legal_document_pipeline import (
    build_html_extraction_blocks,
    build_pdf_text_blocks,
    normalize_extraction_blocks,
)
from api.rag_anything_adapter import normalize_content_list_blocks
from api.routers.legal_search import _redact_extraction_blocks


def test_html_table_keeps_row_column_cell_path_and_source_order():
    html = """
    <main>
      <p>Mức thu như sau:</p>
      <table><tr><th>Thủ tục</th><th>Lệ phí</th></tr>
      <tr><td>Đăng ký</td><td>10.000 đồng</td></tr></table>
    </main>
    """
    blocks = build_html_extraction_blocks(html, extractor="beautifulsoup")

    assert [item["block_type"] for item in blocks] == [
        "text", "table", "table", "table", "table"
    ]
    assert [item.get("table_path") for item in blocks[1:]] == [
        "table:0/row:0/cell:0",
        "table:0/row:0/cell:1",
        "table:0/row:1/cell:0",
        "table:0/row:1/cell:1",
    ]
    assert blocks[-1]["text"] == "10.000 đồng"
    assert all(item["citation_level"] == "content_quote" for item in blocks)


def test_pdf_text_blocks_keep_page_local_offsets_hashes_and_page_identity():
    pages = ["Trang một\nĐiều 1.", "Trang hai\nKhoản 1."]
    asset_hash = hashlib.sha256(b"fixture-pdf").hexdigest()
    blocks = build_pdf_text_blocks(
        pages,
        source_asset_sha256=asset_hash,
        extractor="pymupdf",
        extractor_version="fixture",
    )

    assert [item["page_number"] for item in blocks] == [1, 2]
    assert [item["char_start"] for item in blocks] == [0, 0]
    assert [item["char_end"] for item in blocks] == [len(value) for value in pages]
    assert all(item["source_asset_sha256"] == asset_hash for item in blocks)
    assert all(len(item["text_hash"]) == 64 for item in blocks)
    assert all(item["citation_level"] == "content_quote" for item in blocks)


def test_invalid_optional_parser_blocks_fall_back_explicitly_without_crashing():
    result = normalize_extraction_blocks(
        [{"block_type": "table", "text": "", "page_number": -1}],
        fallback_text="Nội dung dự phòng đã kiểm chứng.",
        extractor="optional-parser",
        extractor_version="unknown",
    )

    assert result["status"] == "fallback"
    assert result["reason_code"] == "EXTRACTION_BLOCKS_INVALID"
    assert len(result["blocks"]) == 1
    assert result["blocks"][0]["text"] == "Nội dung dự phòng đã kiểm chứng."
    assert result["blocks"][0]["citation_level"] == "content_quote"


def test_ocr_layout_keeps_page_bbox_confidence_and_stable_line_order(monkeypatch):
    class FakeDocument:
        page_count = 1

        def close(self):
            return None

    class FakeImage:
        width = 1000
        height = 1400

        def close(self):
            return None

    class FakeTesseract:
        class Output:
            DICT = "dict"

        @staticmethod
        def image_to_string(_image, **_kwargs):
            return "Điều 1\nNội dung"

        @staticmethod
        def image_to_data(_image, **_kwargs):
            return {
                "text": ["Điều", "1", "Nội", "dung"],
                "conf": ["95", "93", "90", "91"],
                "left": [10, 60, 10, 70],
                "top": [20, 20, 60, 60],
                "width": [40, 10, 50, 40],
                "height": [20, 20, 20, 20],
                "block_num": [1, 1, 1, 1],
                "par_num": [1, 1, 1, 1],
                "line_num": [1, 1, 2, 2],
            }

    monkeypatch.setattr(ocr_extractor, "HAS_OCR", True)
    monkeypatch.setattr(ocr_extractor, "_ocr_runtime_readiness", lambda: (True, ""))
    monkeypatch.setattr(ocr_extractor.fitz, "open", lambda **_kwargs: FakeDocument())
    monkeypatch.setattr(ocr_extractor, "_render_page_image", lambda *_args: FakeImage())
    monkeypatch.setattr(ocr_extractor, "pytesseract", FakeTesseract)

    result = ocr_extractor.extract_ocr_from_pdf_bytes(b"%PDF-layout")

    assert result["layout_status"] == "available"
    assert [item["text"] for item in result["extraction_blocks"]] == [
        "Điều 1", "Nội dung"
    ]
    assert all(item["page_number"] == 1 for item in result["extraction_blocks"])
    assert result["extraction_blocks"][0]["bounding_box"] == [10.0, 20.0, 70.0, 40.0]
    assert result["extraction_blocks"][0]["confidence"] == 94.0
    assert all(item["citation_level"] == "physical_span" for item in result["extraction_blocks"])


def test_ocr_batch_merge_preserves_block_order_and_layout_degradation_reason():
    merged = ocr_extractor.merge_ocr_batches(
        [
            {
                "text": "--- Trang 1 ---\nA",
                "processed_pages": 1,
                "failed_pages": [],
                "ocr_confidence": 90,
                "extraction_blocks": [{"block_id": "p1", "page_number": 1}],
                "layout_status": "available",
            },
            {
                "text": "--- Trang 2 ---\nB",
                "processed_pages": 1,
                "failed_pages": [],
                "ocr_confidence": 80,
                "extraction_blocks": [{"block_id": "p2", "page_number": 2}],
                "layout_status": "fallback",
                "layout_reason": "OCR_LAYOUT_DATA_MISSING",
            },
        ],
        total_pages=2,
    )

    assert [item["block_id"] for item in merged["extraction_blocks"]] == ["p1", "p2"]
    assert merged["layout_status"] == "fallback"
    assert merged["layout_reason"] == "OCR_LAYOUT_DATA_MISSING"


def test_optional_parser_table_html_is_retained_as_cells_not_flattened_text():
    blocks = normalize_content_list_blocks(
        [
            {
                "type": "table",
                "page_idx": 4,
                "table_body": (
                    "<table><tr><th>Mục</th><th>Phí</th></tr>"
                    "<tr><td>A</td><td>5.000</td></tr></table>"
                ),
            }
        ],
        source_asset_sha256=hashlib.sha256(b"parser-source").hexdigest(),
    )

    assert len(blocks) == 4
    assert all(item["page_number"] == 5 for item in blocks)
    assert blocks[-1]["table_path"] == "parser:0/table:0/row:1/cell:1"
    assert blocks[-1]["text"] == "5.000"
    assert blocks[-1]["citation_level"] == "content_quote"


def test_layout_metadata_cannot_bypass_upload_pii_redaction():
    blocks = _redact_extraction_blocks(
        [
            {
                "text": "CCCD 012345678901",
                "char_start": 0,
                "char_end": 17,
                "bounding_box": [1, 1, 10, 10],
                "citation_level": "physical_span",
            }
        ]
    )

    assert "012345678901" not in blocks[0]["text"]
    assert blocks[0]["citation_level"] == "metadata_only"
    assert blocks[0]["bounding_box"] is None
    assert blocks[0]["verification_reason"] == "PII_REDACTED_LAYOUT_BLOCK"
