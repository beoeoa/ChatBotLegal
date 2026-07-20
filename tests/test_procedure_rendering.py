import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

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
async def test_get_procedures_list(client):
    """Kiểm tra danh sách thủ tục hành chính có chứa sang_ten_so_do."""
    # Nạp dữ liệu từ seed vào database để đảm bảo có thủ tục mới
    client.post("/api/procedures/import-default")
    
    response = client.get("/api/procedures")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    
    # Tìm thủ tục sang tên sổ đỏ
    sang_ten_proc = next((p for p in data if p["id"] == "sang_ten_so_do"), None)
    assert sang_ten_proc is not None
    assert "Sang tên sổ đỏ" in sang_ten_proc["name"]
    assert sang_ten_proc["department"] == "Địa chính - Xây dựng - Đô thị - Môi trường"
    assert len(sang_ten_proc["forms"]) > 0
    
    form = sang_ten_proc["forms"][0]
    assert form["official_level"] == "reference"
    assert form["review_status"] == "candidate_pending_review"


@pytest.mark.asyncio
@patch("api.routers.search._call_legal_retrieval", new_callable=AsyncMock)
@patch("api.routers.search._call_ollama", new_callable=AsyncMock)
async def test_ask_procedure_matching(mock_ollama, mock_retrieval, client):
    """Kiểm tra câu hỏi 'Sang tên sổ đỏ cần làm gì?' tự động khớp thủ tục cấu trúc."""
    mock_retrieval.return_value = {
        "results": [
            {
                "chunk_id": 1,
                "law_number": "123/2015/NĐ-CP",
                "document_title": "Nghị định đất đai",
                "article_number": "Điều 10",
                "content": "Quy trình sang tên sổ đỏ tại UBND cấp xã"
            }
        ],
        "trace": {}
    }
    mock_ollama.return_value = "Để sang tên sổ đỏ, bạn cần chuẩn bị hồ sơ theo [legal:1]."

    response = client.post(
        "/api/search/ask/simple",
        json={
            "question": "Sang tên sổ đỏ cần làm gì?",
            "offline_mode": True,
            "offline_model": "qwen2.5:3b"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["grounding_status"] in {"fully_grounded", "insufficient_evidence"}
    
    # Kiểm tra procedure_detail được đính kèm
    assert data["procedure_detail"] is not None
    assert data["procedure_detail"]["id"] == "sang_ten_so_do"
    assert "Sang tên sổ đỏ" in data["procedure_detail"]["name"]
    
    # Kiểm tra nhãn biểu mẫu reference
    forms = data["procedure_detail"]["forms"]
    assert isinstance(forms, list)
    if forms:
        assert forms[0]["official_level"] == "reference"
        assert forms[0]["review_status"] == "candidate_pending_review"


@pytest.mark.asyncio
async def test_download_procedure_form_template(client):
    """Kiểm tra endpoint tải biểu mẫu đính kèm của thủ tục."""
    response = client.get("/api/procedures/sang_ten_so_do/forms/0")
    assert response.status_code == 422
    detail = response.json()["detail"]
    if isinstance(detail, dict):
        assert detail["code"] == "form_file_invalid"
    else:
        assert (
            "form_file_invalid" in detail
            or "File biểu mẫu" in detail
            or "File bieu mau" in detail
            or "chưa có file hợp lệ" in detail
        )
