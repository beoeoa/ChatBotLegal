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
@patch("api.routers.legal_search.repo_query", new_callable=AsyncMock)
@patch("api.routers.legal_search.repo_create", new_callable=AsyncMock)
async def test_legal_import_post_to_review_queue(mock_create, mock_query, client):
    """Khi nạp mới tài liệu, hệ thống lưu vào hàng đợi duyệt legal_crawl_candidate (pending) thay vì import thẳng."""
    mock_query.return_value = [{"id": "legal_crawl_source:manual"}]
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


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
async def test_review_candidate_approved_updates_status(mock_update, mock_query, client):
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
    
    assert response.status_code == 200
    # Đảm bảo repo_update được gọi để cập nhật status = approved
    assert mock_update.called
    first_call_args = mock_update.call_args_list[0][0]
    assert first_call_args[0] == "legal_crawl_candidate"
    assert first_call_args[1] == "legal_crawl_candidate:test123"
    assert first_call_args[2]["status"] == "approved"


@pytest.mark.asyncio
@patch("api.routers.ward_procedures._load_forms_json")
@patch("api.routers.ward_procedures.Path.write_text")
async def test_review_form_candidate(mock_write, mock_load, client):
    """Admin duy?t bi?u m?u candidate th?nh approved trong index/catalog/manifest."""
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

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["review_status"] == "approved"

    # Index + catalog + manifest are rewritten after approval.
    assert mock_write.call_count == 3
