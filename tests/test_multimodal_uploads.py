import io
import zipfile

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.multimodal_preprocess import MAX_FILE_BYTES, classify_upload


@pytest.fixture(autouse=True)
def bypass_auth():
    async def mock_has_real_users():
        return False

    with patch("api.auth.has_real_users", new=mock_has_real_users), patch(
        "api.auth.configured_role_passwords", return_value={}
    ):
        yield


def client():
    return TestClient(app)


def _make_docx(text: str) -> bytes:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
</w:document>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_classify_supported_citizen_upload_types():
    assert classify_upload("note.txt", "text/plain") == "text"
    assert classify_upload("van-ban.docx", "application/octet-stream") == "docx"
    assert classify_upload("scan.pdf", "application/pdf") == "pdf"
    assert classify_upload("anh.jpg", "image/jpeg") == "image"
    assert classify_upload("anh.jpeg", "application/octet-stream") == "image"
    assert classify_upload("anh.png", "image/png") == "image"


def test_reject_video_and_audio_upload_types():
    assert classify_upload("clip.mp4", "video/mp4") == "unknown"
    assert classify_upload("recording.m4a", "audio/mp4") == "unknown"


def test_extract_text_endpoint_accepts_utf8_txt():
    response = client().post(
        "/api/media/extract-text",
        files={"file": ("hoi-dap.txt", "N?i dung gi?y t? h? t?ch", "text/plain")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source_type"] == "text"
    assert data["extracted_text"] == "N?i dung gi?y t? h? t?ch"
    assert data["char_count"] == len("N?i dung gi?y t? h? t?ch")


def test_extract_text_endpoint_accepts_docx():
    response = client().post(
        "/api/media/extract-text",
        files={
            "file": (
                "mau-don.docx",
                _make_docx("??n ?? ngh? x?c nh?n c? tr?"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source_type"] == "docx"
    assert "??n ?? ngh? x?c nh?n c? tr?" in data["extracted_text"]


def test_extract_text_endpoint_rejects_unsupported_file_type():
    response = client().post(
        "/api/media/extract-text",
        files={"file": ("video.mp4", b"not a real video", "video/mp4")},
    )

    assert response.status_code == 415
    assert "Không hỗ trợ" in response.json()["detail"]


def test_extract_text_endpoint_rejects_too_large_file():
    response = client().post(
        "/api/media/extract-text",
        files={"file": ("big.txt", b"x" * (MAX_FILE_BYTES + 1), "text/plain")},
    )

    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "file_too_large"
    assert "dung lượng" in detail["message"]
