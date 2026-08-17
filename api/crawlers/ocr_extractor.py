"""Optional, fail-closed OCR adapter for scanned legal PDF candidates.

OCR produces review metadata only. It never approves or imports a legal
document. Missing binaries, languages, partial page coverage, or page failures
remain explicit and cannot be treated as candidate content.
"""

from __future__ import annotations

from io import BytesIO
import hashlib
import os
from pathlib import Path
from typing import Any

from loguru import logger

from api.crawlers.legal_document_pipeline import make_extraction_block

try:
    import fitz  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - environment dependent
    fitz = None  # type: ignore[assignment]

try:
    import pytesseract
except ImportError:  # pragma: no cover - environment dependent
    pytesseract = None  # type: ignore[assignment]

try:
    from PIL import Image
except ImportError:  # pragma: no cover - environment dependent
    Image = None  # type: ignore[assignment]


HAS_OCR = fitz is not None and pytesseract is not None and Image is not None


def _ocr_line_blocks(
    data: dict[str, Any],
    *,
    page_number: int,
    source_asset_sha256: str,
) -> tuple[list[dict[str, Any]], str]:
    """Project Tesseract words into stable line blocks with page-local offsets."""

    words = list(data.get("text") or [])
    if not words:
        return [], ""
    groups: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for index, raw_word in enumerate(words):
        word = str(raw_word or "").strip()
        if not word:
            continue
        try:
            confidence = float((data.get("conf") or [])[index])
        except (IndexError, TypeError, ValueError):
            confidence = -1
        try:
            left = float((data.get("left") or [])[index])
            top = float((data.get("top") or [])[index])
            width = float((data.get("width") or [])[index])
            height = float((data.get("height") or [])[index])
            bbox = [left, top, left + width, top + height]
        except (IndexError, TypeError, ValueError):
            bbox = None
        try:
            key = (
                int((data.get("block_num") or [1] * len(words))[index]),
                int((data.get("par_num") or [1] * len(words))[index]),
                int((data.get("line_num") or [index + 1] * len(words))[index]),
            )
        except (IndexError, TypeError, ValueError):
            key = (1, 1, index + 1)
        groups.setdefault(key, []).append(
            {"text": word, "confidence": confidence, "bbox": bbox}
        )

    blocks: list[dict[str, Any]] = []
    page_parts: list[str] = []
    cursor = 0
    for items in groups.values():
        line = " ".join(str(item["text"]) for item in items).strip()
        if not line:
            continue
        if page_parts:
            cursor += 1
        start = cursor
        end = start + len(line)
        cursor = end
        page_parts.append(line)
        boxes = [item["bbox"] for item in items if item.get("bbox") is not None]
        bbox = None
        if len(boxes) == len(items) and boxes:
            bbox = [
                min(box[0] for box in boxes),
                min(box[1] for box in boxes),
                max(box[2] for box in boxes),
                max(box[3] for box in boxes),
            ]
        scores = [
            float(item["confidence"])
            for item in items
            if float(item["confidence"]) >= 0
        ]
        blocks.append(
            make_extraction_block(
                block_type="ocr",
                text=line,
                extractor="tesseract",
                # Runtime readiness already probes the binary. Do not launch a
                # second subprocess while normalizing returned layout data.
                extractor_version="runtime-verified",
                page_number=page_number,
                char_start=start,
                char_end=end,
                bounding_box=bbox,
                confidence=sum(scores) / len(scores) if scores else None,
                source_asset_sha256=source_asset_sha256,
            )
        )
    return blocks, "\n".join(page_parts)


def _configure_local_ocr_runtime() -> None:
    if not HAS_OCR:
        return
    configured_command = str(os.getenv("TESSERACT_CMD") or "").strip()
    windows_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if not configured_command and windows_default.is_file():
        configured_command = str(windows_default)
    if configured_command:
        pytesseract.pytesseract.tesseract_cmd = configured_command

    configured_data = str(os.getenv("TESSDATA_PREFIX") or "").strip()
    local_data = Path(__file__).resolve().parents[2] / "data" / "ocr_runtime"
    if (
        not configured_data
        and (local_data / "vie.traineddata").is_file()
        and (local_data / "eng.traineddata").is_file()
    ):
        os.environ["TESSDATA_PREFIX"] = str(local_data)


def _ocr_runtime_readiness() -> tuple[bool, str]:
    if not HAS_OCR:
        return False, "OCR_DEPENDENCIES_UNAVAILABLE"
    _configure_local_ocr_runtime()
    try:
        pytesseract.get_tesseract_version()
        languages = set(pytesseract.get_languages(config=""))
    except Exception:  # pragma: no cover - binary/toolchain dependent
        return False, "TESSERACT_RUNTIME_UNAVAILABLE"
    if not {"vie", "eng"}.issubset(languages):
        return False, "OCR_REQUIRED_LANGUAGE_MISSING"
    return True, ""


def _render_page_image(document: Any, index: int, dpi: int) -> Any:
    scale = dpi / 72.0
    pixmap = document.load_page(index).get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        alpha=False,
    )
    return Image.open(BytesIO(pixmap.tobytes("png")))


def extract_ocr_from_pdf_bytes(
    pdf_bytes: bytes,
    max_pages: int | None = None,
    *,
    resume_from_page: int = 0,
    dpi: int = 200,
    page_timeout_seconds: int = 90,
    collect_confidence: bool = True,
) -> dict[str, Any]:
    """OCR a PDF page-by-page and report complete page coverage."""

    ready, readiness_reason = _ocr_runtime_readiness()
    if not ready:
        return {
            "status": "unavailable",
            "text": "",
            "page_count": 0,
            "processed_pages": 0,
            "total_pages": 0,
            "failed_pages": [],
            "complete": False,
            "truncated": False,
            "ocr_confidence": None,
            "language": "vie+eng",
            "reason": readiness_reason,
            "extraction_blocks": [],
            "page_texts": {},
            "layout_status": "unavailable",
            "layout_reason": readiness_reason,
        }

    document = None
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = int(document.page_count)
        start_page = max(0, int(resume_from_page))
        stop_page = total_pages
        if max_pages is not None:
            stop_page = min(total_pages, start_page + max(0, int(max_pages)))
        truncated = stop_page < total_pages
        extracted: list[str] = []
        extraction_blocks: list[dict[str, Any]] = []
        page_texts: dict[int, str] = {}
        confidences: list[float] = []
        failed_pages: list[int] = []
        processed_pages = 0
        source_asset_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        layout_degraded = False

        for index in range(start_page, stop_page):
            image = None
            try:
                image = _render_page_image(document, index, dpi)
                text = pytesseract.image_to_string(
                    image,
                    lang="vie+eng",
                    timeout=page_timeout_seconds,
                )
                if collect_confidence:
                    data = pytesseract.image_to_data(
                        image,
                        lang="vie+eng",
                        output_type=pytesseract.Output.DICT,
                        timeout=page_timeout_seconds,
                    )
                    for value in data.get("conf", []):
                        try:
                            confidence = float(value)
                        except (TypeError, ValueError):
                            continue
                        if confidence >= 0:
                            confidences.append(confidence)
                    page_blocks, page_projection = _ocr_line_blocks(
                        data,
                        page_number=index + 1,
                        source_asset_sha256=source_asset_sha256,
                    )
                    if page_blocks:
                        extraction_blocks.extend(page_blocks)
                        page_texts[index + 1] = page_projection
                        if any(
                            item.get("citation_level") != "physical_span"
                            for item in page_blocks
                        ):
                            layout_degraded = True
                    elif text.strip():
                        layout_degraded = True
                elif text.strip():
                    layout_degraded = True
                extracted.extend((f"--- Trang {index + 1} ---", text.strip()))
            except Exception:  # pragma: no cover - binary/toolchain dependent
                failed_pages.append(index + 1)
                logger.warning("OCR page failure at page {}", index + 1)
            finally:
                processed_pages += 1
                close = getattr(image, "close", None)
                if callable(close):
                    close()

        result_text = "\n\n".join(extracted).strip()
        complete = (
            start_page == 0
            and processed_pages == total_pages
            and not failed_pages
            and not truncated
        )
        if complete and result_text:
            status = "ok"
            reason = ""
        elif result_text:
            status = "partial"
            reason = "OCR_INCOMPLETE_PAGE_COVERAGE"
        elif failed_pages:
            status = "failed"
            reason = "OCR_PAGE_PROCESSING_FAILED"
        else:
            status = "empty"
            reason = "OCR_NO_READABLE_TEXT"
        return {
            "status": status,
            "text": result_text,
            "page_count": processed_pages,
            "processed_pages": processed_pages,
            "total_pages": total_pages,
            "failed_pages": failed_pages,
            "complete": complete,
            "truncated": truncated,
            "ocr_confidence": (
                round(sum(confidences) / len(confidences), 2)
                if confidences
                else None
            ),
            "language": "vie+eng",
            "reason": reason,
            "extraction_blocks": extraction_blocks,
            "page_texts": page_texts,
            "layout_status": (
                "available"
                if extraction_blocks and not layout_degraded and not failed_pages
                else "fallback" if result_text else "unavailable"
            ),
            "layout_reason": (
                ""
                if extraction_blocks and not layout_degraded and not failed_pages
                else "OCR_LAYOUT_DATA_MISSING"
            ),
        }
    except Exception:  # pragma: no cover - binary/toolchain dependent
        logger.warning("OCR document initialization failure")
        return {
            "status": "failed",
            "text": "",
            "page_count": 0,
            "processed_pages": 0,
            "total_pages": 0,
            "failed_pages": [],
            "complete": False,
            "truncated": False,
            "ocr_confidence": None,
            "language": "vie+eng",
            "reason": "OCR_DOCUMENT_OPEN_FAILED",
            "extraction_blocks": [],
            "page_texts": {},
            "layout_status": "unavailable",
            "layout_reason": "OCR_DOCUMENT_OPEN_FAILED",
        }
    finally:
        if document is not None:
            document.close()


def merge_ocr_batches(
    batches: list[dict[str, Any]],
    *,
    total_pages: int,
) -> dict[str, Any]:
    """Merge disjoint OCR batches and keep partial work fail-closed."""

    processed_pages = sum(int(item.get("processed_pages") or 0) for item in batches)
    failed_pages = sorted({
        int(page)
        for item in batches
        for page in (item.get("failed_pages") or [])
    })
    text = "\n\n".join(
        str(item.get("text") or "").strip()
        for item in batches
        if str(item.get("text") or "").strip()
    ).strip()
    confidence_weight = sum(
        int(item.get("processed_pages") or 0)
        for item in batches
        if item.get("ocr_confidence") is not None
    )
    weighted_confidence = sum(
        float(item.get("ocr_confidence") or 0)
        * int(item.get("processed_pages") or 0)
        for item in batches
        if item.get("ocr_confidence") is not None
    )
    complete = (
        processed_pages == int(total_pages)
        and not failed_pages
        and bool(text)
    )
    if complete:
        status = "ok"
        reason = ""
    elif text:
        status = "partial"
        reason = "OCR_INCOMPLETE_PAGE_COVERAGE"
    elif failed_pages:
        status = "failed"
        reason = "OCR_PAGE_PROCESSING_FAILED"
    else:
        status = "empty"
        reason = "OCR_NO_READABLE_TEXT"
    extraction_blocks = [
        dict(block)
        for item in batches
        for block in (item.get("extraction_blocks") or [])
        if isinstance(block, dict)
    ]
    page_texts = {
        int(page): str(value)
        for item in batches
        for page, value in dict(item.get("page_texts") or {}).items()
    }
    layout_reasons = [
        str(item.get("layout_reason") or "")
        for item in batches
        if str(item.get("layout_status") or "") != "available"
        and str(item.get("layout_reason") or "")
    ]
    return {
        "status": status,
        "ocr_status": status,
        "text": text,
        "page_count": processed_pages,
        "processed_pages": processed_pages,
        "total_pages": int(total_pages),
        "failed_pages": failed_pages,
        "complete": complete,
        "truncated": processed_pages < int(total_pages),
        "ocr_confidence": (
            round(weighted_confidence / confidence_weight, 2)
            if confidence_weight
            else None
        ),
        "language": "vie+eng",
        "reason": reason,
        "extraction_blocks": extraction_blocks,
        "page_texts": page_texts,
        "layout_status": (
            "available"
            if extraction_blocks and not layout_reasons and complete
            else "fallback" if text else "unavailable"
        ),
        "layout_reason": layout_reasons[0] if layout_reasons else "",
    }


def run_ocr_on_pdf_bytes(
    pdf_bytes: bytes,
    max_pages: int | None = None,
) -> str:
    """Backward-compatible adapter that never returns an error placeholder."""

    result = extract_ocr_from_pdf_bytes(pdf_bytes, max_pages=max_pages)
    if result["status"] == "ok":
        return str(result["text"])
    return ""
