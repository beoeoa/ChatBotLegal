from scripts.build_retrieval_eval_suite_from_answer_workbook import (
    _question_issue_hints,
    _required_issue_groups,
)


def test_multi_issue_hints_are_extracted_before_quote_normalization() -> None:
    question = (
        'Tôi cần xử lý hai vấn đề: '
        '“CHƯƠNG III > Mục 5 > Điều 47. Thủ tục thay đổi hộ tịch” '
        'và “CHƯƠNG III > Mục 6 > Điều 48. Thẩm quyền ghi vào Sổ hộ tịch”.'
    )

    assert _question_issue_hints(question) == [
        ("47", "Thủ tục thay đổi hộ tịch"),
        ("48", "Thẩm quyền ghi vào Sổ hộ tịch"),
    ]


def test_multi_issue_hints_are_deduplicated_in_question_order() -> None:
    question = '“Điều 7. Thẩm quyền” và "Dieu 7 - Tham quyen"'

    assert [number for number, _ in _question_issue_hints(question)] == ["7"]


def test_two_issues_may_share_one_reviewed_source_group() -> None:
    groups = [{"group_id": "case-g1", "sources": [{"law_number": "1/2026/QH15"}]}]

    assert _required_issue_groups("case", ["multi_issue"], groups) == [
        {"issue_id": "case-i1", "required_source_group_ids": ["case-g1"]},
        {"issue_id": "case-i2", "required_source_group_ids": ["case-g1"]},
    ]
