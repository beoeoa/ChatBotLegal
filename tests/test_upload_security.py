from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from api.upload_security import (
    DOCUMENT_UPLOAD_POLICY,
    SUPPORT_ATTACHMENT_POLICY,
    UploadSecurityError,
    safe_storage_path,
    validate_upload,
    write_validated_upload,
)


def _docx(*, unsafe_name: str | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(unsafe_name or "word/document.xml", "<document>safe</document>")
    return output.getvalue()


def test_valid_document_is_fingerprinted_and_stored_exclusively(tmp_path: Path):
    content = _docx()
    digest = hashlib.sha256(content).hexdigest()
    result = validate_upload(
        filename="mau-hop-le.docx",
        content=content,
        policy=DOCUMENT_UPLOAD_POLICY,
        expected_sha256=digest,
    )
    assert result.sha256 == digest
    assert result.detected_type == "application/zip"
    stored = write_validated_upload(root=tmp_path, storage_name="candidate.docx", content=content)
    assert stored.read_bytes() == content
    with pytest.raises(FileExistsError):
        write_validated_upload(root=tmp_path, storage_name="candidate.docx", content=content)


@pytest.mark.parametrize("filename", ["../evil.pdf", "folder/evil.pdf", "folder\\evil.pdf", "NUL.txt"])
def test_path_and_reserved_names_fail_closed(filename: str, tmp_path: Path):
    with pytest.raises(UploadSecurityError, match="Tên tệp"):
        validate_upload(filename=filename, content=b"%PDF-1.7\n", policy=DOCUMENT_UPLOAD_POLICY)
    with pytest.raises(UploadSecurityError):
        safe_storage_path(tmp_path, filename)


def test_checksum_malware_polyglot_and_archive_traversal_are_rejected():
    with pytest.raises(UploadSecurityError) as mismatch:
        validate_upload(
            filename="safe.pdf", content=b"%PDF-1.7\n", policy=DOCUMENT_UPLOAD_POLICY,
            expected_sha256="0" * 64,
        )
    assert mismatch.value.code == "checksum_mismatch"

    with pytest.raises(UploadSecurityError) as malware:
        validate_upload(
            filename="note.txt", content=b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE",
            policy=SUPPORT_ATTACHMENT_POLICY,
        )
    assert malware.value.code == "malware_detected"

    with pytest.raises(UploadSecurityError) as executable:
        validate_upload(filename="fake.pdf", content=b"MZ-not-a-pdf", policy=DOCUMENT_UPLOAD_POLICY)
    assert executable.value.code == "executable_payload"

    with pytest.raises(UploadSecurityError) as archive:
        validate_upload(
            filename="unsafe.docx", content=_docx(unsafe_name="../payload.js"),
            policy=DOCUMENT_UPLOAD_POLICY,
        )
    assert archive.value.code == "archive_path_traversal"


def test_declared_extension_must_match_file_signature_and_size_limit():
    with pytest.raises(UploadSecurityError) as mismatch:
        validate_upload(filename="fake.pdf", content=b"plain text", policy=DOCUMENT_UPLOAD_POLICY)
    assert mismatch.value.code == "content_type_mismatch"

    with pytest.raises(UploadSecurityError) as too_large:
        validate_upload(
            filename="large.txt",
            content=b"a" * (SUPPORT_ATTACHMENT_POLICY.max_bytes + 1),
            policy=SUPPORT_ATTACHMENT_POLICY,
        )
    assert too_large.value.status_code == 413
