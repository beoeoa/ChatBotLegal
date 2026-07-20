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


async def complete(key: str | None, value: Any) -> None:
    if not key:
        return
    async with _lock:
        future = _inflight.pop(key, None)
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
