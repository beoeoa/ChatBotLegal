"""Near-real-time official legal-validity synchronization.

The service preserves official observations outside the legal corpus and only
publishes an atomic serving snapshot after a complete bounded batch.  Source
failures are explicit and never erase or re-enable the last protected state.
"""

from __future__ import annotations

import asyncio
import json
import os
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable, Iterable
from uuid import uuid4

import httpx

from api.data_paths import notebook_data_dir
from api.legal_validity_models import normalize_law_number, vietnam_legal_date
from api.legal_validity_registry import (
    LegalValidityRegistry,
    default_registry,
    default_snapshot_cache,
)
from api.legal_validity_source import VBPLValiditySource
from open_notebook.database.repository import repo_create, repo_query

REPORT_PATH = notebook_data_dir() / "operations" / "legal-effectivity-latest.json"
LEGAL_SEARCH_URL = os.getenv("LEGAL_SEARCH_URL", "http://127.0.0.1:8765").rstrip("/")

DocumentLoader = Callable[..., Awaitable[list[dict[str, Any]]]]
NotificationHandler = Callable[[dict[str, Any]], Awaitable[None]]


class DocumentInventory(list[dict[str, Any]]):
    def __init__(
        self,
        items: Iterable[dict[str, Any]],
        *,
        eligible_count: int,
        raw_count: int,
    ) -> None:
        super().__init__(items)
        self.eligible_count = max(int(eligible_count), 0)
        self.raw_count = max(int(raw_count), 0)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _canonical_scope(value: Any) -> str | None:
    text = unicodedata.normalize("NFD", str(value or "").casefold()).replace("đ", "d")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = " ".join(text.split())
    if text in {"central", "haiphong", "local"}:
        return text
    if "hai phong" in text:
        return "haiphong"
    if "trung uong" in text or "toan quoc" in text:
        return "central"
    if "dia phuong" in text or "cap tinh" in text or "cap xa" in text:
        return "local"
    return None


def _mode() -> str:
    value = os.getenv("LEGAL_VALIDITY_SYNC_MODE", "protect").casefold()
    return value if value in {"observe", "protect", "strict"} else "protect"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _schedule_after_run(
    *, previous: dict[str, Any], completed_at: datetime, failed: bool
) -> dict[str, Any]:
    interval = _bounded_float(
        "LEGAL_VALIDITY_SYNC_INTERVAL_SECONDS", 1800.0, 60.0, 24 * 60 * 60.0
    )
    previous_failures = int(previous.get("consecutive_failures") or 0)
    consecutive_failures = previous_failures + 1 if failed else 0
    delay = interval
    if failed:
        delay = min(interval * (2 ** max(consecutive_failures - 1, 0)), 6 * 60 * 60.0)
    recovered = not failed and previous.get("status") in {"degraded", "failed"}
    return {
        "consecutive_failures": consecutive_failures,
        "next_run_at": (completed_at + timedelta(seconds=delay)).isoformat(),
        "recovered": recovered,
    }


async def notify_validity_admin(report: dict[str, Any]) -> None:
    """Emit one deduplicated, privacy-safe notification through the existing queue."""

    failures = dict(report.get("failures") or {})
    events_created = int(report.get("events_created") or 0)
    recovered = bool(report.get("recovered"))
    if not failures and not events_created and not recovered:
        return
    if failures:
        category = "source_degraded"
        stable_details = ",".join(sorted(failures))
        title = "Đồng bộ hiệu lực pháp lý đang suy giảm"
        message = "Nguồn chính thức chưa phản hồi ổn định; snapshot bảo vệ gần nhất vẫn được giữ nguyên."
        priority = "critical"
    elif recovered:
        category = "source_recovered"
        stable_details = str(report.get("last_success_at") or report.get("checked_at") or "recovered")
        title = "Nguồn đồng bộ hiệu lực đã phục hồi"
        message = "Lượt đồng bộ mới đã hoàn tất; snapshot bảo vệ được cập nhật bằng bằng chứng chính thức."
        priority = "normal"
    else:
        category = "review_events"
        stable_details = str(events_created)
        title = "Có thay đổi hiệu lực pháp lý chờ kiểm tra"
        message = f"Hệ thống phát hiện {events_created} sự kiện cần Admin đối chiếu bằng chứng."
        priority = "high"
    import hashlib

    dedupe_key = hashlib.sha256(
        f"legal-validity:{category}:{stable_details}".encode("utf-8")
    ).hexdigest()
    try:
        existing = await repo_query(
            "SELECT id FROM legal_admin_notification WHERE dedupe_key = $dedupe_key AND read_at = NONE LIMIT 1;",
            {"dedupe_key": dedupe_key},
        )
        if existing:
            return
        now = datetime.now(timezone.utc)
        await repo_create(
            "legal_admin_notification",
            {
                "type": "legal_validity_sync",
                "dedupe_key": dedupe_key,
                "priority": priority,
                "title": title,
                "message": message,
                "candidate": None,
                "read_at": None,
                "created": now,
                "updated": now,
            },
        )
    except Exception:
        # Notification delivery is observable through source health, but must
        # not invalidate a persisted observation batch or serving snapshot.
        return


async def _load_runtime_documents(
    *, scopes: tuple[str, ...], limit: int, offset: int, as_of: date
) -> list[dict[str, Any]]:
    """Read active document metadata from the existing retrieval service."""

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{LEGAL_SEARCH_URL}/documents",
            params={
                "as_of": as_of.isoformat(),
                "limit": min(max(limit, 1), 100),
                "offset": max(int(offset), 0),
                "tier": "all",
            },
        )
        response.raise_for_status()
        payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("LEGAL_DOCUMENT_INVENTORY_INVALID")
    allowed_scopes = {scope.casefold() for scope in scopes}
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        scope = _canonical_scope(item.get("scope"))
        # Some legacy central rows use Vietnamese display labels. Keep rows
        # when all configured scopes are requested; otherwise require a match.
        if len(allowed_scopes) < 3 and scope not in allowed_scopes:
            continue
        result.append(item)
    all_scopes = allowed_scopes == {"central", "haiphong", "local"}
    eligible_count = int(payload.get("total") or len(items)) if all_scopes else len(result)
    return DocumentInventory(
        result[:limit],
        eligible_count=eligible_count,
        raw_count=len(items),
    )


class LegalValiditySyncService:
    def __init__(
        self,
        *,
        registry: LegalValidityRegistry = default_registry,
        source: VBPLValiditySource | Any | None = None,
        document_loader: DocumentLoader = _load_runtime_documents,
        now: Callable[[], datetime] | None = None,
        batch_size: int | None = None,
        max_concurrency: int | None = None,
        rate_limit_seconds: float | None = None,
        report_path: Path = REPORT_PATH,
        worker_id: str | None = None,
        notifier: NotificationHandler | None = None,
    ) -> None:
        self.registry = registry
        self.source = source or VBPLValiditySource()
        self.document_loader = document_loader
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.batch_size = batch_size or _bounded_int(
            "LEGAL_VALIDITY_SYNC_BATCH_SIZE", 100, 1, 100
        )
        self.max_concurrency = max_concurrency or _bounded_int(
            "LEGAL_VALIDITY_SYNC_MAX_CONCURRENCY", 3, 1, 8
        )
        self.rate_limit_seconds = (
            rate_limit_seconds
            if rate_limit_seconds is not None
            else _bounded_float("LEGAL_VALIDITY_SYNC_RATE_LIMIT_SECONDS", 1.0, 0.0, 30.0)
        )
        self.report_path = Path(report_path)
        self.worker_id = worker_id or f"api-{uuid4().hex[:12]}"
        self.notifier = notifier if notifier is not None else (
            notify_validity_admin if registry is default_registry else None
        )
        self._request_lock = asyncio.Lock()
        self._last_request_tick = 0.0

    async def status(self) -> dict[str, Any]:
        """Return a privacy-safe operational summary for the Admin dashboard."""

        now = self.now().astimezone(timezone.utc)
        snapshot = default_snapshot_cache.load()
        if self.registry.snapshot_path != default_registry.snapshot_path:
            from api.legal_validity_registry import ValiditySnapshotCache

            snapshot = ValiditySnapshotCache(self.registry.snapshot_path).load()
        stale_after = _bounded_float(
            "LEGAL_VALIDITY_STALE_AFTER_SECONDS", 21600.0, 60.0, 7 * 24 * 60 * 60.0
        )
        health = self.registry.snapshot_health(
            snapshot,
            now=now,
            stale_after_seconds=stale_after,
        )
        report = latest_effectivity_report() or {}
        if self.report_path != REPORT_PATH:
            try:
                parsed = json.loads(self.report_path.read_text(encoding="utf-8"))
                report = parsed if isinstance(parsed, dict) else report
            except (OSError, json.JSONDecodeError):
                pass
        documents = (snapshot or {}).get("documents") or {}
        status_counts = Counter(
            str(item.get("normalized_status") or "unknown")
            for item in documents.values()
            if isinstance(item, dict)
        )
        blocked = sum(
            1
            for item in documents.values()
            if isinstance(item, dict)
            and str(item.get("serving_action") or "")
            in {"block", "historical_only", "block_provisions"}
        )
        warnings = sum(
            1
            for item in documents.values()
            if isinstance(item, dict) and bool(item.get("warning_code"))
        )
        open_events = await self.registry.count_open_events()
        last_success_at = (snapshot or {}).get("last_success_at") or report.get("last_success_at")
        checked_at = _as_datetime(report.get("checked_at"))
        next_run_at = report.get("next_run_at")
        if not next_run_at:
            interval = _bounded_float(
                "LEGAL_VALIDITY_SYNC_INTERVAL_SECONDS", 1800.0, 60.0, 24 * 60 * 60.0
            )
            next_run_at = ((checked_at or now) + timedelta(seconds=interval)).isoformat()
        failures = dict(report.get("failures") or {})
        source_status = "healthy"
        reason_code = None
        if failures or report.get("status") in {"degraded", "failed"}:
            source_status = "degraded"
            reason_code = sorted(failures)[0] if failures else "VALIDITY_SYNC_FAILED"
        dashboard_status = health.get("status")
        if source_status == "degraded" and dashboard_status == "healthy":
            dashboard_status = "degraded"
        coverage = dict(
            (snapshot or {}).get(
                "coverage", {"eligible": 0, "observed": 0, "fresh": 0}
            )
        )
        if health.get("status") in {"missing", "stale", "degraded"}:
            coverage["fresh"] = 0
        return {
            "mode": _mode(),
            "status": dashboard_status,
            "generated_at": (snapshot or {}).get("generated_at"),
            "last_success_at": last_success_at,
            "next_run_at": next_run_at,
            "coverage": coverage,
            "counts": {
                "active": status_counts.get("active", 0),
                "blocked": blocked,
                "warning": warnings,
                "open_events": open_events,
            },
            "sources": [
                {
                    "source_kind": "vbpl",
                    "status": source_status,
                    "last_success_at": last_success_at,
                    "reason_code": reason_code,
                }
            ],
            "reason_code": health.get("reason_code"),
            "age_seconds": health.get("age_seconds"),
        }

    async def _fetch_one(self, document: dict[str, Any], as_of: date):
        async with self._request_lock:
            if self.rate_limit_seconds:
                loop = asyncio.get_running_loop()
                wait = self.rate_limit_seconds - (loop.time() - self._last_request_tick)
                if wait > 0:
                    await asyncio.sleep(wait)
                self._last_request_tick = loop.time()
        return await self.source.fetch(
            instrument=str(document.get("law_number") or ""),
            document_id=str(document.get("doc_id") or document.get("document_id") or "") or None,
            as_of=as_of,
            expected_issuing_agency=str(document.get("issuing_agency") or "") or None,
            expected_issued_date=str(document.get("issued_date") or "") or None,
        )

    async def run(
        self,
        *,
        trigger: str,
        requested_by: str | None = None,
        reason: str | None = None,
        scopes: Iterable[str] = ("central", "haiphong", "local"),
        limit: int | None = None,
    ) -> dict[str, Any]:
        started_at = self.now().astimezone(timezone.utc)
        started_tick = perf_counter()
        previous_report = _read_report(self.report_path)
        legal_as_of = vietnam_legal_date(started_at)
        normalized_scopes = tuple(
            scope for scope in (str(value).casefold() for value in scopes) if scope in {"central", "haiphong", "local"}
        ) or ("central", "haiphong", "local")
        bounded_limit = min(max(int(limit or self.batch_size), 1), self.batch_size, 100)
        lease_ttl = max(300.0, bounded_limit * max(self.rate_limit_seconds, 1.0) * 2)
        if not await self.registry.acquire_lease(
            owner=self.worker_id,
            now=started_at,
            ttl_seconds=lease_ttl,
        ):
            return {
                "checked_at": started_at.isoformat(),
                "status": "skipped_lease",
                "scanned": {"documents": 0, "forms": 0},
                "observations_created": 0,
                "events_created": 0,
                "failures": {},
            }

        run_record: dict[str, Any] | None = None
        try:
            run_record = await self.registry.start_sync_run(
                trigger=trigger,
                requested_by=requested_by,
                reason=reason,
                scopes=normalized_scopes,
                started_at=started_at,
            )
            inventory_offset = (
                max(int(previous_report.get("next_offset") or 0), 0)
                if trigger == "scheduler"
                and set(normalized_scopes) == {"central", "haiphong", "local"}
                else 0
            )
            loaded_documents = await self.document_loader(
                scopes=normalized_scopes,
                limit=bounded_limit,
                offset=inventory_offset,
                as_of=legal_as_of,
            )
            eligible_count = max(
                int(getattr(loaded_documents, "eligible_count", len(loaded_documents))),
                len(loaded_documents),
            )
            raw_count = max(
                int(getattr(loaded_documents, "raw_count", len(loaded_documents))),
                len(loaded_documents),
            )
            if set(normalized_scopes) != {"central", "haiphong", "local"}:
                existing_snapshot = (
                    default_snapshot_cache.load()
                    if self.registry is default_registry
                    else None
                )
                eligible_count = max(
                    eligible_count,
                    int(((existing_snapshot or {}).get("coverage") or {}).get("eligible") or 0),
                )
            next_offset = (
                inventory_offset + raw_count
                if raw_count and inventory_offset + raw_count < eligible_count
                else 0
            )
            if trigger != "scheduler":
                next_offset = max(int(previous_report.get("next_offset") or 0), 0)
            documents = list(loaded_documents)
            documents = [
                item
                for item in documents
                if isinstance(item, dict) and normalize_law_number(item.get("law_number"))
            ][:bounded_limit]
            semaphore = asyncio.Semaphore(self.max_concurrency)

            async def fetch(document: dict[str, Any]):
                async with semaphore:
                    return document, await self._fetch_one(document, legal_as_of)

            fetched = await asyncio.gather(
                *(fetch(document) for document in documents), return_exceptions=True
            )
            failures: Counter[str] = Counter()
            counts: Counter[str] = Counter()
            findings: list[dict[str, Any]] = []
            observations_created = 0
            events_created = 0
            for item in fetched:
                if isinstance(item, Exception):
                    failures["VALIDITY_SYNC_UNCLASSIFIED_FAILURE"] += 1
                    continue
                document, result = item
                if result.observation is None:
                    failures[result.reason_code] += 1
                    continue
                recorded = await self.registry.record_observation(
                    result.observation,
                    scope=str(document.get("scope") or "") or None,
                    document_title=str(document.get("title") or document.get("document_title") or "") or None,
                )
                if recorded["created"]:
                    observations_created += 1
                if recorded.get("event"):
                    events_created += 1
                counts[result.observation.normalized_status.value] += 1
                if result.reason_code != "EXACT_OFFICIAL_VALIDITY_OBSERVED" or recorded.get("event"):
                    findings.append(
                        {
                            "document_id": result.observation.document_id,
                            "law_number": result.observation.law_number,
                            "source_url": result.observation.source_url,
                            "normalized_status": result.observation.normalized_status.value,
                            "reason_code": result.reason_code,
                            "event_id": str((recorded.get("event") or {}).get("id") or "") or None,
                        }
                    )

            completed_at = self.now().astimezone(timezone.utc)
            status = "degraded" if failures else "completed"
            if not failures:
                latest = await self.registry.latest_serving_observations()
                self.registry.write_snapshot(
                    observations=latest,
                    last_success_at=completed_at,
                    eligible_count=eligible_count,
                    mode=_mode(),
                )
            report = {
                "checked_at": completed_at.isoformat(),
                "last_success_at": completed_at.isoformat() if not failures else None,
                "status": status,
                "mode": _mode(),
                "trigger": trigger,
                "counts": dict(counts),
                "scanned": {"documents": len(documents), "forms": 0},
                "observations_created": observations_created,
                "events_created": events_created,
                "inventory_offset": inventory_offset,
                "next_offset": next_offset,
                "failures": dict(sorted(failures.items())),
                "findings": findings[:500],
                "duration_ms": round((perf_counter() - started_tick) * 1000, 3),
                "limitation": "Không suy đoán trạng thái hoặc phạm vi hết hiệu lực một phần khi nguồn chính thức không đủ bằng chứng.",
                **_schedule_after_run(
                    previous=previous_report,
                    completed_at=completed_at,
                    failed=bool(failures),
                ),
            }
            _atomic_json(self.report_path, report)
            if run_record:
                await self.registry.finish_sync_run(
                    run_record["id"], completed_at=completed_at, payload=report
                )
            if self.notifier is not None:
                await self.notifier(report)
            return report
        except Exception:
            completed_at = self.now().astimezone(timezone.utc)
            report = {
                "checked_at": completed_at.isoformat(),
                "last_success_at": None,
                "status": "failed",
                "mode": _mode(),
                "trigger": trigger,
                "counts": {},
                "scanned": {"documents": 0, "forms": 0},
                "observations_created": 0,
                "events_created": 0,
                "failures": {"VALIDITY_SYNC_INTERNAL_FAILURE": 1},
                "findings": [],
                "duration_ms": round((perf_counter() - started_tick) * 1000, 3),
                "limitation": "Lần đồng bộ thất bại; snapshot bảo vệ gần nhất được giữ nguyên.",
                **_schedule_after_run(
                    previous=previous_report,
                    completed_at=completed_at,
                    failed=True,
                ),
            }
            _atomic_json(self.report_path, report)
            if run_record:
                await self.registry.finish_sync_run(
                    run_record["id"], completed_at=completed_at, payload=report
                )
            if self.notifier is not None:
                await self.notifier(report)
            return report
        finally:
            await self.registry.release_lease(owner=self.worker_id, now=self.now())


_default_service: LegalValiditySyncService | None = None


def get_legal_validity_sync_service() -> LegalValiditySyncService:
    global _default_service
    if _default_service is None:
        _default_service = LegalValiditySyncService()
    return _default_service


async def run_effectivity_check() -> dict[str, Any]:
    """Backward-compatible Admin action backed by the new validity sync."""

    return await get_legal_validity_sync_service().run(trigger="admin")


def latest_effectivity_report() -> dict[str, Any] | None:
    try:
        payload = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        snapshot = default_snapshot_cache.load()
        if not snapshot:
            return None
        return {
            "checked_at": snapshot.get("generated_at"),
            "last_success_at": snapshot.get("last_success_at"),
            "status": "completed",
            "mode": snapshot.get("mode"),
            "coverage": snapshot.get("coverage"),
            "counts": {},
            "scanned": {"documents": snapshot.get("coverage", {}).get("eligible", 0), "forms": 0},
            "findings": [],
        }


async def legal_effectivity_scheduler_loop() -> None:
    """Run the existing in-process scheduler at a bounded near-real-time interval."""

    interval = _bounded_float(
        "LEGAL_VALIDITY_SYNC_INTERVAL_SECONDS", 1800.0, 60.0, 24 * 60 * 60.0
    )
    while True:
        try:
            enabled = os.getenv("LEGAL_VALIDITY_SYNC_ENABLED", "true").casefold() not in {
                "0",
                "false",
                "no",
            }
            if enabled:
                report = latest_effectivity_report()
                checked_at = _as_datetime((report or {}).get("checked_at"))
                next_run_at = _as_datetime((report or {}).get("next_run_at"))
                now = datetime.now(timezone.utc)
                if next_run_at is not None:
                    due = now >= next_run_at
                else:
                    due = checked_at is None or (now - checked_at).total_seconds() >= interval
                if due:
                    await get_legal_validity_sync_service().run(trigger="scheduler")
        except Exception:
            # The report/snapshot expose degraded state; scheduler failures must
            # never crash the API process or erase the last serving guard.
            pass
        await asyncio.sleep(min(interval, 60.0))


def _as_datetime(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


__all__ = [
    "DocumentInventory",
    "LegalValiditySyncService",
    "get_legal_validity_sync_service",
    "latest_effectivity_report",
    "legal_effectivity_scheduler_loop",
    "notify_validity_admin",
    "run_effectivity_check",
]
