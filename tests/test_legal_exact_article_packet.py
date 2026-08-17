from __future__ import annotations

from api.legal_exact_article import (
    attach_exact_article_packet,
    build_exact_article_packet,
    build_exact_article_serving_packet,
    public_exact_article_packet,
)
from api.legal_exact_article import _exact_article_focus_query
from scripts.legal_search_server import _exact_packet_serving_chunk_ids

ARTICLE_CONTENT = """1. Quy định chung.
2. Người thực hiện phải:
a) Chuẩn bị hồ sơ;
b) Nộp hồ sơ đúng nơi.
3. Cơ quan tiếp nhận kiểm tra."""


def _row(
    chunk_id: int,
    chunk_index: int,
    heading: str,
    content: str,
    *,
    expected_count: int = 4,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "chunk_heading": heading,
        "content": content,
        "article_id": 73,
        "article_number": "73",
        "article_title": "Nội dung Điều 73",
        "article_content": ARTICLE_CONTENT,
        "document_id": 60,
        "document_title": "Luật mẫu",
        "law_number": "60/2014/QH13",
        "exact_article_chunk_count": expected_count,
    }


def _complete_rows() -> list[dict]:
    return [
        _row(104, 3, "Điều 73 > Khoản 3", "3. Cơ quan tiếp nhận kiểm tra."),
        _row(102, 1, "Điều 73 > Khoản 2 > Điểm a", "2. Người thực hiện phải:\na) Chuẩn bị hồ sơ;"),
        _row(101, 0, "Điều 73 > Khoản 1", "1. Quy định chung."),
        _row(103, 2, "Điều 73 > Khoản 2 > Điểm b", "2. Người thực hiện phải:\nb) Nộp hồ sơ đúng nơi."),
    ]


def test_exact_article_focus_query_uses_quoted_facet_over_title():
    query = (
        "Sửa đổi, bổ sung một số điều của Luật Xây dựng "
        "(phần 67: Người quyết định đầu tư có trách nhiệm tổ Điều 57 của Luật này;)"
    )
    assert _exact_article_focus_query(query).startswith("Người quyết định đầu tư")


def test_exact_article_packet_loads_every_chunk_and_restores_source_order() -> None:
    packet = build_exact_article_packet(_complete_rows())

    assert packet["status"] == "complete"
    assert packet["ordered_chunk_ids"] == [101, 102, 103, 104]
    assert packet["loaded_chunk_count"] == 4
    assert packet["expected_chunk_count"] == 4
    assert packet["missing_chunk_indexes"] == []
    assert packet["missing_structural_units"] == []
    assert packet["assembled_content"] == ARTICLE_CONTENT


def test_exact_article_packet_reports_missing_index_and_point() -> None:
    rows = [row for row in _complete_rows() if row["chunk_index"] != 2]
    for row in rows:
        row["exact_article_chunk_count"] = 4

    packet = build_exact_article_packet(rows)

    assert packet["status"] == "incomplete"
    assert "missing_chunk_indexes" in packet["reason_codes"]
    assert "missing_structural_units" in packet["reason_codes"]
    assert packet["missing_chunk_indexes"] == [2]
    assert "Khoản 2 > Điểm b" in packet["missing_structural_units"]


def test_exact_article_packet_ignores_bare_official_gazette_page_number() -> None:
    rows = _complete_rows()
    parent = str(rows[0]["article_content"])
    rows[0]["article_content"] = parent.replace("\n2.", "\n14\n2.")
    for row in rows[1:]:
        row["article_content"] = rows[0]["article_content"]
    packet = build_exact_article_packet(rows)
    assert packet["status"] == "complete"
    assert packet["content_coverage_ratio"] == 1.0


def test_exact_article_packet_excludes_form_tail_attached_after_dispatch_footer() -> None:
    legal = "1. Trách nhiệm thứ nhất.\n2. Trách nhiệm thứ hai."
    contaminated = (
        legal
        + "\n\n| Nơi nhận: - Công báo; - Lưu: VT. | BỘ TRƯỞNG |"
        + "\n\nMẫu CT01 ban hành kèm theo Thông tư\nTỜ KHAI"
    )
    rows = [
        _row(201, 0, "Điều 28 > Khoản 1", "1. Trách nhiệm thứ nhất.", expected_count=3),
        _row(
            202,
            1,
            "Điều 28 > Khoản 2",
            "2. Trách nhiệm thứ hai.\n\n| Nơi nhận: - Công báo; - Lưu: VT. | BỘ TRƯỞNG |\n\nMẫu CT01 ban hành kèm theo Thông tư",
            expected_count=3,
        ),
        _row(203, 2, "Điều 28 > Khoản 1", "1. Họ và tên trong tờ khai", expected_count=3),
    ]
    for row in rows:
        row["article_content"] = contaminated

    packet = build_exact_article_packet(rows)

    assert packet["status"] == "complete"
    assert packet["assembled_content"] == legal
    assert packet["ordered_chunk_ids"] == [201, 202]
    assert packet["attachment_boundary_detected"] is True
    assert packet["excluded_attachment_chunk_count"] == 1


def test_exact_article_packet_rejects_empty_or_duplicate_chunks() -> None:
    rows = _complete_rows()
    rows[1]["content"] = ""
    rows.append({**rows[0], "chunk_id": 999})

    packet = build_exact_article_packet(rows)

    assert packet["status"] == "incomplete"
    assert packet["empty_chunk_ids"] == [102]
    assert packet["duplicate_chunk_indexes"] == [3]


def test_exact_article_packet_rejects_mixed_identity_and_conflicting_count() -> None:
    rows = _complete_rows()
    rows[2]["law_number"] = "99/2099/TT-ABC"
    rows[3]["exact_article_chunk_count"] = 5

    packet = build_exact_article_packet(rows)

    assert packet["status"] == "incomplete"
    assert "mixed_document_or_article_identity" in packet["reason_codes"]
    assert "conflicting_declared_chunk_count" in packet["reason_codes"]


def test_exact_article_packet_rejects_conflicting_parent_bodies() -> None:
    rows = _complete_rows()
    rows[1]["article_content"] = ARTICLE_CONTENT + "\n4. Nội dung không nhất quán."

    packet = build_exact_article_packet(rows)

    assert packet["status"] == "incomplete"
    assert "conflicting_parent_content" in packet["reason_codes"]


def test_exact_article_packet_fails_closed_when_full_article_exceeds_budget() -> None:
    rows = _complete_rows()
    for row in rows:
        row["article_content"] = ARTICLE_CONTENT * 100

    packet = build_exact_article_packet(rows, max_chars=500)

    assert packet["status"] == "incomplete"
    assert "article_context_too_large" in packet["reason_codes"]


def test_complete_packet_is_attached_once_but_all_children_remain_auditable() -> None:
    packet = build_exact_article_packet(_complete_rows())
    attached = attach_exact_article_packet(packet["ordered_rows"], packet)

    assert len(attached) == 4
    assert attached[0]["parent_context"] == ARTICLE_CONTENT
    assert attached[0]["parent_context_primary"] is True
    assert all(row["exact_article_packet_status"] == "complete" for row in attached)
    assert all(row["matched_child_content"] for row in attached)
    assert all(row["parent_context"] is None for row in attached[1:])

    public = public_exact_article_packet(packet)
    assert public["status"] == "complete"
    assert "assembled_content" not in public
    assert "ordered_rows" not in public


def _long_rows() -> list[dict]:
    rows = []
    parts = []
    for index in range(20):
        marker = "quy định mục tiêu đặc biệt" if index == 13 else "quy định chung"
        content = f"{index + 1}. {marker} " + (f"nội dung {index} " * 25)
        parts.append(content)
        rows.append(
            _row(
                500 + index,
                index,
                f"Điều 73 > Khoản {index + 1}",
                content,
                expected_count=20,
            )
        )
    parent = "\n".join(parts)
    for row in rows:
        row["article_content"] = parent
    return rows


def test_long_article_checks_all_chunks_then_serves_bounded_relevant_window() -> None:
    packet = build_exact_article_serving_packet(
        _long_rows(),
        query="Điều 73 quy định mục tiêu đặc biệt là gì?",
        max_chars=2200,
    )

    assert packet["status"] == "complete"
    assert packet["serving_mode"] == "bounded_long_article_window"
    assert packet["loaded_chunk_count"] == 20
    assert packet["ordered_chunk_ids"] == list(range(500, 520))
    assert 513 in packet["selected_chunk_ids"]
    assert _exact_packet_serving_chunk_ids(packet) == packet["selected_chunk_ids"]
    assert packet["omitted_chunk_count"] > 0
    assert "mục tiêu đặc biệt" in packet["assembled_content"]
    assert len(packet["assembled_content"]) <= 1500


def test_long_article_full_text_request_still_fails_closed() -> None:
    packet = build_exact_article_serving_packet(
        _long_rows(),
        query="Xin cho biết đầy đủ Điều 73, giữ đúng thứ tự khoản điểm.",
        max_chars=2200,
    )

    assert packet["status"] == "incomplete"
    assert packet["serving_mode"] == "full_article_too_large_fail_closed"
    assert "article_context_too_large" in packet["reason_codes"]
