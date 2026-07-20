from pathlib import Path

from open_notebook.graphs.ask import (
    _repair_reason,
    _safe_draft_after_failed_repair,
)


EVIDENCE = [
    {
        "id": "legal:101",
        "chunk_id": 101,
        "law_number": "23/2015/NĐ-CP",
        "document_title": "Nghị định về chứng thực",
        "article_number": "Điều 24",
        "content": "Điều 24. Yêu cầu được giải quyết ngay trong ngày.",
    }
]


def test_repair_payload_has_verified_form_context_when_repair_is_needed():
    source = Path("open_notebook/graphs/ask.py").read_text(encoding="utf-8")
    assert '"form_context": form_context' in source


def test_repair_budget_derives_citizen_role_in_the_repair_branch():
    """A legal-risk repair must not fail with an undeclared role variable."""
    source = Path("open_notebook/graphs/ask.py").read_text(encoding="utf-8")
    assert 'is_citizen = str(state.get("role") or "citizen").casefold() == "citizen"' in source
    assert "max_tokens=4096 if is_citizen else 16000" in source


def test_repair_prompt_does_not_apply_marriage_rule_to_every_question():
    prompts = [
        Path("prompts/ask/query_process.jinja"),
        Path("prompts/ask/grounding_review.jinja"),
        Path("prompts/ask/answer_completion.jinja"),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in prompts)
    assert "126/2014/NĐ-CP" not in combined
    assert "BẮT BUỘC phải cùng có mặt" not in combined
    assert "Căn cước công dân" not in combined
    assert "Giấy xác nhận tình trạng hôn nhân" not in combined
    assert combined.count("{{ answer_contract }}") == 3


def test_heading_or_markdown_only_issue_never_triggers_a_model_repair():
    draft = "**Tiêu đề cũ**\n\nNội dung có căn cứ và kết thúc đầy đủ."
    assert _repair_reason(draft, EVIDENCE, completion_marked=False) is None


def test_repair_reasons_are_limited_to_grounding_or_truncation():
    assert _repair_reason(
        "Căn cứ [legal:999].", EVIDENCE, completion_marked=True
    ) == "invalid_citation"
    assert _repair_reason(
        "Theo Nghị định 99/2026/NĐ-CP, Điều 24.",
        EVIDENCE,
        completion_marked=True,
    ) == "unsupported_legal_reference"
    assert _repair_reason(
        "Theo Nghị định 23/2015/NĐ-CP, Điều 24, hồ sơ gồm",
        EVIDENCE,
        completion_marked=False,
    ) == "truncated_answer"


def test_failed_repair_keeps_grounded_fragments_and_drops_only_bad_reference():
    draft = (
        "## Hồ sơ\n"
        "Theo Nghị định 23/2015/NĐ-CP, Điều 24, hồ sơ được tiếp nhận "
        "theo nguồn đã tìm thấy [legal:101].\n\n"
        "## Lệ phí\n"
        "Theo Nghị định 99/2026/NĐ-CP, lệ phí là 100.000 đồng [legal:101]."
    )

    safe = _safe_draft_after_failed_repair(draft, EVIDENCE)

    assert safe is not None
    assert "23/2015/" in safe
    assert "99/2026/" not in safe
    assert "100.000 đồng" not in safe
    assert "đã được lược bỏ" in safe


def test_failed_repair_does_not_keep_a_draft_when_no_grounded_fragment_remains():
    draft = "Theo Nghị định 99/2026/NĐ-CP, lệ phí là 100.000 đồng [legal:999]."

    assert _safe_draft_after_failed_repair(draft, EVIDENCE) is None
