from __future__ import annotations

from datetime import datetime

import pytest

from api.form_notification_projection import (
    list_notifications_for_user,
    mark_notification_read,
    notification_record_id,
    project_pending_notifications,
    public_notification_payload,
)


def test_public_projection_contains_no_audit_or_private_payload() -> None:
    row = {
        "id": "out-1",
        "recipient_id": "officer_cutru",
        "recipient_role": "officer",
        "public_payload": {
            "type": "form_released",
            "release_id": "r1",
            "status": "released",
            "private_path": "C:/private/file.docx",
            "audit_hash": "secret",
        },
    }
    result = public_notification_payload(row)
    assert result["recipient_id"] == "officer_cutru"
    assert result["release_id"] == "r1"
    assert "private_path" not in result
    assert "audit_hash" not in result
    assert isinstance(result["created"], datetime)
    assert notification_record_id("out-1") == notification_record_id("out-1")


@pytest.mark.asyncio
async def test_notification_lookup_accepts_authenticated_id_and_username_alias(
    monkeypatch,
) -> None:
    calls = []

    async def fake_query(statement, variables):
        calls.append((statement, variables))
        return [{"recipient_id": "officer_cutru"}]

    monkeypatch.setattr("open_notebook.database.repository.repo_query", fake_query)
    rows = await list_notifications_for_user(
        "user_account:internal", user_alias="officer_cutru", unread_only=True
    )
    assert rows == [{"recipient_id": "officer_cutru"}]
    assert calls[0][1]["recipient_id"] == "user_account:internal"
    assert calls[0][1]["recipient_alias"] == "officer_cutru"
    assert "read_at = NONE" in calls[0][0]

    marked = await mark_notification_read(
        "user_account:internal",
        "form_workflow_notification:abc",
        user_alias="officer_cutru",
    )
    assert marked == {"recipient_id": "officer_cutru"}
    assert calls[1][1]["recipient_alias"] == "officer_cutru"


@pytest.mark.asyncio
async def test_projection_failure_is_retryable_and_never_changes_legal_state() -> None:
    class Result:
        def mappings(self): return self
        def all(self):
            return [{
                "id": "out-1",
                "recipient_id": "officer_cutru",
                "recipient_role": "officer",
                "public_payload": {"type": "form_released", "release_id": "r1"},
            }]

    class Connection:
        def execute(self, *_args, **_kwargs): return Result()
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    class Engine:
        def connect(self): return Connection()
        def begin(self): return Connection()

    repository = type("Repository", (), {"engine": Engine()})()

    async def failing_writer(_record_id, _payload):
        raise OSError("surreal unavailable")

    report = await project_pending_notifications(repository, writer=failing_writer)
    assert report == {
        "selected": 1,
        "projected": 0,
        "failed": 1,
        "legal_state_changed": False,
    }
