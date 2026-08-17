from api.legal_parent_context import (
    group_parent_child_evidence,
    hydrate_parent_context,
)
from api.legal_grounding import validate_legal_references


def _result(chunk_id: int, article_id: int, document_id: int, content: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "article_id": article_id,
        "document_id": document_id,
        "chunk_heading": f"Điều {article_id} > Khoản {chunk_id}",
        "content": content,
        "score": 0.9,
    }


def _parent(article_id: int, document_id: int, content: str) -> dict:
    return {
        "article_id": article_id,
        "document_id": document_id,
        "article_number": str(article_id),
        "parent_heading": f"Điều {article_id}. Nội dung",
        "parent_content": content,
    }


def test_same_parent_is_projected_once_and_all_children_keep_their_match():
    results = [
        _result(1, 16, 10, "1. Điều kiện"),
        _result(2, 16, 10, "2. Trình tự"),
    ]
    parent_text = "1. Điều kiện\n2. Trình tự\n3. Thời hạn"

    hydrated, summary = hydrate_parent_context(
        results,
        [_parent(16, 10, parent_text)],
    )

    assert hydrated[0]["parent_context"] == parent_text
    assert hydrated[0]["parent_context_primary"] is True
    assert hydrated[1]["parent_context"] is None
    assert hydrated[1]["parent_context_primary"] is False
    assert hydrated[0]["parent_context_ref"] == hydrated[1]["parent_context_ref"]
    assert hydrated[1]["matched_child_content"] == "2. Trình tự"
    assert summary == {
        "unique_parent_count": 1,
        "hydrated_parent_count": 1,
        "truncated_parent_count": 0,
        "parent_fallback_count": 0,
    }

    groups = group_parent_child_evidence(hydrated)
    assert len(groups) == 1
    assert groups[0]["parent_context"] == parent_text
    assert [item["content"] for item in groups[0]["matched_children"]] == [
        "1. Điều kiện",
        "2. Trình tự",
    ]


def test_parent_from_another_document_is_never_attached():
    hydrated, summary = hydrate_parent_context(
        [_result(1, 16, 10, "Nội dung con")],
        [_parent(16, 99, "Nội dung không được phép")],
    )

    assert hydrated[0]["parent_context"] is None
    assert hydrated[0]["parent_context_reason"] == "missing_parent"
    assert hydrated[0]["matched_child_content"] == "Nội dung con"
    assert summary["parent_fallback_count"] == 1


def test_oversized_parent_window_is_deterministic_and_contains_match():
    match = "ĐIỂM KHỚP QUAN TRỌNG"
    parent_text = ("A" * 300) + match + ("B" * 300)
    results = [_result(1, 7, 4, match)]
    parents = [_parent(7, 4, parent_text)]

    first, first_summary = hydrate_parent_context(
        results,
        parents,
        per_parent_char_limit=120,
        total_char_limit=120,
    )
    second, _ = hydrate_parent_context(
        results,
        parents,
        per_parent_char_limit=120,
        total_char_limit=120,
    )

    assert first[0]["parent_context"] == second[0]["parent_context"]
    assert match in first[0]["parent_context"]
    assert first[0]["parent_context_truncated"] is True
    assert first[0]["parent_context_reason"] == "oversized_parent"
    assert first[0]["parent_context_chars"] <= 120
    assert first[0]["parent_context_original_chars"] == len(parent_text)
    assert first_summary["truncated_parent_count"] == 1


def test_oversized_window_finds_point_when_child_repeats_noncontiguous_clause_intro():
    intro = "1. Hồ sơ gồm:"
    point_b = "b) Giấy tờ chứng minh quan hệ."
    parent_text = (
        ("Mở đầu. " * 80)
        + intro
        + "\na) Tờ khai theo mẫu."
        + (" Nội dung giữa." * 40)
        + "\n"
        + point_b
        + (" Kết thúc." * 80)
    )
    child = f"{intro}\n{point_b}"

    hydrated, _ = hydrate_parent_context(
        [_result(1, 7, 4, child)],
        [_parent(7, 4, parent_text)],
        per_parent_char_limit=160,
        total_char_limit=160,
    )

    assert point_b in hydrated[0]["parent_context"]


def test_request_budget_exhaustion_keeps_child_only_fallback():
    results = [
        _result(1, 1, 10, "Con một"),
        _result(2, 2, 10, "Con hai"),
    ]
    parents = [
        _parent(1, 10, "A" * 80),
        _parent(2, 10, "B" * 80),
    ]

    hydrated, summary = hydrate_parent_context(
        results,
        parents,
        per_parent_char_limit=100,
        total_char_limit=80,
    )

    assert hydrated[0]["parent_context"] == "A" * 80
    assert hydrated[1]["parent_context"] is None
    assert hydrated[1]["parent_context_reason"] == "request_budget_exhausted"
    assert summary["parent_fallback_count"] == 1


def test_empty_parent_is_explicit_fallback():
    hydrated, _ = hydrate_parent_context(
        [_result(1, 3, 10, "Con")],
        [_parent(3, 10, "   ")],
    )

    assert hydrated[0]["parent_context_reason"] == "empty_parent"
    assert hydrated[0]["matched_child_content"] == "Con"


def test_grounding_validator_can_verify_a_measure_from_supplied_parent_context():
    evidence = [
        {
            "id": "legal:1",
            "chunk_id": 1,
            "law_number": "01/2026/NĐ-CP",
            "article_number": "16",
            "content": "2. Cơ quan tiếp nhận hồ sơ.",
            "parent_context": "1. Điều kiện áp dụng.\n2. Thời hạn giải quyết là 3 ngày.",
        }
    ]

    validation = validate_legal_references(
        "Theo 01/2026/NĐ-CP, thời hạn giải quyết là 3 ngày.",
        evidence,
    )

    assert validation.status == "fully_grounded"
    assert validation.unsupported_references == ()


def test_long_structured_parent_keeps_governing_clause_not_unrelated_siblings():
    parent_text = (
        "Các trường hợp được xử lý như sau:\n"
        "1. Điều kiện áp dụng.\n"
        + ("Chi tiết điều kiện. " * 30)
        + "\n2. Trình tự thực hiện.\n"
        + ("Cơ quan tiếp nhận và xử lý hồ sơ. " * 30)
        + "\n3. Quy định riêng cho cộng đồng dân cư có đình, đền, miếu.\n"
        + ("Nội dung không cùng chủ thể. " * 30)
    )

    hydrated, _ = hydrate_parent_context(
        [_result(2, 42, 10, "2. Trình tự thực hiện.")],
        [_parent(42, 10, parent_text)],
        per_parent_char_limit=1800,
        total_char_limit=1800,
        short_parent_char_limit=300,
    )

    context = hydrated[0]["parent_context"]
    assert "Các trường hợp được xử lý như sau:" in context
    assert "2. Trình tự thực hiện." in context
    assert "đình, đền, miếu" not in context
    assert hydrated[0]["evidence_capsule_kind"] == "structural_scope"
