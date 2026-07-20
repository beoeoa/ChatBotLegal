from scripts.index_haiphong_official_forms import form_headings


def test_form_heading_requires_form_context():
    assert form_headings("Danh mục có Mẫu số 01 nhưng không chứa biểu mẫu") == []


def test_form_heading_combines_model_number_and_title():
    text = """
    Mẫu số 01
    TỜ KHAI ĐĂNG KÝ KHAI SINH
    CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
    Họ và tên người khai: ........................
    """
    assert form_headings(text) == [
        "Mẫu số 01 - TỜ KHAI ĐĂNG KÝ KHAI SINH"
    ]


def test_form_heading_accepts_standalone_application():
    text = """
    CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM
    ĐƠN ĐỀ NGHỊ CẤP GIẤY PHÉP XÂY DỰNG
    Kính gửi: Ủy ban nhân dân cấp xã
    """
    assert form_headings(text) == ["ĐƠN ĐỀ NGHỊ CẤP GIẤY PHÉP XÂY DỰNG"]
