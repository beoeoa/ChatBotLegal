"""Small, redacted configuration revision store for the local Admin workflow."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from api.user_service import write_audit_log
from open_notebook.database.repository import ensure_record_id, repo_create, repo_query


_SECRET_MARKERS = ("secret", "token", "password", "credential", "api_key", "apikey")
_ALLOWED_TYPES = {"settings", "model_defaults", "crawler_sources"}


def redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): redact_config(item)
            for key, item in value.items()
            if not any(marker in str(key).casefold() for marker in _SECRET_MARKERS)
        }
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def record_config_revision(
    *,
    config_type: str,
    before: dict[str, Any],
    after: dict[str, Any],
    actor_user_id: str | None,
    reason: str,
) -> dict[str, Any]:
    if config_type not in _ALLOWED_TYPES:
        raise ValueError("Unsupported configuration history type")
    record = await repo_create("admin_config_revision", {
        "config_type": config_type,
        "before": redact_config(before),
        "after": redact_config(after),
        "actor_user_id": actor_user_id or None,
        "reason": reason[:1000],
        "created_at": datetime.now(timezone.utc),
    })
    record_value = record if isinstance(record, dict) else {}
    await write_audit_log(
        action="admin.configuration.update",
        entity_type="system_settings",
        entity_id=str(record_value.get("id") or config_type),
        actor_user_id=actor_user_id,
        actor_role="admin",
        details={
            "result": "success",
            "config_type": config_type,
            "updated_fields": sorted(set(before) | set(after)),
            "reason": reason[:1000],
        },
    )
    rows = await repo_query(
        "SELECT id, created_at FROM admin_config_revision WHERE config_type = $config_type ORDER BY created_at DESC START 10 LIMIT 100;",
        {"config_type": config_type},
    )
    for row in rows or []:
        record_id = row.get("id")
        if record_id:
            await repo_query("DELETE $id;", {"id": ensure_record_id(str(record_id))})
    return record_value


async def list_config_revisions(config_type: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
    if config_type and config_type not in _ALLOWED_TYPES:
        return []
    where = "WHERE config_type = $config_type" if config_type else ""
    rows = await repo_query(
        f"SELECT * FROM admin_config_revision {where} ORDER BY created_at DESC LIMIT $limit;",
        {"config_type": config_type, "limit": max(1, min(limit, 30))},
    )
    return [redact_config(row) for row in rows or [] if isinstance(row, dict)]


async def get_config_revision(revision_id: str) -> dict[str, Any] | None:
    record_id = revision_id if revision_id.startswith("admin_config_revision:") else f"admin_config_revision:{revision_id}"
    rows = await repo_query("SELECT * FROM $id LIMIT 1;", {"id": record_id})
    if not rows:
        return None
    row = rows[0]
    return redact_config(row) if isinstance(row, dict) else None
