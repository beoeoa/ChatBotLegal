"""Process-safe quota storage for the public quick-chat route.

SQLite is used deliberately for the local deployment profile: all Uvicorn
workers and local replicas that share this file use one transactional counter
without requiring another daemon. A multi-host deployment should replace this
adapter with a shared quota backend instead of relying on network-filesystem
SQLite locking.
"""

from __future__ import annotations

import math
import os
import sqlite3
import threading
import time
from pathlib import Path

from api.data_paths import PROJECT_ROOT

_GLOBAL_MINUTE_CAPACITY_KEY = "__global_minute_capacity__"
_SCHEMA_INIT_ATTEMPTS = 8
_SCHEMA_INIT_RETRY_SECONDS = 0.05


class SharedQuickChatQuota:
    def __init__(self, path: Path | None = None) -> None:
        configured = str(os.getenv("PUBLIC_QUICK_CHAT_QUOTA_PATH") or "").strip()
        self.path = Path(
            path
            or configured
            or PROJECT_ROOT / "runtime-data" / "public-quick-chat-quota.sqlite3"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            for attempt in range(_SCHEMA_INIT_ATTEMPTS):
                try:
                    with self._connect() as connection:
                        connection.execute("PRAGMA journal_mode=WAL")
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS public_quick_chat_quota_event (
                                namespace TEXT NOT NULL,
                                quota_key TEXT NOT NULL,
                                occurred_at REAL NOT NULL
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_public_quick_chat_quota_window
                            ON public_quick_chat_quota_event(namespace, quota_key, occurred_at)
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_public_quick_chat_quota_cleanup
                            ON public_quick_chat_quota_event(namespace, occurred_at)
                            """
                        )
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS public_quick_chat_idempotency (
                                namespace TEXT NOT NULL,
                                request_key TEXT NOT NULL,
                                created_at REAL NOT NULL,
                                PRIMARY KEY(namespace, request_key)
                            )
                            """
                        )
                        connection.execute(
                            """
                            CREATE INDEX IF NOT EXISTS idx_public_quick_chat_idempotency_cleanup
                            ON public_quick_chat_idempotency(namespace, created_at)
                            """
                        )
                    break
                except sqlite3.OperationalError as exc:
                    locked = "locked" in str(exc).casefold()
                    if not locked or attempt + 1 >= _SCHEMA_INIT_ATTEMPTS:
                        raise
                    # WAL activation can briefly hold a lock while another
                    # worker creates the same database. Retry only that known
                    # transient condition; all other SQLite errors fail fast.
                    time.sleep(_SCHEMA_INIT_RETRY_SECONDS * (attempt + 1))
            self._initialized = True

    def reserve(
        self,
        key: str,
        *,
        minute_limit: int,
        day_limit: int,
        now: float | None = None,
        namespace: str = "public-quick-chat-v1",
    ) -> tuple[bool, int, int]:
        """Reserve a visitor quota slot, preserving the original API contract."""

        allowed, retry_after, remaining, _ = self._reserve(
            key,
            minute_limit=minute_limit,
            day_limit=day_limit,
            global_minute_limit=None,
            now=now,
            namespace=namespace,
        )
        return allowed, retry_after, remaining

    def reserve_with_capacity(
        self,
        key: str,
        *,
        minute_limit: int,
        day_limit: int,
        global_minute_limit: int,
        idempotency_key: str | None = None,
        idempotency_ttl_seconds: int = 900,
        now: float | None = None,
        namespace: str = "public-quick-chat-v1",
    ) -> tuple[bool, int, int, str]:
        """Atomically reserve visitor and installation-wide capacity.

        The global bucket protects a small ward deployment from the legitimate
        aggregate burst that per-visitor quotas alone cannot bound.  Both
        buckets are checked and written in the same SQLite transaction, so a
        rejected request never consumes only one of the two reservations.
        """

        return self._reserve(
            key,
            minute_limit=minute_limit,
            day_limit=day_limit,
            global_minute_limit=max(1, int(global_minute_limit)),
            idempotency_key=idempotency_key,
            idempotency_ttl_seconds=max(60, int(idempotency_ttl_seconds)),
            now=now,
            namespace=namespace,
        )

    def _reserve(
        self,
        key: str,
        *,
        minute_limit: int,
        day_limit: int,
        global_minute_limit: int | None,
        idempotency_key: str | None = None,
        idempotency_ttl_seconds: int = 900,
        now: float | None,
        namespace: str,
    ) -> tuple[bool, int, int, str]:
        """Perform the transactional visitor and optional global reservation."""

        self._ensure_schema()
        current = time.time() if now is None else float(now)
        minute_cutoff = current - 60
        day_cutoff = current - 86400
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM public_quick_chat_quota_event WHERE namespace=? AND occurred_at<=?",
                    (namespace, day_cutoff),
                )
                if global_minute_limit is not None:
                    # Global events are never used for a daily quota. Pruning
                    # them after one minute keeps the table bounded under load.
                    connection.execute(
                        """
                        DELETE FROM public_quick_chat_quota_event
                        WHERE namespace=? AND quota_key=? AND occurred_at<=?
                        """,
                        (namespace, _GLOBAL_MINUTE_CAPACITY_KEY, minute_cutoff),
                    )
                if idempotency_key:
                    idempotency_cutoff = current - idempotency_ttl_seconds
                    connection.execute(
                        """
                        DELETE FROM public_quick_chat_idempotency
                        WHERE namespace=? AND created_at<=?
                        """,
                        (namespace, idempotency_cutoff),
                    )
                minute_count, minute_oldest = connection.execute(
                    """
                    SELECT COUNT(*), MIN(occurred_at)
                    FROM public_quick_chat_quota_event
                    WHERE namespace=? AND quota_key=? AND occurred_at>?
                    """,
                    (namespace, key, minute_cutoff),
                ).fetchone()
                day_count, day_oldest = connection.execute(
                    """
                    SELECT COUNT(*), MIN(occurred_at)
                    FROM public_quick_chat_quota_event
                    WHERE namespace=? AND quota_key=? AND occurred_at>?
                    """,
                    (namespace, key, day_cutoff),
                ).fetchone()
                visitor_retry_values: list[float] = []
                if int(minute_count) >= minute_limit and minute_oldest is not None:
                    visitor_retry_values.append(float(minute_oldest) + 60 - current)
                if int(day_count) >= day_limit and day_oldest is not None:
                    visitor_retry_values.append(float(day_oldest) + 86400 - current)

                global_retry_value: float | None = None
                if global_minute_limit is not None:
                    global_count, global_oldest = connection.execute(
                        """
                        SELECT COUNT(*), MIN(occurred_at)
                        FROM public_quick_chat_quota_event
                        WHERE namespace=? AND quota_key=? AND occurred_at>?
                        """,
                        (namespace, _GLOBAL_MINUTE_CAPACITY_KEY, minute_cutoff),
                    ).fetchone()
                    if (
                        int(global_count) >= global_minute_limit
                        and global_oldest is not None
                    ):
                        global_retry_value = float(global_oldest) + 60 - current

                if idempotency_key:
                    replay = connection.execute(
                        """
                        SELECT 1
                        FROM public_quick_chat_idempotency
                        WHERE namespace=? AND request_key=? AND created_at>?
                        """,
                        (namespace, idempotency_key, idempotency_cutoff),
                    ).fetchone()
                    if replay is not None:
                        connection.commit()
                        remaining = max(
                            0,
                            min(
                                minute_limit - int(minute_count),
                                day_limit - int(day_count),
                            ),
                        )
                        return True, 0, remaining, "IDEMPOTENT_REPLAY"

                if visitor_retry_values or global_retry_value is not None:
                    connection.commit()
                    remaining = max(
                        0,
                        min(
                            minute_limit - int(minute_count), day_limit - int(day_count)
                        ),
                    )
                    retry_values = list(visitor_retry_values)
                    if global_retry_value is not None:
                        retry_values.append(global_retry_value)
                    reason_code = (
                        "GUEST_QUOTA_EXCEEDED"
                        if visitor_retry_values
                        else "QUICK_CHAT_CAPACITY_EXCEEDED"
                    )
                    return (
                        False,
                        max(1, math.ceil(max(retry_values))),
                        remaining,
                        reason_code,
                    )
                connection.execute(
                    "INSERT INTO public_quick_chat_quota_event(namespace, quota_key, occurred_at) VALUES(?,?,?)",
                    (namespace, key, current),
                )
                if global_minute_limit is not None:
                    connection.execute(
                        "INSERT INTO public_quick_chat_quota_event(namespace, quota_key, occurred_at) VALUES(?,?,?)",
                        (namespace, _GLOBAL_MINUTE_CAPACITY_KEY, current),
                    )
                if idempotency_key:
                    connection.execute(
                        """
                        INSERT INTO public_quick_chat_idempotency(namespace, request_key, created_at)
                        VALUES(?,?,?)
                        ON CONFLICT(namespace, request_key)
                        DO UPDATE SET created_at=excluded.created_at
                        """,
                        (namespace, idempotency_key, current),
                    )
                connection.commit()
                remaining = max(
                    0,
                    min(
                        minute_limit - int(minute_count) - 1,
                        day_limit - int(day_count) - 1,
                    ),
                )
                return True, 0, remaining, "QUOTA_RESERVED"
            except Exception:
                connection.rollback()
                raise


__all__ = ["SharedQuickChatQuota"]
