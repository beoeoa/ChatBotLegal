import pytest

from api.legal_section_grounding import (
    aggregate_answer_sections,
    validate_answer_section,
)


def _source():
    return {
        "source_id": "source-1",
        "request_id": "request-1",
        "issue_id": "issue-1",
        "domain": "land",
        "effective_status": "active",
        "official": True,
        "scope": "central",
        "source_url": "https://official.example/land",
        "document_title": "Luật Đất đai",
        "law_number": "31/2024/QH15",
        "article_number": "137",
    }


def test_validator_keeps_sufficient_section_and_localizes_insufficient_section():
    sufficient = validate_answer_section(
        request_id="request-1",
        issue_id="issue-1",
        title="Thẩm quyền",
        answer="Nội dung đã xác minh.",
        sources=[_source()],
    )
    insufficient = validate_answer_section(
        request_id="request-1",
        issue_id="issue-2",
        title="Lệ phí",
        limitation="Chưa có nguồn còn hiệu lực về lệ phí.",
        clarifying_question="Bạn thực hiện thủ tục cụ thể nào?",
        sources=[],
    )

    aggregate = aggregate_answer_sections([sufficient, insufficient])

    assert sufficient.status == "sufficiently_evidenced"
    assert insufficient.status == "insufficiently_evidenced"
    assert aggregate["grounding_status"] == "partially_grounded"
    assert "Nội dung đã xác minh" in aggregate["answer"]
    assert "Chưa có nguồn còn hiệu lực" in aggregate["answer"]


def test_partial_guidance_cannot_contain_legal_claim_or_citation():
    with pytest.raises(ValueError, match="guidance"):
        validate_answer_section(
            request_id="request-1",
            issue_id="issue-1",
            title="Hồ sơ",
            guidance="Theo Điều 5, UBND phải giải quyết trong 5 ngày.",
            limitation="Thiếu nguồn đầy đủ.",
            sources=[],
        )


def test_sufficient_section_deduplicates_identical_public_citations():
    duplicate = dict(_source(), source_id="source-duplicate")

    section = validate_answer_section(
        request_id="request-1",
        issue_id="issue-1",
        title="Hồ sơ",
        answer="Nội dung đã xác minh.",
        sources=[_source(), duplicate],
    )

    assert len(section.citations) == 1
