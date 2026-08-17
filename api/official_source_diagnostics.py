"""Privacy-safe classification for failures from official legal sources."""

from __future__ import annotations

import socket
import ssl
from typing import Any

import httpx


class OfficialSourceError(RuntimeError):
    """Structured official-source failure without persisting response content."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def classify_official_source_failure(exc: BaseException) -> str:
    """Map transport/HTTP failures to stable aggregate reason codes."""

    if isinstance(exc, OfficialSourceError):
        return exc.reason_code
    if isinstance(exc, httpx.ConnectTimeout):
        return "OFFICIAL_SOURCE_CONNECT_TIMEOUT"
    if isinstance(exc, httpx.ReadTimeout):
        return "OFFICIAL_SOURCE_READ_TIMEOUT"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 403:
            return "OFFICIAL_SOURCE_FORBIDDEN"
        if status == 429:
            return "OFFICIAL_SOURCE_RATE_LIMITED"
        if 500 <= status <= 599:
            return "OFFICIAL_SOURCE_SERVER_ERROR"
        if status == 404:
            return "OFFICIAL_SOURCE_ENDPOINT_CHANGED"
    chain = _exception_chain(exc)
    if any(isinstance(item, socket.gaierror) for item in chain):
        return "OFFICIAL_SOURCE_DNS_FAILURE"
    if any(isinstance(item, ssl.SSLError) for item in chain):
        return "OFFICIAL_SOURCE_TLS_FAILURE"
    if isinstance(exc, httpx.ConnectError):
        return "OFFICIAL_SOURCE_CONNECT_TIMEOUT"
    if isinstance(
        exc,
        (httpx.RemoteProtocolError, httpx.TooManyRedirects),
    ):
        return "OFFICIAL_SOURCE_ENDPOINT_CHANGED"
    return "OFFICIAL_SOURCE_ENDPOINT_CHANGED"


def classify_official_response(response: Any) -> str | None:
    """Validate the public response envelope without retaining its body."""

    status = int(getattr(response, "status_code", 0) or 0)
    if status == 403:
        return "OFFICIAL_SOURCE_FORBIDDEN"
    if status == 429:
        return "OFFICIAL_SOURCE_RATE_LIMITED"
    if 500 <= status <= 599:
        return "OFFICIAL_SOURCE_SERVER_ERROR"
    if status == 404:
        return "OFFICIAL_SOURCE_ENDPOINT_CHANGED"
    content_type = str(
        (getattr(response, "headers", {}) or {}).get("content-type") or ""
    ).casefold()
    preview = bytes(getattr(response, "content", b"") or b"")[:4096].lower()
    if b"captcha" in preview or b"verify you are human" in preview:
        return "OFFICIAL_SOURCE_CAPTCHA"
    if status == 200 and "text/x-component" not in content_type:
        return "OFFICIAL_SOURCE_ENDPOINT_CHANGED"
    return None


def require_official_component_response(response: Any) -> None:
    reason_code = classify_official_response(response)
    if reason_code:
        raise OfficialSourceError(reason_code)
