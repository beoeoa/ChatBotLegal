import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from fastapi import HTTPException

from api.main import app

@pytest.fixture
def client():
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
    mock_response = AsyncMock()
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
    
    # Mock httpx response 200
    mock_response = AsyncMock()
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
