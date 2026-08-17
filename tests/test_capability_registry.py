from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException, Request

from api.capability_registry import (
    CapabilityRegistryError,
    discover_api_routers,
    discover_frontend_routes,
    load_registry,
    reconcile_registry,
    validate_registry_payload,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "system-capabilities.json"


def test_registry_has_unique_complete_release_controls():
    records = load_registry(REGISTRY)
    assert records
    ids = [record.capability_id for record in records]
    assert len(ids) == len(set(ids))
    for record in records:
        assert record.owner.strip()
        assert record.roles
        assert record.data_source.strip()
        assert record.audit.strip()
        assert record.retention.strip()
        assert record.sla.strip()
        assert record.tests
        assert record.rollback.strip()


def test_registry_covers_every_frontend_page_and_registered_api_router():
    records = load_registry(REGISTRY)
    result = reconcile_registry(records, root=ROOT)
    assert result.missing_frontend_routes == []
    assert result.missing_api_routers == []
    assert result.duplicate_paths == []


def test_discovery_normalizes_dynamic_and_grouped_frontend_routes():
    routes = discover_frontend_routes(ROOT)
    assert "/login" in routes
    assert "/search" in routes
    assert "/legal-documents/[id]" in routes
    assert "/notebooks/[id]" in routes
    assert all("(dashboard)" not in route for route in routes)


def test_discovery_reads_only_routers_mounted_by_api_main():
    routers = discover_api_routers(ROOT)
    assert "auth" in routers
    assert "search" in routers
    assert "legal_search" in routers
    assert "procedure_forms_catalog" in routers
    assert "podcasts" not in routers


def test_active_or_public_capability_cannot_omit_tests_or_rollback():
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    broken = json.loads(json.dumps(payload))
    broken["capabilities"][0]["tests"] = []
    broken["capabilities"][0]["rollback"] = ""
    with pytest.raises(CapabilityRegistryError):
        validate_registry_payload(broken)


def test_disabled_capability_is_explicit_and_not_public():
    records = load_registry(REGISTRY)
    disabled = [record for record in records if record.status == "disabled"]
    assert disabled
    assert all("public" not in record.roles for record in disabled)


def _request(role: str) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/api/admin/capabilities", "headers": []})
    request.state.user_role = role
    request.state.user_id = f"user:{role}"
    return request


@pytest.mark.asyncio
async def test_capability_admin_api_is_read_only_and_role_protected():
    from api.routers import admin_capabilities

    payload = await admin_capabilities.list_capabilities(_request("admin"))
    assert payload["summary"]["reconciliation"]["status"] == "passed"
    assert payload["capabilities"]

    with pytest.raises(HTTPException) as denied:
        await admin_capabilities.list_capabilities(_request("officer"))
    assert denied.value.status_code == 403


def test_release_audit_command_fails_closed_for_unregistered_route(tmp_path: Path):
    from scripts.audit_capability_registry import audit

    fake_root = tmp_path / "repo"
    page = fake_root / "frontend" / "src" / "app" / "missing" / "page.tsx"
    page.parent.mkdir(parents=True)
    page.write_text("export default function Page() { return null }", encoding="utf-8")
    (fake_root / "api").mkdir()
    (fake_root / "api" / "main.py").write_text("", encoding="utf-8")

    result = audit(registry_path=REGISTRY, root=fake_root)
    assert result["status"] == "failed"
    assert result["reconciliation"]["missing_frontend_routes"] == ["/missing"]
