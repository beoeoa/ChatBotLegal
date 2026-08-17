from api.legal_text_cleaning import clean_gazette_boilerplate, project_legal_evidence


def test_cleaner_removes_known_gazette_lines_but_preserves_legal_dates():
    raw = (
        "70 CÔNG BÁO/Số 365 + 366/Ngày 01-3-2024\n"
        "Điều 42. Trình tự thực hiện\n"
        "1. Văn bản có hiệu lực từ ngày 01 tháng 7 năm 2024.\n"
        "https://congbao.chinhphu.vn/noise/page/70\n"
        "70\n"
    )

    result = clean_gazette_boilerplate(raw)

    assert "CÔNG BÁO/Số" not in result["clean_text"]
    assert "congbao.chinhphu.vn/noise" not in result["clean_text"]
    assert result["clean_text"].endswith("ngày 01 tháng 7 năm 2024.")
    assert {item["reason"] for item in result["removed_noise"]} == {
        "gazette_header",
        "repeated_footer_url",
        "isolated_page_number",
    }


def test_cleaner_does_not_remove_source_url_or_inline_normative_numbers():
    raw = (
        "Xem hồ sơ tại https://dichvucong.gov.vn để thực hiện thủ tục.\n"
        "Trong thời hạn 70 ngày theo trường hợp được quy định."
    )

    result = clean_gazette_boilerplate(raw)

    assert result["clean_text"] == raw
    assert result["removed_noise"] == []


def test_evidence_projection_preserves_raw_fields_and_adds_clean_capsule():
    row = {
        "content": "70 CÔNG BÁO/Số 1/Ngày 01-01-2024\nKhoản 1. Nội dung.",
        "parent_context": "Điều 1. Tiêu đề\n70 CÔNG BÁO/Số 1/Ngày 01-01-2024\nKhoản 1. Nội dung.",
        "chunk_heading": "Điều 1 > Khoản 1",
    }

    projected = project_legal_evidence(row)

    assert projected["content"] == row["content"]
    assert projected["parent_context"] == row["parent_context"]
    assert projected["clean_content"] == "Khoản 1. Nội dung."
    assert "CÔNG BÁO" not in projected["evidence_capsule"]
    assert projected["sanitization_summary"]["removed_count"] == 2


def test_cleaner_removes_flattened_trailing_gazette_footer():
    raw = (
        "6. Người có chung quyền sử dụng đất chịu trách nhiệm đối với việc sử "
        "dụng đất đó. CÔNG BÁO/Số 363 + 364/Ngày 01-3-2024 11"
    )

    result = clean_gazette_boilerplate(raw)

    assert result["clean_text"].endswith("việc sử dụng đất đó.")
    assert "CÔNG BÁO" not in result["clean_text"]
    assert result["removed_noise"][0]["reason"] == "gazette_footer_inline"
