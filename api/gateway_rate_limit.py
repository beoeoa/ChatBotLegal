"""Private Caddy forward-auth rate gate with content-free, opaque client keys."""

from __future__ import annotations

import hashlib
import hmac
import math
import os
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Response


router = APIRouter(tags=["internal-gateway"])

RATE_CLASSES: dict[str, tuple[int, int]] = {
    "auth": (30, 60),
    "upload": (30, 60),
    "ask": (120, 60),
    "general": (600, 60),
}


def classify_request(path: str, method: str) -> str:
    clean = urlsplit(path).path
    if clean.startswith("/api/auth/") and method.upper() == "POST":
        return "auth"
    if method.upper() == "POST" and (
        "/attachments" in clean or "/upload" in clean or clean.endswith("/media/extract-text")
    ):
        return "upload"
    if clean.startswith("/api/search/ask"):
        return "ask"
    return "general"


class GatewayRateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def reserve(self, *, client: str, path: str, method: str, now: float | None = None) -> int | None:
        rate_class = classify_request(path, method)
        limit, window = RATE_CLASSES[rate_class]
        opaque_key = hashlib.sha256(
            f"gateway-rate-v1:{rate_class}:{client}".encode("utf-8")
        ).hexdigest()
        current = time.monotonic() if now is None else now
        cutoff = current - window
        with self._lock:
            attempts = self._attempts.setdefault(opaque_key, deque())
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= limit:
                return max(1, math.ceil(window - (current - attempts[0])))
            attempts.append(current)
        return None


gateway_limiter = GatewayRateLimiter()


@router.get("/internal/gateway-rate-limit", status_code=204)
async def gateway_rate_limit(
    x_gateway_token: str = Header(default=""),
    x_gateway_client_ip: str = Header(default="unknown"),
    x_original_uri: str = Header(default=""),
    x_original_method: str = Header(default="GET"),
) -> Response:
    expected = str(os.getenv("GATEWAY_RATE_LIMIT_TOKEN") or "").strip()
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="gateway_rate_limit_not_configured")
    if not hmac.compare_digest(x_gateway_token, expected):
        raise HTTPException(status_code=403, detail="gateway_rate_limit_forbidden")
    if not x_original_uri.startswith("/api/") or x_original_method.upper() not in {
        "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS",
    }:
        raise HTTPException(status_code=400, detail="gateway_rate_limit_request_invalid")
    retry_after = gateway_limiter.reserve(
        client=x_gateway_client_ip,
        path=x_original_uri,
        method=x_original_method,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="gateway_rate_limited",
            headers={"Retry-After": str(retry_after)},
        )
    return Response(status_code=204)
