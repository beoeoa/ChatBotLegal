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
    monkeypatch.setattr(admin_control, "_dashboard_cache", None)
    monkeypatch.setattr(admin_control, "_dashboard_cache_created_at", 0.0)
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
        await control.system_health(req(role="officer", user_id="user:officer"))
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_dashboard_is_admin_only_and_never_projects_secrets(control_module, monkeypatch):
    control, support = control_module
    from api.routers import legal_search

    async def fake_health():
        return {"components": {"api": {"status": "healthy"}}}

    async def fake_legal_summary(_request):
        return {"documents": {"total": 12}, "vectors": {"status": "available"}}

    async def fake_crawl_summary():
        return {"pending": 2, "import_queue": {"failed": 0}}

    async def fake_users():
        return [
            {
                "id": "user:admin",
                "role": "admin",
                "is_active": True,
                "password_hash": "must-not-leak",
                "profile": {"must_change_password": False},
            }
        ]

    async def fake_models():
        return {
            "credential_count": 1,
            "model_count": 2,
            "providers": ["ollama"],
            "defaults": {"default_chat_model": "model:qwen"},
            "ready_for_answers": True,
        }

    async def fake_audits(**_kwargs):
        return [{
            "id": "audit:1",
            "action": "admin.config.update",
            "entity_type": "credential",
            "actor_role": "admin",
            "created": "2026-08-11T00:00:00Z",
            "details": {"api_key": "must-not-leak", "reason": "private"},
        }]

    monkeypatch.setattr(control, "_system_health", fake_health)
    monkeypatch.setattr(legal_search, "legal_management_summary", fake_legal_summary)
    monkeypatch.setattr(control.LegalCrawlService, "summary", fake_crawl_summary)
    monkeypatch.setattr(control, "list_users_with_profiles", fake_users)
    monkeypatch.setattr(control, "_model_configuration_summary", fake_models)
    monkeypatch.setattr(control, "list_audit_logs", fake_audits)
    monkeypatch.setattr(support, "_list_all_tickets", lambda: [])
    monkeypatch.setattr(control, "_legal_case_metadata", lambda: [])
    monkeypatch.setattr(control.telemetry, "summary", lambda: {"errors": 0})

    payload = await control.dashboard_overview(req())
    serialized = str(payload).lower()
    assert payload["users"]["total"] == 1
    assert payload["models"]["ready_for_answers"] is True
    assert payload["recent_audit"][0]["action"] == "admin.config.update"
    assert "password_hash" not in serialized
    assert "api_key" not in serialized
    assert "must-not-leak" not in serialized
    assert "private" not in serialized

    with pytest.raises(HTTPException) as denied:
        await control.dashboard_overview(req(role="officer", user_id="user:officer"))
    assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_dashboard_section_timeout_is_explicit_and_content_free(control_module):
    control, _ = control_module

    async def slow_read():
        await asyncio.sleep(0.05)
        return {"secret": "must-not-leak"}

    result = await control._dashboard_read(slow_read(), timeout_seconds=0.001)

    assert result == {"status": "unavailable", "reason_code": "timeout"}
    assert "secret" not in str(result)


def test_dashboard_attention_rules_are_server_owned_and_prioritized(control_module):
    control, _ = control_module
    items = control._build_attention_items(
        observed_at="2026-08-11T03:00:00+00:00",
        health={
            "status": "degraded",
            "components": {
                "database": {"status": "unavailable"},
                "crawler": {"status": "degraded"},
            },
        },
        legal={
            "status": "available",
            "documents": {"missing_source": 2, "unknown_status": 3},
            "structure": {"zero_chunk_documents": 1},
        },
        crawl={
            "status": "available",
            "pending_document_candidates": 4,
            "unverified_imported_candidates": 2,
            "import_queue": {"failed": 1, "queued": 2},
        },
        knowledge={
            "status": "available",
            "forms": {"pending": 5},
            "faqs": {"by_status": {"draft": 3}},
            "ocr_failures": 1,
        },
        users={"status": "available", "locked": 2, "must_change_password": 1},
        support={"status": "available", "overdue": 1, "unassigned": 2},
        models={"status": "available", "ready_for_answers": False},
    )

    codes = {item["code"] for item in items}
    assert {
        "health.database.unavailable",
        "import.failed",
        "candidate.pending",
        "legal.missing_source",
        "legal.zero_chunks",
        "forms.pending",
        "users.locked",
        "support.overdue",
        "models.not_ready",
    } <= codes
    severities = [item["severity"] for item in items]
    assert severities == sorted(
        severities, key={"critical": 0, "warning": 1, "info": 2}.get
    )
    assert next(item for item in items if item["code"] == "users.locked")["href"] == "/users?status=locked"


def test_dashboard_audit_groups_seven_local_days(control_module):
    control, _ = control_module
    observed = control.datetime.fromisoformat("2026-08-11T02:00:00+00:00")
    rows = [
        {"created": "2026-08-10T17:30:00Z", "action": "user.lock"},
        {"created": "2026-08-10T16:30:00Z", "action": "admin.legal.import"},
    ]

    summary = control._audit_seven_day_summary(rows, observed_at=observed)

    by_date = {item["date"]: item["count"] for item in summary["daily"]}
    assert by_date["2026-08-11"] == 1
    assert by_date["2026-08-10"] == 1
    assert summary["by_group"] == {"accounts": 1, "legal_data": 1}
    assert len(summary["daily"]) == 7


@pytest.mark.asyncio
async def test_dashboard_cache_avoids_duplicate_read_and_force_refreshes(control_module, monkeypatch):
    control, _ = control_module
    calls = 0

    async def build(_request):
        nonlocal calls
        calls += 1
        return {
            "observed_at": f"2026-08-11T00:00:0{calls}+00:00",
            "freshness": {"generated_at": "2026-08-11T00:00:00+00:00", "cache_ttl_seconds": 30, "cached": False},
            "attention_items": [],
            **{
                key: {"status": "available", "observed_at": "2026-08-11T00:00:00+00:00"}
                for key in ("health", "legal_repository", "crawl_import", "knowledge", "users", "support", "models", "runtime_metrics", "audit_7d")
            },
            "recent_audit": [],
            "legal_cases": {"total": 0, "open": 0},
            "metrics": {},
        }

    monkeypatch.setattr(control, "_build_dashboard_snapshot", build)

    first = await control.dashboard_overview(req())
    second = await control.dashboard_overview(req())
    refreshed = await control.dashboard_overview(req(), force_refresh=True)

    assert calls == 2
    assert first["freshness"]["cached"] is False
    assert second["freshness"]["cached"] is True
    assert refreshed["observed_at"].endswith("02+00:00")


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
    assert not any(
        route.path.endswith("/audit") and "POST" in route.methods
        for route in control.router.routes
    )
