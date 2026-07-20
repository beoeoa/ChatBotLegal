from scripts.crawl_dulieuphapluat_forms import (
    detect_domain,
    extract_detail_links,
    extract_download_links,
    listing_page_url,
)


def test_listing_page_url_sets_and_removes_page_query():
    seed = "https://www.dulieuphapluat.vn/bieu-mau/a/b.html?page=1"

    assert listing_page_url(seed, 1) == "https://www.dulieuphapluat.vn/bieu-mau/a/b.html"
    assert listing_page_url(seed, 3) == "https://www.dulieuphapluat.vn/bieu-mau/a/b.html?page=3"


def test_extract_detail_links_keeps_only_same_category_detail_pages():
    html = """
    <a href="/bieu-mau/giao-dich-voi-bat-dong-san/mua-ban-nha-dat/hop-dong-1.html">Hợp đồng</a>
    <a href="/bieu-mau/giao-dich-voi-bat-dong-san.html">Danh mục</a>
    <a href="/bieu-mau/hon-nhan-gia-dinh/ly-hon/don-2.html">Ngoài mục</a>
    <a href="?page=2">2</a>
    """

    links = extract_detail_links(
        "https://www.dulieuphapluat.vn/bieu-mau/giao-dich-voi-bat-dong-san/mua-ban-nha-dat.html",
        html,
    )

    assert links == [
        {
            "title": "Hợp đồng",
            "url": "https://www.dulieuphapluat.vn/bieu-mau/giao-dich-voi-bat-dong-san/mua-ban-nha-dat/hop-dong-1.html",
        }
    ]


def test_extract_download_links_accepts_download_file_and_word_links():
    html = """
    <a href="/download_file.html?p=form_file&f=form.docx">Tải xuống Word</a>
    <a href="/files/form.pdf">PDF</a>
    <a href="/chinh-sua-bieu-mau/foo.html">Chỉnh sửa</a>
    """

    links = extract_download_links("https://www.dulieuphapluat.vn/bieu-mau/a/b/c-1.html", html)

    assert links == [
        "https://www.dulieuphapluat.vn/download_file.html?p=form_file&f=form.docx",
        "https://www.dulieuphapluat.vn/files/form.pdf",
    ]


def test_detect_domain_marks_business_as_non_core():
    assert detect_domain("https://www.dulieuphapluat.vn/bieu-mau/doanh-nghiep/nhan-su.html") == (
        "unknown",
        False,
        "low_non_core",
    )
    assert detect_domain("https://www.dulieuphapluat.vn/bieu-mau/hon-nhan-gia-dinh/ly-hon.html")[0] == "ho_tich_chung_thuc"
