import pytest

from unittest.mock import AsyncMock

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
    monkeypatch.setattr(
        LegalCrawlService,
        "get_candidate",
        AsyncMock(return_value={"id": "legal_crawl_candidate:ocr-1", "status": "pending"}),
    )

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


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["approved", "import_queued", "imported", "import_failed", "rejected"])
async def test_review_candidate_blocks_non_reviewable_lifecycle_states(monkeypatch, status):
    update = AsyncMock()
    monkeypatch.setattr(
        LegalCrawlService,
        "get_candidate",
        AsyncMock(return_value={"id": "legal_crawl_candidate:locked", "status": status}),
    )
    monkeypatch.setattr("api.legal_crawl_service.repo_update", update)

    with pytest.raises(ValueError, match="không thể nhận quyết định duyệt"):
        await LegalCrawlService.review_candidate(
            "legal_crawl_candidate:locked",
            "rejected",
            "Không được đổi ngược trạng thái.",
            reviewed_by="user_account:admin",
            reviewed_role="admin",
        )

    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_changes_requested_candidate_cannot_repeat_the_same_decision(monkeypatch):
    source = {"id": "legal_crawl_candidate:changes", "status": "changes_requested"}
    monkeypatch.setattr(LegalCrawlService, "get_candidate", AsyncMock(return_value=source))
    update = AsyncMock()
    monkeypatch.setattr("api.legal_crawl_service.repo_update", update)

    with pytest.raises(ValueError, match="đã ở trạng thái cần bổ sung"):
        await LegalCrawlService.review_candidate(
            str(source["id"]),
            "changes_requested",
            "Yêu cầu lặp lại.",
            reviewed_by="user_account:admin",
            reviewed_role="admin",
        )

    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_stops_before_queueing_when_runtime_law_number_exists(monkeypatch):
    updates = []

    async def fake_update(table, candidate_id, payload):
        updates.append((table, candidate_id, payload))
        return [{"id": candidate_id, **payload}]

    candidate = {
        "id": "legal_crawl_candidate:duplicate",
        "status": "pending",
        "law_number": "16/2022/NÄ-CP",
    }
    monkeypatch.setattr(LegalCrawlService, "get_candidate", AsyncMock(return_value=candidate))
    monkeypatch.setattr(
        LegalCrawlService,
        "find_runtime_document_conflict",
        AsyncMock(return_value={"id": 109385, "law_number": "16/2022/NÄ-CP"}),
    )
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)
    enqueue = AsyncMock()
    monkeypatch.setattr(LegalCrawlService, "enqueue_import_job", enqueue)

    result = await LegalCrawlService.review_candidate(
        str(candidate["id"]),
        "approved",
        "Kiểm tra lại bản hiện có.",
        reviewed_by="user_account:admin",
        reviewed_role="admin",
    )

    assert result["status"] == "changes_requested"
    assert result["import_status"] == "duplicate_conflict"
    assert updates[0][2]["duplicate_runtime_document"]["id"] == 109385
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_preserves_duplicate_state_if_conflict_appears_during_enqueue(monkeypatch):
    """A late preflight conflict must not be overwritten as validation_failed."""
    updates = []
    current = {"id": "legal_crawl_candidate:late-duplicate", "status": "pending"}
    reclassified = {
        **current,
        "status": "changes_requested",
        "import_status": "duplicate_conflict",
        "review_note": "Đã có số hiệu trùng trong kho runtime.",
    }

    async def fake_update(table, candidate_id, payload):
        updates.append((table, candidate_id, payload))
        return [{"id": candidate_id, **payload}]

    monkeypatch.setattr(
        LegalCrawlService,
        "get_candidate",
        AsyncMock(side_effect=[current, reclassified]),
    )
    monkeypatch.setattr(LegalCrawlService, "find_runtime_document_conflict", AsyncMock(return_value=None))
    monkeypatch.setattr(
        LegalCrawlService,
        "prepare_candidate_for_import",
        AsyncMock(
            return_value={
                **current,
                "preparation_status": "ready",
                "pipeline_stage": "validated",
                "blockers": [],
            }
        ),
    )
    monkeypatch.setattr(
        LegalCrawlService,
        "enqueue_import_job",
        AsyncMock(side_effect=ValueError("Văn bản đã có số hiệu trùng trong kho runtime")),
    )
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)

    result = await LegalCrawlService.review_candidate(
        str(current["id"]),
        "approved",
        "Kiểm tra nguồn chính thức.",
        reviewed_by="user_account:admin",
        reviewed_role="admin",
    )

    assert result == reclassified
    assert not any(payload.get("import_status") == "validation_failed" for _, _, payload in updates)


@pytest.mark.asyncio
async def test_approval_with_preflight_blockers_is_not_recorded_as_approved(monkeypatch):
    candidate = {
        "id": "legal_crawl_candidate:metadata-only",
        "status": "pending",
        "review_status": "pending",
        "source_url": "https://vbpl.vn/van-ban/chi-tiet/example",
    }
    blocked = {
        **candidate,
        "status": "changes_requested",
        "review_status": "changes_requested",
        "preparation_status": "blocked",
        "pipeline_stage": "blocked",
        "blockers": ["Nội dung trích xuất chưa đủ 100 ký tự để chuẩn hóa/chunk."],
    }
    monkeypatch.setattr(
        LegalCrawlService,
        "get_candidate",
        AsyncMock(return_value=candidate),
    )
    monkeypatch.setattr(
        LegalCrawlService,
        "prepare_candidate_for_import",
        AsyncMock(return_value=blocked),
    )
    monkeypatch.setattr(
        LegalCrawlService,
        "find_runtime_document_conflict",
        AsyncMock(return_value=None),
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(LegalCrawlService, "enqueue_import_job", enqueue)

    result = await LegalCrawlService.review_candidate(
        str(candidate["id"]),
        "approved",
        reviewed_by="user_account:admin",
        reviewed_role="admin",
    )

    assert result["status"] == "changes_requested"
    assert result["review_status"] == "changes_requested"
    assert result["pipeline_stage"] == "blocked"
    assert result["blockers"]
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_stops_and_marks_candidate_when_runtime_law_number_appears(monkeypatch):
    updates = []
    candidate = {
        "id": "legal_crawl_candidate:retry-duplicate",
        "status": "import_failed",
        "law_number": "16/2022/NĐ-CP",
        "title": "Văn bản cần thử lại",
        "scope": "central",
        "source_url": "https://example.test/official",
        "content": "Nội dung đã được xác minh. " * 10,
        "raw_metadata": {
            "confirmed_official_source": True,
            "document_type": "Nghị định",
            "issuing_agency": "Chính phủ",
            "issued_date": "2022-06-15",
            "effective_date": "2024-01-01",
        },
    }

    async def fake_update(table, candidate_id, payload):
        updates.append((table, candidate_id, payload))
        return [{"id": candidate_id, **payload}]

    monkeypatch.setattr(LegalCrawlService, "get_candidate", AsyncMock(return_value=candidate))
    monkeypatch.setattr(
        LegalCrawlService,
        "find_runtime_document_conflict",
        AsyncMock(return_value={"id": 109385, "law_number": "16/2022/NĐ-CP"}),
    )
    monkeypatch.setattr("api.legal_crawl_service.repo_update", fake_update)

    with pytest.raises(ValueError, match="số hiệu trùng"):
        await LegalCrawlService.enqueue_import_job(str(candidate["id"]))

    assert updates[0][0] == "legal_crawl_candidate"
    assert updates[0][2]["status"] == "changes_requested"
    assert updates[0][2]["import_status"] == "duplicate_conflict"


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
        "nguon_chinh_thuc", "du_lieu_bat_buoc", "hieu_luc_ap_dung",
        "trich_xuat_noi_dung", "kiem_tra_trung_lap", "phu_hop_cap_phuong_xa",
    }
    assert recommendation["action"] == "recommended_rejection"
    assert recommendation["evidence"]
    assert {item["kind"] for item in recommendation["evidence_snippets"]} == {
        "trich_doan_noi_dung", "dau_van_tay_noi_dung",
    }
