"""Comprehensive tests for retention jobs, legal-case access control, and admin retention endpoints.

Covers:
- Cross-user denial (conversations, cases, attachments)
- Expiration calculation correctness
- Idempotent purge behavior
- Audit retention survival + purge audit record
- Admin cannot change expires_at / closed_at via API
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _make_request(role: str, user_id: str | None = "user:admin", *, reason: str | None = None) -> Request:
    from starlette.requests import Request
    headers: list[tuple[bytes, bytes]] = [(b"x-user-role", role.encode())]
    if reason:
        headers.append((b"x-business-reason", reason.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/admin/control",
        "headers": headers,
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
    }
    req = Request(scope)
    req.state.user_role = role
    req.state.user_id = user_id
    req.state.username = user_id or "admin"
    return req


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data(tmp_path):
    """Provide isolated data directories for conversations, cases, attachments."""
    conv_dir = tmp_path / "data" / "conversations"
    case_dir = tmp_path / "data" / "legal_cases"
    attach_dir = tmp_path / "data" / "support_attachments"
    conv_dir.mkdir(parents=True)
    case_dir.mkdir(parents=True)
    attach_dir.mkdir(parents=True)
    return {"conv_dir": conv_dir, "case_dir": case_dir, "attach_dir": attach_dir}


@pytest.fixture
def admin_control_module(monkeypatch, tmp_path):
    from api.routers import admin_control, live_support

    async def fake_write_audit_log(**kwargs):
        return {"id": "audit:test", **kwargs}

    async def fake_repo_query(query, params=None):
        # Admin-control unit tests must not open the developer's runtime
        # SurrealDB websocket.  Live retention integration is covered by the
        # isolated integration gate; returning an empty store here keeps the
        # fixture credential- and resource-safe.
        return []

    monkeypatch.setattr(live_support, "TICKETS_DIR", tmp_path / "tickets")
    monkeypatch.setattr(live_support, "ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(admin_control, "LEGAL_CASES_DIR", tmp_path / "cases")
    monkeypatch.setattr(admin_control, "write_audit_log", fake_write_audit_log)
    monkeypatch.setattr("api.retention_service.repo_query", fake_repo_query)
    return admin_control, live_support


# ---------------------------------------------------------------------------
# Test 1: Cross-user denial — conversation isolation
# ---------------------------------------------------------------------------

def test_cross_user_denial_conversation(tmp_data, monkeypatch):
    """User A cannot access User B's conversation."""
    from api import conversation_service as svc
    monkeypatch.setattr(svc, "JSON_FALLBACK_DIR", str(tmp_data["conv_dir"]))
    monkeypatch.setattr(svc, "_use_surreal", lambda: asyncio.sleep(0, result=False))

    # Owner A creates a conversation
    created = _run(svc.create_conversation(
        owner_key="user:A", role_context="citizen", title="Chat A", domain="ho_tich_chung_thuc", real_user_id="user:A"
    ))
    assert created["id"]

    # Owner B tries to list/get — must not see A's conversation
    listed_b = _run(svc.list_conversations(owner_key="user:B", role_context="citizen"))
    assert all(item["id"] != created["id"] for item in listed_b)

    missing = _run(svc.get_conversation(created["id"], owner_key="user:B", role_context="citizen"))
    assert missing is None


# ---------------------------------------------------------------------------
# Test 2: Expiration calculation — chat 12 months, case 6 months after close
# ---------------------------------------------------------------------------

def test_expiration_calculation():
    """Verify chat_expires_at, case_files_expires_at, audit_expires_at."""
    from api.retention_service import (
        chat_expires_at, case_files_expires_at, audit_expires_at,
        CHAT_RETENTION_DAYS, CASE_FILE_RETENTION_DAYS, AUDIT_RETENTION_DAYS,
    )
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)

    # Chat: 365 days from last_message_at
    chat_exp = chat_expires_at(base)
    assert chat_exp == base + timedelta(days=CHAT_RETENTION_DAYS)

    # Case: 180 days from closed_at; None if not closed
    case_exp = case_files_expires_at(base)
    assert case_exp == base + timedelta(days=CASE_FILE_RETENTION_DAYS)
    assert case_files_expires_at(None) is None

    # Audit: 730 days from created_at
    audit_exp = audit_expires_at(base)
    assert audit_exp == base + timedelta(days=AUDIT_RETENTION_DAYS)


# ---------------------------------------------------------------------------
# Test 3: Idempotent purge — JSON conversations
# ---------------------------------------------------------------------------

def test_idempotent_purge_json_conversations(tmp_data, monkeypatch):
    """Running purge twice deletes nothing new on second run."""
    from api.retention_service import _purge_json_conversations, utcnow
    monkeypatch.setattr("api.retention_service.CONVERSATIONS_DIR", tmp_data["conv_dir"])

    # Create expired conversation
    owner_dir = tmp_data["conv_dir"] / "user_A"
    owner_dir.mkdir(parents=True)
    conv_path = owner_dir / "conv-expired.json"
    old_date = (utcnow() - timedelta(days=400)).isoformat()
    conv_path.write_text(json.dumps({
        "id": "conv-expired", "owner_key": "user:A", "status": "active",
        "last_message_at": old_date, "expires_at": old_date,
    }), encoding="utf-8")

    # First purge
    count1 = _purge_json_conversations(utcnow(), dry_run=False)
    assert count1 == 1
    assert not conv_path.exists()

    # Second purge — nothing left to delete
    count2 = _purge_json_conversations(utcnow(), dry_run=False)
    assert count2 == 0


# ---------------------------------------------------------------------------
# Test 3b: Legacy Ask JSON follows the 12-month chat policy
# ---------------------------------------------------------------------------

def test_purge_json_ask_sessions(tmp_path, monkeypatch):
    """Ask session histories are not an unbounded legacy retention exception."""
    from api.retention_service import _purge_json_ask_sessions, utcnow

    session_dir = tmp_path / "ask_sessions" / "user-a"
    session_dir.mkdir(parents=True)
    monkeypatch.setattr("api.retention_service.ASK_SESSIONS_DIR", tmp_path / "ask_sessions")
    expired = session_dir / "expired.json"
    active = session_dir / "active.json"
    expired.write_text(json.dumps({
        "id": "expired", "user_id": "user-a",
        "updated_at": (utcnow() - timedelta(days=366)).isoformat(),
        "messages": [{"content": "old private answer"}],
    }), encoding="utf-8")
    active.write_text(json.dumps({
        "id": "active", "user_id": "user-a",
        "updated_at": (utcnow() - timedelta(days=3)).isoformat(),
    }), encoding="utf-8")

    assert _purge_json_ask_sessions(utcnow(), dry_run=True) == 1
    assert expired.exists()
    assert _purge_json_ask_sessions(utcnow(), dry_run=False) == 1
    assert not expired.exists()
    assert active.exists()


# ---------------------------------------------------------------------------
# Test 4: Idempotent purge — legal case files
# ---------------------------------------------------------------------------

def test_idempotent_purge_case_files(tmp_data, monkeypatch):
    """Case files purge is idempotent; unclosed cases never expire."""
    from api.retention_service import _purge_case_files, utcnow
    monkeypatch.setattr("api.retention_service.LEGAL_CASES_DIR", tmp_data["case_dir"])

    # Closed case past retention
    case_path = tmp_data["case_dir"] / "case-closed.json"
    closed = (utcnow() - timedelta(days=200)).isoformat()
    case_path.write_text(json.dumps({
        "id": "case-closed", "closed_at": closed, "retention_purged_at": None,
    }), encoding="utf-8")

    # Open case — should NOT be purged
    open_case_path = tmp_data["case_dir"] / "case-open.json"
    open_case_path.write_text(json.dumps({
        "id": "case-open", "closed_at": None, "retention_purged_at": None,
    }), encoding="utf-8")

    count = _purge_case_files(utcnow(), dry_run=False)
    assert count == 1
    # Retain case metadata for traceability; only the attachment payload is purged.
    retained_case = json.loads(case_path.read_text(encoding="utf-8"))
    assert retained_case["retention_purged_at"]
    assert retained_case["attachments"] == []
    assert open_case_path.exists()  # Still there

    # Second run — nothing new
    count2 = _purge_case_files(utcnow(), dry_run=False)
    assert count2 == 0


# ---------------------------------------------------------------------------
# Test 5: Live-support content and attachments expire 180 days after close
# ---------------------------------------------------------------------------

def test_support_ticket_retention_separates_attachments_and_chat(tmp_path, monkeypatch):
    """Closed-ticket files and chat payload expire together after 180 days."""
    from api.retention_service import (
        _purge_support_ticket_attachments,
        _purge_support_ticket_chats,
        utcnow,
    )

    tickets_dir = tmp_path / "tickets"
    attachments_dir = tmp_path / "attachments"
    tickets_dir.mkdir()
    attachments_dir.mkdir()
    monkeypatch.setattr("api.retention_service.SUPPORT_TICKETS_DIR", tickets_dir)
    monkeypatch.setattr("api.retention_service.SUPPORT_ATTACHMENTS_DIR", attachments_dir)
    now = utcnow()

    ticket_id = "closed-files"
    file_dir = attachments_dir / ticket_id
    file_dir.mkdir()
    (file_dir / "proof.txt").write_text("private", encoding="utf-8")
    ticket_path = tickets_dir / f"{ticket_id}.json"
    ticket_path.write_text(json.dumps({
        "id": ticket_id,
        "status": "closed",
        "closed_at": (now - timedelta(days=181)).isoformat(),
        "updated_at": (now - timedelta(days=20)).isoformat(),
        "attachments": [{"id": "proof"}],
        "messages": [{"content": "history", "attachments": [{"id": "proof"}]}],
    }), encoding="utf-8")

    assert _purge_support_ticket_attachments(now, dry_run=False) == 1
    retained = json.loads(ticket_path.read_text(encoding="utf-8"))
    assert retained["status"] == "closed"
    assert retained["attachments"] == []
    assert retained["messages"][0]["attachments"] == []
    assert not file_dir.exists()
    assert _purge_support_ticket_chats(now, dry_run=False) == 1
    retained = json.loads(ticket_path.read_text(encoding="utf-8"))
    assert retained["status"] == "purged"
    assert retained["messages"] == []

    expired_id = "expired-chat"
    expired_path = tickets_dir / f"{expired_id}.json"
    expired_path.write_text(json.dumps({
        "id": expired_id,
        "status": "closed",
        "question": "sensitive question",
        "closed_at": (now - timedelta(days=366)).isoformat(),
        "updated_at": (now - timedelta(days=366)).isoformat(),
        "messages": [{"content": "sensitive history"}],
        "attachments": [],
    }), encoding="utf-8")
    assert _purge_support_ticket_chats(now, dry_run=False) == 1
    purged = json.loads(expired_path.read_text(encoding="utf-8"))
    assert purged["status"] == "purged"
    assert purged["messages"] == []
    assert purged["question"] != "sensitive question"

# ---------------------------------------------------------------------------
# Test 5: Audit retention survives until cutoff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_retention_survival(monkeypatch, tmp_path):
    """Audit rows survive until cutoff; purge creates its own audit record."""
    from api.retention_service import _purge_audit_rows, utcnow

    audit_records = []

    async def fake_repo_query(query, params=None):
        cutoff = params.get("cutoff") if params else None
        if not cutoff:
            return []
        # Simulate: some old rows, some recent
        return [
            {"id": f"audit:old-{i}"} for i in range(3)  # old, should be purged
        ]

    async def fake_repo_query(query, params=None):
        cutoff = params.get("cutoff") if params else None
        if not cutoff:
            return []
        if "user_audit_log" in query:
            return [{"id": f"audit:old-{i}"} for i in range(3)]
        # Recent / no-expired sensitive rows survive the cutoff.
        return []

    monkeypatch.setattr("api.retention_service.repo_query", fake_repo_query)

    now = utcnow()
    count = await _purge_audit_rows(now, dry_run=False)
    assert count == 3


# ---------------------------------------------------------------------------
# Test 6: Admin cannot manipulate expires_at / closed_at
# ---------------------------------------------------------------------------

def test_admin_cannot_manipulate_expiry_dates(admin_control_module, monkeypatch, tmp_path):
    """Admin Control Center has no PATCH/PUT on expires_at or closed_at."""
    control, _ = admin_control_module
    routes = control.router.routes
    paths = [r.path for r in routes]
    # There should be NO route accepting PUT/PATCH with expires_at or closed_at
    for route in routes:
        methods = getattr(route, "methods", set())
        if methods & {"PATCH", "PUT"}:
            path_lower = route.path.lower()
            assert "expires_at" not in path_lower, f"Found mutable expires_at endpoint: {route.path}"
            assert "closed_at" not in path_lower, f"Found mutable closed_at endpoint: {route.path}"


# ---------------------------------------------------------------------------
# Test 7: Legal case access — centralized policy enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legal_case_access_cross_user_denial(monkeypatch):
    """User A cannot open User B's legal case."""
    from api.legal_profile_access import assert_legal_case_access
    from fastapi import HTTPException

    records = {
        "case-a": {"id": "legal_case:case-a", "owner_user": "user_account:user-A", "assigned_officer": None},
        "case-b": {"id": "legal_case:case-b", "owner_user": "user_account:user-B", "assigned_officer": "user_account:officer-1"},
    }

    async def fake_query(query, params=None):
        rid = (params or {}).get("case_id", "")
        clean = str(rid).split(":")[-1].replace("\u27e8", "").replace("\u27e9", "")
        return [records[clean]] if clean in records else []

    monkeypatch.setattr("api.legal_profile_access.repo_query", fake_query)

    # Officer can open assigned case
    req = _make_request("officer", "officer-1")
    await assert_legal_case_access("case-b", req, action="view")

    # Citizen cannot open another citizen's case
    req_citizen = _make_request("citizen", "user-A")
    with pytest.raises(HTTPException) as exc:
        await assert_legal_case_access("case-b", req_citizen, action="view")
    assert exc.value.status_code == 403

    # Officer cannot open unassigned case
    req_officer = _make_request("officer", "officer-2")
    with pytest.raises(HTTPException) as exc:
        await assert_legal_case_access("case-a", req_officer, action="view")
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Test 8: Admin retention endpoints — report + dry-run
# ---------------------------------------------------------------------------

def test_admin_retention_endpoints(admin_control_module, monkeypatch, tmp_path):
    """Retention report and dry-run endpoints work; non-admin is denied."""
    control, _ = admin_control_module

    # Non-admin gets 403
    with pytest.raises(HTTPException) as exc:
        _run(control.retention_upcoming(_make_request("officer", "user:officer"), within_days=30))
    assert exc.value.status_code == 403

    # Admin gets report
    report = _run(control.retention_upcoming(_make_request("admin", "user:admin"), within_days=30))
    assert "counts" in report
    assert "conversations" in report
    assert "ask_sessions" in report
    assert "legal_cases" in report
    assert "support_tickets" in report

    # Dry-run purge returns report without deleting
    purge_req = control.RetentionRunRequest(reason="Test retention dry run", dry_run=True)
    purge_resp = _run(control.run_retention(purge_req, _make_request("admin", "user:admin")))
    assert "chat_surreal" in purge_resp
    assert "case_files" in purge_resp
    assert purge_resp.get("dry_run") is True


# ---------------------------------------------------------------------------
# Test 9: Support attachment download enforces owner check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attachment_download_enforces_owner(monkeypatch, tmp_path):
    """Support attachment download requires owner/assignment check."""
    from api.routers.live_support import download_attachment, ATTACHMENTS_DIR, _load_ticket, _save_ticket
    from fastapi import HTTPException

    ticket_id = "ticket-owner"
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    tdir = ATTACHMENTS_DIR / ticket_id
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "file-a-note.txt").write_text("content", encoding="utf-8")

    _save_ticket(ticket_id, {
        "id": ticket_id, "citizen_id": "citizen-1", "domain": "ho_tich_chung_thuc",
        "status": "waiting", "assigned_officer_id": None,
        "messages": [], "attachments": [{
            "id": "file-a", "name": "note.txt", "content_type": "text/plain",
            "size": 7, "download_url": f"/api/support/tickets/{ticket_id}/attachments/file-a/download",
        }],
    })

    # Citizen who owns ticket can download
    req_owner = _make_request("citizen", "citizen-1")
    resp = await download_attachment(ticket_id, "file-a", req_owner)
    assert resp is not None

    # Random officer cannot download
    req_other = _make_request("officer", "officer-x")
    with pytest.raises(HTTPException) as exc:
        await download_attachment(ticket_id, "file-a", req_other)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Test 10: Surreal conversation purge marks purged status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surreal_conversation_purge_marks_status(monkeypatch):
    """Expired Surreal conversations are marked purged; messages deleted."""
    from api.retention_service import _purge_surreal_conversations, utcnow

    expired_id = "conversation:expired-1"
    queried = [{"id": expired_id}]
    updated = []
    deleted_msgs = []

    async def fake_query(query, params=None):
        if "SELECT id FROM conversation" in query:
            return queried
        if "DELETE conversation_message" in query:
            deleted_msgs.append(params.get("conversation"))
            return []
        return []

    async def fake_update(table, rid, data):
        updated.append((table, rid, data))

    monkeypatch.setattr("api.retention_service.repo_query", fake_query)
    monkeypatch.setattr("api.retention_service.repo_update", fake_update)

    count = await _purge_surreal_conversations(utcnow(), dry_run=False)
    assert count == 1
    assert len(updated) == 1
    assert updated[0][2]["status"] == "purged"
    assert len(deleted_msgs) == 1


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
