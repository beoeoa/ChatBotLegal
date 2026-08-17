from api.models import AnswerSection, AskResponse, CitationDisplayItem


def _citation() -> CitationDisplayItem:
    return CitationDisplayItem(
        document_title="Luật Đất đai",
        law_number="31/2024/QH15",
        article_number="137",
        effective_status="active",
        source_url="https://vbpl.vn/31-2024",
    )


def test_legacy_ask_response_keeps_answer_sections_omitted():
    response = AskResponse(answer="Nội dung cũ", question="Câu hỏi cũ")

    assert response.answer_sections is None
    assert response.model_dump(exclude_none=True) == {
        "answer": "Nội dung cũ",
        "question": "Câu hỏi cũ",
        "grounding_status": "unknown",
        "domain_mismatch": False,
        "required_sections": [],
        "forms_unavailable": False,
        "source_gap": [],
        "evidence_coverage": {},
        "claim_validation": [],
        "reason_codes": [],
        "authority_status": "not_applicable",
        "clarifying_questions": [],
        "quality_flags": [],
        "answer_status": "source_gap",
        "evidence_count": 0,
        "fallback_tier": "support",
    }


def test_section_contract_requires_content_allowed_by_each_status():
    sufficient = AnswerSection(
        issue_id="issue-1",
        title="Thẩm quyền",
        status="sufficiently_evidenced",
        answer="Nội dung đã xác minh.",
        citations=[_citation()],
    )
    partial = AnswerSection(
        issue_id="issue-2",
        title="Hồ sơ",
        status="partially_evidenced",
        guidance="Hướng dẫn tham khảo: chuẩn bị giấy tờ đang có để cơ quan đối chiếu.",
        limitation="Chưa có đủ căn cứ chính thức cho toàn bộ hồ sơ.",
    )
    insufficient = AnswerSection(
        issue_id="issue-3",
        title="Lệ phí",
        status="insufficiently_evidenced",
        limitation="Chưa có nguồn còn hiệu lực về lệ phí.",
        clarifying_question="Bạn thực hiện thủ tục cụ thể nào?",
    )

    assert sufficient.citations == [_citation()]
    assert partial.answer is None and partial.citations == []
    assert insufficient.answer is None and insufficient.guidance is None


def test_simple_endpoint_response_shape_is_compatible_with_optional_sections():
    response = AskResponse(
        answer="Nội dung tổng hợp",
        question="Câu hỏi",
        grounding_status="partially_grounded",
        answer_sections=[
            AnswerSection(
                issue_id="issue-1",
                title="Thẩm quyền",
                status="sufficiently_evidenced",
                answer="Nội dung đã xác minh.",
                citations=[_citation()],
            )
        ],
    )

    payload = response.model_dump(mode="json", exclude_none=True)
    assert payload["answer"] == "Nội dung tổng hợp"
    assert payload["answer_sections"][0]["status"] == "sufficiently_evidenced"
