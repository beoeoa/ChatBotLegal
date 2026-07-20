import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock

from api.main import app
from api.models import AskResponse
from api.routers.search import (
    _answer_admits_no_legal_basis,
    _verify_and_ground_answer,
)

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
@patch("api.routers.search._call_legal_retrieval", new_callable=AsyncMock)
@patch("api.routers.search._call_ollama", new_callable=AsyncMock)
async def test_grounding_fully_grounded(mock_ollama, mock_retrieval, client):
    """Kiểm tra trường hợp câu trả lời hợp lệ chứa trích dẫn đúng thực tế."""
    mock_retrieval.return_value = {
        "results": [
            {
                "chunk_id": 1,
                "law_number": "123/2015/NĐ-CP",
                "document_title": "Nghị định hộ tịch",
                "article_number": "Điều 10",
                "content": "Thủ tục kết hôn tại Phường Lê Chân",
                "score": 0.9
            }
        ],
        "trace": {}
    }
    mock_ollama.return_value = "Theo quy định tại [legal:1] của Nghị định 123/2015/NĐ-CP, việc kết hôn thực hiện tại Điều 10."

    response = client.post(
        "/api/search/ask/simple",
        json={
            "question": "thủ tục kết hôn",
            "offline_mode": True,
            "offline_model": "qwen2.5:3b"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["grounding_status"] == "fully_grounded"
    assert "Theo quy định tại" in data["answer"]


@pytest.mark.asyncio
@patch("api.routers.search._call_legal_retrieval", new_callable=AsyncMock)
@patch("api.routers.search._call_ollama", new_callable=AsyncMock)
async def test_grounding_ungrounded_fake_citation(mock_ollama, mock_retrieval, client):
    """Kiểm tra trường hợp câu trả lời chứa trích dẫn bịa (legal:99 không tồn tại)."""
    mock_retrieval.return_value = {
        "results": [
            {
                "chunk_id": 1,
                "law_number": "123/2015/NĐ-CP",
                "document_title": "Nghị định hộ tịch",
                "article_number": "Điều 10",
                "content": "Thủ tục kết hôn tại Phường Lê Chân",
                "score": 0.9
            }
        ],
        "trace": {}
    }
    # legal:99 là citation bịa
    mock_ollama.return_value = "Theo quy định tại [legal:99], việc kết hôn thực hiện tại Điều 10."

    response = client.post(
        "/api/search/ask/simple",
        json={
            "question": "thủ tục kết hôn",
            "offline_mode": True,
            "offline_model": "qwen2.5:3b"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["grounding_status"] == "ungrounded"
    assert "Chưa đủ căn cứ pháp lý sát trong kho hiện tại" in data["answer"]
    assert data["citations"] == []


@pytest.mark.asyncio
@patch("api.routers.search._call_legal_retrieval", new_callable=AsyncMock)
@patch("api.routers.search._call_ollama", new_callable=AsyncMock)
async def test_grounding_ungrounded_fake_law_number(mock_ollama, mock_retrieval, client):
    """Kiểm tra trường hợp câu trả lời tự bịa ra số hiệu văn bản (99/2026/NĐ-CP)."""
    mock_retrieval.return_value = {
        "results": [
            {
                "chunk_id": 1,
                "law_number": "123/2015/NĐ-CP",
                "document_title": "Nghị định hộ tịch",
                "article_number": "Điều 10",
                "content": "Thủ tục kết hôn tại Phường Lê Chân",
                "score": 0.9
            }
        ],
        "trace": {}
    }
    # LLM tự bịa ra số hiệu Nghị định 99/2026/NĐ-CP
    mock_ollama.return_value = "Theo quy định tại [legal:1] của Nghị định 99/2026/NĐ-CP, kết hôn thực hiện tại Điều 10."

    response = client.post(
        "/api/search/ask/simple",
        json={
            "question": "thủ tục kết hôn",
            "offline_mode": True,
            "offline_model": "qwen2.5:3b"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["grounding_status"] == "ungrounded"
    assert "Chưa đủ căn cứ pháp lý sát trong kho hiện tại" in data["answer"]


@pytest.mark.asyncio
@patch("api.routers.search._call_legal_retrieval", new_callable=AsyncMock)
@patch("api.routers.search._call_ollama", new_callable=AsyncMock)
async def test_grounding_ungrounded_fake_article(mock_ollama, mock_retrieval, client):
    """Kiểm tra trường hợp câu trả lời tự bịa ra Điều luật (Điều 99)."""
    mock_retrieval.return_value = {
        "results": [
            {
                "chunk_id": 1,
                "law_number": "123/2015/NĐ-CP",
                "document_title": "Nghị định hộ tịch",
                "article_number": "Điều 10",
                "content": "Thủ tục kết hôn tại Phường Lê Chân",
                "score": 0.9
            }
        ],
        "trace": {}
    }
    # LLM tự bịa ra Điều 99 trong khi retrieval chỉ có Điều 10
    mock_ollama.return_value = "Theo quy định tại [legal:1], việc kết hôn thực hiện tại Điều 99."

    response = client.post(
        "/api/search/ask/simple",
        json={
            "question": "thủ tục kết hôn",
            "offline_mode": True,
            "offline_model": "qwen2.5:3b"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["grounding_status"] == "ungrounded"
    assert "Chưa đủ căn cứ pháp lý sát trong kho hiện tại" in data["answer"]


@pytest.mark.asyncio
@patch("api.routers.search._call_legal_retrieval", new_callable=AsyncMock)
@patch("api.routers.search._call_ollama", new_callable=AsyncMock)
async def test_grounding_insufficient_evidence(mock_ollama, mock_retrieval, client):
    """Kiểm tra trường hợp câu trả lời không chứa bất kỳ trích dẫn legal: nào."""
    mock_retrieval.return_value = {
        "results": [
            {
                "chunk_id": 1,
                "law_number": "123/2015/NĐ-CP",
                "document_title": "Nghị định hộ tịch",
                "article_number": "Điều 10",
                "content": "Thủ tục kết hôn tại Phường Lê Chân",
                "score": 0.9
            }
        ],
        "trace": {}
    }
    # Trả lời chung chung không có [legal:1]
    mock_ollama.return_value = "Để đăng ký kết hôn bạn cần chuẩn bị tờ khai và chứng minh thư."

    response = client.post(
        "/api/search/ask/simple",
        json={
            "question": "thủ tục kết hôn",
            "offline_mode": True,
            "offline_model": "qwen2.5:3b"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["grounding_status"] == "partially_grounded"
    assert "\n\n" in data["answer"] and "123/2015/" in data["answer"]


def test_generic_no_answer_guidance_is_not_promoted_to_partially_grounded():
    answer, status = _verify_and_ground_answer(
        "Đăng ký khai sinh cần giấy tờ gì?",
        (
            "Tôi chưa nhận được đủ nội dung để trả lời câu hỏi này. "
            "Vui lòng mô tả ngắn gọn vấn đề, địa bàn và kết quả bạn cần biết."
        ),
        [
            {
                "chunk_id": "402311",
                "law_number": "60/2014/QH13",
                "document_title": "Luật Hộ tịch",
                "article_number": "16",
                "content": "Điều 16. Thủ tục đăng ký khai sinh.",
            }
        ],
    )

    assert status == "insufficient_evidence"
    assert "Đã rà một số nguồn gần nhất" in answer


def test_localized_evidence_gap_does_not_replace_grounded_section():
    evidence = [
        {
            "chunk_id": "402311",
            "law_number": "60/2014/QH13",
            "document_title": "Luật Hộ tịch",
            "article_number": "16",
            "content": "Điều 16. Thủ tục đăng ký khai sinh.",
        }
    ]
    answer = (
        "## Hồ sơ\n"
        "Theo Luật Hộ tịch 60/2014/QH13, Điều 16, hồ sơ được giải quyết "
        "theo nội dung nguồn đã kiểm tra [legal:402311].\n\n"
        "## Lệ phí\n"
        "Nguồn hiện có chưa nêu lệ phí cho trường hợp này."
    )

    assert _answer_admits_no_legal_basis(answer, evidence) is False
    grounded, status = _verify_and_ground_answer(
        "Đăng ký khai sinh cần giấy tờ gì và lệ phí bao nhiêu?",
        answer,
        evidence,
    )

    assert status == "fully_grounded"
    assert "Luật Hộ tịch" in grounded
    assert "Nguồn hiện có chưa nêu lệ phí" in grounded
    assert "Chưa đủ căn cứ pháp lý sát" not in grounded


def test_no_basis_answer_still_triggers_insufficient_fallback():
    evidence = [
        {
            "chunk_id": "402311",
            "law_number": "60/2014/QH13",
            "document_title": "Luật Hộ tịch",
            "article_number": "16",
            "content": "Điều 16. Thủ tục đăng ký khai sinh.",
        }
    ]
    answer = (
        "Tôi chưa nhận được đủ nội dung để trả lời câu hỏi này. "
        "Vui lòng mô tả ngắn gọn vấn đề, địa bàn và kết quả bạn cần biết."
    )

    assert _answer_admits_no_legal_basis(answer, evidence) is True
    fallback, status = _verify_and_ground_answer(
        "Đăng ký khai sinh cần giấy tờ gì?", answer, evidence
    )

    assert status == "insufficient_evidence"
    assert "Chưa đủ căn cứ pháp lý sát" in fallback


def test_no_answer_template_with_matching_citation_is_still_insufficient():
    """A source appendix must not promote a global refusal to a real answer."""
    evidence = [
        {
            "chunk_id": "402311",
            "law_number": "60/2014/QH13",
            "document_title": "Luật Hộ tịch",
            "article_number": "16",
            "content": "Điều 16. Thủ tục đăng ký khai sinh.",
        }
    ]
    answer = (
        "Tôi chưa nhận được đủ nội dung để trả lời câu hỏi này. "
        "Vui lòng mô tả ngắn gọn vấn đề, địa bàn và kết quả bạn cần biết.\n\n"
        "Căn cứ: Luật Hộ tịch 60/2014/QH13, Điều 16."
    )

    assert _answer_admits_no_legal_basis(answer, evidence) is True
    fallback, status = _verify_and_ground_answer(
        "Đăng ký khai sinh cần giấy tờ gì?", answer, evidence
    )

    assert status == "insufficient_evidence"
    assert "Chưa đủ căn cứ pháp lý sát" in fallback


@pytest.mark.asyncio
@patch("api.routers.search._call_legal_retrieval", new_callable=AsyncMock)
@patch("api.routers.search._call_ollama", new_callable=AsyncMock)
async def test_citizen_cannot_request_rag_trace_from_the_server(
    mock_ollama, mock_retrieval, client
):
    mock_retrieval.return_value = {
        "results": [
            {
                "chunk_id": 1,
                "law_number": "123/2015/NĐ-CP",
                "document_title": "Nghị định hộ tịch",
                "article_number": "Điều 10",
                "content": "Điều 10. Nội dung có căn cứ.",
                "score": 0.9,
            }
        ],
        "trace": {"private_chunk_preview": "must not reach citizen"},
    }
    mock_ollama.return_value = "Theo Nghị định 123/2015/NĐ-CP, Điều 10."

    response = client.post(
        "/api/search/ask/simple",
        json={
            "question": "Thủ tục hộ tịch",
            "role": "citizen",
            "show_rag_trace": True,
            "offline_mode": True,
            "offline_model": "qwen2.5:3b",
        },
    )

    assert response.status_code == 200
    assert response.json()["rag_trace"] is None
