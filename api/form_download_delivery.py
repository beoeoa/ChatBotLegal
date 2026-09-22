"""Deliver catalog-owned form bytes; never accept a caller-supplied URL."""
from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
from pathlib import Path
from urllib.parse import urljoin

import httpx
from fastapi import HTTPException

from api.legal_form_catalog import _file_integrity, _is_official_url

MAX_FORM_BYTES = 20 * 1024 * 1024


async def read_form_bytes(form: dict, root: Path) -> tuple[bytes, str]:
    extension = str(form.get("file_format") or "").lower().lstrip(".")
    if extension not in {"pdf", "doc", "docx", "xls", "xlsx"}:
        raise HTTPException(409, "Biểu mẫu này không có tệp tải trực tiếp.")
    local = str(form.get("local_path") or "").strip()
    if local:
        path = (root / local).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise HTTPException(404, "Không tìm thấy tệp biểu mẫu đã duyệt.")
        if path.stat().st_size > MAX_FORM_BYTES:
            raise HTTPException(413, "Tệp biểu mẫu vượt giới hạn tải.")
        data = await asyncio.to_thread(path.read_bytes)
    else:
        url = str(form.get("official_download_url") or "")
        data = b""
        async with asyncio.timeout(25), httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            for _ in range(4):
                if not _is_official_url(url) or not url.startswith("https://"):
                    raise HTTPException(409, "Địa chỉ tải không thuộc nguồn chính thức đã duyệt.")
                async with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise HTTPException(502, "Nguồn biểu mẫu chưa trả được tệp. Vui lòng thử lại sau.")
                    chunks = []
                    length = 0
                    async for chunk in response.aiter_bytes():
                        length += len(chunk)
                        if length > MAX_FORM_BYTES:
                            raise HTTPException(413, "Tệp biểu mẫu vượt giới hạn tải.")
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    break
            else:
                raise HTTPException(502, "Nguồn tải biểu mẫu chuyển hướng quá nhiều lần.")
    expected = str(form.get("sha256") or "").casefold()
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise HTTPException(409, "Tệp nguồn đã thay đổi so với bản được duyệt; cần cập nhật lại biểu mẫu.")
    # Reuse the catalog's existing file integrity policy, including format
    # validation. The temporary copy is not a corpus or a newly released form.
    with tempfile.TemporaryDirectory(prefix="legal-form-download-") as directory:
        candidate = Path(directory) / f"form.{extension}"
        candidate.write_bytes(data)
        valid, _ = _file_integrity(candidate, extension)
    if not valid:
        raise HTTPException(502, "Nguồn tải không trả về tệp biểu mẫu hợp lệ; không thể cung cấp tệp này.")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", str(form.get("form_code") or form.get("form_id") or "bieu-mau")).strip("-")
    return data, f"{stem or 'bieu-mau'}.{extension}"
