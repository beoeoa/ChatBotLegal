"""Retention and privacy purge service for owner-scoped chat/case data.

Policies are fixed in code and mirrored by migration 29.  No admin endpoint may
change expiry dates. The scheduled runner is idempotent and records audit rows
for each purge batch (including audit-log purge metadata).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from api.user_service import write_audit_log
from api.legal_audit_chain import is_critical_legal_action
from open_notebook.database.repository import ensure_record_id, repo_query, repo_update

ROOT = Path(__file__).resolve().parents[1]
CONVERSATIONS_DIR = ROOT / "data" / "conversations"
ASK_SESSIONS_DIR = ROOT / "data" / "ask_sessions"
LEGAL_CASES_DIR = ROOT / "data" / "legal_cases"
SUPPORT_ATTACHMENTS_DIR = ROOT / "data" / "support_attachments"
SUPPORT_TICKETS_DIR = ROOT / "data" / "support_tickets"
RETENTION_REPORT_PATH = ROOT / "data" / "retention" / "latest_report.json"

CHAT_RETENTION_DAYS = 365
CASE_FILE_RETENTION_DAYS = 180
AUDIT_RETENTION_DAYS = 730
DEFAULT_INTERVAL_MINUTES = int(os.getenv("RETENTION_JOB_INTERVAL_MINUTES", "1440"))


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        value_dt = datetime.fromisoformat(text)
        return value_dt if value_dt.tzinfo else value_dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def chat_expires_at(last_message_at: datetime) -> datetime:
    return last_message_at + timedelta(days=CHAT_RETENTION_DAYS)


def case_files_expires_at(closed_at: datetime | None) -> datetime | None:
    """Legal-case attachments never start a clock before the case is closed."""
    return closed_at + timedelta(days=CASE_FILE_RETENTION_DAYS) if closed_at else None


def audit_expires_at(created_at: datetime) -> datetime:
    return created_at + timedelta(days=AUDIT_RETENTION_DAYS)


def support_expires_at(closed_at: datetime | None) -> datetime | None:
    return closed_at + timedelta(days=CASE_FILE_RETENTION_DAYS) if closed_at else None


def _safe_remove(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
        return True
    except OSError as exc:
        logger.warning("Retention removal failed for {}: {}", path, exc)
        return False


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_report(report: dict[str, Any]) -> None:
    RETENTION_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RETENTION_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


async def _purge_surreal_conversations(now: datetime, dry_run: bool) -> int:
    try:
        rows = await repo_query(
            "SELECT id FROM conversation WHERE expires_at <= $now AND status != 'purged';",
            {"now": now},
        )
    except Exception as exc:
        logger.warning("Cannot enumerate expired conversations: {}", exc)
        return 0
    count = 0
    for row in rows or []:
        conversation_id = str(row.get("id"))
        if not dry_run:
            # Remove message payload/citations/attachments first, then mark conversation.
            await repo_query("DELETE conversation_message WHERE conversation = $conversation;", {"conversation": ensure_record_id(conversation_id)})
            await repo_update("conversation", conversation_id, {"status": "purged", "purged_at": now, "title": "?? x?a theo ch?nh s?ch l?u tr?"})
        count += 1
    return count


def _purge_json_conversations(now: datetime, dry_run: bool) -> int:
    if not CONVERSATIONS_DIR.is_dir():
        return 0
    count = 0
    for path in CONVERSATIONS_DIR.rglob("*.json"):
        data = _load_json(path)
        if not data or data.get("status") == "purged":
            continue
        expires = parse_datetime(data.get("expires_at"))
        if not expires:
            last = parse_datetime(data.get("last_message_at") or data.get("updated_at") or data.get("created_at"))
            expires = chat_expires_at(last) if last else None
        if not expires or expires > now:
            continue
        if dry_run:
            count += 1
        elif _safe_remove(path):
            # Parent dirs can remain harmlessly empty; never infer another owner's data.
            count += 1
    return count


def _purge_json_ask_sessions(now: datetime, dry_run: bool) -> int:
    """Purge legacy Ask-session JSON after the same 12-month chat policy."""
    if not ASK_SESSIONS_DIR.is_dir():
        return 0
    count = 0
    for path in ASK_SESSIONS_DIR.rglob("*.json"):
        data = _load_json(path)
        if not data:
            continue
        last = parse_datetime(data.get("updated_at") or data.get("created_at"))
        expires = chat_expires_at(last) if last else None
        if not expires or expires > now:
            continue
        if dry_run or _safe_remove(path):
            count += 1
    return count



def _case_file_paths(case_path: Path, data: dict[str, Any]) -> list[Path]:
    """Return only attachment targets to destroy; never include the metadata JSON."""
    paths: list[Path] = []
    for item in data.get("attachments") or data.get("files") or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("path") or item.get("file_path") or item.get("storage_path") or "").strip()
        if not raw:
            continue
        candidate = (ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        # Never remove files outside project data storage.
        if str(candidate).startswith(str((ROOT / "data").resolve())):
            paths.append(candidate)
    attachment_dir = case_path.with_suffix("")
    if attachment_dir.is_dir():
        paths.append(attachment_dir)
    return paths


def _purge_case_files(now: datetime, dry_run: bool) -> int:
    """Redact legal-case attachments and mark metadata as purged.

    Metadata JSON must survive for audit trail; only attachment files are
    destroyed.  The record gets ``retention_purged_at`` so the next run skips
    it (idempotent).
    """
    if not LEGAL_CASES_DIR.is_dir():
        return 0
    count = 0
    for case_path in LEGAL_CASES_DIR.glob("*.json"):
        data = _load_json(case_path)
        if not data or data.get("retention_purged_at"):
            continue
        closed = parse_datetime(data.get("closed_at"))
        expires = case_files_expires_at(closed)
        if not expires or expires > now:
            continue
        if dry_run:
            count += 1
            continue
        all_removed = all(_safe_remove(p) for p in _case_file_paths(case_path, data))
        if not all_removed:
            continue
        data["retention_purged_at"] = now.isoformat()
        data["attachments"] = []
        for message in data.get("messages") or []:
            if isinstance(message, dict):
                message["attachments"] = []
        case_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        count += 1
    return count


def _purge_support_ticket_attachments(now: datetime, dry_run: bool) -> int:
    """Remove support attachments only six months after a ticket is closed."""
    if not SUPPORT_TICKETS_DIR.is_dir():
        return 0
    count = 0
    for ticket_path in SUPPORT_TICKETS_DIR.glob("*.json"):
        data = _load_json(ticket_path)
        if not data or data.get("attachments_purged_at"):
            continue
        expires = case_files_expires_at(parse_datetime(data.get("closed_at")))
        if not expires or expires > now:
            continue
        attachment_dir = SUPPORT_ATTACHMENTS_DIR / str(data.get("id") or ticket_path.stem)
        if dry_run:
            count += 1
            continue
        if attachment_dir.exists() and not _safe_remove(attachment_dir):
            continue
        data["attachments"] = []
        for message in data.get("messages") or []:
            if isinstance(message, dict):
                message["attachments"] = []
        data["attachments_purged_at"] = now.isoformat()
        ticket_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        count += 1
    return count


def _purge_support_ticket_chats(now: datetime, dry_run: bool) -> int:
    """Purge support content 180 days after close; open tickets never expire."""
    if not SUPPORT_TICKETS_DIR.is_dir():
        return 0
    count = 0
    for ticket_path in SUPPORT_TICKETS_DIR.glob("*.json"):
        data = _load_json(ticket_path)
        if not data or data.get("status") == "purged":
            continue
        expires = support_expires_at(parse_datetime(data.get("closed_at")))
        if not expires or expires > now:
            continue
        if dry_run:
            count += 1
            continue
        attachment_dir = SUPPORT_ATTACHMENTS_DIR / str(data.get("id") or ticket_path.stem)
        if attachment_dir.exists() and not _safe_remove(attachment_dir):
            continue
        data.update({
            "status": "purged",
            "question": "Da xoa theo chinh sach luu tru.",
            "ai_summary": None,
            "messages": [],
            "attachments": [],
            "resolution_note": "",
            "attachments_purged_at": data.get("attachments_purged_at") or now.isoformat(),
            "purged_at": now.isoformat(),
            "updated_at": now.isoformat(),
        })
        ticket_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        count += 1
    return count

async def _purge_surreal_legal_cases(now: datetime, dry_run: bool) -> int:
    """Purge only closed legal-case records after the fixed six-month policy.

    The Surreal legal_case schema intentionally contains no attachment blob. Any
    legacy attachment paths are handled by _purge_case_files. For persisted rows
    we redact sensitive metadata and mark the record purged instead of letting a
    retained title/assignment continue to expose case context.
    """
    cutoff = now - timedelta(days=CASE_FILE_RETENTION_DAYS)
    try:
        rows = await repo_query(
            "SELECT id FROM legal_case WHERE closed_at != NONE AND closed_at <= $cutoff AND status != 'purged';",
            {"cutoff": cutoff},
        )
    except Exception as exc:
        logger.warning("Cannot enumerate expired legal cases: {}", exc)
        return 0
    count = 0
    for row in rows or []:
        case_id = str(row.get("id") or "")
        if not case_id:
            continue
        if not dry_run:
            await repo_update(
                "legal_case",
                case_id,
                {
                    "status": "purged",
                    "title": "?? x?a theo ch?nh s?ch l?u tr?",
                    "assigned_officer": None,
                    "expires_at": now,
                    "purged_at": now,
                },
            )
        count += 1
    return count


async def _purge_audit_rows(now: datetime, dry_run: bool) -> int:
    cutoff = now - timedelta(days=AUDIT_RETENTION_DAYS)
    total = 0
    for table in ("user_audit_log", "sensitive_access_audit"):
        try:
            rows = await repo_query(
                f"SELECT id, action FROM {table} WHERE (expires_at <= $now OR (expires_at = NONE AND created <= $cutoff));",
                {"now": now, "cutoff": cutoff},
            )
        except Exception as exc:
            # user_audit_log from migration 16 has no expires_at in older deployments.
            try:
                rows = await repo_query(f"SELECT id, action FROM {table} WHERE created <= $cutoff;", {"cutoff": cutoff})
            except Exception as nested:
                logger.warning("Cannot enumerate expired {}: {} / {}", table, exc, nested)
                continue
        ids = [
            str(row.get("id"))
            for row in rows or []
            if row.get("id") and not is_critical_legal_action(str(row.get("action") or ""))
        ]
        if ids and not dry_run:
            for record_id in ids:
                await repo_query(f"DELETE $id;", {"id": ensure_record_id(record_id)})
        total += len(ids)
    return total


async def upcoming_expiration_report(now: datetime | None = None, within_days: int = 30) -> dict[str, Any]:
    """Return metadata-only records approaching deterministic retention cutoffs."""
    now = now or utcnow()
    within_days = max(1, min(180, int(within_days)))
    deadline = now + timedelta(days=within_days)
    result: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "within_days": within_days,
        "conversations": [],
        "ask_sessions": [],
        "legal_cases": [],
        "support_tickets": [],
    }
    if CONVERSATIONS_DIR.is_dir():
        for path in CONVERSATIONS_DIR.rglob("*.json"):
            data = _load_json(path)
            if not data or data.get("status") in {"purged", "deleted"}:
                continue
            expires = parse_datetime(data.get("expires_at"))
            if not expires:
                last = parse_datetime(data.get("last_message_at") or data.get("updated_at") or data.get("created_at"))
                expires = chat_expires_at(last) if last else None
            if expires and now <= expires <= deadline:
                result["conversations"].append({
                    "id": data.get("id") or path.stem,
                    "expires_at": expires.isoformat(),
                    "owner_user_id": data.get("owner_user") or data.get("owner_key"),
                    "storage": "json",
                })
    if ASK_SESSIONS_DIR.is_dir():
        for path in ASK_SESSIONS_DIR.rglob("*.json"):
            data = _load_json(path)
            if not data:
                continue
            last = parse_datetime(data.get("updated_at") or data.get("created_at"))
            expires = chat_expires_at(last) if last else None
            if expires and now <= expires <= deadline:
                result["ask_sessions"].append({
                    "id": data.get("id") or path.stem,
                    "expires_at": expires.isoformat(),
                    "owner_user_id": data.get("user_id"),
                    "storage": "json",
                })

    if SUPPORT_TICKETS_DIR.is_dir():
        for path in SUPPORT_TICKETS_DIR.glob("*.json"):
            data = _load_json(path)
            if not data or data.get("status") == "purged":
                continue
            expires = support_expires_at(parse_datetime(data.get("closed_at")))
            if expires and now <= expires <= deadline:
                result["support_tickets"].append({
                    "id": data.get("id") or path.stem,
                    "expires_at": expires.isoformat(),
                    "owner_user_id": data.get("citizen_id"),
                    "storage": "json",
                })

    if LEGAL_CASES_DIR.is_dir():
        for path in LEGAL_CASES_DIR.glob("*.json"):
            data = _load_json(path)
            if not data or data.get("retention_purged_at"):
                continue
            expires = case_files_expires_at(parse_datetime(data.get("closed_at")))
            if expires and now <= expires <= deadline:
                result["legal_cases"].append({
                    "id": data.get("id") or path.stem,
                    "expires_at": expires.isoformat(),
                    "owner_user_id": data.get("owner_user_id") or data.get("owner_user"),
                    "storage": "json",
                })
    # A query failure simply leaves the DB contribution absent; local retention
    # remains available for standalone deployments.
    try:
        conversations = await repo_query(
            "SELECT id, owner_user, expires_at FROM conversation WHERE expires_at > $now AND expires_at <= $deadline AND status != 'purged';",
            {"now": now, "deadline": deadline},
        )
        for row in conversations or []:
            result["conversations"].append({
                "id": str(row.get("id")), "expires_at": str(row.get("expires_at")),
                "owner_user_id": str(row.get("owner_user") or ""), "storage": "surreal",
            })
        case_cutoff_start = now - timedelta(days=CASE_FILE_RETENTION_DAYS)
        case_cutoff_end = deadline - timedelta(days=CASE_FILE_RETENTION_DAYS)
        cases = await repo_query(
            "SELECT id, owner_user, closed_at FROM legal_case WHERE closed_at != NONE AND closed_at >= $from AND closed_at <= $to AND status != 'purged';",
            {"from": case_cutoff_start, "to": case_cutoff_end},
        )
        for row in cases or []:
            expires = case_files_expires_at(parse_datetime(row.get("closed_at")))
            if expires:
                result["legal_cases"].append({
                    "id": str(row.get("id")), "expires_at": expires.isoformat(),
                    "owner_user_id": str(row.get("owner_user") or ""), "storage": "surreal",
                })
    except Exception as exc:
        logger.debug("Cannot add Surreal records to retention report: {}", exc)
    result["counts"] = {
        "conversations": len(result["conversations"]),
        "ask_sessions": len(result["ask_sessions"]),
        "legal_cases": len(result["legal_cases"]),
        "support_tickets": len(result["support_tickets"]),
    }
    return result

async def run_retention_purge(*, now: datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Idempotent scheduled purge. No user/admin supplied dates are accepted."""
    now = now or utcnow()
    report = {
        "started_at": now.isoformat(), "dry_run": dry_run,
        "chat_surreal": await _purge_surreal_conversations(now, dry_run),
        "chat_json": _purge_json_conversations(now, dry_run),
        "ask_sessions_json": _purge_json_ask_sessions(now, dry_run),
        "case_files": _purge_case_files(now, dry_run),
        "case_surreal": await _purge_surreal_legal_cases(now, dry_run),
        "audit_rows": await _purge_audit_rows(now, dry_run),
        "support_attachments": _purge_support_ticket_attachments(now, dry_run),
        "support_chats": _purge_support_ticket_chats(now, dry_run),
    }
    report["upcoming"] = await upcoming_expiration_report(now)
    report["completed_at"] = utcnow().isoformat()
    _save_report(report)
    # Audit retention job itself survives as a newly created row; never insert a
    # fabricated actor. This is an immutable system operation record.
    if not dry_run:
        try:
            await write_audit_log(
                action="retention.purge.completed", entity_type="retention_job", entity_id="scheduled",
                actor_user_id=None, actor_role="system", details={"reason": "scheduled retention policy", **report},
            )
        except Exception as exc:
            # Purge is already complete; retain report and surface a warning to scheduler.
            logger.error("Retention purge audit write failed: {}", exc)
            report["audit_write_failed"] = True
            _save_report(report)
    return report


async def retention_scheduler_loop(stop_event: asyncio.Event | None = None) -> None:
    """Run once daily by default; testable through an optional stop event."""
    interval_seconds = max(60, DEFAULT_INTERVAL_MINUTES * 60)
    while True:
        try:
            await run_retention_purge()
        except Exception as exc:
            logger.exception("Retention job failed: {}", exc)
        if stop_event and stop_event.is_set():
            return
        try:
            await asyncio.wait_for((stop_event or asyncio.Event()).wait(), timeout=interval_seconds)
            if stop_event:
                return
        except asyncio.TimeoutError:
            continue
