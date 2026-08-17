import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException

from api.main import app

@pytest.fixture
def client():
    # These router tests intentionally bypass the application lifespan because
    # migrations belong to the integration stack, not a unit test client.
    return TestClient(app)

@pytest.fixture(autouse=True)
def bypass_auth():
    async def mock_has_real_users():
        return False
    with patch("api.auth.has_real_users", new=mock_has_real_users), \
         patch("api.auth.configured_role_passwords", return_value={}):
        yield


@pytest.mark.asyncio
@patch("api.routers.legal_search._request", new_callable=AsyncMock)
async def test_get_legal_document_success(mock_request, client):
    """Kiểm tra xem văn bản active có được phép xem hay không."""
    mock_request.return_value = {
        "document": {
            "id": "12345",
            "document_title": "Nghị định mock",
            "law_number": "123/2015/NĐ-CP",
            "effective_status": "active"
        }
    }
    
    response = client.get("/api/legal/docs/12345")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "12345"
    assert data["effective_status"] == "active"
    mock_request.assert_called_once_with("GET", "/documents/12345", params=None)


@pytest.mark.asyncio
@patch("api.routers.legal_search._request", new_callable=AsyncMock)
async def test_get_legal_document_inactive(mock_request, client):
    """Kiểm tra xem văn bản chưa duyệt/inactive có bị chặn hay không."""
    mock_request.return_value = {
        "document": {
            "id": "12345",
            "document_title": "Nghị định mock",
            "law_number": "123/2015/NĐ-CP",
            "effective_status": "pending"  # Inactive
        }
    }
    
    response = client.get("/api/legal/docs/12345")
    assert response.status_code == 404
    assert "Văn bản chưa được duyệt hoặc không còn hiệu lực" in response.json()["detail"]


@pytest.mark.asyncio
@patch("api.routers.legal_search._request", new_callable=AsyncMock)
async def test_get_expired_legal_document_remains_available_for_history(
    mock_request, client
):
    mock_request.return_value = {
        "document": {
            "id": "31285",
            "document_id": "31285",
            "document_title": "Historical legal document",
            "law_number": "96/2014/TT-BQP",
            "effective_status": "expired",
        }
    }
    snapshot = {
        "schema_version": "legal-validity-serving-v1",
        "mode": "protect",
        "document_ids": {"31285": "96/2014/TT-BQP"},
        "documents": {
            "96/2014/TT-BQP": {
                "document_id": "31285",
                "normalized_status": "expired",
                "serving_action": "historical_only",
                "source_url": "https://vbpl.vn/van-ban/chi-tiet/example",
                "verified_at": "2026-08-09T11:37:38+00:00",
                "effective_from": "2014-08-25",
                "effective_to": "2016-12-20",
                "affected_provisions": [],
                "identity_status": "exact",
                "evidence_status": "sufficient",
            }
        },
    }

    with patch(
        "api.routers.legal_search.default_snapshot_cache.load",
        return_value=snapshot,
    ):
        response = client.get("/api/legal/docs/31285")

    assert response.status_code == 200
    data = response.json()
    assert data["validity_sync"]["status"] == "expired"
    assert data["validity_sync"]["current_answer_eligible"] is False
    assert data["validity_sync"]["historical_lookup_allowed"] is True
    assert data["validity_sync"]["display_label"] == (
        "H\u1ebft hi\u1ec7u l\u1ef1c \u2013 kh\u00f4ng d\u00f9ng \u0111\u1ec3 tr\u1ea3 l\u1eddi hi\u1ec7n h\u00e0nh"
    )


@pytest.mark.asyncio
@patch("api.routers.legal_search.get_legal_document", new_callable=AsyncMock)
@patch("httpx.AsyncClient.get", new_callable=AsyncMock)
async def test_download_pdf_not_found(mock_http_get, mock_get_doc, client):
    """Kiểm tra khi không có file PDF vật lý thật và sinh PDF cũng thất bại (404)."""
    mock_get_doc.return_value = {
        "id": "12345",
        "law_number": "123/2015/NĐ-CP",
        "effective_status": "active"
    }
    
    # Mock httpx response 404
    # ``httpx.AsyncClient.get`` is awaited, but the response object's
    # ``raise_for_status`` method is synchronous.  An AsyncMock here creates
    # an un-awaited coroutine and hides a real resource warning.
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_http_get.return_value = mock_response
    
    # Đảm bảo file PDF thật không tồn tại
    pdf_path = os.path.join("data", "uploads", "pdfs", "12345.pdf")
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        
    response = client.get("/api/legal/docs/12345/download.pdf")
    assert response.status_code == 404
    assert "Chưa có file PDF nội bộ hoặc không tạo được PDF" in response.json()["detail"]


@pytest.mark.asyncio
@patch("api.routers.legal_search.get_legal_document", new_callable=AsyncMock)
@patch("httpx.AsyncClient.get", new_callable=AsyncMock)
async def test_download_pdf_generated(mock_http_get, mock_get_doc, client):
    """Kiểm tra sinh PDF thành công từ search server khi không có file PDF vật lý thật."""
    mock_get_doc.return_value = {
        "id": "12345",
        "law_number": "123/2015/NĐ-CP",
        "effective_status": "active"
    }
    
    # Mock httpx response 200.  The response object is synchronous; only the
    # client ``get`` call itself is async.
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"%PDF-1.4 generated content"
    mock_response.headers = {"content-disposition": 'attachment; filename="123-2015-N-CP.pdf"'}
    mock_http_get.return_value = mock_response
    
    # Đảm bảo file PDF thật không tồn tại
    pdf_path = os.path.join("data", "uploads", "pdfs", "12345.pdf")
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
        
    response = client.get("/api/legal/docs/12345/download.pdf")
    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 generated content"
    assert response.headers["content-type"] == "application/pdf"
    assert "123-2015-N-CP.pdf" in response.headers["content-disposition"]


@pytest.mark.asyncio
@patch("api.routers.legal_search.get_legal_document", new_callable=AsyncMock)
async def test_download_pdf_success(mock_get_doc, client):
    """Kiểm tra tải file PDF gốc thành công khi file vật lý thật tồn tại trên server."""
    mock_get_doc.return_value = {
        "id": "12345",
        "law_number": "123/2015/NĐ-CP",
        "effective_status": "active"
    }
    
    # Tạo mock file PDF thật
    pdf_dir = os.path.join("data", "uploads", "pdfs")
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_path = os.path.join(pdf_dir, "12345.pdf")
    
    with open(pdf_path, "wb") as f:
        f.write(b"%PDF-1.4 mock content")
        
    try:
        response = client.get("/api/legal/docs/12345/download.pdf")
        assert response.status_code == 200
        assert response.content == b"%PDF-1.4 mock content"
        assert response.headers["content-type"] == "application/pdf"
        assert "123-2015-N-CP.pdf" in response.headers["content-disposition"]
    finally:
        # Dọn dẹp file mock
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
