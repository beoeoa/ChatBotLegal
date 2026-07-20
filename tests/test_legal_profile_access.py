"""Policy tests for Bước 5 legal-profile visibility and sensitive-access auditing."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import api.legal_profile_access as access


def request_for(role: str, user_id: str | None = "user-1", *, reason: str | None = None) -> Request:
    headers = [(b"x-user-role", role.encode())]
    if reason:
        headers.append((b"x-business-reason", reason.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/notebooks/nb-1",
        "headers": headers,
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
    }
    req = Request(scope)
    req.state.user_role = role
    req.state.user_id = user_id
    req.state.username = role
    return req


@pytest.fixture
def record_query(monkeypatch):
    records = {
        "nb-1": {"id": "notebook:nb-1", "owner_user": "user_account:user-1", "ownership_status": "resolved"},
        "nb-2": {"id": "notebook:nb-2", "owner_user": "user_account:user-2", "ownership_status": "resolved"},
        "src-1": {"id": "source:src-1", "owner_user": "user_account:user-1", "ownership_status": "resolved"},
    }

    async def fake_query(query, params=None):
        resource = (params or {}).get("resource_id")
        if resource is not None:
            key = str(resource)
            # RecordID may stringify as table:id or table:⟨id⟩.
            clean = key.split(":", 1)[-1].replace("\u27e8", "").replace("\u27e9", "").replace("⟨", "").replace("⟩", "")
            return [records[clean]] if clean in records else []
        if "SELECT VALUE out FROM reference" in query:
            return []
        return []

    monkeypatch.setattr(access, "repo_query", fake_query)
    return records


@pytest.mark.asyncio
async def test_citizen_is_denied_notebook_api(record_query):
    with pytest.raises(HTTPException) as exc:
        await access.assert_notebook_access("nb-1", request_for("citizen"))
    assert exc.value.status_code == 403
    assert "Hồ sơ pháp lý" in exc.value.detail


@pytest.mark.asyncio
async def test_officer_owner_allowed_and_unowned_denied(record_query):
    await access.assert_notebook_access("nb-1", request_for("officer", "user-1"))
    with pytest.raises(HTTPException) as exc:
        await access.assert_notebook_access("nb-2", request_for("officer", "user-1"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_explicit_share_does_not_bypass_account_isolation(record_query, monkeypatch, tmp_path):
    (tmp_path / "ticket-1.json").write_text(
        json.dumps({"assigned_officer_id": "user-1", "shared_notebook_ids": ["nb-2"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(access, "SUPPORT_TICKETS_DIR", str(tmp_path))
    with pytest.raises(HTTPException):
        await access.assert_notebook_access("nb-2", request_for("officer", "user-1"))


@pytest.mark.asyncio
async def test_admin_can_only_open_own_notebook(record_query, monkeypatch):
    created = []

    async def fake_create(table, payload):
        created.append((table, payload))
        return {"id": "sensitive_access_audit:test"}

    monkeypatch.setattr(access, "repo_create", fake_create)
    await access.assert_notebook_access(
        "nb-1", request_for("admin", "user-1"), action="view"
    )
    with pytest.raises(HTTPException) as exc:
        await access.assert_notebook_access("nb-2", request_for("admin", "user-1"), action="view")
    assert exc.value.status_code == 403
    assert created == []


@pytest.mark.asyncio
async def test_admin_can_download_only_own_source(record_query, monkeypatch):
    created = []

    async def fake_create(table, payload):
        created.append(payload)
        return {"id": "sensitive_access_audit:test"}

    monkeypatch.setattr(access, "repo_create", fake_create)
    await access.assert_source_access(
        "src-1", request_for("admin", "user-1"), action="download"
    )
    with pytest.raises(HTTPException):
        await access.assert_source_access("src-1", request_for("admin", "user-2"), action="download")
    assert created == []
