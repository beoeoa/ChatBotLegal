"""Bounded in-memory rate limits for public authentication endpoints."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import deque
from collections.abc import Mapping

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


DEFAULT_AUTH_LIMITS: dict[str, tuple[int, int]] = {
    "/api/auth/login": (10, 5 * 60),
    "/api/auth/register": (5, 60 * 60),
    "/api/auth/forgot-password": (5, 60 * 60),
    "/api/auth/reset-password": (5, 60 * 60),
    "/api/auth/totp/setup": (5, 15 * 60),
    "/api/auth/totp/confirm": (10, 15 * 60),
}


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Limit auth mutations by a one-way client/route key.

    This is intentionally local to the supported one-node pilot. It stores no
    request body, identifier, password, raw IP address or response content.
    """

    def __init__(
        self,
        app,
        *,
        limits: Mapping[str, tuple[int, int]] | None = None,
    ) -> None:
        super().__init__(app)
        self._limits = dict(limits or DEFAULT_AUTH_LIMITS)
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _opaque_key(request: Request) -> str:
        host = request.client.host if request.client else "unknown"
        material = f"auth-rate-v1:{request.url.path}:{host}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _reserve(
        self,
        *,
        key: str,
        now: float,
        limit: int,
        window_seconds: int,
    ) -> int | None:
        cutoff = now - window_seconds
        with self._lock:
            attempts = self._attempts.setdefault(key, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= limit:
                return max(1, math.ceil(window_seconds - (now - attempts[0])))
            attempts.append(now)
        return None

    def _clear(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    async def dispatch(self, request: Request, call_next) -> Response:
        configured = self._limits.get(request.url.path)
        if request.method.upper() != "POST" or configured is None:
            return await call_next(request)

        limit, window_seconds = configured
        key = self._opaque_key(request)
        retry_after = self._reserve(
            key=key,
            now=time.monotonic(),
            limit=limit,
            window_seconds=window_seconds,
        )
        if retry_after is not None:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "detail": "Too many authentication attempts",
                    "code": "auth_rate_limited",
                },
            )

        response = await call_next(request)
        if request.url.path == "/api/auth/login" and response.status_code < 400:
            self._clear(key)
        return response
