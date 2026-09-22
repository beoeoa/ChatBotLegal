"""Optional PP-StructureV3 adapter for layout-aware PDF page extraction.

The adapter is deliberately page-scoped.  PyMuPDF remains the authoritative
source for a valid native PDF text layer; PaddleOCR is used only for pages that
need OCR/layout recovery.  Import and model failures are returned as structured
soft failures so candidate review never crashes or silently accepts partial
content.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import threading
from typing import Any, Iterable, Mapping

from loguru import logger

from api.crawlers.legal_document_pipeline import make_extraction_block

try:
    import fitz  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    fitz = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:  # pragma: no cover - optional dependency
    np = None  # type: ignore[assignment]


PADDLE_PACKAGES_AVAILABLE = (
    importlib.util.find_spec("paddleocr") is not None
    and importlib.util.find_spec("paddle") is not None
)
_OCR_PIPELINE: Any | None = None
_STRUCTURE_PIPELINE: Any | None = None
_PIPELINE_LOCK = threading.Lock()


def _enabled() -> bool:
    return str(os.getenv("LEGAL_PADDLE_OCR_ENABLED", "true")).strip().casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }


def paddle_runtime_readiness() -> tuple[bool, str]:
    if not _enabled():
        return False, "PADDLE_OCR_DISABLED"
    if fitz is None or np is None:
        return False, "PADDLE_RENDER_DEPENDENCIES_UNAVAILABLE"
    if not PADDLE_PACKAGES_AVAILABLE:
        return False, "PADDLE_OCR_DEPENDENCIES_UNAVAILABLE"
    return True, ""


def _common_pipeline_options() -> dict[str, Any]:
    """Return documented, CPU-safe options shared by Paddle pipelines."""

    return {
        "device": str(os.getenv("LEGAL_PADDLE_OCR_DEVICE", "cpu")).strip() or "cpu",
        # PaddlePaddle 3.3.x has a known Windows CPU regression in the
        # PIR/oneDNN path for ArrayAttribute<DoubleAttribute> models.
        "enable_mkldnn": False,
        "text_recognition_model_name": str(
            os.getenv(
                "LEGAL_PADDLE_TEXT_RECOGNITION_MODEL",
                "latin_PP-OCRv5_mobile_rec",
            )
        ).strip(),
        # Orientation classifiers load two extra PaddleX models and are not
        # needed for the overwhelmingly common upright Vietnamese legal PDF.
        # Keep them opt-in for rotated/photo-heavy documents so the normal
        # native-first OCR job starts quickly.
        "use_doc_orientation_classify": str(
            os.getenv("LEGAL_PADDLE_DOC_ORIENTATION", "false")
        ).strip().casefold() in {"1", "true", "yes", "on"},
        "use_doc_unwarping": False,
        "use_textline_orientation": str(
            os.getenv("LEGAL_PADDLE_TEXTLINE_ORIENTATION", "false")
        ).strip().casefold() in {"1", "true", "yes", "on"},
    }


def _ocr_pipeline_options() -> dict[str, Any]:
    return {
        **_common_pipeline_options(),
        "text_detection_model_name": str(
            os.getenv(
                "LEGAL_PADDLE_TEXT_DETECTION_MODEL",
                "PP-OCRv5_mobile_det",
            )
        ).strip(),
    }


def _structure_pipeline_options() -> dict[str, Any]:
    return {
        **_common_pipeline_options(),
        "use_seal_recognition": False,
        "use_table_recognition": True,
        "use_formula_recognition": False,
        "use_chart_recognition": False,
    }


def _prepare_native_runtime() -> None:
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "False")
    # On Windows, Paddle and PyTorch currently ship overlapping native
    # runtimes. ModelScope (loaded by PaddleX) imports torch, and torch must be
    # initialized first to avoid a shm.dll procedure mismatch.
    import torch  # noqa: F401


def _get_pipeline() -> Any:
    global _OCR_PIPELINE
    if _OCR_PIPELINE is not None:
        return _OCR_PIPELINE
    with _PIPELINE_LOCK:
        if _OCR_PIPELINE is None:
            _prepare_native_runtime()
            from paddleocr import PaddleOCR

            _OCR_PIPELINE = PaddleOCR(**_ocr_pipeline_options())
    return _OCR_PIPELINE


def _get_structure_pipeline() -> Any:
    global _STRUCTURE_PIPELINE
    if _STRUCTURE_PIPELINE is not None:
        return _STRUCTURE_PIPELINE
    with _PIPELINE_LOCK:
        if _STRUCTURE_PIPELINE is None:
            _prepare_native_runtime()
            from paddleocr import PPStructureV3

            _STRUCTURE_PIPELINE = PPStructureV3(**_structure_pipeline_options())
    return _STRUCTURE_PIPELINE


def _reset_pipeline_cache() -> None:
    """Test/runtime hook used after configuration changes."""

    global _OCR_PIPELINE, _STRUCTURE_PIPELINE
    with _PIPELINE_LOCK:
        _OCR_PIPELINE = None
        _STRUCTURE_PIPELINE = None


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    candidate = getattr(value, "json", None)
    if callable(candidate):
        candidate = candidate()
    if isinstance(candidate, Mapping):
        return candidate
    return {}


def _result_payload(value: Any) -> Mapping[str, Any]:
    payload = _mapping(value)
    nested = payload.get("res")
    return nested if isinstance(nested, Mapping) else payload


def _markdown_text(value: Any) -> str:
    markdown = getattr(value, "markdown", None)
    if callable(markdown):
        markdown = markdown()
    if isinstance(markdown, Mapping):
        candidate = markdown.get("markdown_texts") or markdown.get("markdown")
    else:
        candidate = markdown
    if isinstance(candidate, str):
        return candidate.strip()
    payload = _result_payload(value)
    for key in ("markdown_texts", "markdown", "text"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    overall = payload.get("overall_ocr_res")
    if isinstance(overall, Mapping):
        texts = overall.get("rec_texts")
        if isinstance(texts, Iterable) and not isinstance(texts, (str, bytes)):
            return "\n".join(str(item).strip() for item in texts if str(item).strip())
    texts = payload.get("rec_texts")
    if isinstance(texts, Iterable) and not isinstance(texts, (str, bytes)):
        return "\n".join(str(item).strip() for item in texts if str(item).strip())
    return ""


def _table_payloads(value: Any) -> list[Mapping[str, Any]]:
    payload = _result_payload(value)
    tables = payload.get("table_res_list")
    if not isinstance(tables, list):
        return []
    return [item for item in tables if isinstance(item, Mapping)]


def _table_text(table: Mapping[str, Any]) -> str:
    for key in ("pred_html", "markdown", "text"):
        value = table.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    ocr = table.get("table_ocr_pred")
    if isinstance(ocr, Mapping):
        texts = ocr.get("rec_texts")
        if isinstance(texts, Iterable) and not isinstance(texts, (str, bytes)):
            return " | ".join(str(item).strip() for item in texts if str(item).strip())
    return ""


def _confidence(value: Any) -> float | None:
    payload = _result_payload(value)
    overall = payload.get("overall_ocr_res")
    raw_scores = (
        overall.get("rec_scores")
        if isinstance(overall, Mapping)
        else payload.get("rec_scores")
    )
    if not isinstance(raw_scores, Iterable) or isinstance(raw_scores, (str, bytes)):
        return None
    scores: list[float] = []
    for raw in raw_scores:
        try:
            score = float(raw)
        except (TypeError, ValueError):
            continue
        # Paddle normally reports 0..1; tolerate 0..100 adapters.
        scores.append(score * 100 if 0 <= score <= 1 else score)
    return round(sum(scores) / len(scores), 2) if scores else None


def _ocr_blocks(
    value: Any, *, page_number: int, source_hash: str, extractor: str
) -> list[dict[str, Any]]:
    """Preserve Paddle line polygons and confidence in canonical blocks."""
    payload = _result_payload(value)
    overall = payload.get("overall_ocr_res")
    source = overall if isinstance(overall, Mapping) else payload
    texts = list(source.get("rec_texts") or [])
    scores = list(source.get("rec_scores") or [])
    polygons = list(source.get("rec_polys") or source.get("dt_polys") or [])
    blocks: list[dict[str, Any]] = []
    cursor = 0
    for index, raw_text in enumerate(texts):
        text = str(raw_text or "").strip()
        if not text:
            continue
        polygon = polygons[index] if index < len(polygons) else None
        bounding_box = None
        try:
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
            bounding_box = [min(xs), min(ys), max(xs), max(ys)]
        except (TypeError, ValueError, IndexError):
            pass
        confidence = None
        try:
            confidence = float(scores[index])
            confidence = confidence * 100 if 0 <= confidence <= 1 else confidence
        except (TypeError, ValueError, IndexError):
            pass
        blocks.append(make_extraction_block(
            block_type="ocr",
            text=text,
            extractor=extractor,
            extractor_version="3.x",
            page_number=page_number,
            char_start=cursor,
            char_end=cursor + len(text),
            bounding_box=bounding_box,
            confidence=confidence,
            source_asset_sha256=source_hash,
        ))
        cursor += len(text) + 1
    return blocks


def _render_page(document: Any, page_index: int, dpi: int) -> Any:
    scale = dpi / 72.0
    pixmap = document.load_page(page_index).get_pixmap(
        matrix=fitz.Matrix(scale, scale),
        alpha=False,
    )
    channels = int(pixmap.n)
    return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height,
        pixmap.width,
        channels,
    )


def _looks_like_table(image: Any) -> bool:
    """Use line geometry only to decide whether costly table parsing is useful."""

    try:
        import cv2

        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        horizontal_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (max(20, image.shape[1] // 20), 1),
        )
        vertical_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (1, max(20, image.shape[0] // 25)),
        )
        horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
        vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
        intersections = cv2.bitwise_and(horizontal, vertical)
        return int(cv2.countNonZero(intersections)) >= 40
    except Exception:  # pragma: no cover - optional OpenCV/runtime boundary
        return False


def _empty_result(reason: str, *, total_pages: int = 0) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "text": "",
        "page_count": 0,
        "processed_pages": 0,
        "total_pages": total_pages,
        "requested_pages": [],
        "covered_pages": [],
        "failed_pages": [],
        "complete": False,
        "truncated": False,
        "ocr_confidence": None,
        "language": "vie+eng",
        "reason": reason,
        "extraction_blocks": [],
        "page_texts": {},
        "page_extractors": {},
        "table_count": 0,
        "table_extracted_count": 0,
        "layout_status": "unavailable",
        "layout_reason": reason,
    }


def extract_pages_with_paddle(
    pdf_bytes: bytes,
    page_numbers: Iterable[int],
    *,
    dpi: int = 200,
) -> dict[str, Any]:
    """Extract selected pages with PP-OCR; invoke PP-Structure only for tables."""

    requested = sorted({int(page) for page in page_numbers if int(page) > 0})
    ready, reason = paddle_runtime_readiness()
    if not ready:
        result = _empty_result(reason)
        result["requested_pages"] = requested
        result["failed_pages"] = requested
        return result
    if not pdf_bytes or not requested:
        result = _empty_result("PADDLE_OCR_NO_PAGES_REQUESTED")
        result["status"] = "empty"
        return result

    document = None
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = int(document.page_count)
    except Exception:
        logger.warning("PaddleOCR pipeline or PDF initialization failed")
        return _empty_result("PADDLE_OCR_PIPELINE_UNAVAILABLE")

    page_texts: dict[int, str] = {}
    page_extractors: dict[int, str] = {}
    extraction_blocks: list[dict[str, Any]] = []
    failed_pages: list[int] = []
    confidences: list[float] = []
    table_count = 0
    table_extracted_count = 0
    source_hash = hashlib.sha256(pdf_bytes).hexdigest()
    attempted = 0
    try:
        valid_requested = [page for page in requested if page <= total_pages]
        failed_pages.extend(page for page in requested if page > total_pages)
        for page_number in valid_requested:
            attempted += 1
            try:
                image = _render_page(document, page_number - 1, dpi)
                use_structure = (
                    str(
                        os.getenv(
                            "LEGAL_PADDLE_TABLE_STRUCTURE_ENABLED",
                            "false",
                        )
                    ).strip().casefold()
                    not in {"0", "false", "no", "off"}
                    and _looks_like_table(image)
                )
                pipeline = _get_structure_pipeline() if use_structure else _get_pipeline()
                predictions = list(pipeline.predict(image))
                prediction = predictions[0] if predictions else None
                text = _markdown_text(prediction)
                if not text:
                    failed_pages.append(page_number)
                    continue
                page_texts[page_number] = text
                # Some PaddleOCR adapters expose table results even when the
                # caller selected the lightweight OCR pipeline (for example a
                # provider wrapper may auto-promote layout parsing).  Preserve
                # the actual extractor provenance instead of reporting plain
                # PP-OCR for a page that contains structure output.
                detected_tables = _table_payloads(prediction)
                page_extractors[page_number] = (
                    "paddleocr-ppstructurev3"
                    if use_structure or detected_tables
                    else "paddleocr-ppocr"
                )
                score = _confidence(prediction)
                if score is not None:
                    confidences.append(score)
                line_blocks = _ocr_blocks(
                    prediction,
                    page_number=page_number,
                    source_hash=source_hash,
                    extractor=page_extractors[page_number],
                )
                extraction_blocks.extend(line_blocks or [
                    make_extraction_block(
                        block_type="ocr",
                        text=text,
                        extractor=page_extractors[page_number],
                        extractor_version="3.x",
                        page_number=page_number,
                        source_asset_sha256=source_hash,
                        confidence=score,
                    )
                ])
                tables = detected_tables
                table_count += len(tables)
                for table_index, table in enumerate(tables):
                    table_text = _table_text(table)
                    if not table_text:
                        continue
                    table_extracted_count += 1
                    extraction_blocks.append(
                        make_extraction_block(
                            block_type="table",
                            text=table_text,
                            extractor="paddleocr-ppstructurev3",
                            extractor_version="3.x",
                            page_number=page_number,
                            table_path=f"page:{page_number}/table:{table_index}",
                            source_asset_sha256=source_hash,
                        )
                    )
            except Exception:
                failed_pages.append(page_number)
                logger.warning("PaddleOCR page failure at page {}", page_number)
    finally:
        if document is not None:
            document.close()

    failed_pages = sorted(set(failed_pages))
    covered_pages = sorted(page_texts)
    text = "\n\n".join(
        f"--- Trang {page} ---\n{page_texts[page]}" for page in covered_pages
    ).strip()
    complete = bool(requested) and len(covered_pages) == len(requested) and not failed_pages
    if complete:
        status = "ok"
        result_reason = ""
    elif text:
        status = "partial"
        result_reason = "PADDLE_OCR_INCOMPLETE_PAGE_COVERAGE"
    elif failed_pages:
        status = "failed"
        result_reason = "PADDLE_OCR_PAGE_PROCESSING_FAILED"
    else:
        status = "empty"
        result_reason = "PADDLE_OCR_NO_READABLE_TEXT"
    return {
        "status": status,
        "text": text,
        "page_count": attempted,
        "processed_pages": attempted,
        "total_pages": total_pages,
        "requested_pages": requested,
        "covered_pages": covered_pages,
        "failed_pages": failed_pages,
        "complete": complete,
        "truncated": False,
        "ocr_confidence": round(sum(confidences) / len(confidences), 2) if confidences else None,
        "language": "vie+eng",
        "reason": result_reason,
        "extraction_blocks": extraction_blocks,
        "page_texts": page_texts,
        "page_extractors": page_extractors,
        "table_count": table_count,
        "table_extracted_count": table_extracted_count,
        "layout_status": "available" if extraction_blocks else "unavailable",
        "layout_reason": "" if extraction_blocks else result_reason,
    }


def extract_image_with_paddle(image_bytes: bytes) -> dict[str, Any]:
    """Run PP-OCRv5 on one image, keeping Tesseract outside the primary path."""
    ready, reason = paddle_runtime_readiness()
    if not ready:
        return {"status": "unavailable", "text": "", "reason": reason, "extraction_blocks": []}
    try:
        from io import BytesIO
        from PIL import Image
        image = np.asarray(Image.open(BytesIO(image_bytes)).convert("RGB"))
        predictions = list(_get_pipeline().predict(image))
        prediction = predictions[0] if predictions else None
        text = _markdown_text(prediction)
        if not text:
            return {"status": "empty", "text": "", "reason": "PADDLE_OCR_NO_READABLE_TEXT", "extraction_blocks": []}
        digest = hashlib.sha256(image_bytes).hexdigest()
        score = _confidence(prediction)
        blocks = _ocr_blocks(
            prediction, page_number=1, source_hash=digest,
            extractor="paddleocr-ppocrv5",
        )
        if not blocks:
            blocks = [make_extraction_block(
                block_type="ocr", text=text, extractor="paddleocr-ppocrv5",
                extractor_version="3.x", page_number=1,
                source_asset_sha256=digest, confidence=score,
            )]
        return {"status": "ok", "text": text, "reason": "", "extractor_used": "paddleocr-ppocrv5", "ocr_status": "ok", "ocr_confidence": score, "extraction_blocks": blocks, "file_fingerprint": digest, "complete": True, "page_count": 1, "layout_status": "available"}
    except Exception as exc:  # pragma: no cover - optional native runtime
        logger.warning("PaddleOCR image failure: {}", type(exc).__name__)
        return {"status": "failed", "text": "", "reason": "PADDLE_OCR_IMAGE_FAILED", "error_type": type(exc).__name__, "extraction_blocks": []}
