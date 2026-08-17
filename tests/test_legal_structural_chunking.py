from api.legal_structural_chunking import (
    parse_structural_parents,
    parse_structural_path,
    split_parent_children,
)


def test_parent_parser_uses_line_boundaries_and_stops_article_before_appendix():
    content = """Căn cứ Điều 5 của Luật liên quan.

Điều 1. Phạm vi điều chỉnh
Nội dung của Điều thứ nhất.

Điều 2. Thành phần hồ sơ
1. Tờ khai theo mẫu.
2. Bản sao giấy tờ.

PHỤ LỤC I. DANH MỤC BIỂU MẪU
Mẫu số 01: Tờ khai.
"""

    parents = parse_structural_parents(content)

    assert [parent["parent_kind"] for parent in parents] == [
        "article",
        "article",
        "appendix",
    ]
    assert [parent["article_number"] for parent in parents] == ["1", "2", "PL-I"]
    assert "PHỤ LỤC" not in parents[1]["content"]
    assert parents[2]["content"] == "Mẫu số 01: Tờ khai."


def test_consecutive_appendices_are_isolated_parents():
    parents = parse_structural_parents(
        """PHỤ LỤC I
Nội dung phụ lục thứ nhất.

PHỤ LỤC II
Nội dung phụ lục thứ hai.
"""
    )

    assert len(parents) == 2
    assert parents[0]["article_number"] == "PL-I"
    assert parents[0]["content"] == "Nội dung phụ lục thứ nhất."
    assert parents[1]["article_number"] == "PL-II"
    assert parents[1]["content"] == "Nội dung phụ lục thứ hai."


def test_splitter_targets_points_and_preserves_their_clause_path():
    parent = {
        "article_number": "16",
        "title": "Điều 16. Thủ tục đăng ký",
        "content": """1. Người yêu cầu nộp hồ sơ gồm:
a) Tờ khai theo mẫu;
b) Giấy tờ chứng minh.
2. Thời hạn giải quyết là ba ngày làm việc.""",
        "parent_kind": "article",
    }

    children = split_parent_children(parent)

    assert [child["chunk_index"] for child in children] == list(range(len(children)))
    point_a = next(child for child in children if child.get("point_number") == "a")
    point_b = next(child for child in children if child.get("point_number") == "b")
    clause_2 = next(child for child in children if child.get("clause_number") == "2")
    assert point_a["child_kind"] == "point"
    assert point_a["clause_number"] == "1"
    assert point_a["heading"] == "Điều 16. Thủ tục đăng ký > Khoản 1 > Điểm a"
    assert "Tờ khai" in point_a["content"]
    assert "Giấy tờ chứng minh" not in point_a["content"]
    assert "Tờ khai" not in point_b["content"]
    assert clause_2["child_kind"] == "clause"
    assert "ba ngày" in clause_2["content"]


def test_splitter_uses_explicit_fallback_when_no_child_structure_exists():
    parent = {
        "article_number": "3",
        "title": "Điều 3. Nguyên tắc",
        "content": "Một đoạn văn không có khoản hoặc điểm nhưng vẫn phải được tìm kiếm.",
        "parent_kind": "article",
    }

    children = split_parent_children(parent)

    assert len(children) == 1
    assert children[0]["child_kind"] == "fallback"
    assert children[0]["heading"] == "Điều 3. Nguyên tắc"
    assert children[0]["content"] == parent["content"]


def test_prose_mentions_do_not_create_structural_parents():
    assert parse_structural_parents(
        "Việc thực hiện theo Điều 5 và Phụ lục kèm theo văn bản này."
    ) == []


def test_structural_path_can_be_recovered_from_persisted_heading():
    assert parse_structural_path(
        "Điều 16. Thủ tục đăng ký > Khoản 2 > Điểm a"
    ) == {"clause_number": "2", "point_number": "a"}
    assert parse_structural_path("Điều 3. Nguyên tắc") == {
        "clause_number": None,
        "point_number": None,
    }
