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


def test_direct_article_explanation_does_not_invent_condition_or_exception():
    policy = classify_question(
        "Điều 18 của văn bản 60/2014/QH13 quy định nội dung gì? "
        "Hãy giải thích bằng từ dễ hiểu."
    )

    assert policy["question_type"] == "legal_explanation"
    assert policy["required_sections"] == [
        "applicable_rule",
        "legal_basis_links",
    ]


def test_domain_specific_categories_are_stable():
    assert classify_question("Tôi muốn đăng ký tạm trú") ["question_type"] == "residence_security"
    assert classify_question("Tôi muốn khiếu nại quyết định xử phạt") ["question_type"] == "complaint_sanction"
    assert classify_question("Xin trợ cấp cho người khuyết tật") ["question_type"] == "social_support"


def test_parenthetical_legal_excerpt_does_not_change_residence_route_to_complaint():
    policy = classify_question(
        "Tôi cần biết quyền về cư trú (phần 8: Khiếu nại, tố cáo, khởi kiện) "
        "và các hành vi bị nghiêm cấm (phần 7: Làm giả giấy tờ căn cước)."
    )
    assert policy["question_type"] == "residence_security"
    assert "authority" not in policy["required_sections"]
    assert "deadline" not in policy["required_sections"]


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
    # "tại UBND phường" is a confirmed place in the user's facts, not an
    # authority question.  Creating a second authority facet here makes the
    # timeout fallback reuse the same birth-registration provision twice.
    assert "submission_place" not in policy["required_sections"]


def test_explicit_choice_between_administrative_levels_requests_authority():
    policy = classify_question(
        "Tôi đăng ký khai sinh ở UBND cấp xã hay UBND cấp huyện?"
    )

    assert "submission_place" in policy["required_sections"]


def test_nop_cho_ai_requests_submission_place():
    policy = classify_question(
        "Tôi muốn khiếu nại lần đầu, cần chuẩn bị gì và nộp cho ai?"
    )

    assert "submission_place" in policy["required_sections"]


def test_missing_sections_checks_only_the_intents_requested_by_user():
    policy = classify_question("Đăng ký khai sinh cần chuẩn bị giấy tờ gì?")
    missing = missing_answer_sections(
        "Kết luận: hồ sơ gồm tờ khai và giấy chứng sinh. Căn cứ: Luật Hộ tịch.",
        policy["question_type"],
        policy["is_procedural"],
        required_sections=policy["required_sections"],
    )

    assert missing == []


def test_mixed_procedure_detects_authority_tax_fee_steps_condition_and_form():
    policy = classify_question(
        "Trường hợp nào hợp pháp, ai giải quyết, các bước ra sao, "
        "thuế phí thế nào và cho tôi Mẫu 09/ĐK?"
    )

    assert "conditions_or_rights" in policy["required_sections"]
    assert "submission_place" in policy["required_sections"]
    assert "steps" in policy["required_sections"]
    assert "fee" in policy["required_sections"]
    assert "official_forms" in policy["required_sections"]


def test_verification_and_enforcement_action_phrases_require_procedure_steps():
    verification = classify_question(
        "Cơ quan xác minh thế nào và thời hạn bao lâu?"
    )
    enforcement = classify_question(
        "Cấp phường có thẩm quyền gì; lập biên bản, ngăn chặn, "
        "xử phạt và buộc khắc phục thực hiện theo căn cứ nào?"
    )

    assert "steps" in verification["required_sections"]
    assert "steps" in enforcement["required_sections"]
    assert "submission_place" in enforcement["required_sections"]
    assert "legal_basis_links" in enforcement["required_sections"]


def test_authority_only_land_question_does_not_invent_condition_or_exception():
    policy = classify_question(
        "Tranh chấp ranh giới thửa đất thì nên liên hệ cơ quan nào?"
    )

    assert policy["question_type"] == "land_construction"
    assert policy["required_sections"] == ["conclusion", "submission_place"]
    assert policy["is_procedural"] is True


def test_land_threshold_question_keeps_condition_and_authority_facets():
    policy = classify_question(
        "Theo Nghị quyết 22/2025/NQ-HĐND, khu đất xen kẹt tại phường và xã "
        "phải đạt diện tích tối thiểu bao nhiêu; tỷ lệ đất ở tối đa là bao nhiêu, "
        "và trường hợp nhỏ hơn ngưỡng thì cơ quan nào xem xét?"
    )

    assert policy["question_type"] == "land_construction"
    assert "conditions_or_rights" in policy["required_sections"]
    assert "submission_place" in policy["required_sections"]
