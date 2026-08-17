from __future__ import annotations

from pathlib import Path
import zipfile

from api.crawlers.office_converter import (
    convert_legacy_doc_bytes,
    detect_office_converter,
)


OLE_HEADER = bytes.fromhex("D0CF11E0A1B11AE1")


def test_missing_libreoffice_is_a_structured_fail_closed_result(
    monkeypatch,
):
    monkeypatch.delenv("LIBREOFFICE_CMD", raising=False)
    monkeypatch.setattr(
        "api.crawlers.office_converter.shutil.which",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "api.crawlers.office_converter.COMMON_WINDOWS_PATHS",
        (),
    )

    result = convert_legacy_doc_bytes(OLE_HEADER + b"legacy", filename="form.doc")

    assert result["status"] == "unavailable"
    assert result["complete"] is False
    assert result["reason_code"] == "LIBREOFFICE_RUNTIME_UNAVAILABLE"
    assert result["content"] == b""


def test_converter_rejects_non_ole_input_before_starting_process():
    result = convert_legacy_doc_bytes(b"<html>not a doc</html>", filename="form.doc")

    assert result["status"] == "failed"
    assert result["reason_code"] == "LEGACY_DOC_MAGIC_INVALID"


def test_headless_conversion_returns_verified_docx(
    tmp_path: Path,
    monkeypatch,
):
    fake_command = tmp_path / "soffice.exe"
    fake_command.write_bytes(b"MZ")
    monkeypatch.setenv("LIBREOFFICE_CMD", str(fake_command))

    def runner(command, **_kwargs):
        outdir = Path(command[command.index("--outdir") + 1])
        output = outdir / "form.docx"
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(
                "[Content_Types].xml",
                (
                    "<?xml version='1.0'?>"
                    "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>"
                ),
            )

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    result = convert_legacy_doc_bytes(
        OLE_HEADER + b"legacy",
        filename="form.doc",
        command_runner=runner,
    )

    assert detect_office_converter() == fake_command
    assert result["status"] == "ok"
    assert result["complete"] is True
    assert result["content"].startswith(b"PK")
    assert len(result["sha256"]) == 64
