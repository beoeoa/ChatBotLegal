"""Shared bounded HTTP client for the local legal retrieval service."""

from __future__ import annotations

import asyncio
import os
from typing import Any, Iterable

import httpx


class LegalSearchClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_concurrency: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv(
            "LEGAL_SEARCH_URL", "http://host.docker.internal:8765"
        )).rstrip("/")
        self.timeout_seconds = timeout_seconds or float(
            os.getenv("LEGAL_RETRIEVAL_HTTP_TIMEOUT_SECONDS", "120")
        )
        concurrency = max_concurrency or int(
            os.getenv("LEGAL_RETRIEVAL_MAX_CONCURRENCY", "20")
        )
        self._max_concurrency = max(1, concurrency)
        self._semaphore = asyncio.Semaphore(self._max_concurrency)
        self._client = client
        self._owns_client = client is None
        self._client_lock = asyncio.Lock()

    async def _http(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        async with self._client_lock:
            if self._client is None or self._client.is_closed:
                limits = httpx.Limits(
                    max_connections=max(4, self._max_concurrency),
                    max_keepalive_connections=max(2, self._max_concurrency),
                )
                self._client = httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    limits=limits,
                )
        return self._client

    async def search(
        self,
        payload: dict[str, Any],
        *,
        compatibility_fields: Iterable[str] = ("scope_filter", "retrieval_tier"),
    ) -> dict[str, Any]:
        """Post one search, retrying once without optional legacy fields."""
        async with self._semaphore:
            client = await self._http()
            response = await client.post(f"{self.base_url}/search", json=payload)
            if response.status_code == 422:
                retry_payload = dict(payload)
                removed = False
                for field in compatibility_fields:
                    removed = retry_payload.pop(field, None) is not None or removed
                if removed:
                    response = await client.post(
                        f"{self.base_url}/search", json=retry_payload
                    )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise httpx.DecodingError("Legal retrieval returned a non-object")
            return data

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()


_shared_client: LegalSearchClient | None = None


def get_legal_search_client() -> LegalSearchClient:
    global _shared_client
    if _shared_client is None:
        _shared_client = LegalSearchClient()
    return _shared_client
