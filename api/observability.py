"""Bounded, privacy-preserving runtime telemetry for the local pilot.

The telemetry store intentionally contains no request/answer text, user IDs,
cookies, authorization headers, uploaded file names or upload content.  It is a
small in-memory operational aid for the admin quality dashboard, not an audit
log and not a product analytics service.
"""

from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timezone
from math import ceil
from time import perf_counter
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware


_SAFE_METADATA_KEYS = {
    "status",
    "outcome",
    "error_class",
    "job_type",
    "source_type",
    "cached",
    "origin",
    "planner_mode",
    "issue_count",
    "query_count",
    "candidate_count",
    "rejected_count",
    "missing_coverage_count",
    "supplemental_round",
    "removed_noise_count",
    "prompt_chars",
}
_SAFE_OUTCOMES = {"success", "error", "cancelled", "queued", "failed", "skipped"}
_ASK_STAGES = (
    "queue",
    "retrieval",
    "generation",
    "repair",
    "validation",
    "persistence",
    "total",
)
_ASK_OUTCOMES = {
    "repair": ("attempted", "skipped", "success", "failed"),
    "audit": ("success", "failed"),
}


class RuntimeTelemetry:
    """Retain a bounded, sanitized window of runtime operation measurements."""

    def __init__(self, max_events: int = 1000, max_issues: int = 300) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=max(1, max_events))
        self._issues: deque[dict[str, Any]] = deque(maxlen=max(1, max_issues))
        # Keys and values both come from fixed allowlists, so this counter can
        # never retain questions, answers, actor identifiers or error text.
        self._ask_outcomes: Counter[tuple[str, str]] = Counter()

    @staticmethod
    def category(path: str) -> str:
        route = (path or "").split("?", 1)[0]
        if route.startswith("/api/search/ask"):
            return "ask"
        if route.startswith("/api/legal/docs/") and route.endswith("/download.pdf"):
            return "pdf_stream_export"
        if route.startswith("/api/legal/docs/"):
            return "document_view"
        if route.startswith("/api/faq"):
            return "faq_load"
        if route.startswith("/api/conversations"):
            return "conversation_load"
        if route.startswith("/api/live-support"):
            return "support_delivery"
        if "/crawl/candidates/" in route and route.endswith("/extract"):
            return "ocr"
        if "/crawl/" in route:
            return "crawler"
        if "/import" in route:
            return "import_job"
        if "/download" in route:
            return "form_download"
        return "other"

    @staticmethod
    def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        if not metadata:
            return {}
        safe: dict[str, Any] = {}
        for key, value in metadata.items():
            if key not in _SAFE_METADATA_KEYS:
                continue
            if isinstance(value, (str, int, float, bool)) and len(str(value)) <= 80:
                safe[key] = value
        return safe

    def record_operation(
        self,
        *,
        category: str,
        duration_ms: int | float,
        outcome: str = "success",
        status_code: int | None = None,
        route: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record sanitized timing data for a completed operation.

        `route` must be a route template or path without a query string.  Callers
        must never pass question text, URL query values, actor IDs or file names.
        """
        safe_outcome = outcome if outcome in _SAFE_OUTCOMES else "error"
        event: dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "category": str(category or "other")[:80],
            "duration_ms": max(0, int(duration_ms)),
            "outcome": safe_outcome,
        }
        if status_code is not None:
            event["status_code"] = int(status_code)
        if route:
            event["route"] = route.split("?", 1)[0][:160]
        event.update(self._safe_metadata(metadata))
        self._events.append(event)

    def add(self, path: str, status_code: int, duration_ms: int) -> None:
        if not path.startswith("/api/"):
            return
        self.record_operation(
            category=self.category(path),
            route=path.split("?", 1)[0],
            status_code=status_code,
            duration_ms=duration_ms,
            outcome="error" if int(status_code) >= 400 else "success",
        )

    def record_ask_stage(
        self,
        stage: str,
        *,
        duration_ms: int | float,
        outcome: str = "success",
        cached: bool | None = None,
    ) -> None:
        """Record one Ask stage using a fixed, privacy-safe category."""
        if stage not in _ASK_STAGES:
            raise ValueError(f"Unsupported Ask stage: {stage!r}")
        metadata = {"cached": cached} if cached is not None else None
        self.record_operation(
            category=f"ask.{stage}",
            duration_ms=duration_ms,
            outcome=outcome,
            metadata=metadata,
        )

    def record_ask_outcome(self, kind: str, outcome: str) -> None:
        """Increment a fixed repair/audit outcome without retaining payloads."""
        allowed = _ASK_OUTCOMES.get(kind)
        if allowed is None:
            raise ValueError(f"Unsupported Ask outcome kind: {kind!r}")
        if outcome not in allowed:
            raise ValueError(f"Unsupported Ask outcome for {kind!r}: {outcome!r}")
        self._ask_outcomes[(kind, outcome)] += 1

    def record_legal_orchestration(
        self,
        *,
        duration_ms: int | float,
        planner_mode: str,
        issue_count: int,
        query_count: int,
        candidate_count: int,
        rejected_count: int,
        missing_coverage_count: int,
        supplemental_round: int,
        removed_noise_count: int,
        prompt_chars: int,
        outcome: str = "success",
    ) -> None:
        """Record bounded multi-issue counters without legal or user content."""

        self.record_operation(
            category="ask.legal_orchestration",
            duration_ms=duration_ms,
            outcome=outcome,
            metadata={
                "planner_mode": planner_mode,
                "issue_count": issue_count,
                "query_count": query_count,
                "candidate_count": candidate_count,
                "rejected_count": rejected_count,
                "missing_coverage_count": missing_coverage_count,
                "supplemental_round": supplemental_round,
                "removed_noise_count": removed_noise_count,
                "prompt_chars": prompt_chars,
            },
        )

    def record_issue(
        self,
        issue_type: str,
        *,
        category: str,
        status_code: int | None = None,
        error_class: str | None = None,
    ) -> None:
        """Record a bounded operational issue without sensitive diagnostics."""
        issue = {
            "at": datetime.now(timezone.utc).isoformat(),
            "issue_type": str(issue_type)[:80],
            "category": str(category)[:80],
        }
        if status_code is not None:
            issue["status_code"] = int(status_code)
        if error_class:
            issue["error_class"] = str(error_class)[:80]
        self._issues.append(issue)

    @staticmethod
    def _nearest_rank_percentile(values: list[int], percentile: int) -> int:
        """Return a deterministic nearest-rank percentile for a non-empty list."""
        ordered = sorted(values)
        index = max(0, ceil((percentile / 100) * len(ordered)) - 1)
        return ordered[index]

    def summary(self, slow_threshold_ms: int = 5000) -> dict[str, Any]:
        events = list(self._events)
        by_category: dict[str, dict[str, int]] = {}
        for category in sorted({str(event["category"]) for event in events}):
            rows = [event for event in events if event["category"] == category]
            durations = [int(row["duration_ms"]) for row in rows]
            by_category[category] = {
                "count": len(rows),
                "avg_ms": round(sum(durations) / max(1, len(durations))),
                "p50_ms": self._nearest_rank_percentile(durations, 50),
                "p95_ms": self._nearest_rank_percentile(durations, 95),
                "max_ms": max(durations, default=0),
                "error_count": sum(row.get("outcome") in {"error", "failed"} or int(row.get("status_code", 0)) >= 400 for row in rows),
                "cancelled_count": sum(row.get("outcome") == "cancelled" for row in rows),
            }
        slow = sorted(
            (event for event in events if int(event["duration_ms"]) >= max(1, slow_threshold_ms)),
            key=lambda item: int(item["duration_ms"]),
            reverse=True,
        )[:50]
        issues = list(self._issues)
        return {
            "event_count": len(events),
            "by_category": by_category,
            "slow_requests": slow,
            "error_statuses": dict(Counter(str(event["status_code"]) for event in events if int(event.get("status_code", 0)) >= 400)),
            "issues": issues[-100:],
            "issue_counts": dict(Counter(str(issue["issue_type"]) for issue in issues)),
            "ask_outcomes": {
                kind: {
                    outcome: int(self._ask_outcomes[(kind, outcome)])
                    for outcome in outcomes
                }
                for kind, outcomes in _ASK_OUTCOMES.items()
            },
            "privacy": "No request/answer content, identity, cookie, query string, file name or upload content is retained.",
        }


telemetry = RuntimeTelemetry()


class RuntimeTelemetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        started = perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-Duration-Ms"] = str(round((perf_counter() - started) * 1000, 1))
            return response
        finally:
            # Ask endpoints record one explicit total stage in the router so
            # aggregate counts are not doubled by this generic middleware.
            if not request.url.path.startswith("/api/search/ask/simple"):
                telemetry.add(
                    request.url.path,
                    status_code,
                    round((perf_counter() - started) * 1000),
                )
