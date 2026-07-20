import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from api.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMS_MANIFEST_PATH = PROJECT_ROOT / "notebook_data" / "forms" / "forms_manifest.json"

@pytest.fixture(autouse=True)
def bypass_auth():
    async def mock_has_real_users():
        return False

    with patch("api.auth.has_real_users", new=mock_has_real_users), \
         patch("api.auth.configured_role_passwords", return_value={}):
        yield

@pytest.fixture
def client():
    return TestClient(app)

def test_manifest_file_exists():
    """Verify that forms_manifest.json exists in the correct path."""
    assert FORMS_MANIFEST_PATH.exists(), f"forms_manifest.json does not exist at {FORMS_MANIFEST_PATH}"

def test_manifest_forms_have_required_metadata():
    """Verify that all forms in the manifest contain the required standardized metadata fields."""
    with open(FORMS_MANIFEST_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    assert "forms" in data, "forms key missing in manifest"
    forms_dict = data["forms"]
    
    required_keys = {
        "official_level",
        "review_status",
        "source_url",
        "department",
        "ward_scope",
        "version"
    }
    
    total_checked = 0
    for domain, forms in forms_dict.items():
        if not forms:
            continue
        for form in forms:
            form_id = form.get("id")
            for key in required_keys:
                assert key in form, f"Required metadata key '{key}' missing in form ID {form_id} (domain: {domain})"
            
            # Validate default levels
            assert form["official_level"] in ("reference", "official"), f"Invalid official_level value for form ID {form_id}"
            assert form["review_status"] in ("candidate_pending_review", "approved"), f"Invalid review_status value for form ID {form_id}"
            assert isinstance(form["ward_scope"], bool), f"ward_scope must be a boolean for form ID {form_id}"
            total_checked += 1
            
    assert total_checked > 0, "No forms were checked in the manifest"
    print(f"\n[INFO] Validated metadata for all {total_checked} forms in manifest.")

def test_forms_catalog_api_returns_metadata(client):
    """Verify that the API endpoint returns the new standardized metadata fields for each record."""
    response = client.get("/api/procedures/forms-catalog/status?limit=5")
    assert response.status_code == 200
    
    data = response.json()
    assert "records" in data
    records = data["records"]
    
    required_keys = {
        "official_level",
        "review_status",
        "source_url",
        "department",
        "ward_scope",
        "version"
    }
    
    # Check if records contain the keys
    for record in records:
        for key in required_keys:
            assert key in record, f"API record missing required metadata key '{key}'"
            
    print(f"\n[INFO] Verified API response returns metadata fields for {len(records)} records.")
