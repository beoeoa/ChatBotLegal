from api.legal_retrieval_policy import expanded_retrieval_reason, retrieval_has_query_overlap


def test_retrieval_accepts_relevant_hits():
    assert retrieval_has_query_overlap(
        "Tôi muốn đăng ký khai sinh cho con",
        [{"document_title": "Luật Hộ tịch", "content": "đăng ký khai sinh"}],
    )


def test_retrieval_rejects_unrelated_non_empty_hits():
    assert not retrieval_has_query_overlap(
        "Tôi muốn đăng ký khai sinh cho con",
        [{"document_title": "Quy định về xây dựng", "content": "giấy phép xây dựng nhà ở"}],
    )


def test_compound_land_question_expands_even_with_generic_core_hits():
    core_hits = [
        {"article_id": str(index), "document_title": "Quy định chung về đất", "content": "đất và giấy chứng nhận"}
        for index in range(1, 4)
    ]
    assert expanded_retrieval_reason(
        "Tôi mua đất bằng giấy tay, người bán đã mất và đất có quy hoạch thì làm sao?",
        core_hits,
    ) == "complex_land_or_inheritance_question"
