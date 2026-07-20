from api.legal_section_grounding import aggregate_answer_sections, validate_answer_section


def test_one_sufficient_section_prevents_global_insufficient_fallback():
    source = {
        "source_id": "source-1",
        "request_id": "request-1",
        "issue_id": "issue-1",
        "domain": "land",
        "effective_status": "active",
        "official": True,
        "scope": "central",
        "source_url": "https://official.example/land",
        "document_title": "Luật Đất đai",
    }
    aggregate = aggregate_answer_sections([
        validate_answer_section(
            request_id="request-1", issue_id="issue-1", title="Tranh chấp",
            answer="Nội dung có căn cứ.", sources=[source],
        ),
        validate_answer_section(
            request_id="request-1", issue_id="issue-2", title="Lệ phí",
            limitation="Chưa đủ căn cứ.", sources=[],
        ),
    ])

    assert aggregate["grounding_status"] == "partially_grounded"
    assert aggregate["citations"]
