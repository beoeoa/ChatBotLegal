from api.legal_question_policy import classify_question, missing_answer_sections


def test_procedure_requires_procedure_sections():
    policy = classify_question("Đăng ký kết hôn ở phường cần hồ sơ, lệ phí và mẫu nào?")
    assert policy["question_type"] == "form_request"
    assert "documents" in policy["required_sections"]
    assert "official_forms" in policy["required_sections"]


def test_explanation_is_not_forced_into_procedure_template():
    policy = classify_question("Điều kiện để được cải chính hộ tịch là gì?")
    assert policy["question_type"] == "legal_explanation"
    assert "fee" not in policy["required_sections"]
    assert missing_answer_sections("Quy định áp dụng và điều kiện theo nguồn.", policy["question_type"]) == []


def test_domain_specific_categories_are_stable():
    assert classify_question("Tôi muốn đăng ký tạm trú") ["question_type"] == "residence_security"
    assert classify_question("Tôi muốn khiếu nại quyết định xử phạt") ["question_type"] == "complaint_sanction"
    assert classify_question("Xin trợ cấp cho người khuyết tật") ["question_type"] == "social_support"


def test_missing_procedure_sections_are_reported_without_fabrication():
    missing = missing_answer_sections("Kết luận: nộp tại UBND phường. Căn cứ: Luật hiện hành.", "procedure")
    assert "documents" in missing
    assert "processing_time" in missing
    assert "fee" in missing


def test_documents_only_question_does_not_require_unasked_fee_time_or_form():
    policy = classify_question(
        "Tôi cần chuẩn bị giấy tờ gì để đăng ký khai sinh cho con tại UBND phường?"
    )

    assert "documents" in policy["required_sections"]
    assert "processing_time" not in policy["required_sections"]
    assert "fee" not in policy["required_sections"]
    assert "official_forms" not in policy["required_sections"]
    assert "steps" not in policy["required_sections"]
    assert "legal_basis_links" not in policy["required_sections"]


def test_missing_sections_checks_only_the_intents_requested_by_user():
    policy = classify_question("Đăng ký khai sinh cần chuẩn bị giấy tờ gì?")
    missing = missing_answer_sections(
        "Kết luận: hồ sơ gồm tờ khai và giấy chứng sinh. Căn cứ: Luật Hộ tịch.",
        policy["question_type"],
        policy["is_procedural"],
        required_sections=policy["required_sections"],
    )

    assert missing == []
