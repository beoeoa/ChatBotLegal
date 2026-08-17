"""Optional local LibreOffice adapter for legacy official DOC packages."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Sequence


OLE_HEADER = bytes.fromhex("D0CF11E0A1B11AE1")
COMMON_WINDOWS_PATHS: Sequence[Path] = (
    Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
    Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
)


def detect_office_converter() -> Path | None:
    configured = str(os.getenv("LIBREOFFICE_CMD") or "").strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return path
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return next((path for path in COMMON_WINDOWS_PATHS if path.is_file()), None)


def convert_legacy_doc_bytes(
    content: bytes,
    *,
    filename: str,
    timeout_seconds: int = 120,
    command_runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    """Convert one OLE DOC to DOCX without exposing process output."""

    if not content.startswith(OLE_HEADER):
        return {
            "status": "failed",
            "complete": False,
            "reason_code": "LEGACY_DOC_MAGIC_INVALID",
            "content": b"",
            "sha256": None,
            "converter": None,
        }
    command = detect_office_converter()
    if command is None:
        return {
            "status": "unavailable",
            "complete": False,
            "reason_code": "LIBREOFFICE_RUNTIME_UNAVAILABLE",
            "content": b"",
            "sha256": None,
            "converter": None,
        }

    safe_stem = Path(filename).stem or "form"
    with tempfile.TemporaryDirectory(prefix="chatbotlegal-doc-") as temp:
        directory = Path(temp)
        source = directory / f"{safe_stem}.doc"
        source.write_bytes(content)
        try:
            result = command_runner(
                [
                    str(command),
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(directory),
                    str(source),
                ],
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                ),
            )
        except (OSError, subprocess.TimeoutExpired):
            return {
                "status": "failed",
                "complete": False,
                "reason_code": "LIBREOFFICE_CONVERSION_FAILED",
                "content": b"",
                "sha256": None,
                "converter": "libreoffice-headless",
            }
        destination = directory / f"{safe_stem}.docx"
        if result.returncode != 0 or not destination.is_file():
            return {
                "status": "failed",
                "complete": False,
                "reason_code": "LIBREOFFICE_CONVERSION_FAILED",
                "content": b"",
                "sha256": None,
                "converter": "libreoffice-headless",
            }
        converted = destination.read_bytes()
        if not converted.startswith(b"PK"):
            return {
                "status": "failed",
                "complete": False,
                "reason_code": "CONVERTED_DOCX_MAGIC_INVALID",
                "content": b"",
                "sha256": None,
                "converter": "libreoffice-headless",
            }
        return {
            "status": "ok",
            "complete": True,
            "reason_code": "LEGACY_DOC_CONVERTED",
            "content": converted,
            "sha256": hashlib.sha256(converted).hexdigest(),
            "source_sha256": hashlib.sha256(content).hexdigest(),
            "converter": "libreoffice-headless",
        }
