from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(autouse=True)
def bypass_auth():
    async def mock_has_real_users():
        return False

    with patch("api.auth.has_real_users", new=mock_has_real_users), patch(
        "api.auth.configured_role_passwords", return_value={}
    ):
        yield


def client():
    return TestClient(app)


def test_transcribe_voice_reports_stt_unavailable_when_not_configured():
    with patch("api.routers.search.model_manager.get_speech_to_text", new=AsyncMock(return_value=None)):
        response = client().post(
            "/api/media/transcribe-voice",
            files={"file": ("question.webm", b"fake audio", "audio/webm")},
        )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "speech-to-text model" in detail
    assert "Chưa cấu hình" in detail


def test_transcribe_voice_rejects_video_without_loading_stt():
    with patch("api.routers.search.model_manager.get_speech_to_text", new=AsyncMock()) as get_stt:
        response = client().post(
            "/api/media/transcribe-voice",
            files={"file": ("clip.mp4", b"not a real video", "video/mp4")},
        )

    assert response.status_code == 415
    assert "Không hỗ trợ video" in response.json()["detail"]
    get_stt.assert_not_called()
