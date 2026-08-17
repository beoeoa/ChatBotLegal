"""In-process liveness signal for the legal import worker.

The import worker runs in the API process today. Queue state alone cannot tell
an admin whether that task is still alive, so the worker records a monotonic
heartbeat which the import readiness gate can safely expose.
"""

from __future__ import annotations

import os
from time import monotonic
from typing import Any


_last_heartbeat: float | None = None


def _enabled() -> bool:
    return str(os.getenv("LEGAL_IMPORT_WORKER_ENABLED", "true")).strip().lower() not in {
        "false",
        "0",
        "no",
    }


def _max_age_seconds() -> float:
    try:
        value = float(os.getenv("LEGAL_IMPORT_WORKER_HEARTBEAT_SECONDS", "30"))
    except (TypeError, ValueError):
        value = 30.0
    return min(max(value, 5.0), 300.0)


def record_import_worker_heartbeat() -> None:
    """Record progress from the worker's own event loop iteration."""
    global _last_heartbeat
    _last_heartbeat = monotonic()


def import_worker_component() -> dict[str, Any]:
    """Return a privacy-safe readiness component for queue consumption."""
    if not _enabled():
        return {"healthy": False, "code": "disabled", "required": True}
    if _last_heartbeat is None:
        return {"healthy": False, "code": "not_started", "required": True}
    age_seconds = max(0, round(monotonic() - _last_heartbeat, 1))
    max_age_seconds = _max_age_seconds()
    return {
        "healthy": age_seconds <= max_age_seconds,
        "code": "ready" if age_seconds <= max_age_seconds else "stale",
        "required": True,
        "heartbeat_age_seconds": age_seconds,
        "max_heartbeat_age_seconds": max_age_seconds,
    }


def _reset_for_test() -> None:
    """Reset only for isolated test processes."""
    global _last_heartbeat
    _last_heartbeat = None
