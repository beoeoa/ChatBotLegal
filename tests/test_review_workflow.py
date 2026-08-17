import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
import json

from api.main import app

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture(autouse=True)
def bypass_auth():
    async def mock_has_real_users():
        return False

    async def mock_write_audit_log(**kwargs):
        return {"id": "audit:test", **kwargs}

    with patch("api.auth.has_real_users", new=mock_has_real_users), \
         patch("api.auth.configured_role_passwords", return_value={}), \
         patch("api.routers.legal_search.write_audit_log", new=mock_write_audit_log):
        yield

@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_query", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_create", new_callable=AsyncMock)
async def test_legal_import_post_to_review_queue(mock_create, mock_query, mock_update, client):
    """Khi nạp mới tài liệu, hệ thống lưu vào hàng đợi duyệt legal_crawl_candidate (pending) thay vì import thẳng."""
    mock_query.side_effect = [[{"id": "legal_crawl_source:manual"}], []]
    mock_create.return_value = [{"id": "legal_crawl_candidate:test123"}]
    
    headers = {
        "X-User-Role": "admin",
        "X-User-Id": "user_account:admin1"
    }
    
    response = client.post(
        "/api/legal/import",
        json={
            "title": "Nghị định quy định chi tiết hộ tịch",
            "law_number": "123/2015/NĐ-CP",
            "document_type": "Nghị định",
            "issuing_agency": "Chính phủ",
            "scope": "Trung ương - toàn quốc",
            "sector": "Hộ tịch",
            "field_id": 1,
            "effective_date": "2015-11-15",
            "content": "Điều 1. Phạm vi điều chỉnh...",
            "confirmed_official_source": True
        },
        headers=headers
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Văn bản đã được đưa vào hàng đợi duyệt của admin."
    assert "candidate_id" in data
    
    # Kiểm tra xem mock_create được gọi với thông tin status='pending'
    mock_create.assert_called_once()
    called_args = mock_create.call_args[0]
    assert called_args[0] == "legal_crawl_candidate"
    assert called_args[1]["status"] == "pending"
    assert called_args[1]["law_number"] == "123/2015/NĐ-CP"
    assert called_args[1]["domain"] == "ho_tich_chung_thuc"
    assert called_args[1]["raw_metadata"]["matched_domains"] == ["ho_tich_chung_thuc"]
    assert called_args[1]["raw_metadata"]["domain_evidence"]["ho_tich_chung_thuc"]


@pytest.mark.asyncio
@patch("api.routers.legal_search.LegalCrawlService.persist_automatic_assessment", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_update", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_query", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_create", new_callable=AsyncMock)
async def test_legal_import_resubmission_reopens_rejected_candidate_without_duplicate(
    mock_create,
    mock_query,
    mock_update,
    mock_assessment,
    client,
):
    existing = {
        "id": "legal_crawl_candidate:test123",
        "external_id": "vbpl:rejected-source-hash",
        "status": "rejected",
        "review_status": "rejected",
    }
    mock_query.side_effect = [
        [{"id": "legal_crawl_source:manual"}],
        [existing],
    ]
    mock_update.return_value = [
        {
            **existing,
            "status": "pending",
            "review_status": "pending",
            "domain": "ho_tich_chung_thuc",
        }
    ]
    mock_assessment.side_effect = lambda candidate: candidate

    response = client.post(
        "/api/legal/import",
        json={
            "title": "Nghị định quy định chi tiết hộ tịch",
            "law_number": "123/2015/NĐ-CP",
            "document_type": "Nghị định",
            "issuing_agency": "Chính phủ",
            "scope": "Trung ương - toàn quốc",
            "sector": "Hộ tịch",
            "field_id": 1,
            "effective_date": "2015-11-15",
            "content": "Điều 1. Phạm vi điều chỉnh và đăng ký hộ tịch.",
            "confirmed_official_source": True,
        },
        headers={"X-User-Role": "admin", "X-User-Id": "user_account:admin1"},
    )

    assert response.status_code == 200
    assert response.json()["candidate_id"] == "legal_crawl_candidate:test123"
    assert response.json()["deduplicated"] is True
    mock_create.assert_not_awaited()
    update_payload = mock_update.await_args.args[2]
    assert update_payload["status"] == "pending"
    assert update_payload["review_status"] == "pending"
    assert update_payload["domain"] == "ho_tich_chung_thuc"
    assert update_payload["external_id"] == "vbpl:rejected-source-hash"


@pytest.mark.asyncio
@patch("api.routers.legal_search.LegalCrawlService.persist_automatic_assessment", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_update", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_query", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_create", new_callable=AsyncMock)
async def test_legal_import_enriches_pending_metadata_only_candidate_without_duplicate(
    mock_create,
    mock_query,
    mock_update,
    mock_assessment,
    client,
):
    existing = {
        "id": "legal_crawl_candidate:metadata-only",
        "external_id": "vbpl:source-hash",
        "status": "pending",
        "review_status": "pending",
        "content": None,
        "raw_metadata": {"metadata_only": True, "confirmed_official_source": False},
    }
    mock_query.side_effect = [
        [{"id": "legal_crawl_source:manual"}],
        [existing],
    ]
    mock_update.return_value = [{
        **existing,
        "content": "Điều 1. Nội dung đầy đủ đã đối chiếu nguồn chính thức.",
        "raw_metadata": {"metadata_only": False, "confirmed_official_source": True},
    }]
    mock_assessment.side_effect = lambda candidate: candidate

    response = client.post(
        "/api/legal/import",
        json={
            "title": "Quyết định về chương trình khuyến công Hải Phòng",
            "law_number": "53/2026/QĐ-UBND",
            "document_type": "Quyết định",
            "issuing_agency": "UBND thành phố Hải Phòng",
            "scope": "Thành phố Hải Phòng",
            "sector": "Hành chính công",
            "field_id": 1,
            "issued_date": "2026-07-01",
            "effective_date": "2026-07-15",
            "source_url": "https://vbpl.vn/van-ban/chi-tiet/example",
            "content": "Điều 1. Nội dung đầy đủ đã đối chiếu nguồn chính thức.",
            "confirmed_official_source": True,
        },
        headers={"X-User-Role": "admin", "X-User-Id": "user_account:admin1"},
    )

    assert response.status_code == 200
    assert response.json()["candidate_id"] == "legal_crawl_candidate:metadata-only"
    assert response.json()["deduplicated"] is True
    assert response.json()["enriched"] is True
    mock_create.assert_not_awaited()
    update_payload = mock_update.await_args.args[2]
    assert update_payload["status"] == "pending"
    assert update_payload["review_status"] == "pending"
    assert update_payload["external_id"] == "vbpl:source-hash"
    assert update_payload["content"].startswith("Điều 1.")
    assert update_payload["raw_metadata"]["metadata_only"] is False
    assert update_payload["raw_metadata"]["confirmed_official_source"] is True
    existing_lookup = mock_query.await_args_list[1]
    assert "external_id = $external_id OR law_number = $law_number" in existing_lookup.args[0]
    assert existing_lookup.args[1]["law_number"] == "53/2026/QĐ-UBND"


@pytest.mark.asyncio
@patch("api.legal_crawl_service.LegalCrawlService.enqueue_import_job", new_callable=AsyncMock)
@patch("api.legal_crawl_service.LegalCrawlService.prepare_candidate_for_import", new_callable=AsyncMock)
@patch("api.legal_crawl_service.LegalCrawlService.find_runtime_document_conflict", new_callable=AsyncMock)
@patch("api.routers.legal_search.readiness.collect_import_readiness", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
async def test_review_candidate_approved_updates_status(
    mock_update,
    mock_query,
    mock_import_readiness,
    mock_find_runtime_conflict,
    mock_prepare,
    mock_enqueue_import,
    client,
):
    """Khi duyệt approved, candidate được cập nhật trạng thái và bắt đầu auto-import."""
    candidate_data = {
        "id": "legal_crawl_candidate:test123",
        "title": "Nghị định quy định chi tiết hộ tịch",
        "law_number": "123/2015/NĐ-CP",
        "content": "Điều 1. Phạm vi điều chỉnh...",
        "scope": "central",
        "status": "pending",
        "raw_metadata": {
            "title": "Nghị định quy định chi tiết hộ tịch",
            "law_number": "123/2015/NĐ-CP",
            "document_type": "Nghị định",
            "issuing_agency": "Chính phủ",
            "field_id": 1,
            "effective_date": "2015-11-15",
        }
    }
    mock_query.return_value = [candidate_data]
    mock_update.return_value = [{**candidate_data, "status": "approved"}]
    mock_find_runtime_conflict.return_value = None
    mock_prepare.return_value = {
        **candidate_data,
        "preparation_status": "ready",
        "pipeline_stage": "validated",
        "blockers": [],
    }
    mock_enqueue_import.return_value = {"id": "legal_import_job:test123"}
    mock_import_readiness.return_value = {
        "status": "ready",
        "components": {},
        "embedding_device": {"requested": "auto", "active": "cpu"},
    }

    headers = {
        "X-User-Role": "admin",
        "X-User-Id": "user_account:admin1"
    }
    
    response = client.post(
        "/api/legal/crawl/candidates/legal_crawl_candidate:test123/review",
        json={
            "decision": "approved",
            "review_note": "Nội dung chuẩn"
        },
        headers=headers
    )
    
    assert response.status_code == 202
    mock_prepare.assert_awaited_once_with("legal_crawl_candidate:test123")
    mock_enqueue_import.assert_awaited_once()
    assert mock_enqueue_import.await_args.args[0] == "legal_crawl_candidate:test123"
    assert response.json()["candidate"]["status"] == "import_queued"


@patch("api.routers.legal_search.LegalCrawlService.review_candidate", new_callable=AsyncMock)
@patch("api.routers.legal_search.readiness.collect_import_readiness", new_callable=AsyncMock)
def test_review_candidate_does_not_queue_when_import_plane_is_not_ready(
    mock_import_readiness,
    mock_review_candidate,
    client,
):
    mock_import_readiness.return_value = {
        "status": "not_ready",
        "components": {"import_worker": {"healthy": False, "code": "stale"}},
        "embedding_device": {"requested": "auto", "active": "cpu"},
    }

    response = client.post(
        "/api/legal/crawl/candidates/legal_crawl_candidate:test123/review",
        json={"decision": "approved", "review_note": "Đủ chứng cứ."},
        headers={"X-User-Role": "admin", "X-User-Id": "user_account:admin1"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "import_pipeline_not_ready"
    mock_review_candidate.assert_not_awaited()


@patch("api.routers.legal_search.LegalCrawlService.get_candidate", new_callable=AsyncMock)
@patch("api.routers.legal_search.readiness.collect_import_readiness", new_callable=AsyncMock)
def test_retry_import_does_not_queue_when_import_plane_is_not_ready(
    mock_import_readiness,
    mock_get_candidate,
    client,
):
    mock_import_readiness.return_value = {
        "status": "not_ready",
        "components": {"import_worker": {"healthy": False, "code": "stale"}},
        "embedding_device": {"requested": "auto", "active": "cpu"},
    }

    response = client.post(
        "/api/legal/crawl/candidates/legal_crawl_candidate:test123/import",
        headers={"X-User-Role": "admin", "X-User-Id": "user_account:admin1"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "import_pipeline_not_ready"
    mock_get_candidate.assert_not_awaited()


@patch("api.routers.legal_search.LegalCrawlService.validate_candidate_for_import", return_value=[])
@patch("api.routers.legal_search.LegalCrawlService.prepare_candidate_for_import", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.enqueue_import_job", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.get_candidate", new_callable=AsyncMock)
@patch("api.routers.legal_search.readiness.collect_import_readiness", new_callable=AsyncMock)
def test_retry_import_returns_reclassified_duplicate_to_the_admin(
    mock_import_readiness,
    mock_get_candidate,
    mock_enqueue_import,
    mock_prepare,
    _mock_validate,
    client,
):
    initial = {
        "id": "legal_crawl_candidate:duplicate",
        "status": "import_failed",
        "import_status": "failed",
    }
    reclassified = {
        **initial,
        "status": "changes_requested",
        "import_status": "duplicate_conflict",
        "review_note": "Đã có văn bản cùng số hiệu trong kho.",
    }
    mock_import_readiness.return_value = {
        "status": "ready",
        "components": {},
        "embedding_device": {"requested": "auto", "active": "cpu"},
    }
    mock_get_candidate.side_effect = [initial, reclassified]
    mock_prepare.return_value = {
        **initial,
        "preparation_status": "ready",
        "pipeline_stage": "validated",
        "blockers": [],
    }
    mock_enqueue_import.side_effect = ValueError("Văn bản đã có số hiệu trùng trong kho runtime")

    response = client.post(
        "/api/legal/crawl/candidates/legal_crawl_candidate:duplicate/import",
        headers={"X-User-Role": "admin", "X-User-Id": "user_account:admin1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "duplicate_conflict"
    assert response.json()["candidate"]["status"] == "changes_requested"
    mock_enqueue_import.assert_awaited_once()
    assert mock_enqueue_import.await_args.args[0] == "legal_crawl_candidate:duplicate"


@pytest.mark.asyncio
@patch("api.routers.ward_procedures._load_forms_json")
@patch("api.routers.ward_procedures.Path.write_text")
async def test_legacy_form_candidate_review_requires_legal_attestation(mock_write, mock_load, client):
    """Legacy candidate endpoint must not promote a form into runtime directly."""
    # Endpoint loads: classified candidates check -> official index -> catalog -> forms_manifest.
    mock_load.side_effect = [
        # Classified candidates check (returns no match)
        {"summary": {}, "records": [], "top10": [], "notes": []},
        # Official index load
        {
            "forms": [
                {
                    "id": "form_123",
                    "title": "T? khai k?t h?n",
                    "review_status": "candidate_pending_review",
                }
            ]
        },
        # Catalog load
        {"forms": []},
        # Manifest load
        {
            "forms": {
                "ho_tich_chung_thuc": [
                    {
                        "id": "form_123",
                        "title": "T? khai k?t h?n",
                        "review_status": "candidate_pending_review",
                    }
                ]
            }
        },
    ]

    headers = {
        "X-User-Role": "admin"
    }

    response = client.post(
        "/api/procedures/forms-catalog/candidates/form_123/review",
        json={
            "decision": "approved",
            "review_note": "?? ??i chi?u",
        },
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "LEGAL_ATTESTATION_REQUIRED"
    # No legacy catalog/index artifact is written by a preliminary decision.
    assert mock_write.call_count == 0
