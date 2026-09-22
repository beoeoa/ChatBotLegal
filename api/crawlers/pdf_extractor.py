"""PDF extraction utilities for candidate review.

The helper in this module never treats OCR errors as extracted content.  It is
used only for candidate review; callers must keep the candidate pending until a
human verifies both source and extracted text.
"""

from __future__ import annotations

import hashlib
import io
import re
from typing import Any

import httpx
from loguru import logger

from api.crawlers.legal_document_pipeline import build_pdf_text_blocks

try:
    import fitz  # type: ignore[import-untyped]

    HAS_FITZ = True
except ImportError:  # pragma: no cover - optional dependency
    HAS_FITZ = False


def _normalize_pdf_text(text: str) -> str:
    """Normalize whitespace while preserving valid Vietnamese Unicode."""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _extract_title(text: str) -> str | None:
    for line in text.splitlines()[:10]:
        stripped = line.strip()
        if 20 < len(stripped) < 300 and not re.search(r"\b(pdf|download|trang|page)\b", stripped, re.I):
            return stripped
    return None


def _text_fingerprint(text: str) -> str | None:
    normalized = _normalize_pdf_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else None


def _native_text_is_usable(text: str) -> bool:
    """Reject an empty/near-empty page layer without judging the whole PDF."""

    normalized = _normalize_pdf_text(text)
    if len(normalized) < 20:
        return False
    meaningful = sum(character.isalnum() for character in normalized)
    return meaningful >= 10 and meaningful / max(1, len(normalized)) >= 0.35


def _join_page_texts(page_texts: dict[int, str], *, add_page_markers: bool) -> str:
    if add_page_markers:
        return "\n\n".join(
            f"--- Trang {page} ---\n{page_texts[page]}"
            for page in sorted(page_texts)
            if page_texts[page]
        ).strip()
    return _normalize_pdf_text("\n\n".join(page_texts[page] for page in sorted(page_texts)))


def extract_pdf_bytes_for_review(
    pdf_bytes: bytes,
    *,
    filename: str = "document.pdf",
    max_pages: int | None = None,
    perform_ocr: bool = True,
) -> dict[str, Any]:
    """Extract a PDF byte stream and optionally OCR a probable scan.

    ``pdf_kind`` is ``text_based``, ``scan`` or ``unknown``.  All outcomes have
    the same review fields so the UI can explain a soft failure without needing
    to infer state from an error string.
    """
    result: dict[str, Any] = {
        "status": "error",
        "text": "",
        "preview": "",
        "text_fingerprint": None,
        "file_fingerprint": hashlib.sha256(pdf_bytes).hexdigest(),
        "page_count": 0,
        "processed_pages": 0,
        "total_pages": 0,
        "failed_pages": [],
        "complete": False,
        "truncated": False,
        "language": "unknown",
        "pdf_kind": "unknown",
        "extractor_used": "pymupdf",
        "ocr_status": "not_required",
        "ocr_confidence": None,
        "title": None,
        "reason": "",
        "filename": filename,
        "extraction_blocks": [],
        "layout_status": "unavailable",
        "layout_reason": "PDF_LAYOUT_NOT_EXTRACTED",
        "native_text_pages": [],
        "ocr_requested_pages": [],
        "ocr_pages": [],
        "page_extractors": {},
        "coverage_percent": 0,
        "table_count": 0,
        "table_extracted_count": 0,
    }
    if not pdf_bytes:
        result["reason"] = "PDF rỗng."
        return result
    if not HAS_FITZ:
        result.update({"extractor_used": "unavailable", "reason": "PyMuPDF (fitz) chưa được cấu hình."})
        return result

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = int(document.page_count)
        pages_to_read = total_pages if max_pages is None else min(
            total_pages, max(1, int(max_pages))
        )
        result.update({
            "page_count": pages_to_read,
            "processed_pages": pages_to_read,
            "total_pages": total_pages,
            "complete": pages_to_read == total_pages,
            "truncated": pages_to_read < total_pages,
        })
        parts = [
            document.load_page(index).get_text("text")
            for index in range(pages_to_read)
        ]
        normalized_pages = [_normalize_pdf_text(part) for part in parts]
        native_text_pages = [
            index + 1
            for index, part in enumerate(normalized_pages)
            if _native_text_is_usable(part)
        ]
        weak_pages = [
            index + 1
            for index in range(pages_to_read)
            if index + 1 not in native_text_pages
        ]
        result["native_text_pages"] = native_text_pages
        result["ocr_requested_pages"] = weak_pages
        result["pages_without_text"] = weak_pages
        # Table geometry is reported independently from text completeness.
        # Scanned tables may not be detected; the original PDF remains necessary.
        table_count = 0
        native_table_pages: list[int] = []
        for page_number in native_text_pages:
            try:
                page_tables = document.load_page(page_number - 1).find_tables().tables
                table_count += len(page_tables)
                if page_tables:
                    native_table_pages.append(page_number)
            except Exception:
                continue
        result["table_count"] = table_count
        document.close()
    except Exception as exc:
        logger.warning("PDF extraction failure for {}: {}", filename, exc)
        result["reason"] = f"Không thể đọc PDF: {exc.__class__.__name__}."
        return result

    native_page_texts = {
        page: normalized_pages[page - 1]
        for page in native_text_pages
    }
    text = _join_page_texts(native_page_texts, add_page_markers=False)
    extraction_blocks = build_pdf_text_blocks(
        normalized_pages,
        source_asset_sha256=str(result["file_fingerprint"]),
        extractor="pymupdf",
        extractor_version=str(getattr(fitz, "VersionBind", "unknown")),
    )
    if not weak_pages:
        complete = pages_to_read == total_pages
        result.update({
            "status": "ok" if complete else "review_required",
            "text": text,
            "preview": text[:2000],
            "text_fingerprint": _text_fingerprint(text),
            "language": "vie_or_unicode_text",
            "pdf_kind": "text_based",
            "ocr_status": "not_required",
            "title": _extract_title(text),
            "extraction_blocks": extraction_blocks,
            "complete": complete,
            "failed_pages": [],
            "page_extractors": {page: "pymupdf" for page in native_text_pages},
            "coverage_percent": 100 if complete else round(pages_to_read / max(1, total_pages) * 100),
            "layout_status": "available" if extraction_blocks else "fallback",
            "layout_reason": "" if extraction_blocks else "PDF_TEXT_BLOCKS_UNAVAILABLE",
            "reason": "" if complete else "PDF_PAGE_LIMIT_REACHED",
        })
        # Native tables and columns keep PyMuPDF's physical blocks. Do not OCR
        # an already-readable page or load a second engine just for review.
        if native_table_pages:
            result["layout_reason"] = "PDF_NATIVE_TABLE_GEOMETRY"
        return result

    result.update({
        "pdf_kind": "hybrid" if native_text_pages else "scan",
        "ocr_status": "pending" if perform_ocr else "queued",
        "reason": (
            "Một số trang thiếu lớp chữ; hệ thống chỉ nhận dạng các trang đó."
            if perform_ocr
            else "Đã đọc nhanh lớp chữ. Các trang ảnh sẽ được OCR trong tác vụ nền sau khi gửi duyệt."
        ),
    })
    if not perform_ocr:
        native_text = _join_page_texts(native_page_texts, add_page_markers=True)
        result.update({
            "status": "review_required" if native_text else "processing",
            "text": native_text,
            "preview": native_text[:2000],
            "text_fingerprint": _text_fingerprint(native_text),
            "title": _extract_title(native_text),
            "processed_pages": len(native_text_pages),
            "complete": False,
            "failed_pages": [],
            "pages_without_text": weak_pages,
            "page_extractors": {page: "pymupdf" for page in native_text_pages},
            "coverage_percent": round(len(native_text_pages) / max(1, total_pages) * 100),
            "extraction_blocks": extraction_blocks,
            "layout_status": "available" if extraction_blocks else "fallback",
            "layout_reason": "" if extraction_blocks else "PDF_TEXT_BLOCKS_UNAVAILABLE",
            "deferred_ocr": True,
        })
        return result
    try:
        from api.crawlers.ocr_extractor import extract_ocr_pages_with_fallback

        ocr = extract_ocr_pages_with_fallback(pdf_bytes, weak_pages)
    except Exception as exc:  # pragma: no cover - defensive adapter boundary
        ocr = {
            "status": "failed",
            "page_texts": {},
            "ocr_confidence": None,
            "language": "vie",
            "reason": f"Không thể gọi OCR: {exc.__class__.__name__}.",
            "failed_pages": weak_pages,
            "extraction_blocks": [],
            "page_extractors": {},
        }
    ocr_page_texts = {
        int(page): _normalize_pdf_text(str(value))
        for page, value in dict(ocr.get("page_texts") or {}).items()
        if _normalize_pdf_text(str(value))
    }
    merged_page_texts = {**native_page_texts, **ocr_page_texts}
    merged_text = _join_page_texts(merged_page_texts, add_page_markers=True)
    failed_pages = [page for page in weak_pages if page not in ocr_page_texts]
    covered_pages = sorted(merged_page_texts)
    complete = (
        pages_to_read == total_pages
        and len(covered_pages) == pages_to_read
        and not failed_pages
    )
    page_extractors = {
        **{page: "pymupdf" for page in native_text_pages},
        **{
            int(page): str(engine)
            for page, engine in dict(ocr.get("page_extractors") or {}).items()
        },
    }
    merged_blocks = extraction_blocks + [
        dict(block)
        for block in (ocr.get("extraction_blocks") or [])
        if isinstance(block, dict)
    ]
    ocr_table_count = int(ocr.get("table_count") or 0)
    result.update({
        "ocr_status": str(ocr.get("status") or "failed"),
        "ocr_confidence": ocr.get("ocr_confidence"),
        "language": ocr.get("language") or "vie",
        "page_count": pages_to_read,
        "processed_pages": pages_to_read,
        "total_pages": total_pages,
        "failed_pages": failed_pages,
        "complete": complete,
        "truncated": pages_to_read < total_pages,
        "extractor_used": (
            "pymupdf+" + str(ocr.get("extractor_used") or "ocr")
            if ocr_page_texts
            else "pymupdf"
        ),
        "reason": "" if complete else str(ocr.get("reason") or result["reason"]),
        "extraction_blocks": merged_blocks,
        "layout_status": str(ocr.get("layout_status") or "fallback"),
        "layout_reason": str(
            ocr.get("layout_reason") or "OCR_LAYOUT_DATA_MISSING"
        ),
        "ocr_pages": sorted(ocr_page_texts),
        "pages_without_text": failed_pages,
        "page_extractors": page_extractors,
        "coverage_percent": round(len(covered_pages) / max(1, total_pages) * 100),
        "table_count": max(table_count, ocr_table_count),
        "table_extracted_count": int(ocr.get("table_extracted_count") or 0),
    })
    if merged_text:
        result.update({
            "status": "ok" if complete else "review_required",
            "text": merged_text,
            "preview": merged_text[:2000],
            "text_fingerprint": _text_fingerprint(merged_text),
            "title": _extract_title(merged_text),
        })
    else:
        # Do not return a fabricated placeholder or adapter error as candidate text.
        result.update({
            "status": "review_required",
            "text": "",
            "preview": "",
            "text_fingerprint": None,
            "title": None,
            "complete": False,
            "processed_pages": pages_to_read,
            "truncated": pages_to_read < total_pages,
            "layout_status": "unavailable",
            "reason": f"{result['reason']} Cần đối chiếu các trang còn thiếu với PDF gốc.",
        })
    return result


async def extract_pdf(
    url: str,
    *,
    timeout_ms: int = 30000,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Download an allowed PDF asset and extract it for review.

    This is a binary asset transfer, not an HTML crawl. HTML pages that
    discover the asset are rendered by Crawl4AI before this helper is called.
    """
    if not url.startswith(("http://", "https://")):
        return {"status": "error", "text": "", "page_count": 0, "reason": "URL PDF không hợp lệ."}
    try:
        async with httpx.AsyncClient(timeout=timeout_ms / 1000.0) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return {"status": "error", "text": "", "page_count": 0, "reason": f"Không tải được PDF: {exc.__class__.__name__}."}
    return extract_pdf_bytes_for_review(response.content, filename=url.rsplit("/", 1)[-1], max_pages=max_pages)
