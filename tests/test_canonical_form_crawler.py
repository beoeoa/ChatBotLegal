from __future__ import annotations

from scripts.crawl_canonical_forms import (
    candidate_from_download,
    is_allowed_official_url,
    validate_download,
)


def test_crawler_allows_only_official_hosts():
    assert is_allowed_official_url("https://dichvucong.gov.vn/form.pdf")
    assert is_allowed_official_url("https://cdn.haiphong.gov.vn/form.docx")
    assert is_allowed_official_url("https://vbpl.vn/attachments/form.pdf")
    assert not is_allowed_official_url("https://dulieuphapluat.vn/form.pdf")
    assert not is_allowed_official_url("https://evil.example/haiphong.gov.vn.pdf")


def test_crawler_rejects_html_disguised_as_pdf_and_macro_files():
    ok, reason, _fmt = validate_download(
        b"<!doctype html><html>blocked</html>" + b"0" * 400,
        "application/pdf",
        "https://dichvucong.gov.vn/form.pdf",
    )
    assert ok is False
    assert reason == "HTML_DISGUISED_AS_FILE"

    ok, reason, _fmt = validate_download(
        b"PK" + b"0" * 400,
        "application/vnd.ms-word.document.macroEnabled.12",
        "https://dichvucong.gov.vn/form.docm",
    )
    assert ok is False
    assert reason == "MACRO_FILE_QUARANTINED"


def test_downloaded_asset_is_always_pending_review(tmp_path):
    content = b"%PDF-1.4\n" + b"0" * 512
    candidate = candidate_from_download(
        requirement_id="req-1",
        procedure_id="dang_ky_khai_sinh",
        canonical_name="Tờ khai đăng ký khai sinh",
        source_page="https://dichvucong.gov.vn/p/home/procedure",
        download_url="https://dichvucong.gov.vn/files/birth.pdf",
        content=content,
        content_type="application/pdf",
        download_dir=tmp_path,
    )

    assert candidate["status"] == "AVAILABLE_OFFICIAL_FILE"
    assert candidate["review_status"] == "candidate_pending_review"
    assert candidate["approved"] is False
    assert candidate["sha256"]
    assert (tmp_path / candidate["file_name"]).is_file()
