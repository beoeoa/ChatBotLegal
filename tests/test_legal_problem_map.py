import asyncio

from api.legal_problem_map import (
    build_deterministic_problem_map,
    build_hybrid_problem_map,
    is_complex_legal_question,
)

LAND_QUESTION = (
    "Tôi mua đất bằng giấy viết tay năm 2009, chưa có sổ đỏ; người bán đã mất, "
    "các con của người bán không hợp tác và thửa đất có một phần nằm trong quy hoạch. "
    "Tôi có được cấp Giấy chứng nhận không, cần chứng minh những gì, nộp hồ sơ ở đâu, "
    "thời hạn và nghĩa vụ tài chính thế nào?"
)


def test_complex_land_question_produces_eight_bounded_issues_and_branches():
    problem_map = build_deterministic_problem_map(
        LAND_QUESTION,
        role="citizen",
        required_sections=[
            "conclusion",
            "conditions_or_rights",
            "documents",
            "submission_place",
            "processing_time",
            "fee",
        ],
    )

    assert is_complex_legal_question(LAND_QUESTION, required_sections=problem_map.requested_outputs)
    assert problem_map.role == "citizen"
    assert len(problem_map.legal_issues) == 8
    assert sum(len(issue.queries) for issue in problem_map.legal_issues) <= 16
    assert all(1 <= len(issue.queries) <= 4 for issue in problem_map.legal_issues)
    assert problem_map.legal_issues[0].required_fact_ids == ["fact-signatures"]
    assert problem_map.legal_issues[3].required_fact_ids == ["fact-dispute"]
    assert problem_map.legal_issues[4].required_fact_ids == ["fact-recovery"]
    assert any(
        fact.fact == "Chưa có Giấy chứng nhận" and fact.value is True
        for fact in problem_map.confirmed_facts
    )
    assert all(
        "Chưa có Giấy chứng nhận" in issue.facts
        for issue in problem_map.legal_issues
    )
    assert (
        "nhận chuyển quyền sử dụng đất trước ngày 01 tháng 7 năm 2014"
        in problem_map.legal_issues[0].queries[0].query
    )
    titles = " ".join(issue.title.casefold() for issue in problem_map.legal_issues)
    for phrase in (
        "giấy viết tay",
        "giấy chứng nhận",
        "người chuyển quyền đã chết",
        "tranh chấp",
        "quy hoạch",
        "hồ sơ",
        "thời hạn",
        "nghĩa vụ tài chính",
    ):
        assert phrase in titles
    assert any("không hợp tác" in item.condition.casefold() for item in problem_map.conditional_branches)
    assert any("thu hồi" in item.distinguish_from.casefold() for item in problem_map.conditional_branches)


def test_simple_question_stays_deterministic_and_single_issue():
    result = build_deterministic_problem_map(
        "Thời hạn đăng ký khai sinh là bao lâu?",
        role="citizen",
        required_sections=["processing_time"],
    )
    assert len(result.legal_issues) == 1
    assert result.planner_mode == "deterministic"


def test_hybrid_planner_timeout_falls_back_without_changing_role():
    async def timeout(_prompt: str) -> str:
        await asyncio.sleep(0.02)
        return "{}"

    result = asyncio.run(
        build_hybrid_problem_map(
            LAND_QUESTION,
            role="officer",
            required_sections=["documents", "processing_time", "fee"],
            invoke_model=timeout,
            timeout_seconds=0.001,
        )
    )

    assert result.role == "officer"
    assert result.planner_mode == "deterministic_fallback"
    assert result.fallback_reason == "planner_timeout"
    assert len(result.legal_issues) >= 3


def test_hybrid_planner_rejects_invented_article_number():
    async def invented(_prompt: str) -> str:
        return '{"legal_issues":[{"issue_id":"issue-1","title":"X","description":"X","priority":"critical","required_answer":true,"intent":"rule","domain":"land","queries":[{"query_id":"q-1","query_type":"article","query":"Điều 999 Luật đất đai"}]}]}'

    result = asyncio.run(
        build_hybrid_problem_map(
            LAND_QUESTION,
            role="citizen",
            required_sections=["conclusion"],
            invoke_model=invented,
        )
    )

    assert result.planner_mode == "deterministic_fallback"
    assert result.fallback_reason == "planner_untrusted_legal_identifier"
