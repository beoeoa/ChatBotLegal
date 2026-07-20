from bs4 import BeautifulSoup

from api.crawlers.base_crawler import (
    detect_scope,
    extract_date,
    extract_issuing_agency,
    extract_law_number,
)
from api.crawlers.dvc_crawler import DVCCrawler
from api.legal_crawl_service import (
    LegalCrawlService,
    _detect_scope,
    _repair_display_metadata,
)


def test_vietnamese_legal_metadata_patterns_are_functional():
    text = (
        "ỦY BAN NHÂN DÂN THÀNH PHỐ HẢI PHÒNG ban hành "
        "Quyết định 123/2026/QĐ-UBND ngày 05/07/2026"
    )

    assert extract_law_number(text) == "123/2026/QĐ-UBND"
    assert extract_date(text) == "2026-07-05"
    assert extract_issuing_agency(text).casefold().startswith("ủy ban nhân dân")
    assert detect_scope(text, "", "") == "haiphong"


def test_dvc_parser_extracts_fields_and_absolute_form_url():
    html = """
    <main>
      <p>Cơ quan tiếp nhận: Ủy ban nhân dân cấp xã</p>
      <p>Thời hạn giải quyết: Trong ngày làm việc</p>
      <p>Lệ phí: 8.000 đồng</p>
      <p>Thành phần hồ sơ: Tờ khai đăng ký khai sinh</p>
      <a href="/files/mau-khai-sinh.docx">Tải biểu mẫu khai sinh</a>
    </main>
    """
    soup = BeautifulSoup(html, "html.parser")
    crawler = object.__new__(DVCCrawler)

    fields = crawler.extract_procedure_fields(soup)
    forms = crawler.discover_form_links(soup, "https://dichvucong.example/thu-tuc/1")

    assert fields["co_quan_tiep_nhan"] == "Ủy ban nhân dân cấp xã"
    assert fields["thoi_han_xu_ly"] == "Trong ngày làm việc"
    assert fields["le_phi"] == "8.000 đồng"
    assert fields["thanh_phan_ho_so"] == "Tờ khai đăng ký khai sinh"
    assert forms == [
        {
            "name": "Tải biểu mẫu khai sinh",
            "url": "https://dichvucong.example/files/mau-khai-sinh.docx",
            "type": "docx",
        }
    ]


def test_legacy_candidate_text_is_repaired_for_display_only():
    value = {"title": "ThÃ´ng tÆ° sá»‘ 01/2026/TT-BTP"}
    repaired = _repair_display_metadata(value)

    assert repaired["title"] == "Thông tư số 01/2026/TT-BTP"
    assert value["title"] == "ThÃ´ng tÆ° sá»‘ 01/2026/TT-BTP"
    assert _detect_scope(None, "Quyết định của UBND Hải Phòng", "", "") == "haiphong"


def test_mojibake_candidate_cannot_enter_official_rag():
    candidate = {
        "title": "ThÃ´ng tÆ° sá»‘ 01/2026/TT-BTP",
        "law_number": "01/2026/TT-BTP",
        "source_url": "https://vbpl.vn/example",
        "scope": "central",
        "content": "Nội dung hợp lệ " * 20,
        "raw_metadata": {
            "confirmed_official_source": True,
            "effective_date": "2026-01-01",
        },
    }

    errors = LegalCrawlService.validate_candidate_for_import(candidate)

    assert any("lỗi mã hóa" in error for error in errors)
