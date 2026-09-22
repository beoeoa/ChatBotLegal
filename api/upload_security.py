"""Fail-closed validation shared by every user-controlled file upload.

The built-in scanner is deliberately deterministic and local-first.  It
rejects known test malware, executable/polyglot payloads, unsafe archives and
macro-bearing Office packages.  A deployment may provide an additional scanner
callback, but an optional external daemon is never required for the safe
baseline.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Callable, FrozenSet


class UploadSecurityError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class UploadPolicy:
    allowed_extensions: FrozenSet[str]
    max_bytes: int
    min_bytes: int = 1
    allow_office_macros: bool = False
    max_archive_entries: int = 2_000
    max_archive_uncompressed_bytes: int = 100 * 1024 * 1024


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    extension: str
    size_bytes: int
    sha256: str
    detected_type: str
    scan_engine: str = "builtin-signature-v1"


DOCUMENT_UPLOAD_POLICY = UploadPolicy(
    frozenset({".pdf", ".doc", ".docx", ".xls", ".xlsx", ".rtf", ".txt", ".md", ".json", ".zip", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}),
    max_bytes=25 * 1024 * 1024,
)
SUPPORT_ATTACHMENT_POLICY = UploadPolicy(
    frozenset({".pdf", ".doc", ".docx", ".txt", ".png", ".jpg", ".jpeg"}),
    max_bytes=10 * 1024 * 1024,
)
SOURCE_ASSET_POLICY = UploadPolicy(
    frozenset(
        {
            ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
            ".txt", ".md", ".epub", ".mp4", ".avi", ".mov", ".wmv",
            ".mp3", ".wav", ".m4a", ".aac", ".jpg", ".jpeg", ".png",
            ".tiff", ".tif", ".webp", ".bmp", ".zip", ".tar", ".gz", ".html", ".csv",
        }
    ),
    max_bytes=250 * 1024 * 1024,
)

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_EICAR = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
_OFFICE_MACRO_SUFFIXES = ("vbaproject.bin", ".vbs", ".js", ".exe", ".dll", ".scr")


def _safe_client_filename(filename: str) -> str:
    value = str(filename or "").strip()
    if not value or "\x00" in value or "/" in value or "\\" in value:
        raise UploadSecurityError("unsafe_filename", "Tên tệp không hợp lệ.", status_code=400)
    if value in {".", ".."} or Path(value).name != value:
        raise UploadSecurityError("unsafe_filename", "Tên tệp không hợp lệ.", status_code=400)
    if any(ord(character) < 32 for character in value):
        raise UploadSecurityError("unsafe_filename", "Tên tệp chứa ký tự điều khiển.", status_code=400)
    if Path(value).stem.casefold() in _WINDOWS_RESERVED:
        raise UploadSecurityError("unsafe_filename", "Tên tệp dành riêng không được phép.", status_code=400)
    return value


def _inspect_zip(content: bytes, policy: UploadPolicy) -> None:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > policy.max_archive_entries:
                raise UploadSecurityError("archive_too_many_entries", "Gói tệp có quá nhiều mục.")
            total = 0
            for entry in entries:
                normalized = entry.filename.replace("\\", "/")
                path = PurePosixPath(normalized)
                if path.is_absolute() or ".." in path.parts:
                    raise UploadSecurityError("archive_path_traversal", "Gói tệp chứa đường dẫn không an toàn.")
                total += max(0, int(entry.file_size))
                if total > policy.max_archive_uncompressed_bytes:
                    raise UploadSecurityError("archive_expansion_limit", "Gói tệp vượt giới hạn giải nén.")
                lowered = normalized.casefold()
                if not policy.allow_office_macros and lowered.endswith(_OFFICE_MACRO_SUFFIXES):
                    raise UploadSecurityError("active_content_detected", "Tệp chứa mã thực thi hoặc macro.")
    except zipfile.BadZipFile as exc:
        raise UploadSecurityError("invalid_archive", "Cấu trúc tệp nén/Office không hợp lệ.") from exc


def _detect_type(extension: str, content: bytes, policy: UploadPolicy) -> str:
    if content.startswith(b"MZ"):
        raise UploadSecurityError("executable_payload", "Không chấp nhận nội dung thực thi.", status_code=415)
    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise UploadSecurityError("content_type_mismatch", "Nội dung không phải PDF hợp lệ.", status_code=415)
        return "application/pdf"
    if extension in {".doc", ".xls"}:
        if not content.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
            raise UploadSecurityError("content_type_mismatch", "Nội dung Office cũ không hợp lệ.", status_code=415)
        return "application/x-ole-storage"
    if extension in {".docx", ".xlsx", ".zip"}:
        if not content.startswith(b"PK"):
            raise UploadSecurityError("content_type_mismatch", "Nội dung gói Office/ZIP không hợp lệ.", status_code=415)
        _inspect_zip(content, policy)
        return "application/zip"
    if extension == ".rtf":
        if not content.lstrip().startswith(b"{\\rtf"):
            raise UploadSecurityError("content_type_mismatch", "Nội dung RTF không hợp lệ.", status_code=415)
        return "application/rtf"
    if extension == ".png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise UploadSecurityError("content_type_mismatch", "Nội dung PNG không hợp lệ.", status_code=415)
        return "image/png"
    if extension in {".jpg", ".jpeg"}:
        if not (content.startswith(b"\xff\xd8\xff") and content.rstrip().endswith(b"\xff\xd9")):
            raise UploadSecurityError("content_type_mismatch", "Nội dung JPEG không hợp lệ.", status_code=415)
        return "image/jpeg"
    if extension in {".webp", ".bmp", ".tif", ".tiff"}:
        from PIL import Image
        try:
            with Image.open(BytesIO(content)) as image:
                expected = {'.webp': 'WEBP', '.bmp': 'BMP', '.tif': 'TIFF', '.tiff': 'TIFF'}[extension]
                if image.format != expected:
                    raise ValueError('format mismatch')
                image.verify()
        except Exception as exc:
            raise UploadSecurityError('content_type_mismatch', 'Nội dung ảnh không hợp lệ.', status_code=415) from exc
        return {'.webp': 'image/webp', '.bmp': 'image/bmp', '.tif': 'image/tiff', '.tiff': 'image/tiff'}[extension]
    if extension in {".txt", ".md", ".json"}:
        if b"\x00" in content:
            raise UploadSecurityError("binary_text_payload", "Tệp văn bản chứa dữ liệu nhị phân.", status_code=415)
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UploadSecurityError("invalid_utf8", "Tệp văn bản phải dùng UTF-8.", status_code=415) from exc
        return "text/plain"
    return "application/octet-stream"


def validate_upload(
    *,
    filename: str,
    content: bytes,
    policy: UploadPolicy,
    expected_sha256: str | None = None,
    malware_scanner: Callable[[bytes], bool] | None = None,
) -> ValidatedUpload:
    safe_name = _safe_client_filename(filename)
    extension = Path(safe_name).suffix.casefold()
    if extension not in policy.allowed_extensions:
        raise UploadSecurityError("extension_not_allowed", "Định dạng tệp không được hỗ trợ.", status_code=415)
    size = len(content)
    if size < policy.min_bytes:
        raise UploadSecurityError("file_empty_or_too_small", "Tệp trống hoặc quá nhỏ.", status_code=400)
    if size > policy.max_bytes:
        raise UploadSecurityError("file_too_large", "Tệp vượt quá dung lượng cho phép.", status_code=413)
    if _EICAR in content.upper():
        raise UploadSecurityError("malware_detected", "Tệp không vượt qua kiểm tra nội dung độc hại.", status_code=422)
    detected_type = _detect_type(extension, content, policy)
    digest = hashlib.sha256(content).hexdigest()
    expected = str(expected_sha256 or "").strip().casefold()
    if expected:
        if not _HEX_64.fullmatch(expected) or not hmac.compare_digest(digest, expected):
            raise UploadSecurityError("checksum_mismatch", "Checksum tệp không khớp.", status_code=409)
    if malware_scanner is not None and not bool(malware_scanner(content)):
        raise UploadSecurityError("malware_detected", "Tệp không vượt qua bộ quét nội dung độc hại.", status_code=422)
    return ValidatedUpload(safe_name, extension, size, digest, detected_type)


def safe_storage_path(root: Path, storage_name: str) -> Path:
    root_resolved = Path(root).resolve()
    name = _safe_client_filename(storage_name)
    destination = (root_resolved / name).resolve()
    try:
        destination.relative_to(root_resolved)
    except ValueError as exc:
        raise UploadSecurityError("storage_path_escape", "Đường dẫn lưu tệp không an toàn.", status_code=400) from exc
    return destination


def write_validated_upload(*, root: Path, storage_name: str, content: bytes) -> Path:
    destination = safe_storage_path(root, storage_name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination
