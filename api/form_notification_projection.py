"""Project Feature 017 PostgreSQL outbox events to the SurrealDB inbox.

PostgreSQL remains canonical. Projection is retryable and may fail without
changing the release pointer or any legal state.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping

from sqlalchemy import text


SurrealWriter = Callable[[str, Mapping[str, Any]], Awaitable[str]]


def notification_record_id(outbox_id: str) -> str:
    digest = hashlib.sha256(str(outbox_id).encode("utf-8")).hexdigest()
    return f"form_workflow_notification:{digest[:32]}"


def public_notification_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row.get("public_payload") or {})
    return {
        "outbox_id": str(row["id"]),
        "recipient_id": str(row["recipient_id"]),
        "recipient_role": str(row["recipient_role"]),
        "notification_type": str(
            payload.get("notification_type") or payload.get("type") or "form_workflow"
        ),
        "case_id": payload.get("case_id"),
        "release_id": payload.get("release_id"),
        "status": payload.get("status"),
        "message": str(
            payload.get("message")
            or "Trạng thái thủ tục/biểu mẫu đã được cập nhật."
        ),
        "read_at": None,
        "created": datetime.now(timezone.utc),
        "updated": datetime.now(timezone.utc),
    }


async def _default_surreal_writer(record_id: str, payload: Mapping[str, Any]) -> str:
    from open_notebook.database.repository import repo_query

    await repo_query(
        "UPSERT type::thing('form_workflow_notification', $record_key) CONTENT $payload;",
        {"record_key": record_id.split(":", 1)[1], "payload": dict(payload)},
    )
    return record_id


async def project_pending_notifications(
    repository: Any,
    *,
    writer: SurrealWriter | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    writer = writer or _default_surreal_writer
    with repository.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id,recipient_id,recipient_role,public_payload "
                "FROM form_notification_outbox "
                "WHERE projection_status IN ('pending','failed') "
                "ORDER BY created_at,id LIMIT :limit"
            ),
            {"limit": max(1, min(int(limit), 1000))},
        ).mappings().all()

    projected = 0
    failed = 0
    for row in rows:
        record_id = notification_record_id(str(row["id"]))
        try:
            surreal_id = await writer(record_id, public_notification_payload(row))
        except Exception as exc:
            failed += 1
            error_code = hashlib.sha256(
                type(exc).__name__.encode("utf-8")
            ).hexdigest()[:24]
            with repository.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE form_notification_outbox SET projection_status='failed', "
                        "attempts=attempts+1,last_error_code=:error "
                        "WHERE id=:id AND projection_status IN ('pending','failed')"
                    ),
                    {"id": row["id"], "error": error_code},
                )
            continue
        with repository.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE form_notification_outbox SET projection_status='projected', "
                    "attempts=attempts+1,surreal_notification_id=:surreal_id,"
                    "last_error_code=NULL,projected_at=CURRENT_TIMESTAMP "
                    "WHERE id=:id AND projection_status IN ('pending','failed')"
                ),
                {"id": row["id"], "surreal_id": surreal_id},
            )
        projected += 1
    return {
        "selected": len(rows),
        "projected": projected,
        "failed": failed,
        "legal_state_changed": False,
    }


async def list_notifications_for_user(
    user_id: str,
    *,
    user_alias: str | None = None,
    unread_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    from open_notebook.database.repository import repo_query

    unread_clause = " AND read_at = NONE" if unread_only else ""
    rows = await repo_query(
        "SELECT * FROM form_workflow_notification "
        f"WHERE (recipient_id = $recipient_id OR recipient_id = $recipient_alias){unread_clause} "
        "ORDER BY created DESC LIMIT $limit;",
        {
            "recipient_id": user_id,
            "recipient_alias": user_alias or user_id,
            "limit": max(1, min(int(limit), 200)),
        },
    )
    return list(rows or [])


async def mark_notification_read(
    user_id: str, notification_id: str, *, user_alias: str | None = None
) -> dict[str, Any]:
    from open_notebook.database.repository import repo_query

    key = str(notification_id).split(":", 1)[-1]
    rows = await repo_query(
        "UPDATE type::thing('form_workflow_notification', $record_key) "
        "SET read_at = time::now(), updated = time::now() "
        "WHERE recipient_id = $recipient_id OR recipient_id = $recipient_alias "
        "RETURN AFTER;",
        {
            "record_key": key,
            "recipient_id": user_id,
            "recipient_alias": user_alias or user_id,
        },
    )
    if not rows:
        raise LookupError("FORM_NOTIFICATION_NOT_FOUND")
    return dict(rows[0])
