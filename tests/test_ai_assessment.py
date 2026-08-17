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

    with patch("api.auth.has_real_users", new=mock_has_real_users), \
         patch("api.auth.configured_role_passwords", return_value={}):
        yield


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.provision_langchain_model", new_callable=AsyncMock)
async def test_assess_candidate_with_llm(mock_provision, mock_update):
    """Kiểm tra assess_candidate phân tích đúng cấu trúc JSON trả về từ LLM và lưu vào database."""
    from api.legal_crawl_service import LegalCrawlService
    
    candidate = {
        "id": "legal_crawl_candidate:test_assess_1",
        "title": "Quyết định xử phạt vi phạm hành chính",
        "law_number": "99/QĐ-XPHC",
        "content": "Điều 1. Xử phạt ông Nguyễn Văn A vì hành vi vi phạm trật tự xây dựng...",
        "scope": "local",
        "status": "pending",
        "raw_metadata": {}
    }
    
    # Mock LLM return value
    mock_model = MagicMock()
    mock_response = MagicMock()
    mock_response.content = json.dumps({
        "domain": "trat_tu_do_thi",
        "scope": "local",
        "official_level": "official",
        "effective_status": "con_hieu_luc",
        "duplicate_risk": "none",
        "confidence": 0.95,
        "reasons": [
            "Quyết định xử phạt trực tiếp thuộc thẩm quyền phường",
            "Đảm bảo an ninh trật tự xây dựng địa phương"
        ]
    })
    mock_model.ainvoke = AsyncMock(return_value=mock_response)
    mock_provision.return_value = mock_model
    
    # Mock repo_update return values
    mock_update.return_value = [candidate]
    
    result = await LegalCrawlService.assess_candidate(candidate, force=True)
    
    # Verify AI assessment fields
    assert "ai_assessment" in result
    ai = result["ai_assessment"]
    assert ai["domain"] == "trat_tu_do_thi"
    assert ai["scope"] == "local"
    assert ai["official_level"] == "official"
    assert ai["effective_status"] == "con_hieu_luc"
    assert ai["duplicate_risk"] == "none"
    assert ai["confidence"] == 0.95
    assert len(ai["reasons"]) == 2
    
    # Backward compatibility checks
    assert result["inferred_domain"] is None
    assert result["ai_suggested_domain"] == "trat_tu_do_thi"
    assert "Quyết định xử phạt" in result["suitability_recommendation"]
    
    # Verify DB update call
    mock_update.assert_called_once()
    called_args = mock_update.call_args[0]
    assert called_args[0] == "legal_crawl_candidate"
    assert called_args[1] == "legal_crawl_candidate:test_assess_1"
    assert called_args[2]["ai_assessment"] == ai


@pytest.mark.asyncio
@patch("api.legal_crawl_service.repo_query", new_callable=AsyncMock)
@patch("api.legal_crawl_service.repo_update", new_callable=AsyncMock)
@patch("api.legal_crawl_service.provision_langchain_model", new_callable=AsyncMock)
async def test_assess_candidate_api_endpoint(mock_provision, mock_update, mock_query, client):
    """Kiểm tra API endpoint trigger AI assessment thủ công trả đúng kết quả mà không thay đổi status."""
    candidate_data = {
        "id": "legal_crawl_candidate:test_assess_2",
        "title": "Thông tư về quản lý hộ tịch",
        "law_number": "04/2020/TT-BTP",
        "content": "Điều 1. Phạm vi điều chỉnh...",
        "scope": "central",
        "status": "pending",
        "raw_metadata": {}
    }
    
    mock_query.return_value = [candidate_data]
    
    # Mock LLM return value
    mock_model = MagicMock()
    mock_response = MagicMock()
    mock_response.content = json.dumps({
        "domain": "ho_tich_chung_thuc",
        "scope": "central",
        "official_level": "official",
        "effective_status": "con_hieu_luc",
        "duplicate_risk": "possible",
        "confidence": 0.9,
        "reasons": ["Thông tư trung ương về hộ tịch chi tiết"]
    })
    mock_model.ainvoke = AsyncMock(return_value=mock_response)
    mock_provision.return_value = mock_model
    
    mock_update.return_value = [{**candidate_data, "status": "pending"}]
    
    headers = {
        "X-User-Role": "admin"
    }
    
    # Router tests exercise assessment behavior only; audit persistence belongs
    # to the isolated SurrealDB integration suite.
    with patch(
        "api.routers.legal_search.write_audit_log", new_callable=AsyncMock
    ):
        response = client.post(
            "/api/legal/crawl/candidates/legal_crawl_candidate:test_assess_2/assess",
            headers=headers,
        )
    
    assert response.status_code == 200
    data = response.json()
    assert "ai_assessment" in data
    assert data["ai_assessment"]["domain"] == "ho_tich_chung_thuc"
    assert data["ai_assessment"]["duplicate_risk"] == "possible"
    
    # Đảm bảo status của candidate vẫn là pending (chưa được approve)
    assert data["candidate"]["status"] == "pending"
