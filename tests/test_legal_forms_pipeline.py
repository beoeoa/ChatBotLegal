from io import BytesIO
import zipfile

from scripts.crawl_legal_forms import candidate_download_urls, detect_file_type


def _minimal_docx() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<w:document />")
    return buffer.getvalue()


def test_detect_file_type_rejects_html_disguised_as_document():
    content = b"<!DOCTYPE html><html><body>Login required</body></html>"
    assert detect_file_type(content, "text/html", "https://example.test/form.docx") is None


def test_detect_file_type_accepts_valid_pdf_and_docx():
    assert detect_file_type(b"%PDF-1.7\n", "application/pdf", "https://example.test/form") == "pdf"
    assert (
        detect_file_type(
            _minimal_docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "https://example.test/form",
        )
        == "docx"
    )


def test_candidate_download_urls_only_keeps_download_links():
    page = """
    <html><body>
      <a href="/about">About</a>
      <a href="/files/form.docx">Tải DOCX</a>
      <a href="/Download.ashx?id=12">Download</a>
      <a href="/files/form.docx">Duplicate</a>
    </body></html>
    """
    assert candidate_download_urls("https://example.test/forms/12", page) == [
        "https://example.test/files/form.docx",
        "https://example.test/Download.ashx?id=12",
    ]
