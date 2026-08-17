from api.legal_section_grounding import (
    _scope_rank,
    classify_issue_domain,
    plan_legal_issues,
)


def test_foreign_birth_anchor_stays_in_civil_status_domain():
    assert (
        classify_issue_domain(
            "Tr\u1ebb sinh \u1edf n\u01b0\u1edbc ngo\u00e0i, "
            "cha c\u01b0 tr\u00fa t\u1ea1i H\u1ea3i Ph\u00f2ng"
        )
        == "civil_status"
    )


def test_planner_splits_bounded_distinct_issues_and_removes_duplicate_spans():
    issues = plan_legal_issues(
        "Đất nhà tôi đang tranh chấp; UBND phường có thẩm quyền hòa giải không; hồ sơ cần gì; hồ sơ cần gì; lệ phí bao nhiêu?"
    )

    assert 1 < len(issues) <= 4
    assert len({issue.query_text.casefold() for issue in issues}) == len(issues)
    assert {issue.intent for issue in issues}.intersection({"dispute", "authority", "documents", "fee"})


def test_low_confidence_question_falls_back_to_one_current_question_issue():
    question = "Tôi cần được hướng dẫn về việc này."

    issues = plan_legal_issues(question)

    assert len(issues) == 1
    assert issues[0].query_text == question
    assert issues[0].split_confidence == "low"


def test_domain_classification_is_deterministic_for_land_and_administrative_topics():
    assert classify_issue_domain("tranh chấp quyền sử dụng đất") == "land"
    assert classify_issue_domain("nộp hồ sơ tại UBND phường") == "administrative"


def test_hai_phong_city_scope_is_treated_as_reviewed_local_scope():
    assert _scope_rank("Thành phố Hải Phòng") == 1
