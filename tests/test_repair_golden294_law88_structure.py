import unicodedata

from scripts.repair_golden294_law88_structure import (
    UNIT_DEFINITIONS,
    allocate_existing_chunk_ids,
    build_units,
    split_swallowed_unit,
)


def _content(number: str, next_number: str, title: str) -> str:
    return (
        "1. Nội dung cha.\n\n"
        f" {number}. Sửa đổi, bổ sung Điều {title} như sau:\n\n"
        " “\n"
        f"Điều {title}. Tiêu đề nhúng\n\n"
        " 1. Nội dung nhúng.”.\n\n"
        f" {next_number}. Sửa đổi, bổ sung Điều 999 như sau:\n\n “"
    )


def test_split_swallowed_unit_extracts_only_the_embedded_body():
    parent, embedded = split_swallowed_unit(
        _content("17", "18", "99"),
        start_marker="17.",
        end_marker="18.",
        embedded_title_prefix="Điều 99.",
    )
    assert parent == "1. Nội dung cha."
    assert embedded == "1. Nội dung nhúng."
    assert "17." not in parent
    assert "18." not in embedded


def test_split_swallowed_unit_accepts_decomposed_vietnamese_heading():
    content = _content("17", "18", "99")
    content = content.replace(
        "Điều 99. Tiêu đề nhúng",
        unicodedata.normalize("NFD", "Điều 99. Tiêu đề nhúng"),
    )
    parent, embedded = split_swallowed_unit(
        content,
        start_marker="17.",
        end_marker="18.",
        embedded_title_prefix="Điều 99.",
    )
    assert parent == "1. Nội dung cha."
    assert unicodedata.normalize("NFC", embedded) == "1. Nội dung nhúng."


def test_build_units_adds_exactly_99_and_101_without_replacing_parents():
    rows = {
        "87": {
            "id": 879432,
            "title": "Điều 87",
            "content": _content("17", "18", "99"),
            "status": "active",
        },
        "100": {
            "id": 879433,
            "title": "Điều 100",
            "content": _content("19", "20", "101"),
            "status": "active",
        },
    }
    units = build_units(rows)
    assert [unit["article_number"] for unit in units] == ["87", "99", "100", "101"]
    assert units[0]["article_id"] == 879432
    assert units[1]["article_id"] is None
    assert units[1]["amendment_path"] == "Khoản 17 Điều 1"
    assert units[3]["amendment_path"] == "Khoản 19 Điều 1"


def test_live_unit_definitions_are_bounded_to_two_missing_units():
    assert set(UNIT_DEFINITIONS) == {"87", "100"}
    assert {
        value["embedded_number"] for value in UNIT_DEFINITIONS.values()
    } == {"99", "101"}
