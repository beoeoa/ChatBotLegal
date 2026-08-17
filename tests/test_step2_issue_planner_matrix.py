from scripts.evaluate_step2_issue_planner import evaluate_all


def test_all_167_golden_and_nine_role_cases_have_complete_issue_plans():
    report = evaluate_all()
    assert report["golden_count"] == 167
    assert report["role_count"] == 9
    assert report["case_count"] == 176
    assert report["failed_count"] == 0
    assert report["max_issue_count"] <= 6
    assert report["pass"] is True
