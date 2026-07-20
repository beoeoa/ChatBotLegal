"""Feature 005 regression requirements for section retrieval provenance.

These are deliberately pure-policy tests: no corpus, model, or network is
needed to show whether a candidate is eligible for the current legal issue.
"""

from api.legal_section_grounding import (
    LegalIssue,
    evaluate_evidence_eligibility,
    select_eligible_evidence,
)


LAND_ISSUE = LegalIssue(
    issue_id="issue-land",
    request_id="request-land",
    text="Thủ tục cấp giấy chứng nhận quyền sử dụng đất thế nào?",
    domain="dat_dai_moi_truong",
    intent="procedure",
)


def _evidence(**overrides):
    source = {
        "chunk_id": 101,
        "request_id": "request-land",
        "issue_id": "issue-land",
        "domain_slug": "dat_dai_moi_truong",
        "document_status": "active",
        "article_status": "active",
        "effective_date": "2025-01-01",
        "source_url": "https://vbpl.vn/land",
        "official_level": "central",
        "scope": "central",
        "issuing_agency": "Quốc hội",
        "law_number": "31/2024/QH15",
    }
    source.update(overrides)
    return source


def test_land_issue_rejects_labour_evidence_even_when_keywords_overlap():
    decision = evaluate_evidence_eligibility(
        _evidence(domain_slug="lao_dong", law_number="45/2019/QH14"),
        LAND_ISSUE,
        legal_as_of="2026-07-18",
    )

    assert decision.eligible is False
    assert decision.reason == "wrong_domain"


def test_expired_conflicting_and_metadata_incomplete_evidence_do_not_support_issue():
    expired = evaluate_evidence_eligibility(
        _evidence(document_status="expired", expired_date="2025-12-31"),
        LAND_ISSUE,
        legal_as_of="2026-07-18",
    )
    incomplete = evaluate_evidence_eligibility(
        _evidence(source_url="", official_level="", issuing_agency=""),
        LAND_ISSUE,
        legal_as_of="2026-07-18",
    )
    conflict = _evidence(
        chunk_id=102,
        relationships=[{"relation_type": "superseded_by", "status": "active"}],
    )

    assert expired.eligible is False
    assert expired.reason == "not_effective"
    assert incomplete.eligible is False
    assert incomplete.reason == "missing_required_metadata"
    assert select_eligible_evidence(
        [conflict], LAND_ISSUE, legal_as_of="2026-07-18"
    ) == []


def test_central_source_is_retained_before_compatible_hai_phong_implementation():
    central = _evidence(chunk_id=101, official_level="central", scope="central")
    local = _evidence(
        chunk_id=102,
        official_level="haiphong",
        scope="haiphong",
        issuing_agency="UBND thành phố Hải Phòng",
    )

    selected = select_eligible_evidence(
        [local, central], LAND_ISSUE, legal_as_of="2026-07-18"
    )

    assert [item["chunk_id"] for item in selected] == [101, 102]
