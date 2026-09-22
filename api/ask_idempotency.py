"""Short-lived in-process idempotency for non-streaming Ask requests."""

from __future__ import annotations

import asyncio
import time
from typing import Any


_TTL_SECONDS = 300.0
_lock = asyncio.Lock()
_completed: dict[str, tuple[float, Any]] = {}
_inflight: dict[str, asyncio.Future] = {}


async def begin(key: str | None) -> tuple[str, Any]:
    if not key:
        return "disabled", None
    now = time.monotonic()
    async with _lock:
        for old_key, (expires_at, _) in list(_completed.items()):
            if expires_at <= now:
                _completed.pop(old_key, None)
        cached = _completed.get(key)
        if cached:
            return "cached", cached[1]
        future = _inflight.get(key)
        if future:
            return "wait", future
        future = asyncio.get_running_loop().create_future()
        # The owner may fail before another request starts waiting. Consume the
        # exception so asyncio does not report an unhandled future.
        future.add_done_callback(
            lambda completed: completed.exception()
            if not completed.cancelled() and completed.exception() is not None
            else None
        )
        _inflight[key] = future
        return "owner", future


def is_cacheable_result(value: Any) -> bool:
    """Return whether an Ask result is safe to replay for a retry.

    Idempotency still resolves concurrent waiters with every completed result,
    but transient/failed legal answers must not be retained as a five-minute
    replay.  Otherwise the UI's intentional stable retry key would replay a
    ``cannot_verify`` response after the retrieval service has recovered.
    """

    def field(name: str, default: Any = None) -> Any:
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    status = str(field("answer_status", "") or "").strip().casefold()
    grounding = str(field("grounding_status", "") or "").strip().casefold()
    try:
        evidence_count = int(field("evidence_count", 0) or 0)
    except (TypeError, ValueError):
        evidence_count = 0
    if status == "cannot_verify":
        return False
    if grounding in {"insufficient_evidence", "retrieval_unavailable"}:
        return False
    if evidence_count <= 0:
        return False
    return True


async def complete(
    key: str | None,
    value: Any,
    *,
    cacheable: bool = True,
) -> None:
    if not key:
        return
    async with _lock:
        future = _inflight.pop(key, None)
        if cacheable:
            _completed[key] = (time.monotonic() + _TTL_SECONDS, value)
        if future and not future.done():
            future.set_result(value)


async def fail(key: str | None, exc: BaseException) -> None:
    if not key:
        return
    async with _lock:
        future = _inflight.pop(key, None)
        if future and not future.done():
            future.set_exception(exc)


def scoped_key(*, user_id: str | None, role: str | None, key: str | None) -> str | None:
    if not key:
        return None
    return f"{user_id or 'legacy'}:{role or 'citizen'}:{key}"
