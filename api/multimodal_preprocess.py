"""
Multimodal preprocessing service for the citizen question flow.

Stage 1 converts supported uploads (txt/docx/pdf/png/jpg/jpeg) into plain
Vietnamese text/context. The frontend appends extracted context to the question
and sends it through the existing RAG ask pipeline (Stage 2).
"""

from __future__ import annotations

import html
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

from loguru import logger

TEXT_MIMES = {"text/plain"}
DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/x-docx",
}
PDF_MIMES = {"application/pdf"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg"}
SUPPORTED_MIMES = TEXT_MIMES | DOCX_MIMES | PDF_MIMES | IMAGE_MIMES
SUPPORTED_EXTENSIONS = {".txt", ".docx", ".pdf", ".png", ".jpg", ".jpeg"}

SourceType = Literal["text", "docx", "pdf", "image", "unknown"]

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_EXTRACTED_CHARS = 80_000

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
    if mt in PDF_MIMES or ext == ".pdf":
        return "pdf"
    if mt in IMAGE_MIMES or ext in {".png", ".jpg", ".jpeg"}:
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
            pages_text.append(text.strip())
        full_text = "\n\n".join(t for t in pages_text if t)
        if len(full_text.strip()) < 80:
            return None
        return full_text
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"pypdf extraction failed: {exc}")
        return None


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
    if len(file_bytes) > MAX_FILE_BYTES:
        raise ValueError(
            f"File '{filename}' quá lớn ({len(file_bytes) // 1024} KB). "
            f"Giới hạn tối đa là {MAX_FILE_BYTES // 1024 // 1024} MB."
        )

    source_type = classify_upload(filename, mime_type)
    if source_type == "unknown":
        raise ValueError(
            f"Định dạng file '{filename}' ({mime_type or 'unknown'}) chưa được hỗ trợ. "
            "Chấp nhận: txt, docx, pdf, png, jpg, jpeg. Không hỗ trợ audio/video."
        )

    logger.info(f"Preprocessing '{filename}' as {source_type}")
    if source_type == "text":
        return _trim_extracted_text(_extract_txt_local(file_bytes)), "text"
    if source_type == "docx":
        return _trim_extracted_text(_extract_docx_local(file_bytes)), "docx"
    if source_type == "pdf":
        local_text = _extract_pdf_local(file_bytes)
        if local_text:
            logger.info(f"PDF '{filename}' extracted locally ({len(local_text)} chars)")
            return _trim_extracted_text(local_text), "pdf"
        logger.info(f"PDF '{filename}' needs OCR fallback")
        extracted = await _call_gemini_flash(
            prompt=_PDF_VISION_PROMPT,
            file_bytes=file_bytes,
            mime_type="application/pdf",
        )
        return _trim_extracted_text(extracted), "pdf"

    ocr_mime = "image/jpeg" if _normalize_ext(filename) in {".jpg", ".jpeg"} else "image/png"
    if (mime_type or "").lower().split(";", 1)[0].strip() == "image/jpeg":
        ocr_mime = "image/jpeg"
    extracted = await _call_gemini_flash(
        prompt=_VISION_PROMPT,
        file_bytes=file_bytes,
        mime_type=ocr_mime,
    )
    return _trim_extracted_text(extracted), "image"
