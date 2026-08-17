from __future__ import annotations

from api.legal_problem_map import build_deterministic_problem_map
from api.legal_question_policy import classify_question
from api.legal_section_grounding import plan_legal_issues
from api.legal_structured_answer import ensure_required_facet_issues


def test_direct_content_question_wins_over_generic_registration_verb():
    policy = classify_question(
        "Nội dung đăng ký khai sinh gồm những thông tin gì về trẻ và cha mẹ?"
    )

    assert policy["question_type"] == "legal_explanation"
    assert policy["is_procedural"] is False
    assert policy["required_sections"] == ["recorded_content"]


def test_natural_birth_record_wording_is_content_not_procedure():
    question = (
        "Khi đăng ký khai sinh cho con, giấy khai sinh sẽ ghi quê quán của con "
        "theo quê quán cha hay mẹ?"
    )
    policy = classify_question(question)

    assert policy["question_type"] == "legal_explanation"
    assert policy["is_procedural"] is False
    assert policy["required_sections"] == ["recorded_content"]

    focused = ensure_required_facet_issues(
        question=question,
        issues=plan_legal_issues(question),
        required_sections=policy["required_sections"],
    )
    assert len(focused) == 1
    assert focused[0].intent == "recording"
    assert "quê quán" in focused[0].query_text.casefold()


def test_direct_content_intent_generalizes_beyond_civil_status():
    questions = (
        "Hợp đồng lao động phải có những nội dung chính nào?",
        "Biên bản vi phạm hành chính cần ghi những nội dung gì?",
        "Giấy xác nhận thông tin cư trú thể hiện các thông tin nào?",
    )

    for question in questions:
        policy = classify_question(question)
        assert "recorded_content" in policy["required_sections"]
        assert "steps" not in policy["required_sections"]


def test_complex_reregistration_question_requests_every_explicit_output():
    policy = classify_question(
        "Khi tiếp nhận hồ sơ đăng ký lại khai sinh, người yêu cầu không còn "
        "bản sao giấy khai sinh và sổ hộ tịch cũ cũng không còn lưu giữ, cán bộ "
        "cần xử lý theo trình tự nào; phải kiểm tra điều kiện, giấy tờ, trách "
        "nhiệm xác minh và nội dung cần ghi nhận ra sao?"
    )

    assert policy["question_type"] == "procedure"
    assert {
        "conditions_or_rights",
        "documents",
        "steps",
        "verification_duties",
        "recorded_content",
    }.issubset(policy["required_sections"])


def test_planner_preserves_longest_procedure_identity_and_negative_facts():
    question = (
        "Đăng ký lại khai sinh khi người yêu cầu không còn bản sao giấy khai "
        "sinh và sổ hộ tịch cũ không còn lưu giữ thì xử lý thế nào?"
    )

    issues = plan_legal_issues(question, max_issues=8)

    assert issues
    assert all(issue.subject == "đăng ký lại khai sinh" for issue in issues)
    facts = " | ".join(issues[0].facts).casefold()
    assert "không còn bản sao giấy khai sinh" in facts
    assert "sổ hộ tịch cũ không còn lưu giữ" in facts


def test_problem_map_keeps_reregistration_facts_in_all_focused_queries():
    question = (
        "Đăng ký lại khai sinh khi không còn bản sao giấy khai sinh, sổ hộ "
        "tịch cũ không còn lưu giữ: điều kiện, giấy tờ, trình tự, trách nhiệm "
        "xác minh và nội dung ghi nhận thế nào?"
    )
    policy = classify_question(question)
    seed = ensure_required_facet_issues(
        question=question,
        issues=plan_legal_issues(question, max_issues=8),
        required_sections=policy["required_sections"],
        max_issues=8,
    )
    problem_map = build_deterministic_problem_map(
        question,
        role="officer",
        required_sections=policy["required_sections"],
        seed_issues=seed,
    )

    intents = {issue.intent for issue in problem_map.legal_issues}
    assert {"condition", "documents", "procedure", "verification", "recording"}.issubset(intents)
    assert problem_map.procedure_type == "đăng ký lại khai sinh"
    assert len(problem_map.confirmed_facts) >= 2
    for issue in problem_map.legal_issues:
        combined = " ".join(query.query for query in issue.queries).casefold()
        assert "đăng ký lại khai sinh" in combined
        assert "không còn" in combined
