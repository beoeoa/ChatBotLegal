"""Optional OCR adapter for scanned PDF pages.

OCR produces review metadata only. It never approves or imports a legal
document, and missing optional dependencies result in a soft failure.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

try:
    import pytesseract
    from pdf2image import convert_from_bytes

    HAS_OCR = True
except ImportError:  # pragma: no cover - environment dependent
    HAS_OCR = False


def extract_ocr_from_pdf_bytes(pdf_bytes: bytes, max_pages: int = 20) -> dict[str, Any]:
    """Return OCR text, confidence and a stable error status for a PDF scan."""
    if not HAS_OCR:
        return {
            "status": "unavailable",
            "text": "",
            "page_count": 0,
            "ocr_confidence": None,
            "language": "vie+eng",
            "reason": "OCR optional dependencies are not installed or configured.",
        }

    try:
        images = convert_from_bytes(pdf_bytes, dpi=200, fmt="jpeg")
        extracted: list[str] = []
        confidences: list[float] = []
        for index, image in enumerate(images[:max_pages]):
            text = pytesseract.image_to_string(image, lang="vie+eng")
            data = pytesseract.image_to_data(
                image, lang="vie+eng", output_type=pytesseract.Output.DICT
            )
            for value in data.get("conf", []):
                try:
                    confidence = float(value)
                except (TypeError, ValueError):
                    continue
                if confidence >= 0:
                    confidences.append(confidence)
            extracted.extend((f"--- Trang {index + 1} ---", text.strip()))

        result_text = "\n\n".join(extracted).strip()
        return {
            "status": "ok" if result_text else "empty",
            "text": result_text,
            "page_count": min(len(images), max_pages),
            "ocr_confidence": round(sum(confidences) / len(confidences), 2) if confidences else None,
            "language": "vie+eng",
            "reason": "" if result_text else "OCR completed but did not detect readable text.",
        }
    except Exception as exc:  # pragma: no cover - binary/toolchain dependent
        logger.warning(f"OCR failure: {exc}")
        return {
            "status": "failed",
            "text": "",
            "page_count": 0,
            "ocr_confidence": None,
            "language": "vie+eng",
            "reason": f"OCR failed: {exc.__class__.__name__}",
        }


def run_ocr_on_pdf_bytes(pdf_bytes: bytes, max_pages: int = 20) -> str:
    """Backward-compatible string adapter for legacy upload extraction."""
    result = extract_ocr_from_pdf_bytes(pdf_bytes, max_pages=max_pages)
    if result["status"] == "ok":
        return str(result["text"])
    return f"[OCR unavailable: {result['reason']}]"
