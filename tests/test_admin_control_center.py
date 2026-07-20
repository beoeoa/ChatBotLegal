import asyncio

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


@pytest.fixture
def control_module(monkeypatch, tmp_path):
    from api.routers import admin_control, live_support

    monkeypatch.setattr(live_support, "TICKETS_DIR", tmp_path / "tickets")
    monkeypatch.setattr(live_support, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(admin_control, "LEGAL_CASES_DIR", tmp_path / "cases")
    return admin_control, live_support


def req(role="admin", user_id="user:admin"):
    scope = {"type": "http", "method": "GET", "path": "/api/admin/control", "headers": []}
    request = Request(scope)
    request.state.user_role = role
    request.state.user_id = user_id
    request.state.username = "admin"
    return request


@pytest.mark.asyncio
async def test_sensitive_detail_requires_reason_and_audits(control_module, monkeypatch):
    control, support = control_module
    audit = []

    async def write_audit(**kwargs):
        audit.append(kwargs)

    monkeypatch.setattr(control, "write_audit_log", write_audit)
    support._save_ticket("ticket-1", {
        "id": "ticket-1", "citizen_id": "citizen-1", "domain": "ho_tich_chung_thuc",
        "question": "private question", "status": "waiting", "messages": [], "created_at": "2026-01-01T00:00:00+00:00",
    })

    with pytest.raises(HTTPException) as missing:
        await control.support_detail("ticket-1", req(), reason="")
    assert missing.value.status_code == 400

    detail = await control.support_detail("ticket-1", req(), reason="R? so?t nghi?p v?")
    assert detail["question"] == "private question"
    assert audit[-1]["action"] == "admin.support.detail.view"
    assert audit[-1]["details"]["reason"] == "R? so?t nghi?p v?"


@pytest.mark.asyncio
async def test_permission_boundary_rejects_officer(control_module):
    control, _ = control_module
    with pytest.raises(HTTPException) as denied:
        await control.overview(req(role="officer", user_id="user:officer"))
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_audit_endpoint_is_read_only_and_filters(control_module, monkeypatch):
    control, _ = control_module
    captured = {}

    async def fake_list_audit_logs(**kwargs):
        captured.update(kwargs)
        return [{"id": "audit:1", "action": "support.chat.view"}]

    monkeypatch.setattr(control, "list_audit_logs", fake_list_audit_logs)
    rows = await control.audit_overview(
        req(), actor_role="admin", resource_type="support_session", resource_id="ticket-1",
        action="support.chat.view", reason="R? so?t", date_from=None, date_to=None, limit=20,
    )
    assert rows[0]["id"] == "audit:1"
    assert captured["resource_type"] == "support_session"
    assert captured["resource_id"] == "ticket-1"
    assert captured["reason"] == "R? so?t"
    # Router deliberately exposes GET only: there is no mutable audit handler.
    assert not any(route.path.endswith("/audit") and "POST" in route.methods for route in control.router.routes)


@pytest.mark.asyncio
async def test_overview_exposes_counters_and_sla(control_module, monkeypatch):
    control, support = control_module
    support._save_ticket("old-ticket", {
        "id": "old-ticket", "citizen_id": "citizen-1", "domain": "trat_tu_do_thi", "status": "waiting",
        "messages": [], "created_at": "2020-01-01T00:00:00+00:00", "updated_at": "2020-01-01T00:00:00+00:00",
    })

    async def users(): return [{"id": "user:admin", "role": "admin", "profile": {}}]
    async def candidates(limit=500): return [{"status": "pending"}, {"status": "pending"}, {"status": "pending"}]
    async def fake_query(*_args, **_kwargs): return [{"count": 2}]
    monkeypatch.setattr(control, "list_users_with_profiles", users)
    monkeypatch.setattr(control.LegalCrawlService, "list_candidates", candidates)
    monkeypatch.setattr(control, "repo_query", fake_query)

    data = await control.overview(req())
    assert data["live_support"]["waiting"] == 1
    assert data["live_support"]["sla_overdue"] == 1
    assert data["quality"]["insufficient_evidence"] == 2
