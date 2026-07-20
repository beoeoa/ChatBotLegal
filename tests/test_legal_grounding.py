from datetime import date

from open_notebook.graphs.ask import (
    _format_evidence_for_prompt,
    _has_invalid_citation_ids,
    _has_unsupported_references,
    _needs_quality_repair,
    _normalize_answer_format,
    _remove_invalid_citations,
)
from scripts.legal_search_server import _has_expired_legal_basis


EVIDENCE = [
    {
        "id": "legal:101",
        "law_number": "23/2015/NĐ-CP",
        "document_title": "Nghị định về chứng thực",
        "article_number": "24",
        "content": (
            "Điều 24. Yêu cầu chứng thực chữ ký được giải quyết "
            "ngay trong ngày."
        ),
        "relationships": [],
    }
]


def test_rejects_expired_legal_basis():
    assert _has_expired_legal_basis(
        [
            {
                "relationship_type": "Văn bản căn cứ",
                "related_status": "expired",
                "related_expired_date": date(2020, 1, 1),
            }
        ],
        date(2026, 6, 12),
    )


def test_accepts_grounded_answer_without_changing_style():
    answer = (
        "Yêu cầu được giải quyết ngay trong ngày theo Điều 24 "
        "Nghị định 23/2015/NĐ-CP [legal:101]."
    )
    assert not _has_unsupported_references(answer, EVIDENCE)


def test_detects_invented_law_name_and_deadline():
    answer = (
        "Giải quyết trong 15 phút theo Luật Chữ ký số 2023 "
        "[legal:101]."
    )
    assert _has_unsupported_references(answer, EVIDENCE)


def test_requires_at_least_one_valid_citation():
    assert _has_unsupported_references(
        "UBND phường có thẩm quyền chứng thực.", EVIDENCE
    )


def test_accepts_only_citations_from_retrieved_evidence():
    assert not _has_invalid_citation_ids(
        "Nội dung có căn cứ [legal:101].", EVIDENCE
    )
    assert _has_invalid_citation_ids(
        "Nội dung dùng nhầm nguồn [legal:999].", EVIDENCE
    )
    assert not _has_invalid_citation_ids(
        "Hướng dẫn chung: bạn nên chuẩn bị thông tin hồ sơ.", []
    )


def test_removes_only_unknown_citations():
    answer = "Nguồn đúng [legal:101], nguồn sai [legal:999]."
    assert _remove_invalid_citations(answer, EVIDENCE) == (
        "Nguồn đúng [legal:101], nguồn sai ."
    )


def test_formats_evidence_as_readable_legal_source():
    prompt_text = _format_evidence_for_prompt(EVIDENCE)
    assert "## Nguồn 1" in prompt_text
    assert "[legal:101]" not in prompt_text
    assert "- Văn bản: 23/2015/NĐ-CP" in prompt_text
    assert "- Nguyên văn đoạn nguồn:" in prompt_text
    assert "'law_number':" not in prompt_text


def test_normalizes_broken_citation_markup():
    text = "Căn cứ perlegal:101` (Điều 24)."
    assert _normalize_answer_format(text) == (
        "Căn cứ theo [legal:101] (Điều 24)."
    )


def test_detects_english_leak_and_cutoff():
    assert _needs_quality_repair(
        "**Thời hạn (Processing time):** According to legal:101: Within **03"
    )
    assert not _needs_quality_repair(
        "Yêu cầu được giải quyết ngay trong ngày [legal:101]."
    )
    assert _needs_quality_repair(
        "1. Về giao dịch mua bán đất bằng giấy viết tay năm 2 ,"
    )

def test_role_prompt_distinguishes_admin_from_officer():
    from api.routers.search import _build_local_prompt

    retrieval = {"results": []}
    officer_prompt = _build_local_prompt("Can bo hoi ve thm quyen", "officer", retrieval)
    admin_prompt = _build_local_prompt("Can bo hoi ve thm quyen", "admin", retrieval)

    assert "Người đọc là cán bộ" in officer_prompt
    assert "Người đọc là quản trị hệ thống" in admin_prompt
    assert "Người đọc là cán bộ" not in admin_prompt
    assert "Người đọc là quản trị hệ thống" not in officer_prompt
