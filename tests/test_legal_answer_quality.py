from datetime import date

from api.legal_answer_quality import (
    apply_claim_validation,
    build_claim_validation,
    build_evidence_coverage,
    clarifying_questions_for_gaps,
    effective_legal_date,
    quality_preview,
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
