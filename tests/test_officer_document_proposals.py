from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def bypass_auth():
    async def mock_has_real_users():
        return False

    with patch("api.auth.has_real_users", new=mock_has_real_users), patch(
        "api.auth.configured_role_passwords", return_value={}
    ):
        yield


def _officer_headers():
    return {
        "X-User-Role": "officer",
        "X-User-Id": "user_account:officer_hotich",
    }


def _admin_headers():
    return {
        "X-User-Role": "admin",
        "X-User-Id": "user_account:admin",
    }


@pytest.mark.asyncio
@patch("api.routers.legal_search.write_audit_log", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_create", new_callable=AsyncMock)
@patch("api.routers.legal_search.get_user_profile", new_callable=AsyncMock)
async def test_officer_wrong_domain_denied(mock_profile, mock_create, _mock_audit, client):
    mock_profile.return_value = {"allowed_domains": ["ho_tich_chung_thuc"]}

    response = client.post(
        "/api/legal/proposals",
        data={
            "domain": "dat_dai_xay_dung",
            "source_type": "document",
            "title": "Tai lieu dat dai moi",
            "reason": "Can bo de xuat bo sung tai lieu",
            "content": "Noi dung de xuat du dai.",
        },
        headers=_officer_headers(),
    )

    assert response.status_code == 403
    assert not mock_create.called


@pytest.mark.asyncio
@patch("api.legal_crawl_service.LegalCrawlService.import_candidate", new_callable=AsyncMock)
@patch("api.routers.legal_search.write_audit_log", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_create", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_query", new_callable=AsyncMock)
@patch("api.routers.legal_search.get_user_profile", new_callable=AsyncMock)
async def test_officer_proposal_pending_excluded_from_ask(
    mock_profile,
    mock_query,
    mock_create,
    mock_audit,
    mock_import,
    client,
):
    mock_profile.return_value = {"allowed_domains": ["ho_tich_chung_thuc"]}
    mock_query.side_effect = [
        [],  # duplicate lookup
        [{"id": "legal_crawl_source:officer_proposal"}],  # proposal source
    ]
    mock_create.return_value = [{"id": "legal_crawl_candidate:proposal1"}]

    response = client.post(
        "/api/legal/proposals",
        data={
            "domain": "ho_tich_chung_thuc",
            "source_type": "document",
            "title": "Huong dan ho tich moi",
            "reason": "Can bo de xuat bo sung nguon ho tich",
            "source_url": "https://example.test/official",
            "law_number": "01/2026/QD-TEST",
            "document_type": "Quyet dinh",
            "effective_date": "2026-01-01",
            "content": "Dieu 1. Noi dung ho tich de xuat.",
        },
        headers=_officer_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["review_status"] == "pending"
    assert not mock_import.called
    assert mock_audit.called

    candidate_payload = mock_create.call_args[0][1]
    assert candidate_payload["status"] == "pending"
    assert candidate_payload["review_status"] == "pending"
    assert candidate_payload["domain"] == "ho_tich_chung_thuc"
    assert candidate_payload["raw_metadata"]["confirmed_official_source"] is False
    assert candidate_payload["raw_metadata"]["candidate_origin"] == "officer_document_proposal"


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_create", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
async def test_approved_candidate_enqueues_persistent_import_job(
    mock_query,
    mock_create,
    mock_update,
):
    from api.legal_crawl_service import LegalCrawlService

    candidate = {
        "id": "legal_crawl_candidate:proposal1",
        "title": "Hướng dẫn hộ tịch mới",
        "law_number": "01/2026/QĐ-TEST",
        "content": "Điều 1. Nội dung hộ tịch đề xuất có đủ một trăm ký tự để qua kiểm tra chất lượng trước khi nhập kho pháp luật nội bộ.",
        "status": "approved",
        "review_status": "approved",
        "scope": "central",
        "source_url": "https://example.test/official",
        "raw_metadata": {
            "confirmed_official_source": True,
            "domain": "ho_tich_chung_thuc",
            "effective_date": "2026-01-01",
            "ocr_status": "not_required",
        },
    }
    mock_query.side_effect = [
        [candidate],
        [],
    ]
    mock_create.return_value = [{"id": "legal_import_job:proposal1", "status": "queued"}]

    job = await LegalCrawlService.enqueue_import_job("legal_crawl_candidate:proposal1")

    assert job["status"] == "queued"
    assert mock_create.call_args.args[0] == "legal_import_job"
    assert any(
        call.args[0] == "legal_crawl_candidate" and call.args[2]["status"] == "import_queued"
        for call in mock_update.call_args_list
    )


def test_officer_cannot_control_crawler_or_approve_candidate(client):
    scan_response = client.post("/api/legal/crawl/scan", headers=_officer_headers())
    assert scan_response.status_code == 403

    review_response = client.post(
        "/api/legal/crawl/candidates/legal_crawl_candidate:proposal1/review",
        json={"decision": "approved", "review_note": "not allowed"},
        headers=_officer_headers(),
    )
    assert review_response.status_code == 403

    sources_response = client.get("/api/legal/crawl/sources", headers=_officer_headers())
    assert sources_response.status_code == 403


@patch("api.routers.legal_search.legal_crawl_preview", new_callable=AsyncMock)
def test_officer_can_preview_official_proposal_url(mock_preview, client):
    mock_preview.return_value = {
        "source_url": "https://vbpl.vn/van-ban/test",
        "title": "Văn bản thử nghiệm",
        "characters": 100,
        "content": "Nội dung chỉ dùng để admin kiểm tra.",
        "crawler": "test",
        "requires_manual_review": True,
    }


@pytest.mark.asyncio
@patch("api.routers.legal_search.repo_update", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_query", new_callable=AsyncMock)
async def test_internal_officer_proposal_source_is_never_crawled(mock_query, mock_update):
    from api.routers.legal_search import _candidate_source_for

    mock_query.return_value = [{
        "id": "legal_crawl_source:officer_proposal",
        "enabled": True,
        "last_status": "failed",
        "last_error": "UnsupportedProtocol",
    }]

    source_id = await _candidate_source_for()

    assert str(source_id) == "legal_crawl_source:officer_proposal"
    update = mock_update.await_args.args[2]
    assert update["enabled"] is False
    assert update["last_status"] == "internal_queue"
    assert update["last_error"] is None

    response = client.post(
        "/api/legal/proposals/preview",
        json={"url": "https://vbpl.vn/van-ban/test"},
        headers=_officer_headers(),
    )

    assert response.status_code == 200
    assert response.json()["proposal_only"] is True
    assert response.json()["review_status"] == "candidate_pending_review"
    mock_preview.assert_awaited_once()


def test_officer_proposal_preview_rejects_non_official_url(client):
    response = client.post(
        "/api/legal/proposals/preview",
        json={"url": "https://example.com/untrusted"},
        headers=_officer_headers(),
    )

    assert response.status_code == 400
    assert "cơ quan nhà nước" in response.json()["detail"]


def test_citizen_cannot_preview_officer_proposal_url(client):
    response = client.post(
        "/api/legal/proposals/preview",
        json={"url": "https://vbpl.vn/van-ban/test"},
        headers={"X-User-Role": "citizen", "X-User-Id": "user_account:citizen"},
    )

    assert response.status_code == 403


@patch("api.routers.legal_search.LegalCrawlService.list_candidates", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.source_status", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.ensure_vbpl_sources", new_callable=AsyncMock)
@patch("api.routers.legal_search.get_user_profile", new_callable=AsyncMock)
def test_officer_weekly_monitor_is_scoped_to_assigned_domain(
    mock_profile,
    mock_sources,
    mock_status,
    mock_candidates,
    client,
):
    mock_profile.return_value = {"allowed_domains": ["ho_tich_chung_thuc"]}
    mock_sources.return_value = []
    mock_status.return_value = [
        {
            "id": "legal_crawl_source:all",
            "name": "Nguồn chung",
            "domains": ["ho_tich_chung_thuc", "dat_dai_xay_dung"],
            "enabled": True,
            "interval_minutes": 10080,
        },
        {
            "id": "legal_crawl_source:land",
            "name": "Nguồn chỉ đất đai",
            "domains": ["dat_dai_xay_dung"],
            "enabled": True,
            "interval_minutes": 10080,
        },
    ]
    mock_candidates.return_value = [
        {
            "id": "legal_crawl_candidate:weekly",
            "title": "Quy định hộ tịch mới",
            "domain": "ho_tich_chung_thuc",
            "status": "pending",
            "raw_metadata": {"candidate_origin": "vbpl_listing_scan"},
        },
        {
            "id": "legal_crawl_candidate:manual",
            "title": "Đề xuất thủ công",
            "domain": "ho_tich_chung_thuc",
            "status": "pending",
            "raw_metadata": {"candidate_origin": "officer_document_proposal"},
        },
    ]

    response = client.get("/api/legal/proposals/weekly-monitor", headers=_officer_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["allowed_domains"] == ["ho_tich_chung_thuc"]
    assert [source["id"] for source in body["sources"]] == ["legal_crawl_source:all"]
    assert [item["id"] for item in body["pending_candidates"]] == ["legal_crawl_candidate:weekly"]
    mock_candidates.assert_awaited_once_with(status="pending", limit=100, domain="ho_tich_chung_thuc")


@patch("api.routers.legal_search.get_user_profile", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.list_candidates", new_callable=AsyncMock)
def test_officer_candidate_history_only_returns_own_proposals(mock_list, mock_profile, client):
    mock_profile.return_value = {"allowed_domains": ["ho_tich_chung_thuc"]}
    base = {
        "domain": "ho_tich_chung_thuc",
        "raw_metadata": {"candidate_origin": "officer_document_proposal"},
        "status": "pending",
    }
    mock_list.return_value = [
        {**base, "id": "legal_crawl_candidate:mine", "submitted_by": "user_account:officer_hotich"},
        {**base, "id": "legal_crawl_candidate:other", "submitted_by": "user_account:officer_other"},
        {**base, "id": "legal_crawl_candidate:crawler", "submitted_by": None, "raw_metadata": {"candidate_origin": "vbpl_listing_scan"}},
    ]

    response = client.get("/api/legal/proposals/candidates", headers=_officer_headers())

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["candidates"]] == ["legal_crawl_candidate:mine"]


@patch("api.routers.legal_search.write_audit_log", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.scan_source", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.ensure_vbpl_sources", new_callable=AsyncMock)
def test_admin_manual_scan_forces_all_enabled_sources(mock_sources, mock_scan, mock_audit, client):
    mock_sources.return_value = [
        {"id": "legal_crawl_source:on1", "enabled": True},
        {"id": "legal_crawl_source:off", "enabled": False},
        {"id": "legal_crawl_source:on2", "enabled": True},
    ]
    mock_scan.side_effect = [
        {"status": "completed", "statistics": {"created": 2}},
        {"status": "completed", "statistics": {"created": 1}},
    ]

    response = client.post("/api/legal/crawl/scan", headers=_admin_headers())

    assert response.status_code == 200
    assert response.json()["run_count"] == 2
    assert response.json()["created"] == 3
    assert [call.args[0] for call in mock_scan.call_args_list] == [
        "legal_crawl_source:on1",
        "legal_crawl_source:on2",
    ]
    assert mock_audit.called

