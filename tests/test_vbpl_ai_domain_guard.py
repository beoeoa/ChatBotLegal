from api.legal_crawl_service import LegalCrawlService


def test_ai_assessment_cannot_replace_deterministic_candidate_domain():
    parsed = LegalCrawlService._parse_ai_assessment(
        '{"domain":"trat_tu_do_thi","scope":"central","official_level":"official",'
        '"effective_status":"khong_ro","duplicate_risk":"none",'
        '"confidence":0.6,"reasons":["Gợi ý theo nội dung"]}'
    )

    assert parsed is not None
    candidate = {
        "id": "legal_crawl_candidate:test",
        "domain": "ho_tich_chung_thuc",
        "ai_assessment": parsed,
    }
    # Cached assessments retain the deterministic domain used by visibility and import.
    import asyncio
    from unittest.mock import AsyncMock, patch

    with patch("api.legal_crawl_service.repo_update", new=AsyncMock()):
        assessed = asyncio.run(LegalCrawlService.assess_candidate(candidate))

    assert assessed["inferred_domain"] == "ho_tich_chung_thuc"
    assert assessed["ai_suggested_domain"] == "trat_tu_do_thi"
