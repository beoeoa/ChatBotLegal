from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest

from api import retention_service as retention


def test_support_content_and_attachment_purge_is_close_based_and_idempotent(tmp_path, monkeypatch):
    tickets = tmp_path / "tickets"
    attachments = tmp_path / "attachments"
    tickets.mkdir()
    attachments.mkdir()
    monkeypatch.setattr(retention, "SUPPORT_TICKETS_DIR", tickets)
    monkeypatch.setattr(retention, "SUPPORT_ATTACHMENTS_DIR", attachments)
    now = retention.utcnow()

    old_closed = {
        "id": "closed-old",
        "status": "closed",
        "closed_at": (now - timedelta(days=181)).isoformat(),
        "updated_at": now.isoformat(),
        "question": "private question",
        "messages": [{"content": "private answer", "attachments": [{"id": "a"}]}],
        "attachments": [{"id": "a"}],
    }
    open_old = {
        "id": "open-old",
        "status": "active",
        "created_at": (now - timedelta(days=900)).isoformat(),
        "updated_at": (now - timedelta(days=900)).isoformat(),
        "question": "still active",
        "messages": [{"content": "must remain"}],
        "attachments": [],
    }
    for payload in (old_closed, open_old):
        (tickets / f"{payload['id']}.json").write_text(json.dumps(payload), encoding="utf-8")
    old_files = attachments / "closed-old"
    old_files.mkdir()
    (old_files / "a.pdf").write_bytes(b"private")

    assert retention._purge_support_ticket_attachments(now, dry_run=False) == 1
    assert retention._purge_support_ticket_chats(now, dry_run=False) == 1
    assert retention._purge_support_ticket_attachments(now, dry_run=False) == 0
    assert retention._purge_support_ticket_chats(now, dry_run=False) == 0
    purged = json.loads((tickets / "closed-old.json").read_text(encoding="utf-8"))
    active = json.loads((tickets / "open-old.json").read_text(encoding="utf-8"))
    assert purged["status"] == "purged" and purged["messages"] == []
    assert not old_files.exists()
    assert active["messages"] == [{"content": "must remain"}]


@pytest.mark.asyncio
async def test_audit_retention_preserves_critical_legal_history(monkeypatch):
    deleted: list[str] = []

    async def query(sql, params):
        if sql.startswith("DELETE"):
            deleted.append(str(params["id"]))
            return []
        if "user_audit_log" in sql:
            return [
                {"id": "user_audit_log:ordinary", "action": "support.ticket.view"},
                {"id": "user_audit_log:legal", "action": "legal.lifecycle.confirm"},
            ]
        return []

    monkeypatch.setattr(retention, "repo_query", query)
    count = await retention._purge_audit_rows(retention.utcnow(), dry_run=False)
    assert count == 1
    assert deleted == ["user_audit_log:ordinary"]
    assert "user_audit_log:legal" not in deleted


@pytest.mark.asyncio
async def test_retention_dry_run_does_not_remove_content_or_emit_audit(tmp_path, monkeypatch):
    tickets = tmp_path / "tickets"
    tickets.mkdir()
    monkeypatch.setattr(retention, "SUPPORT_TICKETS_DIR", tickets)
    monkeypatch.setattr(retention, "SUPPORT_ATTACHMENTS_DIR", tmp_path / "attachments")
    monkeypatch.setattr(retention, "CONVERSATIONS_DIR", tmp_path / "conversations")
    monkeypatch.setattr(retention, "ASK_SESSIONS_DIR", tmp_path / "asks")
    monkeypatch.setattr(retention, "LEGAL_CASES_DIR", tmp_path / "cases")
    monkeypatch.setattr(retention, "RETENTION_REPORT_PATH", tmp_path / "report.json")
    monkeypatch.setattr(retention, "repo_query", AsyncMock(return_value=[]))
    audit = AsyncMock()
    monkeypatch.setattr(retention, "write_audit_log", audit)
    now = retention.utcnow()
    path = tickets / "old.json"
    path.write_text(
        json.dumps(
            {
                "id": "old",
                "status": "closed",
                "closed_at": (now - timedelta(days=181)).isoformat(),
                "messages": [{"content": "private"}],
                "attachments": [],
            }
        ),
        encoding="utf-8",
    )
    report = await retention.run_retention_purge(now=now, dry_run=True)
    assert report["support_chats"] == 1
    assert json.loads(path.read_text(encoding="utf-8"))["messages"]
    audit.assert_not_awaited()
