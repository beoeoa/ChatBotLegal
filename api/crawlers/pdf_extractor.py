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


def extract_pdf_bytes_for_review(
    pdf_bytes: bytes,
    *,
    filename: str = "document.pdf",
    max_pages: int = 50,
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
        "language": "unknown",
        "pdf_kind": "unknown",
        "extractor_used": "pymupdf",
        "ocr_status": "not_required",
        "ocr_confidence": None,
        "title": None,
        "reason": "",
        "filename": filename,
    }
    if not pdf_bytes:
        result["reason"] = "PDF rỗng."
        return result
    if not HAS_FITZ:
        result.update({"extractor_used": "unavailable", "reason": "PyMuPDF (fitz) chưa được cấu hình."})
        return result

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        result["page_count"] = min(document.page_count, max(1, max_pages))
        parts = [document.load_page(index).get_text("text") for index in range(result["page_count"])]
        document.close()
    except Exception as exc:
        logger.warning("PDF extraction failure for {}: {}", filename, exc)
        result["reason"] = f"Không thể đọc PDF: {exc.__class__.__name__}."
        return result

    text = _normalize_pdf_text("\n\n".join(parts))
    average_chars = len(text) / max(1, result["page_count"])
    # A real text layer can be short (for example a one-page decision title),
    # but is still text-based. OCR is reserved for empty or nearly empty layers.
    if text and average_chars >= 20:
        result.update({
            "status": "ok",
            "text": text,
            "preview": text[:2000],
            "text_fingerprint": _text_fingerprint(text),
            "language": "vie_or_unicode_text",
            "pdf_kind": "text_based",
            "ocr_status": "not_required",
            "title": _extract_title(text),
        })
        return result

    result.update({
        "pdf_kind": "scan",
        "ocr_status": "pending",
        "reason": "PDF có rất ít hoặc không có lớp văn bản; đã chuyển sang OCR tùy chọn.",
    })
    try:
        from api.crawlers.ocr_extractor import extract_ocr_from_pdf_bytes

        ocr = extract_ocr_from_pdf_bytes(pdf_bytes, max_pages=max_pages)
    except Exception as exc:  # pragma: no cover - defensive adapter boundary
        ocr = {
            "status": "failed", "text": "", "page_count": 0,
            "ocr_confidence": None, "language": "vie+eng",
            "reason": f"Không thể gọi OCR: {exc.__class__.__name__}.",
        }
    ocr_text = _normalize_pdf_text(str(ocr.get("text") or "")) if ocr.get("status") == "ok" else ""
    result.update({
        "ocr_status": str(ocr.get("status") or "failed"),
        "ocr_confidence": ocr.get("ocr_confidence"),
        "language": ocr.get("language") or "vie+eng",
        "page_count": int(ocr.get("page_count") or result["page_count"]),
        "extractor_used": "pymupdf+ocr" if ocr_text else "pymupdf",
        "reason": str(ocr.get("reason") or result["reason"]),
    })
    if ocr_text:
        result.update({
            "status": "ok",
            "text": ocr_text,
            "preview": ocr_text[:2000],
            "text_fingerprint": _text_fingerprint(ocr_text),
            "title": _extract_title(ocr_text),
        })
    else:
        # Do not return a fabricated placeholder or adapter error as candidate text.
        result["status"] = "review_required"
    return result


async def extract_pdf(url: str, *, timeout_ms: int = 30000, max_pages: int = 50) -> dict[str, Any]:
    """Download an allowed PDF and return the same review extraction schema."""
    if not url.startswith(("http://", "https://")):
        return {"status": "error", "text": "", "page_count": 0, "reason": "URL PDF không hợp lệ."}
    try:
        async with httpx.AsyncClient(timeout=timeout_ms / 1000.0) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return {"status": "error", "text": "", "page_count": 0, "reason": f"Không tải được PDF: {exc.__class__.__name__}."}
    return extract_pdf_bytes_for_review(response.content, filename=url.rsplit("/", 1)[-1], max_pages=max_pages)
