import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock

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

@pytest.fixture(autouse=True)
def mock_db_repository():
    """Globally mock repository methods across all relevant modules to prevent real DB operations."""
    timestamp = datetime(2026, 7, 7, tzinfo=timezone.utc)
    mock_create = AsyncMock(return_value=[{"id": "source:fake", "created": timestamp, "updated": timestamp}])
    mock_query = AsyncMock(return_value=[])
    mock_update = AsyncMock(return_value=[{"id": "source:fake", "created": timestamp, "updated": timestamp}])

    with patch("open_notebook.database.repository.repo_create", new=mock_create), \
         patch("open_notebook.database.repository.repo_query", new=mock_query), \
         patch("open_notebook.database.repository.repo_update", new=mock_update), \
         patch("open_notebook.domain.base.repo_create", new=mock_create), \
         patch("open_notebook.domain.base.repo_query", new=mock_query), \
         patch("open_notebook.domain.base.repo_update", new=mock_update), \
         patch("api.user_service.repo_create", new=mock_create), \
         patch("api.user_service.repo_query", new=mock_query), \
         patch("api.routers.sources.repo_query", new=mock_query):
        yield mock_create, mock_query

class TestDomainAuthorization:
    """Verify that officer users are restricted to allowed_domains for search, ask, and import, while admin is not."""

    @pytest.mark.asyncio
    @patch("api.user_service.get_user_profile", new_callable=AsyncMock)
    @patch("api.routers.search._search_legal_documents", new_callable=AsyncMock)
    @patch("api.routers.search.text_search", new_callable=AsyncMock)
    async def test_officer_search_is_filtered(self, mock_text_search, mock_legal_search, mock_get_profile, client):
        """Officer search should filter out results outside allowed_domains."""
        # Mock officer user profile
        mock_get_profile.return_value = {
            "department": "Tư pháp - Hộ tịch",
            "allowed_domains": ["ho_tich", "chung_thuc"]
        }
        
        # Isolate notebook-domain filtering; legal-first retrieval has its
        # own test coverage and remains enabled in production.
        mock_legal_search.return_value = []
        # Isolate notebook-domain filtering; legal-first retrieval has separate coverage.
        mock_legal_search.return_value = []
        # Mock raw search results containing mixed domains
        mock_text_search.return_value = [
            {"id": "source:1", "title": "Khai sinh", "domain": "ho_tich"},
            {"id": "source:2", "title": "Đất đai", "domain": "dat_dai"},
            {"id": "source:3", "title": "Chứng thực", "domain": "chung_thuc"}
        ]
        
        headers = {
            "X-User-Role": "officer",
            "X-User-Id": "user_account:officer1"
        }
        
        response = client.post(
            "/api/search",
            json={"query": "kết hôn", "type": "text", "limit": 10},
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        results = data["results"]
        
        # Should only retain "ho_tich" and "chung_thuc" results
        assert len(results) == 2
        assert results[0]["id"] == "source:1"
        assert results[1]["id"] == "source:3"

    @pytest.mark.asyncio
    @patch("api.user_service.get_user_profile", new_callable=AsyncMock)
    async def test_officer_ask_forbidden_domain(self, mock_get_profile, client):
        """Officer ask on a forbidden domain should return 403 Forbidden."""
        mock_get_profile.return_value = {
            "department": "Tư pháp - Hộ tịch",
            "allowed_domains": ["ho_tich", "chung_thuc"]
        }
        
        headers = {
            "X-User-Role": "officer",
            "X-User-Id": "user_account:officer1"
        }
        
        response = client.post(
            "/api/search/ask/simple",
            json={"question": "Mua đất thế nào?", "role": "officer", "domain": "dat_dai"},
            headers=headers
        )
        
        assert response.status_code == 403
        assert "không có quyền truy cập lĩnh vực này" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("api.user_service.get_user_profile", new_callable=AsyncMock)
    @patch("api.routers.search._ask_local", new_callable=AsyncMock)
    async def test_officer_ask_allowed_domain(self, mock_ask_local, mock_get_profile, client):
        """Officer ask on an allowed domain should proceed successfully."""
        mock_get_profile.return_value = {
            "department": "Tư pháp - Hộ tịch",
            "allowed_domains": ["ho_tich", "chung_thuc"]
        }
        
        from api.models import AskResponse
        mock_ask_local.return_value = AskResponse(
            question="Khai sinh thế nào?",
            answer="Hộ tịch ok",
            sources=[],
            rag_trace={}
        )
        
        headers = {
            "X-User-Role": "officer",
            "X-User-Id": "user_account:officer1"
        }
        
        response = client.post(
            "/api/search/ask/simple",
            json={"question": "Khai sinh thế nào?", "role": "officer", "domain": "ho_tich", "offline_mode": True},
            headers=headers
        )
        
        assert response.status_code == 200

    @pytest.mark.asyncio
    @patch("api.user_service.get_user_profile", new_callable=AsyncMock)
    async def test_officer_import_forbidden_domain(self, mock_get_profile, client):
        """Officer importing source with forbidden domain should return 403."""
        mock_get_profile.return_value = {
            "department": "Tư pháp - Hộ tịch",
            "allowed_domains": ["ho_tich", "chung_thuc"]
        }
        
        headers = {
            "X-User-Role": "officer",
            "X-User-Id": "user_account:officer1"
        }
        
        # Test JSON import endpoint
        response = client.post(
            "/api/sources/json",
            json={
                "type": "text",
                "content": "Luật Đất Đai...",
                "title": "Tài liệu đất đai",
                "domain": "dat_dai"
            },
            headers=headers
        )
        
        assert response.status_code == 403
        assert "không có quyền import tài liệu ngoài lĩnh vực" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("api.user_service.get_user_profile", new_callable=AsyncMock)
    @patch("api.routers.sources.Notebook.get", new_callable=AsyncMock)
    @patch("api.routers.sources.CommandService.submit_command_job", new_callable=AsyncMock)
    async def test_officer_import_allowed_domain(self, mock_submit, mock_nb_get, mock_get_profile, client):
        """Officer importing source with allowed domain should succeed."""
        mock_get_profile.return_value = {
            "department": "Tư pháp - Hộ tịch",
            "allowed_domains": ["ho_tich", "chung_thuc"]
        }
        mock_nb_get.return_value = MagicMock()
        mock_submit.return_value = "command:123"
        
        headers = {
            "X-User-Role": "officer",
            "X-User-Id": "user_account:officer1"
        }
        
        response = client.post(
            "/api/sources/json",
            json={
                "type": "text",
                "content": "Hướng dẫn đăng ký kết hôn...",
                "title": "Tài liệu kết hôn",
                "domain": "ho_tich",
                "async_processing": True
            },
            headers=headers
        )
        
        assert response.status_code == 200

    @pytest.mark.asyncio
    @patch("api.routers.search._search_legal_documents", new_callable=AsyncMock)
    @patch("api.routers.search.text_search", new_callable=AsyncMock)
    async def test_admin_search_not_filtered(self, mock_text_search, mock_legal_search, client):
        """Admin search should not filter search results."""
        # Isolate notebook visibility from legal-first retrieval.
        mock_legal_search.return_value = []
        # Isolate notebook visibility; legal-first retrieval remains enabled in production.
        mock_legal_search.return_value = []
        mock_text_search.return_value = [
            {"id": "source:1", "title": "Khai sinh", "domain": "ho_tich"},
            {"id": "source:2", "title": "Đất đai", "domain": "dat_dai"}
        ]
        
        headers = {
            "X-User-Role": "admin"
        }
        
        response = client.post(
            "/api/search",
            json={"query": "tất cả", "type": "text", "limit": 10},
            headers=headers
        )
        
        assert response.status_code == 200
        data = response.json()
        results = data["results"]
        # Admin gets everything
        assert len(results) == 2
