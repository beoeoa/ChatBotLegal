from unittest.mock import AsyncMock, patch
from pathlib import Path

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
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
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
    mock_update,
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


@patch("api.routers.legal_search.repo_create", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_query", new_callable=AsyncMock)
@patch("api.routers.legal_search.get_user_profile", new_callable=AsyncMock)
def test_repeated_officer_proposal_is_idempotent(
    mock_profile, mock_query, mock_create, client
):
    import hashlib

    mock_profile.return_value = {"allowed_domains": ["ho_tich_chung_thuc"]}
    content = "Điều 1. Nội dung hộ tịch đề xuất."
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    external_seed = "|".join([
        "officer-proposal",
        "user_account:officer_hotich",
        "ho_tich_chung_thuc",
        "document",
        "01/2026/QĐ-TEST",
        "https://vbpl.vn/van-ban/test",
        content_hash,
    ])
    external_id = "officer-proposal:" + hashlib.sha256(
        external_seed.encode("utf-8")
    ).hexdigest()[:32]
    mock_query.return_value = [{
        "id": "legal_crawl_candidate:existing",
        "title": "Hướng dẫn hộ tịch mới",
        "law_number": "01/2026/QĐ-TEST",
        "status": "pending",
        "review_status": "pending",
        "source_url": "https://vbpl.vn/van-ban/test",
        "external_id": external_id,
    }]

    response = client.post(
        "/api/legal/proposals",
        data={
            "domain": "ho_tich_chung_thuc",
            "source_type": "document",
            "title": "Hướng dẫn hộ tịch mới",
            "reason": "Cán bộ đề xuất bổ sung nguồn hộ tịch",
            "source_url": "https://vbpl.vn/van-ban/test",
            "law_number": "01/2026/QĐ-TEST",
            "content": content,
        },
        headers=_officer_headers(),
    )

    assert response.status_code == 200
    assert response.json()["candidate_id"] == "legal_crawl_candidate:existing"
    assert response.json()["deduplicated"] is True
    mock_create.assert_not_called()


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
            "document_type": "Quyết định",
            "issuing_agency": "UBND Thành phố Hải Phòng",
            "issued_date": "2025-12-20",
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


@patch("api.routers.legal_search.write_audit_log", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_update", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.get_candidate", new_callable=AsyncMock)
def test_admin_can_correct_candidate_title_and_verified_metadata(
    mock_get_candidate,
    mock_update,
    _mock_audit,
    client,
):
    candidate = {
        "id": "legal_crawl_candidate:proposal1",
        "title": "Tieu de loi",
        "law_number": "01/2026/TT-TEST",
        "scope": "central",
        "source_url": "https://vbpl.vn/van-ban/test",
        "raw_metadata": {"effective_date": "2026-01-01"},
        "content": "Nội dung hợp lệ " * 20,
    }
    mock_get_candidate.return_value = candidate
    mock_update.return_value = [{
        **candidate,
        "title": "Thông tư thử nghiệm đã sửa đúng",
        "issuing_agency": "Bộ thử nghiệm",
        "raw_metadata": {
            **candidate["raw_metadata"],
            "confirmed_official_source": True,
        },
    }]

    response = client.patch(
        "/api/legal/crawl/candidates/legal_crawl_candidate:proposal1/metadata",
        json={
            "title": "Thông tư thử nghiệm đã sửa đúng",
            "issuing_agency": "Bộ thử nghiệm",
            "confirmed_official_source": True,
            "expired_date": "",
        },
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    update_payload = mock_update.await_args.args[2]
    assert update_payload["title"] == "Thông tư thử nghiệm đã sửa đúng"
    assert update_payload["raw_metadata"]["confirmed_official_source"] is True
    assert update_payload["raw_metadata"]["expired_date"] is None


def test_officer_uploaded_form_is_bridged_to_admin_form_queue(tmp_path, monkeypatch):
    from api.routers import ward_procedures

    project_root = tmp_path / "project"
    upload = project_root / "data" / "uploads" / "officer_proposals" / "mau.docx"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"official form bytes" * 100)
    queue_path = project_root / "data" / "forms" / "official_forms_candidates_classified.json"
    monkeypatch.setattr(ward_procedures, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(ward_procedures, "CLASSIFIED_FORMS_CANDIDATES_PATH", queue_path)

    record = ward_procedures.bridge_officer_form_candidate({
        "id": "legal_crawl_candidate:form1",
        "title": "Biểu mẫu chính thức số 1",
        "domain": "ho_tich_chung_thuc",
        "issuing_agency": "Bộ Tư pháp",
        "source_url": "https://moj.gov.vn/mau-so-1.docx",
        "submitted_by": "user_account:officer_hotich",
        "uploaded_file": {"filename": "mau.docx", "path": str(upload)},
        "raw_metadata": {"effective_date": "2026-01-01"},
    })

    assert record["review_status"] == "candidate_pending_review"
    assert record["runtime_eligible"] is False
    assert record["candidate_id"] == "legal_crawl_candidate:form1"
    payload = ward_procedures._load_forms_json(queue_path, {})
    assert payload["records"][0]["detected_form_name"] == "Biểu mẫu chính thức số 1"


def test_non_admin_cannot_use_admin_preview_or_notifications(client):
    preview = client.post(
        "/api/legal/crawl/preview",
        json={"url": "https://vbpl.vn/van-ban/test"},
        headers=_officer_headers(),
    )
    notifications = client.get(
        "/api/legal/crawl/notifications",
        headers=_officer_headers(),
    )
    mark_read = client.post(
        "/api/legal/crawl/notifications/legal_crawl_notification:test/read",
        headers=_officer_headers(),
    )

    assert preview.status_code == 403
    assert notifications.status_code == 403
    assert mark_read.status_code == 403


def test_officer_crawl_endpoints_are_removed(client):
    preview = client.post(
        "/api/legal/proposals/preview",
        json={"url": "https://vbpl.vn/van-ban/test"},
        headers=_officer_headers(),
    )
    weekly_monitor = client.get(
        "/api/legal/proposals/weekly-monitor",
        headers=_officer_headers(),
    )

    assert preview.status_code == 404
    assert weekly_monitor.status_code == 404


def test_officer_page_keeps_manual_proposal_without_crawl_controls():
    page = Path("frontend/src/app/(dashboard)/officer-proposals/page.tsx").read_text(
        encoding="utf-8"
    )

    assert "/legal/proposals/preview" not in page
    assert "/legal/proposals/weekly-monitor" not in page
    assert "Quét URL" not in page
    assert "apiClient.post('/legal/proposals', body" in page
    assert "Nội dung văn bản (nếu có)" in page


@pytest.mark.asyncio
@patch("api.routers.legal_search.repo_update", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_query", new_callable=AsyncMock)
async def test_internal_officer_proposal_source_is_never_crawled(
    mock_query, mock_update
):
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


@patch("api.routers.legal_search.get_user_profile", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.list_candidates", new_callable=AsyncMock)
def test_officer_candidate_history_supports_multi_domain_and_safe_projection(
    mock_list, mock_profile, client
):
    mock_profile.return_value = {"allowed_domains": ["ho_tich_chung_thuc"]}
    mock_list.return_value = [{
        "id": "legal_crawl_candidate:multi",
        "domain": "dat_dai_xay_dung",
        "submitted_by": "officer_hotich",
        "status": "pending",
        "content": "toan bo van ban khong duoc tra ve",
        "ai_assessment": {"score": 1},
        "extraction_result": {"preview": "x" * 700, "internal": "secret"},
        "raw_metadata": {
            "candidate_origin": "officer_document_proposal",
            "matched_domains": ["dat_dai_xay_dung", "ho_tich_chung_thuc"],
        },
    }]

    response = client.get("/api/legal/proposals/candidates", headers=_officer_headers())

    assert response.status_code == 200
    item = response.json()["candidates"][0]
    assert item["matched_domains"] == ["dat_dai_xay_dung", "ho_tich_chung_thuc"]
    assert len(item["content_preview"]) == 500
    assert "content" not in item
    assert "extraction_result" not in item
    assert "ai_assessment" not in item


@patch("api.routers.legal_search.write_audit_log", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.scan_source", new_callable=AsyncMock)
@patch("api.routers.legal_search.LegalCrawlService.ensure_vbpl_sources", new_callable=AsyncMock)
def test_admin_manual_scan_forces_all_enabled_sources(mock_sources, mock_scan, mock_audit, client):
    mock_sources.return_value = [
        {"id": "legal_crawl_source:on1", "enabled": True, "source_type": "vbpl_listing", "base_url": "https://vbpl.vn"},
        {"id": "legal_crawl_source:off", "enabled": False, "source_type": "vbpl_listing", "base_url": "https://vbpl.vn"},
        {"id": "legal_crawl_source:on2", "enabled": True, "source_type": "vbpl_listing", "base_url": "https://vbpl.vn"},
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

