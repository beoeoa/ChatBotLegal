from __future__ import annotations

from api.legal_problem_map import build_deterministic_problem_map
from api.legal_section_grounding import plan_legal_issues
from api.legal_structured_answer import (
    build_issue_coverage_matrix,
    derive_required_facets_by_issue,
    ensure_required_facet_issues,
)


def _context() -> dict[str, object]:
    return {
        "subject": "đăng ký biến động đất đai",
        "location": "Hải Phòng",
        "facts": ("đã nhận chuyển nhượng", "có Giấy chứng nhận"),
        "applied_date": "2024-01-15",
        "domain": "dat_dai_xay_dung",
        "expected_sources": ("Luật Đất đai 2024", "Nghị định 101/2024/NĐ-CP"),
        "expected_form": {
            "name": "Mẫu 09/ĐK",
            "procedure_id": "sang_ten_so_do",
        },
    }


def _assert_safe_issues(issues, *, minimum=2, expected_domain=None):
    assert 1 <= len(issues) <= 6
    assert len(issues) >= minimum
    assert all(len(issue.query_text.split()) >= 2 for issue in issues)
    assert len({issue.query_text.casefold() for issue in issues}) == len(issues)
    assert all(issue.subject == _context()["subject"] for issue in issues)
    assert all(issue.location == _context()["location"] for issue in issues)
    assert all(issue.facts == _context()["facts"] for issue in issues)
    assert all(issue.applied_date == _context()["applied_date"] for issue in issues)
    if expected_domain is not None:
        assert all(issue.domain == expected_domain for issue in issues)
    assert all(issue.expected_sources == _context()["expected_sources"] for issue in issues)
    assert all(issue.expected_form == _context()["expected_form"] for issue in issues)


def test_comma_and_conjunction_preserve_context_and_split_facets():
    issues = plan_legal_issues(
        "Tôi đăng ký biến động đất đai tại Hải Phòng, cần hồ sơ gì và lệ phí bao nhiêu?",
        context=_context(),
    )
    _assert_safe_issues(issues, expected_domain="dat_dai_xay_dung")
    assert [issue.intent for issue in issues] == ["documents", "fee"]


def test_but_clause_is_not_left_as_a_keyword_only_issue():
    issues = plan_legal_issues(
        "Tôi đã nhận chuyển nhượng nhưng chưa sang tên, phải làm thủ tục gì và nộp ở đâu?",
        context=_context(),
    )
    _assert_safe_issues(issues, expected_domain="dat_dai_xay_dung")
    assert {issue.intent for issue in issues} >= {"procedure", "authority"}


def test_conditional_clause_gets_a_condition_issue_and_retains_date():
    issues = plan_legal_issues(
        "Nếu giao dịch ngày 15/01/2024 thì điều kiện nào áp dụng, thủ tục ra sao và thời hạn bao lâu?",
        context=_context(),
    )
    _assert_safe_issues(issues, expected_domain="dat_dai_xay_dung")
    assert "condition" in {issue.intent for issue in issues}
    assert "deadline" in {issue.intent for issue in issues}
    assert all("15/01/2024" in issue.query_text for issue in issues)


def test_multi_domain_question_is_bounded_and_keeps_each_topic():
    issues = plan_legal_issues(
        "Tôi muốn đăng ký khai sinh cho con và sang tên sổ đỏ tại Hải Phòng, cần hồ sơ gì?",
        context=_context(),
    )
    assert 1 <= len(issues) <= 6
    assert all(len(issue.query_text.split()) >= 2 for issue in issues)
    assert len(issues) <= 6
    assert any("khai sinh" in issue.query_text.casefold() for issue in issues)
    assert any("sang tên" in issue.query_text.casefold() for issue in issues)


def test_specific_form_is_planned_as_form_issue():
    issues = plan_legal_issues(
        "Tôi cần Mẫu 09/ĐK chính thức để đăng ký biến động đất đai tại Hải Phòng.",
        context=_context(),
    )
    _assert_safe_issues(issues, minimum=1, expected_domain="dat_dai_xay_dung")
    assert any(issue.intent == "form" for issue in issues)
    assert all("09/đk" in issue.query_text.casefold() for issue in issues if issue.intent == "form")


def test_deadline_and_fee_are_distinct_facets():
    issues = plan_legal_issues(
        "Đăng ký biến động đất đai tại Hải Phòng mất bao lâu và lệ phí bao nhiêu?",
        context=_context(),
    )
    _assert_safe_issues(issues, expected_domain="dat_dai_xay_dung")
    assert {issue.intent for issue in issues} >= {"deadline", "fee"}


def test_foreign_factor_is_retained_on_every_issue():
    issues = plan_legal_issues(
        "Đăng ký kết hôn có yếu tố nước ngoài tại Hải Phòng cần thẩm quyền nào, hồ sơ gì và mẫu nào?",
        context={**_context(), "subject": "đăng ký kết hôn có yếu tố nước ngoài"},
    )
    assert 1 <= len(issues) <= 6
    assert all("nước ngoài" in issue.query_text.casefold() for issue in issues)
    assert all(issue.subject == "đăng ký kết hôn có yếu tố nước ngoài" for issue in issues)


def test_different_transaction_dates_are_not_collapsed():
    first = plan_legal_issues(
        "Giao dịch ngày 15/01/2024 cần thủ tục gì?",
        context={**_context(), "applied_date": "2024-01-15"},
    )
    second = plan_legal_issues(
        "Giao dịch ngày 20/02/2025 cần thủ tục gì?",
        context={**_context(), "applied_date": "2025-02-20"},
    )
    assert first[0].applied_date != second[0].applied_date
    assert "15/01/2024" in first[0].query_text
    assert "20/02/2025" in second[0].query_text


def test_coverage_matrix_plans_every_requested_facet_without_losing_context():
    question = (
        "Nếu đã nhận chuyển nhượng thì điều kiện gì, nộp ở đâu, hồ sơ nào, "
        "thủ tục ra sao, thời hạn bao lâu, lệ phí và Mẫu 09/ĐK nào?"
    )
    issues = ensure_required_facet_issues(
        question=question,
        issues=plan_legal_issues(question, context=_context()),
        required_sections=[
            "condition",
            "authority",
            "documents",
            "procedure",
            "deadline",
            "fee",
            "form",
        ],
        max_issues=6,
    )
    assert 1 <= len(issues) <= 6
    assert all(issue.subject == _context()["subject"] for issue in issues)
    assert all(issue.applied_date == _context()["applied_date"] for issue in issues)
    required = derive_required_facets_by_issue(
        issues=issues,
        required_sections=[
            "condition",
            "authority",
            "documents",
            "procedure",
            "deadline",
            "fee",
            "form",
        ],
    )
    assert set().union(*map(set, required.values())) >= {
        "condition",
        "authority",
        "documents",
        "procedure",
        "deadline",
        "fee",
        "form",
    }
    matrix = build_issue_coverage_matrix(
        issues=issues,
        evidence_by_id={},
        required_facets_by_issue=required,
    )
    assert set(matrix) == {issue.issue_id for issue in issues}
    assert all(matrix[issue.issue_id] for issue in issues)


def test_planner_uses_specific_procedure_subject_and_clean_location_boundary():
    question = (
        "Tôi xin Giấy xác nhận tình trạng hôn nhân tại Hải Phòng. "
        "Cần hồ sơ và thời hạn bao lâu?"
    )

    issues = plan_legal_issues(question)

    assert issues
    assert all(
        issue.subject == "Giấy xác nhận tình trạng hôn nhân"
        for issue in issues
    )
    assert all(issue.location == "Hải Phòng" for issue in issues)


def test_procedure_facets_use_facet_first_legal_retrieval_phrases():
    question = (
        "Tôi xin giấy phép xây dựng nhà ở riêng lẻ tại Hải Phòng. "
        "Cần điều kiện, hồ sơ, nộp ở đâu và thời hạn bao lâu?"
    )
    issues = ensure_required_facet_issues(
        question=question,
        issues=plan_legal_issues(question),
        required_sections=["condition", "documents", "submission_place", "processing_time"],
        max_issues=8,
    )
    queries = {issue.intent: issue.query_text for issue in issues}

    assert queries["condition"].startswith(
        "điều kiện cấp giấy phép xây dựng nhà ở riêng lẻ"
    )
    assert queries["documents"].startswith(
        "hồ sơ cấp giấy phép xây dựng nhà ở riêng lẻ gồm"
    )
    assert queries["authority"].startswith(
        "thẩm quyền cấp giấy phép xây dựng nhà ở riêng lẻ"
    )
    assert "kể từ ngày nhận đủ hồ sơ hợp lệ" in queries["deadline"]


def test_building_permit_problem_map_uses_reviewed_source_routes_without_rule_issue():
    question = (
        "Tôi xin giấy phép xây dựng nhà ở riêng lẻ tại Hải Phòng. "
        "Cần điều kiện, hồ sơ, nộp ở đâu, thời hạn và biểu mẫu nào?"
    )
    seed = ensure_required_facet_issues(
        question=question,
        issues=plan_legal_issues(question),
        required_sections=[
            "conclusion", "condition", "documents", "submission_place",
            "processing_time", "official_forms",
        ],
        max_issues=8,
    )
    problem_map = build_deterministic_problem_map(
        question,
        role="citizen",
        required_sections=[
            "conclusion", "condition", "documents", "submission_place",
            "processing_time", "official_forms",
        ],
        seed_issues=seed,
    )

    assert "rule" not in {issue.intent for issue in problem_map.legal_issues}
    routes = {
        issue.intent: [query.query for query in issue.queries]
        for issue in problem_map.legal_issues
    }
    assert any("217/2026/NĐ-CP" in query for query in routes["authority"])
    assert any("217/2026/NĐ-CP" in query for query in routes["documents"])
    assert any("217/2026/NĐ-CP" in query for query in routes["deadline"])
    assert sum(len(issue.queries) for issue in problem_map.legal_issues) <= 16


def test_documents_only_question_does_not_create_duplicate_rule_issue():
    question = "Tôi cần biết hồ sơ đăng ký khai sinh gồm những gì."
    issues = ensure_required_facet_issues(
        question=question,
        issues=plan_legal_issues(question),
        required_sections=["conclusion", "documents"],
        max_issues=6,
    )
    required = derive_required_facets_by_issue(
        issues=issues,
        required_sections=["conclusion", "documents"],
    )

    assert len(issues) == 1
    assert issues[0].intent == "documents"
    assert required == {issues[0].issue_id: ["documents", "rule"]}
