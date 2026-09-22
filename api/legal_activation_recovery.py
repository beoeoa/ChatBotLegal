"""Resume approved activation after index/network/projection failures.

Runs inside the existing API process. PostgreSQL's version journal serializes
index mutations across API workers; lifecycle permissions are rechecked on
every attempt using the original reviewer, never a privileged invented actor.
"""
import asyncio
import hashlib
import time
from loguru import logger

from api.legal_lifecycle_service import default_lifecycle_service, lifecycle_capabilities


async def reviewer_is_active_admin(actor: str) -> bool:
    from open_notebook.database.repository import repo_query
    rows = await repo_query(
        "SELECT role, is_active FROM type::record($actor);", {"actor": actor})
    return bool(rows and rows[0].get("role") == "admin" and rows[0].get("is_active") is True)


class ActivationRecovery:
    def __init__(self, service=default_lifecycle_service, clock=time.monotonic):
        self.service = service
        self.clock = clock
        self.failures = {}

    async def run_once(self, rows):
        recovered = 0
        for row in rows:
            identity = str(row.get("id") or "")
            actor = str(row.get("reviewed_by") or "")
            version = str((row.get("version") or {}).get("version_key") or "")
            if not identity or not actor or not version or row.get("state") != "approved" or row.get("activation_state") == "active":
                continue
            caps = lifecycle_capabilities(actor, "admin")
            if not all(caps[key] for key in ("writes_enabled", "reviewer", "activation_enabled")):
                continue
            if actor == row.get("submitted_by"):
                continue
            attempts, due = self.failures.get(identity, (0, 0))
            if self.clock() < due:
                continue
            revision = int(row.get("revision") or 0)
            key = hashlib.sha256(f"activation-recovery:{identity}:{version}:{revision}".encode()).hexdigest()
            try:
                # The historical review is not a permanent authorization:
                # locked, deleted or demoted reviewers cannot activate data.
                if not await reviewer_is_active_admin(actor):
                    self.failures.pop(identity, None)
                    continue
                await self.service.activate_draft(identity, actor=actor, role="admin",
                    expected_revision=revision, reason="Tự động tiếp tục lập chỉ mục phiên bản đã được duyệt.",
                    idempotency_key=key)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failures[identity] = (attempts + 1, self.clock() + min(900, 15 * 2 ** min(attempts, 6)))
                logger.warning("activation_recovery_failed draft={} type={}", identity, type(exc).__name__)
            else:
                self.failures.pop(identity, None)
                recovered += 1
        return recovered


async def activation_recovery_loop():
    from open_notebook.database.repository import repo_query
    worker = ActivationRecovery()
    offset = 0
    while True:
        try:
            # A successful SQL receipt followed by a failed SurrealDB update
            # leaves the row approved; retry repairs that projection as well.
            rows = await repo_query(
                "SELECT id, state, revision, version, reviewed_by, submitted_by, activation_state, updated "
                "FROM legal_document_draft WHERE state = 'approved' "
                "AND (activation_state = NONE OR activation_state = NULL OR activation_state != 'active') "
                "ORDER BY updated ASC LIMIT 100 START $offset;", {"offset": offset})
            await worker.run_once(rows)
            offset = offset + len(rows) if len(rows) == 100 else 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("activation_recovery_scan_failed type={}", type(exc).__name__)
        await asyncio.sleep(30)
