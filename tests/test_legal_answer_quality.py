from datetime import date

from api.legal_answer_quality import (
    apply_claim_validation,
    build_claim_validation,
    build_evidence_coverage,
    clarifying_questions_for_gaps,
    extractive_conclusion_from_evidence,
    effective_legal_date,
    quality_preview,
    prepend_extractive_conclusion_when_supported,
    vietnamese_section_names,
)


def _legal_result(content: str) -> dict:
    return {
        "chunk_id": 42,
        "document_id": 99,
        "law_number": "01/2026/NĐ-CP",
        "document_title": "Nghị định kiểm thử",
        "article_number": "10",
        "content": content,
    }


def test_explicit_legal_as_of_wins_over_event_date():
    assert effective_legal_date(
        legal_as_of=date(2025, 7, 1),
        event_date=date(2020, 1, 1),
    ) == date(2025, 7, 1)
    assert effective_legal_date(
        legal_as_of=None,
        event_date=date(2020, 1, 1),
    ) == date(2020, 1, 1)


def test_evidence_coverage_only_verifies_observable_source_content():
    coverage = build_evidence_coverage(
        [_legal_result("UBND cấp xã tiếp nhận hồ sơ gồm tờ khai. Thời hạn 03 ngày làm việc.")],
        required_sections=["submission_place", "documents", "processing_time", "fee", "official_forms"],
        recommended_forms=[],
    )
    assert coverage["authority"]["status"] == "verified"
    assert coverage["documents"]["status"] == "verified"
    assert coverage["processing_time"]["status"] == "verified"
    assert coverage["fee"]["status"] == "missing"
    assert coverage["forms"]["status"] == "missing"
    assert coverage["citations"]["status"] == "verified"


def test_unverified_official_form_is_not_accepted_as_evidence():
    coverage = build_evidence_coverage(
        [_legal_result("Có tờ khai theo mẫu.")],
        required_sections=["official_forms"],
        recommended_forms=[{
            "form_id": "form-1",
            "review_status": "pending",
            "official_level": "official",
            "download_url": "https://example.test/form.pdf",
        }],
    )
    assert coverage["forms"]["status"] == "missing"


def test_claim_validation_and_preview_flag_rejected_claims():
    validation = build_claim_validation(
        removed_claims=[{"claim_type": "fee", "reason": "unsupported", "original": "50.000 đồng"}],
        citations=[{"chunk_id": "42", "doc_id": 99}],
        legal_as_of=date(2026, 7, 14),
    )
    coverage = build_evidence_coverage([_legal_result("Quy định áp dụng.")])
    score, flags = quality_preview(
        coverage=coverage,
        grounding_status="partially_grounded",
        claim_validation=validation,
    )
    assert 0 <= score <= 10
    assert "rejected_unsupported_claims" in flags
    assert any(item["status"] == "rejected" for item in validation)


def test_answer_claims_are_mapped_to_direct_section_evidence():
    coverage = build_evidence_coverage(
        [_legal_result("UBND cấp xã có thẩm quyền tiếp nhận hồ sơ gồm tờ khai.")],
        required_sections=["authority", "documents", "fee"],
    )
    validation = build_claim_validation(
        removed_claims=[],
        citations=[],
        legal_as_of=date(2026, 7, 14),
        answer="UBND cấp xã tiếp nhận hồ sơ. Lệ phí là 50.000 đồng.",
        coverage=coverage,
    )
    authority = next(item for item in validation if item["claim_type"] == "authority")
    fee = next(item for item in validation if item["claim_type"] == "fee")
    assert authority["status"] == "verified"
    assert authority["evidence_ids"] == ["42"]
    assert fee["status"] == "rejected"
    filtered = apply_claim_validation(
        "UBND cấp xã tiếp nhận hồ sơ. Lệ phí là 50.000 đồng.",
        validation,
    )
    assert "UBND cấp xã tiếp nhận hồ sơ" in filtered
    assert "50.000 đồng" not in filtered
    assert "Phần này chưa được xác minh" in filtered


def test_multiple_rejections_emit_one_combined_limitation():
    answer = (
        "## Kết luận\n"
        "Gia đình chưa đủ điều kiện đăng ký thường trú.\n\n"
        "## Các bước\n"
        "Bước 1: Nộp hồ sơ tại cơ quan giả định."
    )
    decisions = [
        {
            "claim_type": "conclusion",
            "status": "rejected",
            "original": "Gia đình chưa đủ điều kiện đăng ký thường trú.",
        },
        {
            "claim_type": "authority",
            "status": "rejected",
            "original": "Bước 1: Nộp hồ sơ tại cơ quan giả định.",
        },
    ]

    filtered = apply_claim_validation(answer, decisions)

    assert "chưa đủ điều kiện đăng ký thường trú" not in filtered
    assert "cơ quan giả định" not in filtered
    assert filtered.count("Phần này chưa được xác minh từ nguồn hiện có.") == 1


def test_unrelated_retrieval_cannot_verify_conclusion_or_score_seven():
    answer = (
        "## Kết luận\n"
        "Gia đình chưa đủ điều kiện đăng ký thường trú tại căn hộ thuê."
    )
    coverage = build_evidence_coverage(
        [_legal_result("Cơ quan quản lý được khai thác dữ liệu về chỗ ở hợp pháp.")],
        answer=answer,
    )
    validation = build_claim_validation(
        removed_claims=[],
        citations=[],
        legal_as_of=date(2026, 7, 30),
        answer=answer,
        coverage=coverage,
    )
    score, flags = quality_preview(
        coverage=coverage,
        grounding_status="fully_grounded",
        claim_validation=validation,
    )

    assert coverage["conclusion"]["status"] == "missing"
    assert any(
        item["claim_type"] == "conclusion" and item["status"] == "rejected"
        for item in validation
    )
    assert score < 7
    assert "missing_conclusion" in flags


def test_conclusion_allows_token_preserving_paraphrase_from_source():
    source = {
        "id": "legal:9",
        "chunk_id": 9,
        "document_id": "law-9",
        "law_number": "68/2020/QH14",
        "article_number": "20",
        "content": (
            "Bảo đảm điều kiện về diện tích nhà ở tối thiểu do Hội đồng "
            "nhân dân cấp tỉnh quy định nhưng không thấp hơn 08 m2 sàn/người."
        ),
    }
    coverage = build_evidence_coverage(
        [source],
        required_sections=["conclusion"],
        answer=(
            "## Kết luận\nĐiều kiện diện tích nhà ở tối thiểu không thấp hơn "
            "08 m2 sàn/người."
        ),
    )

    assert coverage["conclusion"]["status"] == "verified"
    assert coverage["conclusion"]["evidence_ids"] == ["9"]


def test_bulleted_conclusion_label_is_not_part_of_legal_claim():
    source = {
        "id": "legal:10",
        "chunk_id": 10,
        "document_id": "law-10",
        "law_number": "68/2020/QH14",
        "article_number": "20",
        "content": (
            "Xác nhận về điều kiện diện tích bình quân nhà ở để đăng ký "
            "thường trú vào chỗ ở do thuê, mượn, ở nhờ."
        ),
    }
    answer = (
        "## Kết luận và căn cứ\n"
        "- Kết luận: Xác nhận về điều kiện diện tích bình quân nhà ở để "
        "đăng ký thường trú vào chỗ ở do thuê, mượn, ở nhờ.\n"
        "- Biểu mẫu: Tờ khai thay đổi thông tin cư trú."
    )

    coverage = build_evidence_coverage(
        [source],
        required_sections=["conclusion"],
        answer=answer,
    )
    validation = build_claim_validation(
        removed_claims=[],
        citations=[],
        legal_as_of=date(2026, 7, 30),
        answer=answer,
        coverage=coverage,
    )

    assert coverage["conclusion"]["status"] == "verified"
    assert not any(
        item.get("original") == "## Kết luận và căn cứ"
        for item in validation
    )


def test_approved_catalog_form_verifies_form_lookup_conclusion_and_source():
    form = {
        "form_id": "form-19",
        "name": "Đơn đề nghị cấp lại",
        "form_code": "19",
        "review_status": "approved",
        "official_level": "official",
        "source_url": "https://vbpl.vn/example",
        "download_url": "/api/forms/form-19/download",
        "legal_basis": ["141/2024/NĐ-CP"],
    }
    coverage = build_evidence_coverage(
        [],
        required_sections=["conclusion", "official_forms", "legal_basis_links"],
        recommended_forms=[form],
        answer="## Kết luận\nThủ tục sử dụng Đơn đề nghị cấp lại (Mẫu 19).",
    )

    assert coverage["conclusion"]["status"] == "verified"
    assert coverage["forms"]["status"] == "verified"
    assert coverage["citations"]["status"] == "verified"


def test_attested_form_catalog_citation_is_not_rejected():
    decisions = build_claim_validation(
        removed_claims=[],
        citations=[
            {
                "law_number": "141/2024/NĐ-CP",
                "source_url": "https://vbpl.vn/example",
                "verification_source": "approved_form_catalog",
            }
        ],
        legal_as_of=date(2026, 7, 30),
    )

    assert decisions == [
        {
            "claim_type": "citation",
            "status": "verified",
            "reason": "approved_form_catalog_source",
            "evidence_ids": ["141/2024/NĐ-CP"],
            "legal_as_of": "2026-07-30",
        }
    ]


def test_bulleted_conclusion_is_detected_outside_conclusion_heading():
    source = {
        "id": "legal:7",
        "chunk_id": 7,
        "document_id": "law-7",
        "law_number": "02/2011/QH13",
        "article_number": "7",
        "content": (
            "Người khiếu nại khiếu nại lần đầu đến người đã ra quyết định "
            "hành chính."
        ),
    }
    coverage = build_evidence_coverage(
        [source],
        required_sections=["conclusion"],
        answer=(
            "## Thẩm quyền giải quyết\n"
            "- Kết luận: Người khiếu nại khiếu nại lần đầu đến người đã ra "
            "quyết định hành chính."
        ),
    )

    assert coverage["conclusion"]["status"] == "verified"


def test_authority_heading_is_a_verifiable_direct_outcome():
    source = _legal_result("Chủ tịch Ủy ban nhân dân cấp xã giải quyết khiếu nại lần đầu.")
    coverage = build_evidence_coverage(
        [source],
        required_sections=["authority"],
        answer=(
            "## Thẩm quyền giải quyết\n"
            "Chủ tịch Ủy ban nhân dân cấp xã giải quyết khiếu nại lần đầu."
        ),
    )

    assert coverage["conclusion"]["status"] == "verified"


def test_removed_rejected_claim_does_not_reduce_final_quality_score():
    coverage = {
        "conclusion": {"status": "verified"},
        "citations": {"status": "verified"},
    }
    score, flags = quality_preview(
        coverage=coverage,
        grounding_status="fully_grounded",
        answer="## Kết luận\nNội dung đã được đối chiếu nguồn.",
        claim_validation=[{
            "claim_type": "conclusion",
            "status": "rejected",
            "original": "Câu đã bị loại khỏi câu trả lời cuối cùng.",
        }],
    )

    assert score == 10.0
    assert "rejected_unsupported_claims" not in flags


def test_missing_conclusion_is_repaired_with_source_verbatim_fact():
    source = _legal_result(
        "Ủy ban nhân dân cấp xã nơi cư trú của người cha hoặc người mẹ "
        "thực hiện đăng ký khai sinh."
    )
    answer = "## Kết luận ngắn\nChưa xác minh được nội dung này từ nguồn hiện có."

    repaired = prepend_extractive_conclusion_when_supported(
        answer,
        [source],
        required_sections=["authority"],
    )

    assert "Kết luận đã kiểm chứng" in repaired
    assert source["content"] in repaired
    assert "Chưa xác minh được nội dung này" not in repaired


def test_extractive_conclusion_contains_only_matching_source_content():
    source = _legal_result("Ủy ban nhân dân cấp xã thực hiện đăng ký khai sinh.")

    conclusion = extractive_conclusion_from_evidence(
        [source],
        required_sections=["authority"],
    )

    assert conclusion is not None
    assert source["content"] in conclusion
    assert "Điều " not in conclusion


def test_extractive_conclusion_prefers_direct_submission_rule_over_result_issuer():
    result_issuer = _legal_result("Chủ tịch Ủy ban nhân dân cấp xã cấp Giấy khai sinh.")
    submission_rule = _legal_result(
        "Ủy ban nhân dân cấp xã nơi cư trú của người cha hoặc người mẹ thực hiện đăng ký khai sinh."
    )

    conclusion = extractive_conclusion_from_evidence(
        [result_issuer, submission_rule],
        required_sections=["submission_place"],
    )

    assert conclusion is not None
    assert submission_rule["content"] in conclusion
    assert result_issuer["content"] not in conclusion


def test_numbered_source_passage_is_not_rejected_as_a_one_token_claim():
    source = _legal_result("1. Chủ tịch Ủy ban nhân dân cấp xã giải quyết khiếu nại lần đầu.")
    coverage = build_evidence_coverage(
        [source],
        required_sections=["conclusion"],
        answer=(
            "## Kết luận đã kiểm chứng\n"
            "- 1. Chủ tịch Ủy ban nhân dân cấp xã giải quyết khiếu nại lần đầu."
        ),
    )

    assert coverage["conclusion"]["status"] == "verified"


def test_complete_substantive_evidence_is_not_capped_for_missing_heading():
    score, flags = quality_preview(
        coverage={
            "conclusion": {"status": "missing"},
            "authority": {"status": "verified"},
            "documents": {"status": "verified"},
            "citations": {"status": "verified"},
        },
        grounding_status="fully_grounded",
        claim_validation=[],
        answer="Câu trả lời đã có thẩm quyền và hồ sơ được xác minh.",
    )

    assert score > 7.0
    assert "missing_conclusion" in flags


def test_gap_questions_are_short_and_limited_to_three():
    questions = clarifying_questions_for_gaps(
        "Tôi bị lập biên bản xây sai phép, mức phạt và quy hoạch thế nào?",
        {"penalty": {"status": "missing"}, "conclusion": {"status": "missing"}},
    )
    assert 1 <= len(questions) <= 3
    assert all(question.endswith("?") for question in questions)


def test_source_gap_labels_do_not_leak_internal_keys():
    assert vietnamese_section_names(["fee", "processing_time", "official_forms"]) == [
        "lệ phí",
        "thời hạn giải quyết",
        "biểu mẫu chính thức",
    ]
