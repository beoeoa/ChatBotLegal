from copy import deepcopy

from scripts.remap_golden294_residence_cases import (
    CASE_RULES,
    CURRENT_LAW,
    OLD_LAWS,
    _clean_article,
    _question_for_case,
    _selected_text,
    target_case_ids,
)


def test_case_map_is_exactly_the_approved_90_case_slice():
    assert len(CASE_RULES) == 90
    assert len(set(CASE_RULES)) == 90


def test_all_case_rules_point_to_non_empty_contiguous_selectors():
    for article, selectors in CASE_RULES.values():
        assert article.isdigit()
        if selectors is not None:
            assert selectors
            assert tuple(range(min(selectors), max(selectors) + 1)) == selectors


def test_selected_text_preserves_exact_offsets():
    content = "1. Một\n\n2. Hai\n\n3. Ba"
    selected = _selected_text(content, (1, 2))
    start = content.index(selected)
    assert selected == "2. Hai\n\n3. Ba"
    assert content[start : start + len(selected)] == selected


def test_clean_article_removes_next_chapter_and_annex_only():
    assert _clean_article("6", "Nội dung\n\nChương II\n\nSai") == "Nội dung"
    assert _clean_article("28", "Nội dung\n\n| Nơi nhận: x\n\nMẫu CT01") == "Nội dung"
    assert _clean_article("3", "Nội dung\n\nMẫu CT01") == "Nội dung\n\nMẫu CT01"


def test_target_case_ids_uses_only_expired_residence_sources():
    dataset = {
        "cases": [
            {
                "case_id": "a",
                "expected_sources": [{"law_number": next(iter(OLD_LAWS))}],
            },
            {
                "case_id": "b",
                "expected_sources": [{"law_number": CURRENT_LAW}],
            },
        ]
    }
    assert target_case_ids(deepcopy(dataset)) == {"a"}


def test_single_question_preserves_scenario_but_has_one_current_exact_pair():
    case = {
        "questions": {"citizen": ""},
        "expected_sources": [{"law_number": CURRENT_LAW, "article": "16"}],
        "required_claims": [{"text": "Nội dung hiện hành"}],
    }
    question = _question_for_case(
        case,
        False,
        original_question=(
            "Tôi hỏi tình huống A. Theo Điều 17 55/2021/TT-BCA thì xử lý thế nào?"
        ),
        old_source={"law_number": "55/2021/TT-BCA", "article": "17"},
        article_number="16",
        evidence="1. Nội dung hiện hành.",
    )
    assert "tình huống A" in question
    assert "55/2021/TT-BCA" not in question
    assert "Điều 17" not in question
    assert "Điều 16 116/2026/TT-BCA" in question
