from io import BytesIO
import zipfile

from scripts.crawl_haiphong_official_forms import (
    attachment_links,
    article_links,
    classify_domain,
    commune_evidence,
    detect_file_type,
    pagination_config,
)
from unittest.mock import Mock


def _minimal_docx() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<w:document />")
    return buffer.getvalue()


def test_pagination_config_reads_hai_phong_ajax_parameters():
    html = """
    <script>
      var maxPage = parseInt(9);
      $.ajax({ data: {
        article_category_id: 86821,
        categoryIds: "86821",
        site_id: 2989,
        page_size: 15
      }});
    </script>
    """
    assert pagination_config(html) == {
        "article_category_id": 86821,
        "categoryIds": "86821",
        "site_id": 2989,
        "page_size": 15,
        "max_page": 9,
    }


def test_article_links_stays_inside_listing_category():
    html = """
    <a href="/quy-trinh/a-123">Thủ tục A</a>
    <a href="/tin-tuc/b-456">Tin khác</a>
    """
    assert article_links("https://example.gov.vn/quy-trinh", html) == [
        {
            "title": "Thủ tục A",
            "detail_url": "https://example.gov.vn/quy-trinh/a-123",
        }
    ]


def test_scope_domain_and_file_validation():
    assert classify_domain("Đăng ký khai sinh tại UBND cấp xã") == "ho_tich_chung_thuc"
    assert commune_evidence("", "Cơ quan thực hiện: Ủy ban nhân dân cấp xã")
    assert detect_file_type(_minimal_docx(), "https://cdn.test/form.docx") == "docx"
    assert detect_file_type(b"<html>login</html>", "https://cdn.test/form.docx") is None


def test_attachment_links_reads_links_and_embedded_documents():
    html = """
    <a href="https://cdn.test/form.docx">Mẫu đơn</a>
    <iframe src="https://cdn.test/decision.pdf"></iframe>
    <img src="https://cdn.test/banner.png">
    """
    assert attachment_links("https://example.test/detail", html) == [
        {"label": "Mẫu đơn", "url": "https://cdn.test/form.docx"},
        {"label": "decision.pdf", "url": "https://cdn.test/decision.pdf"},
    ]


def test_category_discovery_keeps_only_project_legal_domains(monkeypatch):
    from scripts import crawl_haiphong_official_forms as module

    response = Mock()
    response.text = """
    <a href="/linh-vuc-ho-tich">Lĩnh vực Hộ tịch</a>
    <a href="/linh-vuc-dat-dai">Lĩnh vực Đất đai</a>
    <a href="/linh-vuc-kinh-doanh-khi">Kinh doanh khí</a>
    <a href="/tin-tuc">Tin tức</a>
    """
    response.raise_for_status.return_value = None
    client = Mock()
    client.get.return_value = response

    rows = module.category_listings(client)
    assert [row["source_domain"] for row in rows] == [
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
    ]
