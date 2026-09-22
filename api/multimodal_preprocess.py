"""
Multimodal preprocessing service for the citizen question flow.

Convert supported uploads into text supplied separately from the user's question.
Local parsing runs outside the API event loop; scanned pages require complete OCR.
"""

from __future__ import annotations

import html
import asyncio
import io
import os
import re
import zipfile
from threading import BoundedSemaphore
from pathlib import Path
from typing import Any, Literal, Mapping
from xml.etree import ElementTree

from loguru import logger

TEXT_MIMES = {"text/plain"}
DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-docx",
}
LEGACY_DOC_MIMES = {"application/msword"}
XLS_MIMES = {"application/vnd.ms-excel", "application/xls"}
XLSX_MIMES = {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
PDF_MIMES = {"application/pdf"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/bmp", "image/tiff"}
SUPPORTED_MIMES = TEXT_MIMES | DOCX_MIMES | LEGACY_DOC_MIMES | XLS_MIMES | XLSX_MIMES | PDF_MIMES | IMAGE_MIMES
SUPPORTED_EXTENSIONS = {".txt", ".doc", ".docx", ".pdf", ".xls", ".xlsx", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

SourceType = Literal["text", "doc", "docx", "pdf", "xls", "xlsx", "image", "unknown"]

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_EXTRACTED_CHARS = 80_000
MAX_ARTIFACT_CHARS = 2_000_000
EXTRACTION_PIPELINE_VERSION = "document-pipeline-v3-tesseract-vie"
# Tesseract is CPU-bound. One document at a time keeps the other local
# services responsive while every page in the active document is completed.
_pdf_slots = BoundedSemaphore(1)

_VISION_PROMPT = (
    "Hãy đọc toàn bộ văn bản có trong ảnh này và trả về đúng nội dung bằng tiếng Việt. "
    "Nếu ảnh là tài liệu hành chính hoặc văn bản pháp luật, hãy giữ nguyên cấu trúc, "
    "số điều, tên văn bản và số hiệu nếu nhìn thấy. Không thêm nhận xét, không tóm tắt. "
    "Nếu không có chữ trong ảnh, trả về chuỗi rỗng."
)

_PDF_VISION_PROMPT = (
    "Hãy OCR nội dung PDF scan này thành văn bản tiếng Việt. Giữ nguyên cấu trúc, "
    "điều khoản, số điều, tên văn bản và số hiệu nếu nhìn thấy. Không thêm nhận xét, "
    "không tóm tắt, không diễn giải."
)


def _normalize_ext(filename: str) -> str:
    return Path(filename or "").suffix.lower().strip()


def classify_upload(filename: str, mime_type: str) -> SourceType:
    mt = (mime_type or "").lower().split(";", 1)[0].strip()
    ext = _normalize_ext(filename)
    if mt in TEXT_MIMES or ext == ".txt":
        return "text"
    if mt in DOCX_MIMES or ext == ".docx":
        return "docx"
    if mt in LEGACY_DOC_MIMES or ext == ".doc":
        return "doc"
    if mt in XLSX_MIMES or ext == ".xlsx":
        return "xlsx"
    if mt in XLS_MIMES or ext == ".xls":
        return "xls"
    if mt in PDF_MIMES or ext == ".pdf":
        return "pdf"
    if mt in IMAGE_MIMES or ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
        return "image"
    return "unknown"


def is_supported_upload(filename: str, mime_type: str) -> bool:
    return classify_upload(filename, mime_type) != "unknown"


def _trim_extracted_text(text: str) -> str:
    cleaned = re.sub(r"\r\n?", "\n", text or "").strip()
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    if len(cleaned) <= MAX_EXTRACTED_CHARS:
        return cleaned
    return cleaned[:MAX_EXTRACTED_CHARS].rstrip() + "\n\n[Đã cắt bớt nội dung vì file quá dài.]"


def _extract_txt_local(file_bytes: bytes) -> str:
    if file_bytes.startswith(b"\xef\xbb\xbf"):
        file_bytes = file_bytes[3:]
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "File TXT phải dùng UTF-8. Hệ thống không tự đoán mã hóa để tránh lỗi tiếng Việt."
        ) from exc


def _extract_docx_local(file_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            if zf.getinfo("word/document.xml").file_size > 16 * 1024 * 1024:
                raise ValueError("Nội dung DOCX sau giải nén vượt giới hạn 16 MB.")
            document_xml = zf.read("word/document.xml")
    except KeyError as exc:
        raise ValueError("File DOCX không hợp lệ: thiếu word/document.xml.") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError("File DOCX không hợp lệ hoặc bị hỏng.") from exc

    root = ElementTree.fromstring(document_xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts: list[str] = []
        for node in paragraph.findall(".//w:t", namespace):
            if node.text:
                parts.append(node.text)
        line = "".join(parts).strip()
        if line:
            paragraphs.append(html.unescape(line))
    return "\n".join(paragraphs)


def _extract_legacy_office(file_bytes: bytes, filename: str, source_type: SourceType) -> str:
    """Convert legacy OLE Office files through LibreOffice, then parse native."""
    from api.crawlers.office_converter import convert_legacy_doc_bytes, convert_legacy_xls_bytes

    converted = (
        convert_legacy_doc_bytes(file_bytes, filename=filename)
        if source_type == "doc"
        else convert_legacy_xls_bytes(file_bytes, filename=filename)
    )
    if converted.get("status") != "ok":
        raise ValueError(
            "Không đọc được file Office cũ: cần LibreOffice để chuyển đổi an toàn "
            f"({converted.get('reason_code') or 'conversion_failed'})."
        )
    data = converted.get("content") or b""
    if source_type == "doc":
        return _extract_docx_local(data)
    return _extract_spreadsheet(data, ".xlsx")


def _extract_spreadsheet(file_bytes: bytes, extension: str) -> str:
    """Read workbook cells without serializing the binary workbook to a prompt."""
    try:
        if extension == ".xlsx":
            import openpyxl
            workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            chunks: list[str] = []
            for sheet in workbook.worksheets:
                chunks.append(f"[Trang tính: {sheet.title}]")
                for row in sheet.iter_rows(values_only=True):
                    values = [str(value).strip() for value in row if value is not None and str(value).strip()]
                    if values:
                        chunks.append(" | ".join(values))
            return "\n".join(chunks)
        import xlrd
        workbook = xlrd.open_workbook(file_contents=file_bytes, on_demand=True)
        chunks = []
        for sheet in workbook.sheets():
            chunks.append(f"[Trang tính: {sheet.name}]")
            for row_index in range(sheet.nrows):
                values = [str(value).strip() for value in sheet.row_values(row_index) if str(value).strip()]
                if values:
                    chunks.append(" | ".join(values))
        return "\n".join(chunks)
    except ImportError as exc:
        raise ValueError("Chưa cài bộ đọc bảng tính (openpyxl/xlrd).") from exc
    except Exception as exc:
        raise ValueError("File bảng tính không hợp lệ hoặc bị hỏng.") from exc


def _extract_pdf_local(file_bytes: bytes) -> str | None:
    try:
        import pypdf  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.info(f"pypdf unavailable; PDF OCR fallback may be required: {exc}")
        return None
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        pages_text: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            # A text layer on another page must never hide a scanned page.
            if not text.strip():
                return None
            pages_text.append(text.strip())
        full_text = "\n\n".join(t for t in pages_text if t)
        if len(full_text.strip()) < 80:
            return None
        return full_text
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"pypdf extraction failed: {exc}")
        return None


def _pdf_has_images(resources, seen=None) -> bool:
    """Follow Form XObjects as well as direct images, with a cycle bound."""
    seen = set() if seen is None else seen
    resources = resources.get_object() if hasattr(resources, "get_object") else resources
    if not resources or id(resources) in seen:
        return False
    if len(seen) >= 128:
        raise ValueError("Cấu trúc ảnh PDF quá phức tạp. Hãy xuất lại tài liệu thành PDF đơn giản hơn.")
    seen.add(id(resources))
    objects = resources.get("/XObject") or {}
    objects = objects.get_object() if hasattr(objects, "get_object") else objects
    for value in objects.values():
        value = value.get_object() if hasattr(value, "get_object") else value
        if value.get("/Subtype") == "/Image":
            return True
        if value.get("/Subtype") == "/Form" and _pdf_has_images(value.get("/Resources"), seen):
            return True
    return False


def _extract_pdf_complete(file_bytes: bytes) -> str:
    """Keep native text and OCR only image pages, preserving physical page order."""
    try:
        import pypdf
    except ImportError as exc:
        raise ValueError("Chưa có bộ đọc PDF cục bộ. Quản trị viên cần cài phụ thuộc pypdf.") from exc
    from api.crawlers.ocr_extractor import extract_ocr_from_pdf_bytes

    if not _pdf_slots.acquire(timeout=5):
        raise ValueError("Đang xử lý nhiều PDF. Vui lòng thử lại sau.")
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF được bảo vệ bằng mật khẩu. Hãy gửi bản đã mở khóa.")
        if len(reader.pages) > 200:
            raise ValueError("PDF vượt 200 trang. Hãy chia thành các phần nhỏ hơn.")
        texts: list[str] = []
        scans: list[int] = []
        for index, page in enumerate(reader.pages):
            native = (page.extract_text() or "").strip()
            # Sparse headers/page numbers over a full-page image are not a
            # sufficient text layer. Blank native pages need no OCR.
            has_image = _pdf_has_images(page.get("/Resources"))
            if has_image and len(native) < 80:
                scans.append(index)
            texts.append(native)
        if scans:
            # One OCR engine only. PyMuPDF/pypdf keep native pages and page
            # order; Tesseract-vie reads the selected image pages.
            result = extract_ocr_from_pdf_bytes(
                file_bytes,
                page_numbers=[page + 1 for page in scans],
                dpi=200,
            )
            pages = result.get("page_texts") or {}
            if not result.get("complete") or result.get("status") != "ok":
                raise ValueError("OCR PDF chưa đọc đủ tài liệu: " + str(result.get("reason") or "không có chữ"))
            for original in scans:
                page_number = original + 1
                recovered = str(pages.get(page_number) or pages.get(str(page_number)) or "").strip()
                if not recovered:
                    raise ValueError(f"Chưa đọc được chữ ở trang {page_number}. Hãy gửi bản rõ hơn.")
                texts[original] = recovered
        if not any(texts):
            raise ValueError("PDF không có nội dung chữ đọc được.")
        return "\n\n".join(f"--- Trang {index + 1} ---\n{text}" for index, text in enumerate(texts))
    finally:
        _pdf_slots.release()


def _get_gemini_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


async def _call_gemini_flash(
    prompt: str,
    file_bytes: bytes,
    mime_type: str,
    model: str = "gemini-2.0-flash",
) -> str:
    import google.genai as genai  # type: ignore
    from google.genai import types as genai_types  # type: ignore

    api_key = _get_gemini_key()
    if not api_key:
        raise ValueError(
            "Chưa cấu hình GOOGLE_API_KEY hoặc GEMINI_API_KEY nên không thể OCR ảnh/PDF scan. "
            "TXT, DOCX và PDF có text vẫn được xử lý local."
        )

    client = genai.Client(api_key=api_key)
    file_part = genai_types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
    text_part = genai_types.Part.from_text(text=prompt)
    response = await client.aio.models.generate_content(
        model=model,
        contents=[genai_types.Content(parts=[file_part, text_part], role="user")],
        config=genai_types.GenerateContentConfig(temperature=0.0, max_output_tokens=8192),
    )
    return (response.text or "").strip()


async def extract_text_from_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
) -> tuple[str, SourceType]:
    # Preserve the lightweight text-only entry point for callers that do not
    # need the canonical layout artifact. Upload, crawler and notebook flows
    # call ``extract_artifact_from_file`` directly and retain blocks/tables.
    # Keeping this adapter also makes the potentially expensive local parser
    # explicitly run off the API event loop.
    source_type = classify_upload(filename, mime_type)
    if source_type == "docx":
        text = await asyncio.to_thread(_extract_docx_local, file_bytes)
        if not text.strip():
            raise ValueError("Không trích xuất được nội dung chữ từ tệp.")
        return _trim_extracted_text(text), "docx"
    result, source_type = await extract_artifact_from_file(
        file_bytes=file_bytes,
        filename=filename,
        mime_type=mime_type,
    )
    text = str(result.get("text") or "")
    if not text.strip():
        raise ValueError(str(result.get("error") or "Không trích xuất được nội dung chữ từ tệp."))
    return _trim_extracted_text(text), source_type


def _bounded_artifact_text(text: str) -> tuple[str, bool]:
    normalized = re.sub(r"\r\n?", "\n", text or "").strip()
    normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
    if len(normalized) <= MAX_ARTIFACT_CHARS:
        return normalized, False
    return normalized[:MAX_ARTIFACT_CHARS].rstrip(), True


def _docx_artifact(file_bytes: bytes) -> dict[str, Any]:
    """Read paragraphs, tables and embedded-image identities in document order."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            if archive.getinfo("word/document.xml").file_size > 32 * 1024 * 1024:
                raise ValueError("Nội dung DOCX sau giải nén vượt giới hạn 32 MB.")
            root = ElementTree.fromstring(archive.read("word/document.xml"))
            image_names = sorted(
                name for name in archive.namelist() if name.startswith("word/media/")
            )
    except KeyError as exc:
        raise ValueError("File DOCX không hợp lệ: thiếu word/document.xml.") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError("File DOCX không hợp lệ hoặc bị hỏng.") from exc

    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = root.find(f"{word}body")
    text_parts: list[str] = []
    blocks: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    cursor = 0
    for child in (list(body) if body is not None else []):
        if child.tag == f"{word}p":
            value = "".join(node.text or "" for node in child.iter(f"{word}t")).strip()
            if not value:
                continue
            start = cursor
            cursor += len(value)
            blocks.append({
                "block_type": "text", "text": value, "page_number": None,
                "char_start": start, "char_end": cursor, "bounding_box": None,
                "extractor": "docx-native", "extractor_version": EXTRACTION_PIPELINE_VERSION,
            })
            text_parts.append(value)
            cursor += 2
        elif child.tag == f"{word}tbl":
            rows: list[list[str]] = []
            for row in child.iter(f"{word}tr"):
                cells = [
                    " ".join("".join(node.text or "" for node in cell.iter(f"{word}t")).split())
                    for cell in row.iter(f"{word}tc")
                ]
                if any(cells):
                    rows.append(cells)
            if not rows:
                continue
            table_text = "\n".join(" | ".join(row) for row in rows)
            table = {
                "block_type": "table", "text": table_text, "page_number": None,
                "rows": rows, "row_count": len(rows),
                "column_count": max((len(row) for row in rows), default=0),
                "bounding_box": None, "extractor": "docx-native",
                "extractor_version": EXTRACTION_PIPELINE_VERSION,
            }
            tables.append(table)
            blocks.append(table)
            text_parts.append(table_text)
            cursor += len(table_text) + 2
    text, truncated = _bounded_artifact_text("\n\n".join(text_parts))
    warnings = ["ARTIFACT_TEXT_TRUNCATED"] if truncated else []
    return {
        "status": "partial" if truncated else "complete",
        "text": text,
        "pages": [],
        "blocks": blocks,
        "tables": tables,
        "images": [
            {"image_index": index + 1, "path": name, "page_number": None}
            for index, name in enumerate(image_names)
        ],
        "confidence": None,
        "warnings": warnings,
        "extractor": "docx-native",
        "extractor_version": EXTRACTION_PIPELINE_VERSION,
    }


def _spreadsheet_artifact(file_bytes: bytes, extension: str) -> dict[str, Any]:
    if extension != ".xlsx":
        raise ValueError("Bảng tính cũ phải được chuyển đổi sang XLSX trước khi phân tích.")
    try:
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except ImportError as exc:
        raise ValueError("Chưa cài bộ đọc bảng tính openpyxl.") from exc
    except Exception as exc:
        raise ValueError("File bảng tính không hợp lệ hoặc bị hỏng.") from exc
    tables: list[dict[str, Any]] = []
    chunks: list[str] = []
    try:
        for sheet_index, sheet in enumerate(workbook.worksheets, 1):
            rows: list[list[str]] = []
            for row in sheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value).strip() for value in row]
                while values and not values[-1]:
                    values.pop()
                if any(values):
                    rows.append(values)
            table_text = "\n".join(" | ".join(row) for row in rows)
            chunks.append(f"[Trang tính: {sheet.title}]\n{table_text}".strip())
            tables.append({
                "block_type": "table", "sheet_index": sheet_index,
                "sheet_name": sheet.title, "text": table_text, "rows": rows,
                "row_count": len(rows),
                "column_count": max((len(row) for row in rows), default=0),
                "page_number": sheet_index, "bounding_box": None,
                "extractor": "xlsx-native", "extractor_version": EXTRACTION_PIPELINE_VERSION,
            })
    finally:
        workbook.close()
    text, truncated = _bounded_artifact_text("\n\n".join(chunks))
    return {
        "status": "partial" if truncated else "complete", "text": text,
        "pages": [{"page_number": item["sheet_index"], "name": item["sheet_name"], "text": item["text"]} for item in tables],
        "blocks": tables, "tables": tables, "images": [], "confidence": None,
        "warnings": ["ARTIFACT_TEXT_TRUNCATED"] if truncated else [],
        "extractor": "xlsx-native", "extractor_version": EXTRACTION_PIPELINE_VERSION,
    }


def _native_pdf_blocks(file_bytes: bytes, page_number: int) -> list[dict[str, Any]]:
    try:
        import fitz
        document = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            page = document.load_page(page_number - 1)
            output = []
            for index, raw in enumerate(page.get_text("blocks")):
                if len(raw) < 5 or not str(raw[4] or "").strip():
                    continue
                output.append({
                    "block_type": "text", "text": str(raw[4]).strip(),
                    "page_number": page_number,
                    "bounding_box": [float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])],
                    "block_index": index, "extractor": "pdf-native",
                    "extractor_version": EXTRACTION_PIPELINE_VERSION,
                })
            return output
        finally:
            document.close()
    except Exception:
        return []


def _pdf_artifact_unlocked(file_bytes: bytes) -> dict[str, Any]:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    except ImportError as exc:
        raise ValueError("Chưa có bộ đọc PDF cục bộ pypdf.") from exc
    except Exception as exc:
        raise ValueError("PDF không hợp lệ hoặc bị hỏng.") from exc
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF được bảo vệ bằng mật khẩu. Hãy gửi bản đã mở khóa.")
    if len(reader.pages) > 200:
        raise ValueError("PDF vượt 200 trang. Hãy chia thành các phần nhỏ hơn.")

    page_texts: dict[int, str] = {}
    blocks: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    scan_pages: list[int] = []
    for page_number, page in enumerate(reader.pages, 1):
        native = (page.extract_text() or "").strip()
        has_image = _pdf_has_images(page.get("/Resources"))
        if has_image:
            images.append({"page_number": page_number, "kind": "embedded_or_scan"})
        if has_image and len(native) < 80:
            scan_pages.append(page_number)
        elif native:
            page_texts[page_number] = native
            blocks.extend(_native_pdf_blocks(file_bytes, page_number))
        else:
            page_texts[page_number] = ""
    warnings: list[str] = []
    extractors = {"pdf-native"}
    failed_pages: set[int] = set()
    if scan_pages:
        from api.crawlers.ocr_extractor import extract_ocr_from_pdf_bytes
        ocr = extract_ocr_from_pdf_bytes(
            file_bytes,
            page_numbers=scan_pages,
            dpi=200,
        )
        for page, value in (ocr.get("page_texts") or {}).items():
            page_texts[int(page)] = str(value or "").strip()
        blocks.extend(list(ocr.get("extraction_blocks") or []))
        if ocr.get("page_texts"):
            extractors.add("tesseract-vie")
        if ocr.get("reason"):
            warnings.append(str(ocr["reason"]))
        failed_pages.update(page for page in scan_pages if not page_texts.get(page))

    ordered_pages = [
        {"page_number": page, "text": page_texts.get(page, "")}
        for page in range(1, len(reader.pages) + 1)
    ]
    combined = "\n\n".join(
        f"--- Trang {item['page_number']} ---\n{item['text']}"
        for item in ordered_pages if str(item["text"]).strip()
    )
    text, truncated = _bounded_artifact_text(combined)
    if truncated:
        warnings.append("ARTIFACT_TEXT_TRUNCATED")
    tables = [item for item in blocks if item.get("block_type") == "table"]
    status = "complete"
    if failed_pages or truncated:
        status = "partial" if text else "error"
    if not text:
        status = "error"
    return {
        "status": status, "text": text, "pages": ordered_pages,
        "blocks": blocks, "tables": tables, "images": images,
        "confidence": None, "warnings": warnings,
        "failed_pages": sorted(failed_pages),
        "extractor": "+".join(sorted(extractors)),
        "extractor_version": EXTRACTION_PIPELINE_VERSION,
    }


def _pdf_artifact(file_bytes: bytes) -> dict[str, Any]:
    """Build a complete artifact without allowing concurrent CPU saturation."""

    if not _pdf_slots.acquire(timeout=5):
        raise ValueError("Đang xử lý một PDF khác. Tệp vẫn có thể được thử lại từ hàng đợi.")
    try:
        return _pdf_artifact_unlocked(file_bytes)
    finally:
        _pdf_slots.release()


async def extract_artifact_from_file(
    file_bytes: bytes,
    filename: str,
    mime_type: str,
) -> tuple[dict[str, Any], SourceType]:
    """Canonical native-first/layout-aware extraction used by every upload worker."""
    if len(file_bytes) > MAX_FILE_BYTES:
        raise ValueError(
            f"File '{filename}' quá lớn ({len(file_bytes) // 1024} KB). "
            f"Giới hạn tối đa là {MAX_FILE_BYTES // 1024 // 1024} MB."
        )

    source_type = classify_upload(filename, mime_type)
    if source_type == "unknown":
        raise ValueError(
            f"Định dạng file '{filename}' ({mime_type or 'unknown'}) chưa được hỗ trợ. "
            "Chấp nhận: doc, docx, pdf, txt, xls, xlsx, jpg, png. Không hỗ trợ audio/video."
        )

    logger.info(f"Preprocessing '{filename}' as {source_type}")
    if source_type == "text":
        text, truncated = _bounded_artifact_text(await asyncio.to_thread(_extract_txt_local, file_bytes))
        return {"status": "partial" if truncated else "complete", "text": text, "pages": [], "blocks": [{"block_type": "text", "text": text, "extractor": "txt-native", "extractor_version": EXTRACTION_PIPELINE_VERSION}], "tables": [], "images": [], "warnings": ["ARTIFACT_TEXT_TRUNCATED"] if truncated else [], "extractor": "txt-native", "extractor_version": EXTRACTION_PIPELINE_VERSION}, "text"
    if source_type == "docx":
        return await asyncio.to_thread(_docx_artifact, file_bytes), "docx"
    if source_type == "doc":
        from api.crawlers.office_converter import convert_legacy_doc_bytes
        converted = await asyncio.to_thread(convert_legacy_doc_bytes, file_bytes, filename=filename)
        if converted.get("status") != "ok":
            raise ValueError("Không đọc được file DOC cũ: cần LibreOffice để chuyển đổi an toàn (" + str(converted.get("reason_code") or "conversion_failed") + ").")
        result = await asyncio.to_thread(_docx_artifact, converted.get("content") or b"")
        result["extractor"] = "libreoffice+docx-native"
        return result, "doc"
    if source_type == "xlsx":
        return await asyncio.to_thread(_spreadsheet_artifact, file_bytes, ".xlsx"), "xlsx"
    if source_type == "xls":
        from api.crawlers.office_converter import convert_legacy_xls_bytes
        converted = await asyncio.to_thread(convert_legacy_xls_bytes, file_bytes, filename=filename)
        if converted.get("status") != "ok":
            raise ValueError("Không đọc được file XLS cũ: cần LibreOffice để chuyển đổi an toàn (" + str(converted.get("reason_code") or "conversion_failed") + ").")
        result = await asyncio.to_thread(_spreadsheet_artifact, converted.get("content") or b"", ".xlsx")
        result["extractor"] = "libreoffice+xlsx-native"
        return result, "xls"
    if source_type == "pdf":
        return await asyncio.to_thread(_pdf_artifact, file_bytes), "pdf"

    from api.local_image_ocr import extract_image
    extracted = await asyncio.to_thread(extract_image, file_bytes)
    text, truncated = _bounded_artifact_text(str(extracted.get("text") or ""))
    result = {
        "status": "partial" if truncated else ("complete" if text else "error"),
        "text": text, "pages": [{"page_number": 1, "text": text}],
        "blocks": list(extracted.get("extraction_blocks") or []),
        "tables": [], "images": [{"page_number": 1, "kind": "source_image"}],
        "confidence": extracted.get("ocr_confidence"),
        "warnings": ["ARTIFACT_TEXT_TRUNCATED"] if truncated else [],
        "extractor": str(extracted.get("extractor_used") or "tesseract-vie"),
        "extractor_version": EXTRACTION_PIPELINE_VERSION,
    }
    return result, "image"
