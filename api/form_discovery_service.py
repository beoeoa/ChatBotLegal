"""Non-blocking discovery of missing official forms.

The chat path may queue discovery, but it must never wait for a website or
expose a discovered file before admin review. The worker writes only to the
candidate queue maintained by ``scripts.discover_missing_faq_forms``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

_task: asyncio.Task[Any] | None = None
_last_report: dict[str, Any] | None = None
_last_error: str | None = None


def _run(limit: int) -> dict[str, Any]:
    from scripts.discover_missing_faq_forms import discover_missing_forms

    return discover_missing_forms(limit=limit)


async def _worker(limit: int) -> None:
    global _last_report, _last_error
    try:
        _last_report = await asyncio.to_thread(_run, limit)
        _last_error = None
    except Exception as exc:  # discovery must never break chat or API startup
        _last_error = str(exc)
        _last_report = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "checked": 0,
            "candidates": 0,
            "downloaded": 0,
            "errors": [{"error": str(exc)}],
        }


def queue_missing_form_discovery(limit: int = 50) -> dict[str, Any]:
    """Queue one discovery run and return its current status immediately."""
    global _task
    if _task is not None and not _task.done():
        return {"status": "running", "report": _last_report}
    _task = asyncio.create_task(_worker(max(1, min(int(limit), 100))))
    return {"status": "queued", "report": _last_report}


def discovery_status() -> dict[str, Any]:
    running = _task is not None and not _task.done()
    return {
        "status": "running" if running else "idle",
        "last_error": _last_error,
        "report": _last_report,
    }
