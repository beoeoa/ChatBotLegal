from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _request(role="admin", user_id="user:admin"):
    return SimpleNamespace(state=SimpleNamespace(role=role, user_id=user_id))


def _rows():
    return [
        {
            "id": "audit:3", "actor_user": "user:admin", "actor_role": "admin",
            "action": "admin.legal.import", "entity_type": "legal_document",
            "entity_id": "doc-3", "details": {"result": "success", "reason": "Bổ sung văn bản"},
            "ip_address": "127.0.0.1", "user_agent": "secret-agent",
            "created": "2026-08-13T10:00:00Z",
        },
        {
            "id": "audit:2", "actor_user": "user:officer", "actor_role": "officer",
            "action": "support.ticket.assign_failed", "entity_type": "support_ticket",
            "entity_id": "ticket-2", "details": {"result": "failed", "private_note": "do not list"},
            "ip_address": "10.0.0.2", "user_agent": "private",
            "created": "2026-08-13T09:00:00Z",
        },
        {
            "id": "audit:1", "actor_user": "user:admin", "actor_role": "admin",
            "action": "admin.model.configure", "entity_type": "credential",
            "entity_id": "credential-1", "details": {"api_key": "must-not-leak", "result": "success"},
            "ip_address": "127.0.0.1", "user_agent": "private",
            "created": "2026-08-12T09:00:00Z",
        },
    ]


def test_retention_event_is_presented_in_plain_vietnamese():
    from api.routers import admin_activity

    item = admin_activity._project({
        "id": "audit:retention",
        "actor_user": "system",
        "actor_role": "system",
        "action": "retention.purge.completed",
        "entity_type": "retention_job",
        "entity_id": "scheduled",
        "details": {"result": "completed"},
        "created": "2026-08-16T06:37:37Z",
    })

    assert item["actor_label"] == "Hệ thống"
    assert item["action_label"] == "Đã hoàn tất xóa dữ liệu hết hạn"
    assert item["result"] == "success"


@pytest.mark.asyncio
async def test_activity_filters_role_module_result_actor_and_projects_content_free(monkeypatch):
    from api.routers import admin_activity

    monkeypatch.setattr(admin_activity, "get_request_role", lambda request: request.state.role)
    monkeypatch.setattr(admin_activity, "get_request_user_id", lambda request: request.state.user_id)
    monkeypatch.setattr(admin_activity, "list_audit_logs", lambda **kwargs: _async(_rows()))

    response = await admin_activity.list_activity(
        _request(), role="officer", module="support", result="failed",
        actor="user:officer", date_from=None, date_to=None, cursor=None, limit=20,
    )

    assert response["total"] == 1
    item = response["items"][0]
    assert item["module"] == "support"
    assert item["activity_type"] == "support"
    assert item["result"] == "failed"
    assert item["actor_role"] == "officer"
    assert item["actor_label"] == "Cán bộ"
    assert item["action_label"] == "Phân công hỗ trợ thất bại"
    assert "details" not in item
    assert "ip_address" not in item
    assert "user_agent" not in item


@pytest.mark.asyncio
async def test_sensitive_view_requires_reason_redacts_secrets_and_writes_audit(monkeypatch):
    from api.routers import admin_activity

    audits = []
    monkeypatch.setattr(admin_activity, "get_request_role", lambda request: request.state.role)
    monkeypatch.setattr(admin_activity, "get_request_user_id", lambda request: request.state.user_id)
    monkeypatch.setattr(admin_activity, "list_audit_logs", lambda **kwargs: _async(_rows()))
    monkeypatch.setattr(admin_activity, "write_audit_log", lambda **kwargs: _capture(audits, kwargs))

    with pytest.raises(HTTPException) as missing:
        await admin_activity.view_sensitive_activity(
            "audit:1", admin_activity.SensitiveViewRequest(reason="ngắn"), _request()
        )
    assert missing.value.status_code == 400

    with pytest.raises(HTTPException) as meaningless:
        await admin_activity.view_sensitive_activity(
            "audit:1",
            admin_activity.SensitiveViewRequest(reason="dddddddddddddddd"),
            _request(),
        )
    assert meaningless.value.status_code == 400
    assert meaningless.value.detail == "ACTIVITY_REASON_NOT_MEANINGFUL"

    detail = await admin_activity.view_sensitive_activity(
        "audit:1",
        admin_activity.SensitiveViewRequest(reason="Rà soát cấu hình model theo yêu cầu"),
        _request(),
    )
    assert detail["details"]["api_key"] == "[REDACTED]"
    assert detail["ip_address"] == "[REDACTED]"
    assert audits[-1]["action"] == "admin.activity.sensitive_view"
    assert audits[-1]["details"]["reason"] == "Rà soát cấu hình model theo yêu cầu"


@pytest.mark.asyncio
async def test_activity_search_and_business_type_use_vietnamese_projection(monkeypatch):
    from api.routers import admin_activity

    monkeypatch.setattr(admin_activity, "get_request_role", lambda request: request.state.role)
    monkeypatch.setattr(admin_activity, "get_request_user_id", lambda request: request.state.user_id)
    monkeypatch.setattr(admin_activity, "list_audit_logs", lambda **kwargs: _async(_rows()))

    response = await admin_activity.list_activity(
        _request(), role=None, module=None, result=None, actor=None,
        search="nhập văn bản", activity_type="data_ingestion",
        date_from=None, date_to=None, cursor=None, limit=20,
    )

    assert response["total"] == 1
    item = response["items"][0]
    assert item["action"] == "admin.legal.import"
    assert item["action_label"] == "Đã nhập văn bản pháp luật"
    assert item["resource_label"] == "Văn bản pháp luật"


async def _async(value):
    return value


async def _capture(target, value):
    target.append(value)
