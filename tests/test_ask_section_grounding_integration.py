from api.legal_section_grounding import build_sectioned_answer


def test_multi_issue_keeps_cited_land_section_and_localizes_fee_gap():
    source = {
        "source_id": "source-1", "request_id": "request-1", "issue_id": "issue-1",
        "domain": "land", "effective_status": "active", "official": True,
        "scope": "central", "source_url": "https://official.example/land",
        "document_title": "Luật Đất đai", "law_number": "31/2024/QH15",
    }
    sections, aggregate = build_sectioned_answer(
        question="Cơ quan nào giải quyết tranh chấp đất; lệ phí bao nhiêu?",
        request_id="request-1",
        generated_answers={"issue-1": "Theo 31/2024/QH15, nội dung đã được đối chiếu."},
        evidence_by_issue={"issue-1": [source]},
    )

    assert sections[0].status == "sufficiently_evidenced"
    assert any(section.status == "insufficiently_evidenced" for section in sections[1:])
    assert aggregate["grounding_status"] == "partially_grounded"
    assert aggregate["citations"]
