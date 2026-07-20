from api.legal_question_policy import (
    answer_contract,
    missing_contract_headings,
    normalize_answer_markdown,
    render_answer_contract,
)


def test_citizen_procedure_contract_contains_only_requested_headings():
    contract = answer_contract(
        "citizen",
        "procedure",
        ["conclusion", "submission_place", "documents", "steps"],
    )

    assert contract["profile"] == "citizen_procedure"
    assert contract["headings"] == [
        "## Kết luận ngắn",
        "## Nơi nộp / cơ quan giải quyết",
        "## Hồ sơ bắt buộc",
        "## Bạn cần làm gì ngay",
        "## Các bước thực hiện",
    ]
    rendered = render_answer_contract(contract)
    assert all(heading in rendered for heading in contract["headings"])


def test_explanation_and_admin_are_not_forced_into_citizen_procedure_shape():
    explanation = answer_contract("citizen", "legal_explanation", [])
    admin = answer_contract("admin", "legal_explanation", [])

    assert explanation["profile"] == "citizen_explanation"
    assert explanation["headings"] == [
        "## Kết luận",
        "## Điều kiện áp dụng",
        "## Căn cứ pháp lý",
        "## Việc cần xác minh",
    ]
    assert admin["profile"] == "admin"
    assert admin["headings"] == []


def test_contract_checker_reports_structure_but_does_not_invent_content():
    contract = answer_contract("citizen", "procedure", ["documents", "steps"])
    answer = "## Kết luận ngắn\nBạn có thể nộp hồ sơ khi đủ căn cứ."

    missing = missing_contract_headings(answer, contract)

    assert "## Kết luận ngắn" not in missing
    assert "## Hồ sơ bắt buộc" in missing
    assert answer == "## Kết luận ngắn\nBạn có thể nộp hồ sơ khi đủ căn cứ."


def test_documents_only_contract_does_not_force_place_deadline_or_fee():
    contract = answer_contract(
        "citizen",
        "procedure",
        ["conclusion", "documents", "legal_basis_links"],
    )
    assert contract["headings"] == [
        "## Kết luận ngắn",
        "## Hồ sơ bắt buộc",
        "## Biểu mẫu và căn cứ pháp lý",
    ]
    assert "## Nơi nộp / cơ quan giải quyết" not in contract["headings"]
    assert "## Thời hạn, lệ phí hoặc mức phạt" not in contract["headings"]


def test_markdown_normalizer_repairs_presentation_without_adding_legal_facts():
    draft = "  ** Kết luận **  \n\n\n-   Nội dung từ nguồn.   \n"

    normalized = normalize_answer_markdown(draft)

    assert normalized == "## Kết luận\n\n- Nội dung từ nguồn."
    assert "Điều" not in normalized
    assert "Nghị định" not in normalized
