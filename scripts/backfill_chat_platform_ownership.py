"""
Safe backfill for chat-platform migrations 27-29.

Rules:
- Default mode is --dry-run.
- Never auto-assign owner for legacy / ambiguous / missing owners.
- When owner cannot be resolved to a real user_account record, create a
  needs_admin_review row and leave the original JSON/source untouched.
- When owner is a real user_account, create conversation / support_session rows
  only if no prior row with the same legacy_source_path exists.
- Existing notebook/note/source/chat_session rows without a resolvable owner are
  only flagged ownership_status=needs_admin_review; they are never reassigned.

Examples:
  python scripts/backfill_chat_platform_ownership.py --dry-run
  python scripts/backfill_chat_platform_ownership.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

ASK_SESSIONS_DIR = ROOT / "data" / "ask_sessions"
SUPPORT_TICKETS_DIR = ROOT / "data" / "support_tickets"
LEGAL_CASES_DIR = ROOT / "data" / "legal_cases"
REPORT_PATH = ROOT / "notebook_data" / "chat_platform_ownership_backfill_report.json"
CHAT_RETENTION_DAYS = 365
CASE_RETENTION_DAYS = 180


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _as_record(user_id: str | None) -> str | None:
    if not user_id:
        return None
    value = str(user_id).strip()
    if not value:
        return None
    if value.startswith("user_account:"):
        return value
    if ":" in value:
        return None
    return f"user_account:{value}"


def _is_real_user_key(value: str | None) -> bool:
    if not value:
        return False
    text = str(value).strip()
    if not text:
        return False
    if text.startswith("legacy:") or text.startswith("user:"):
        return False
    if text.startswith("user_account:"):
        return True
    return ":" not in text


def _load_json_files(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not root.exists():
        return []
    items: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(root.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            items.append((path, data))
    return items


async def _user_exists(user_ref: str | None) -> bool:
    if not user_ref:
        return False
    from open_notebook.database.repository import repo_query

    rows = await repo_query(
        "SELECT id FROM type::record($user_id) LIMIT 1;",
        {"user_id": user_ref},
    )
    return bool(rows)


async def _existing_by_legacy_path(table: str, path: str) -> dict[str, Any] | None:
    from open_notebook.database.repository import repo_query

    rows = await repo_query(
        f"SELECT * FROM {table} WHERE legacy_source_path = $path LIMIT 1;",
        {"path": path},
    )
    return rows[0] if rows else None


async def _create_row(table: str, data: dict[str, Any], apply: bool) -> dict[str, Any]:
    if not apply:
        return {"dry_run": True, "table": table, **data}
    from open_notebook.database.repository import ensure_record_id, repo_create

    payload = dict(data)
    for key in ("owner_user", "sender_user", "citizen", "officer", "conversation", "submitted_by", "actor", "assigned_officer"):
        if payload.get(key):
            payload[key] = ensure_record_id(str(payload[key]))
    created = await repo_create(table, payload)
    return created[0] if isinstance(created, list) else created


async def backfill_ask_sessions(apply: bool) -> dict[str, Any]:
    summary = {
        "scanned": 0,
        "migrated": 0,
        "needs_admin_review": 0,
        "already_migrated": 0,
        "errors": [],
        "samples": [],
    }
    for path, data in _load_json_files(ASK_SESSIONS_DIR):
        summary["scanned"] += 1
        legacy_path = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            existing = await _existing_by_legacy_path("conversation", legacy_path)
            if existing:
                summary["already_migrated"] += 1
                continue

            owner_raw = data.get("user_id") or data.get("owner_user_id") or data.get("owner_user")
            owner_ref = _as_record(str(owner_raw)) if _is_real_user_key(str(owner_raw or "")) else None
            owner_ok = await _user_exists(owner_ref)
            messages = data.get("messages") or []
            created_at = _parse_dt(data.get("created_at") or data.get("created")) or _utcnow()
            last_message_at = created_at
            for msg in messages:
                msg_dt = _parse_dt(msg.get("created_at") or msg.get("created"))
                if msg_dt and msg_dt > last_message_at:
                    last_message_at = msg_dt
            expires_at = last_message_at + timedelta(days=CHAT_RETENTION_DAYS)

            if owner_ok:
                conversation = await _create_row(
                    "conversation",
                    {
                        "owner_user": owner_ref,
                        "role_context": data.get("role") or "citizen",
                        "domain": data.get("domain"),
                        "title": data.get("title") or "Cuộc trò chuyện",
                        "status": "active",
                        "ownership_status": "resolved",
                        "legacy_source_path": legacy_path,
                        "created_at": created_at,
                        "last_message_at": last_message_at,
                        "expires_at": expires_at,
                    },
                    apply,
                )
                conversation_id = conversation.get("id")
                if apply and conversation_id:
                    for msg in messages:
                        sender_role = msg.get("role") or "user"
                        sender_user = owner_ref if sender_role in {"user", "citizen"} else None
                        await _create_row(
                            "conversation_message",
                            {
                                "conversation": conversation_id,
                                "sender_user": sender_user,
                                "sender_role": sender_role,
                                "content": msg.get("content") or "",
                                "attachments": msg.get("attachments") or [],
                                "citations_snapshot": msg.get("citations") or [],
                                "created_at": _parse_dt(msg.get("created_at")) or last_message_at,
                            },
                            apply,
                        )
                summary["migrated"] += 1
                summary["samples"].append(
                    {
                        "type": "conversation",
                        "path": legacy_path,
                        "ownership_status": "resolved",
                        "owner_user": owner_ref,
                    }
                )
            else:
                await _create_row(
                    "conversation",
                    {
                        "owner_user": None,
                        "role_context": data.get("role") or "citizen",
                        "domain": data.get("domain"),
                        "title": data.get("title") or "Cuộc trò chuyện cần admin xử lý",
                        "status": "needs_admin_review",
                        "ownership_status": "needs_admin_review",
                        "legacy_source_path": legacy_path,
                        "created_at": created_at,
                        "last_message_at": last_message_at,
                        "expires_at": expires_at,
                    },
                    apply,
                )
                summary["needs_admin_review"] += 1
                summary["samples"].append(
                    {
                        "type": "conversation",
                        "path": legacy_path,
                        "ownership_status": "needs_admin_review",
                        "owner_raw": owner_raw,
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive reporting
            summary["errors"].append({"path": legacy_path, "error": str(exc)})
    return summary


async def backfill_support_tickets(apply: bool) -> dict[str, Any]:
    summary = {
        "scanned": 0,
        "migrated": 0,
        "needs_admin_review": 0,
        "already_migrated": 0,
        "errors": [],
        "samples": [],
    }
    for path, data in _load_json_files(SUPPORT_TICKETS_DIR):
        summary["scanned"] += 1
        legacy_path = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            existing = await _existing_by_legacy_path("support_session", legacy_path)
            if existing:
                summary["already_migrated"] += 1
                continue

            citizen_raw = data.get("citizen_id")
            officer_raw = data.get("assigned_officer_id")
            citizen_ref = _as_record(str(citizen_raw)) if _is_real_user_key(str(citizen_raw or "")) else None
            officer_ref = _as_record(str(officer_raw)) if _is_real_user_key(str(officer_raw or "")) else None
            citizen_ok = await _user_exists(citizen_ref)
            officer_ok = await _user_exists(officer_ref) if officer_ref else False

            status_map = {
                "open": "queued",
                "assigned": "assigned",
                "answered": "active",
                "closed": "closed",
            }
            queue_status = status_map.get(str(data.get("status") or "open"), "queued")
            assigned_at = _parse_dt(data.get("updated_at") or data.get("created_at"))
            closed_at = assigned_at if queue_status == "closed" else None
            ownership_status = "resolved" if citizen_ok else "needs_admin_review"

            await _create_row(
                "support_session",
                {
                    "citizen": citizen_ref if citizen_ok else None,
                    "officer": officer_ref if officer_ok else None,
                    "conversation": None,
                    "domain": data.get("assigned_department") or data.get("department"),
                    "queue_status": queue_status,
                    "ownership_status": ownership_status,
                    "legacy_source_path": legacy_path,
                    "assigned_at": assigned_at if queue_status in {"assigned", "active", "closed"} else None,
                    "closed_at": closed_at,
                    "resolution_note": data.get("ai_summary"),
                },
                apply,
            )
            if ownership_status == "resolved":
                summary["migrated"] += 1
            else:
                summary["needs_admin_review"] += 1
            summary["samples"].append(
                {
                    "type": "support_session",
                    "path": legacy_path,
                    "ownership_status": ownership_status,
                    "citizen_raw": citizen_raw,
                    "officer_raw": officer_raw,
                }
            )
        except Exception as exc:  # pragma: no cover
            summary["errors"].append({"path": legacy_path, "error": str(exc)})
    return summary


async def backfill_legal_cases(apply: bool) -> dict[str, Any]:
    summary = {
        "scanned": 0,
        "migrated": 0,
        "needs_admin_review": 0,
        "already_migrated": 0,
        "errors": [],
        "samples": [],
    }
    for path, data in _load_json_files(LEGAL_CASES_DIR):
        summary["scanned"] += 1
        legacy_path = str(path.relative_to(ROOT)).replace("\\", "/")
        try:
            existing = await _existing_by_legacy_path("legal_case", legacy_path)
            if existing:
                summary["already_migrated"] += 1
                continue
            owner_raw = data.get("owner_user_id") or data.get("owner_user") or data.get("user_id")
            officer_raw = data.get("assigned_officer_id") or data.get("assigned_officer")
            owner_ref = _as_record(str(owner_raw)) if _is_real_user_key(str(owner_raw or "")) else None
            officer_ref = _as_record(str(officer_raw)) if _is_real_user_key(str(officer_raw or "")) else None
            owner_ok = await _user_exists(owner_ref)
            officer_ok = await _user_exists(officer_ref) if officer_ref else False
            closed_at = _parse_dt(data.get("closed_at"))
            base_dt = closed_at or _parse_dt(data.get("updated_at") or data.get("created_at")) or _utcnow()
            expires_at = base_dt + timedelta(days=CASE_RETENTION_DAYS)
            ownership_status = "resolved" if owner_ok else "needs_admin_review"
            await _create_row(
                "legal_case",
                {
                    "owner_user": owner_ref if owner_ok else None,
                    "assigned_officer": officer_ref if officer_ok else None,
                    "status": data.get("status") or "open",
                    "ownership_status": ownership_status,
                    "legacy_source_path": legacy_path,
                    "closed_at": closed_at,
                    "expires_at": expires_at,
                },
                apply,
            )
            if ownership_status == "resolved":
                summary["migrated"] += 1
            else:
                summary["needs_admin_review"] += 1
            summary["samples"].append(
                {
                    "type": "legal_case",
                    "path": legacy_path,
                    "ownership_status": ownership_status,
                    "owner_raw": owner_raw,
                }
            )
        except Exception as exc:  # pragma: no cover
            summary["errors"].append({"path": legacy_path, "error": str(exc)})
    return summary


async def backfill_user_profiles(apply: bool) -> dict[str, Any]:
    from open_notebook.database.repository import repo_query, repo_update

    summary = {"scanned": 0, "updated": 0, "errors": [], "samples": []}
    profiles = await repo_query("SELECT * FROM user_profile;")
    for profile in profiles:
        summary["scanned"] += 1
        try:
            ward = profile.get("ward") or profile.get("ward_scope") or "Phường Lê Chân, Hải Phòng"
            payload = {
                "ward": ward,
                "ward_scope": profile.get("ward_scope") or ward,
                "allowed_domains": profile.get("allowed_domains") or [],
                "must_change_password": bool(profile.get("must_change_password", False)),
            }
            if apply:
                await repo_update("user_profile", str(profile["id"]), payload)
            summary["updated"] += 1
            if len(summary["samples"]) < 5:
                summary["samples"].append(
                    {
                        "profile_id": str(profile.get("id")),
                        "ward_scope": payload["ward_scope"],
                        "must_change_password": payload["must_change_password"],
                    }
                )
        except Exception as exc:  # pragma: no cover
            summary["errors"].append({"profile_id": str(profile.get("id")), "error": str(exc)})
    return summary



async def backfill_existing_legal_profiles(apply: bool) -> dict[str, Any]:
    """Flag unresolved notebook/source/note/chat_session owners without reassignment."""
    from open_notebook.database.repository import repo_query, repo_update

    summary: dict[str, Any] = {
        "scanned": 0,
        "resolved": 0,
        "needs_admin_review": 0,
        "already_flagged": 0,
        "errors": [],
        "samples": [],
        "by_table": {},
    }
    tables = ("notebook", "note", "source", "chat_session")
    for table in tables:
        table_summary = {
            "scanned": 0,
            "resolved": 0,
            "needs_admin_review": 0,
            "already_flagged": 0,
            "errors": [],
        }
        try:
            rows = await repo_query(f"SELECT id, owner_user, ownership_status FROM {table};")
        except Exception as exc:  # pragma: no cover - defensive reporting
            table_summary["errors"].append({"error": str(exc)})
            summary["errors"].append({"table": table, "error": str(exc)})
            summary["by_table"][table] = table_summary
            continue

        for row in rows:
            summary["scanned"] += 1
            table_summary["scanned"] += 1
            try:
                owner_ref = _as_record(str(row.get("owner_user") or "")) if row.get("owner_user") else None
                owner_ok = await _user_exists(owner_ref)
                current_status = str(row.get("ownership_status") or "resolved")
                if owner_ok:
                    desired = "resolved"
                    if current_status == desired:
                        table_summary["resolved"] += 1
                        summary["resolved"] += 1
                        continue
                    if apply:
                        await repo_update(table, str(row["id"]), {"ownership_status": desired})
                    table_summary["resolved"] += 1
                    summary["resolved"] += 1
                else:
                    desired = "needs_admin_review"
                    if current_status == desired:
                        table_summary["already_flagged"] += 1
                        summary["already_flagged"] += 1
                        continue
                    if apply:
                        # Never invent owner_user. Only mark for admin review.
                        await repo_update(table, str(row["id"]), {"ownership_status": desired})
                    table_summary["needs_admin_review"] += 1
                    summary["needs_admin_review"] += 1
                    if len(summary["samples"]) < 20:
                        summary["samples"].append(
                            {
                                "table": table,
                                "id": str(row.get("id")),
                                "owner_raw": str(row.get("owner_user") or ""),
                                "ownership_status": desired,
                            }
                        )
            except Exception as exc:  # pragma: no cover
                table_summary["errors"].append({"id": str(row.get("id")), "error": str(exc)})
                summary["errors"].append({"table": table, "id": str(row.get("id")), "error": str(exc)})
        summary["by_table"][table] = table_summary
    return summary

async def run(apply: bool) -> dict[str, Any]:
    report = {
        "generated_at": _utcnow().isoformat(),
        "mode": "apply" if apply else "dry_run",
        "rules": [
            "Do not auto-assign owner for legacy/ambiguous data",
            "Unresolved owners are marked needs_admin_review",
            "Original JSON files are never deleted or rewritten",
            "Existing notebook/note/source/chat_session without real owner are flagged only",
        ],
        "ask_sessions": await backfill_ask_sessions(apply),
        "support_tickets": await backfill_support_tickets(apply),
        "legal_cases": await backfill_legal_cases(apply),
        "user_profiles": await backfill_user_profiles(apply),
        "legal_profiles": await backfill_existing_legal_profiles(apply),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Safe ownership backfill for chat platform schema")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Report only (default)")
    mode.add_argument("--apply", action="store_true", help="Write rows to SurrealDB")
    args = parser.parse_args()
    apply = bool(args.apply)
    report = asyncio.run(run(apply=apply))
    print(json.dumps(
        {
            "mode": report["mode"],
            "report": str(REPORT_PATH),
            "ask_sessions": {
                k: report["ask_sessions"][k]
                for k in ("scanned", "migrated", "needs_admin_review", "already_migrated", "errors")
            },
            "support_tickets": {
                k: report["support_tickets"][k]
                for k in ("scanned", "migrated", "needs_admin_review", "already_migrated", "errors")
            },
            "legal_cases": {
                k: report["legal_cases"][k]
                for k in ("scanned", "migrated", "needs_admin_review", "already_migrated", "errors")
            },
            "user_profiles": {
                k: report["user_profiles"][k]
                for k in ("scanned", "updated", "errors")
            },
            "legal_profiles": {
                k: report["legal_profiles"][k]
                for k in ("scanned", "resolved", "needs_admin_review", "already_flagged", "errors")
            },
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
