from api.legal_section_grounding import plan_legal_issues, retrieval_domain_slug
from api.routers.search import _detect_question_domain


def test_school_procedure_routes_to_education_domain():
    detected = _detect_question_domain(
        "Tôi cần làm thủ tục Giải thể trường tiểu học, hồ sơ và biểu mẫu gồm gì?"
    )

    assert detected is not None
    assert detected["slug"] == "an_sinh_y_te_giao_duc"


def test_apartment_management_procedure_routes_to_land_domain():
    detected = _detect_question_domain(
        "Công nhận Ban quản trị nhà chung cư, hồ sơ và biểu mẫu gồm những gì?"
    )

    assert detected is not None
    assert detected["slug"] == "dat_dai_xay_dung"


def test_exact_identity_keeps_the_reviewed_selected_domain():
    assert retrieval_domain_slug(
        "civil_status",
        "an_sinh_y_te_giao_duc",
        "Điều 13 188/2025/NĐ-CP có nhắc đăng ký khai tử",
    ) == "an_sinh_y_te_giao_duc"


def test_planner_splits_natural_vietnamese_facets_and_keeps_subject_anchor():
    question = (
        "Tôi đăng ký kết hôn tại Hải Phòng, hồ sơ cần gì, "
        "thời hạn bao lâu và lệ phí bao nhiêu?"
    )

    issues = plan_legal_issues(question, max_issues=6)

    assert [issue.intent for issue in issues] == ["documents", "deadline", "fee"]
    assert all("đăng ký kết hôn" in issue.query_text.casefold() for issue in issues)
    assert all("hải phòng" in issue.query_text.casefold() for issue in issues)


def test_planner_never_creates_more_than_six_issues():
    issues = plan_legal_issues(
        "Tôi đăng ký khai sinh, điều kiện gì, nộp ở đâu, hồ sơ gì, "
        "các bước ra sao, thời hạn bao lâu, lệ phí bao nhiêu và dùng biểu mẫu nào?",
        max_issues=6,
    )

    assert 1 < len(issues) <= 6


def test_planner_treats_submit_where_as_authority_issue():
    question = (
        "T\u00f4i \u0111\u0103ng k\u00fd khai sinh cho con t\u1ea1i H\u1ea3i Ph\u00f2ng, "
        "n\u1ed9p \u1edf \u0111\u00e2u, c\u1ea7n h\u1ed3 s\u01a1 g\u00ec, th\u1eddi h\u1ea1n v\u00e0 l\u1ec7 ph\u00ed?"
    )

    issues = plan_legal_issues(question, max_issues=6)

    assert "authority" in [issue.intent for issue in issues]


def test_explicit_alphanumeric_article_remains_one_structural_issue():
    question = (
        "Theo Điều 18a của Luật 88/2025/QH15, việc xử lý vi phạm hành chính "
        "trên môi trường điện tử được thực hiện khi nào và phải bảo đảm những "
        "yêu cầu gì?"
    )

    issues = plan_legal_issues(question, max_issues=6)

    assert len(issues) == 1
    assert "Điều 18a" in issues[0].query_text
    assert "88/2025/QH15" in issues[0].query_text


def test_numbered_question_is_split_into_exactly_two_top_level_issues():
    question = (
        "Tôi cần tách riêng hai vấn đề theo đúng thứ tự: "
        "(1) Điều 6 Luật 31/2024/QH15 quy định trách nhiệm đối với đất đã giao "
        "cho Ủy ban nhân dân cấp xã; "
        "(2) Luật sửa đổi, bổ sung một số điều của Luật Xây dựng quy định về "
        "hạ tầng và bàn giao công trình đối với dự án khu đô thị?"
    )

    issues = plan_legal_issues(question, max_issues=6)

    assert len(issues) == 2
    assert "31/2024/QH15" in issues[0].query_text
    assert "Luật Xây dựng" in issues[1].query_text
    assert "31/2024/QH15" not in issues[1].query_text


def test_two_quoted_golden_issues_are_not_split_inside_legal_titles():
    question = (
        "Tôi đồng thời cần xử lý hai vấn đề: "
        "“Điều 48. Thẩm quyền ghi vào Sổ hộ tịch” "
        "(phần 3: việc hộ tịch đã được giải quyết ở nước ngoài.) và "
        "“Điều 49. Thủ tục ghi vào Sổ hộ tịch việc khai sinh; giám hộ; "
        "nhận cha, mẹ, con” (phần 1: giấy tờ phải nộp.). "
        "Xin tách riêng từng vấn đề và trả lời theo đúng thứ tự."
    )

    issues = plan_legal_issues(question, max_issues=6)

    assert len(issues) == 2
    assert "Điều 48" in issues[0].query_text
    assert "Điều 49" in issues[1].query_text
    assert "giám hộ; nhận cha, mẹ, con" in issues[1].query_text
