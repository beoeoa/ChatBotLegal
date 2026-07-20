import pytest

from api.legal_crawl_service import LegalCrawlService


@pytest.mark.asyncio
async def test_admin_override_rejects_despite_positive_ai_recommendation(monkeypatch):
    updates = []

    async def fake_update(table, candidate_id, payload):
        updates.append((table, candidate_id, payload))
        return [{"id": candidate_id, **payload}]

    async def fail_import(_candidate_id):
        raise AssertionError("Rejected candidate must never be imported")

    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)
    monkeypatch.setattr(LegalCrawlService, "import_candidate", fail_import)

    result = await LegalCrawlService.review_candidate(
        "legal_crawl_candidate:ocr-1",
        "rejected",
        "Bản scan không đủ rõ để xác minh.",
        reviewed_by="user_account:admin",
        reviewed_role="admin",
    )

    assert result["status"] == "rejected"
    assert result["review_status"] == "rejected"
    assert updates[0][2]["review_note"] == "Bản scan không đủ rõ để xác minh."


def test_recommendation_contains_all_review_scores_and_preview_evidence():
    recommendation = LegalCrawlService.build_review_recommendation({
        "domain": "ho_tich_chung_thuc",
        "scope": "haiphong",
        "source_type": "form",
        "source_url": "https://example.gov.vn/form.pdf",
        "content": "Nội dung biểu mẫu đã trích xuất. " * 40,
        "extraction_result": {
            "characters": 1200,
            "pdf_kind": "scan",
            "ocr_status": "ok",
            "ocr_confidence": 87.5,
            "preview": "Nội dung kiểm chứng từ PDF scan.",
            "text_fingerprint": "abc123def4567890",
        },
        "duplicate_candidates": [],
    })

    assert set(recommendation["scores"]) == {
        "domain_fit", "ward_haiphong_relevance", "form_relevance",
        "duplicate_risk", "extraction_quality", "estimated_usefulness",
    }
    assert recommendation["action"] == "manual_review_required"
    assert recommendation["evidence"]
    assert {item["kind"] for item in recommendation["evidence_snippets"]} == {
        "extraction_preview", "text_fingerprint",
    }
