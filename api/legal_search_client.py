"""Shared bounded HTTP client for the local legal retrieval service."""

from __future__ import annotations

import asyncio
import os
from datetime import date
from typing import Any, Callable, Iterable, Mapping, Sequence

import httpx

from api.legal_validity_registry import (
    apply_validity_overlay,
    current_answer_validity_readiness,
    default_snapshot_cache,
)


class LegalSearchClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        base_urls: Sequence[str] | None = None,
        timeout_seconds: float | None = None,
        max_concurrency: int | None = None,
        client: httpx.AsyncClient | None = None,
        validity_snapshot_loader: Callable[[], Mapping[str, Any] | None] | None = None,
        strict_current_answers: bool | None = None,
    ) -> None:
        if base_url:
            configured_urls = (base_url,)
        elif base_urls is not None:
            configured_urls = tuple(base_urls)
        else:
            replica_urls = str(os.getenv("LEGAL_SEARCH_URLS") or "").strip()
            if replica_urls:
                configured_urls = tuple(replica_urls.split(","))
            else:
                # Use the frozen R28 reconciled-v2 local serving route. A
                # legacy endpoint is selected only by explicit LEGAL_SEARCH_URL.
                configured_urls = (
                    os.getenv("LEGAL_SEARCH_URL")
                    or os.getenv("LEGAL_RETRIEVAL_V2_URL", "http://127.0.0.1:8766"),
                )
        normalized_urls = tuple(
            str(value or "").strip().rstrip("/")
            for value in configured_urls
            if str(value or "").strip()
        )
        if not normalized_urls:
            raise ValueError("legal_search_base_url_required")
        self.base_urls = tuple(dict.fromkeys(normalized_urls))
        # Compatibility for callers and diagnostics that expect one endpoint.
        self.base_url = self.base_urls[0]
        self._next_endpoint = 0
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
        self._validity_snapshot_loader = (
            validity_snapshot_loader or default_snapshot_cache.load
        )
        self._strict_current_answers = (
            strict_current_answers
            if strict_current_answers is not None
            else str(
                os.getenv("LEGAL_CURRENT_ANSWER_STRICT_VALIDITY", "false")
            ).strip().casefold()
            in {"1", "true", "yes", "on"}
        )

    @staticmethod
    def _as_of(payload: Mapping[str, Any]) -> date:
        try:
            return date.fromisoformat(str(payload.get("as_of") or "")[:10])
        except ValueError:
            return date.today()

    def _apply_validity(
        self, data: dict[str, Any], payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        has_results = bool(data.get("results")) or bool(data.get("context_results"))
        if not has_results and isinstance(data.get("issues"), list):
            has_results = any(
                isinstance(issue, Mapping) and bool(issue.get("results"))
                for issue in data["issues"]
            )
        # Preserve the legacy empty-response contract while still guarding all
        # model-facing non-empty result paths.
        if not has_results:
            return data
        snapshot = self._validity_snapshot_loader()
        overlaid = apply_validity_overlay(
            data,
            snapshot=snapshot,
            as_of=self._as_of(payload),
            mode="strict" if self._strict_current_answers else None,
        )
        overlaid["current_answer_validity_readiness"] = (
            current_answer_validity_readiness(snapshot)
        )
        overlaid["current_answer_validity_readiness"]["strict_gate_enabled"] = bool(
            self._strict_current_answers
        )
        return overlaid

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

    def _ordered_base_urls(self) -> tuple[str, ...]:
        start = self._next_endpoint % len(self.base_urls)
        self._next_endpoint = (self._next_endpoint + 1) % len(self.base_urls)
        return self.base_urls[start:] + self.base_urls[:start]

    async def _post_read(
        self,
        path: str,
        payload: Mapping[str, Any],
    ) -> tuple[httpx.Response, str]:
        """Round-robin reads and fail over only on transport/5xx failures."""

        client = await self._http()
        endpoints = self._ordered_base_urls()
        last_transport_error: httpx.TransportError | None = None
        for index, base_url in enumerate(endpoints):
            try:
                response = await client.post(f"{base_url}{path}", json=payload)
            except httpx.TransportError as exc:
                last_transport_error = exc
                if index + 1 < len(endpoints):
                    continue
                raise
            if response.status_code >= 500 and index + 1 < len(endpoints):
                continue
            return response, base_url
        if last_transport_error is not None:
            raise last_transport_error
        raise RuntimeError("legal_retrieval_no_endpoint_available")

    async def search(
        self,
        payload: dict[str, Any],
        *,
        compatibility_fields: Iterable[str] = ("scope_filter", "retrieval_tier"),
    ) -> dict[str, Any]:
        """Post one search, retrying once without optional legacy fields."""
        async with self._semaphore:
            response, selected_url = await self._post_read("/search", payload)
            if response.status_code == 422:
                retry_payload = dict(payload)
                removed = False
                for field in compatibility_fields:
                    removed = retry_payload.pop(field, None) is not None or removed
                if removed:
                    client = await self._http()
                    response = await client.post(
                        f"{selected_url}/search", json=retry_payload
                    )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise httpx.DecodingError("Legal retrieval returned a non-object")
            return self._apply_validity(data, payload)

    async def search_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Read fresh serving decisions, even when the query is unchanged.

        Caching complete evidence here bypasses the retrieval service's SQL
        revocation check after an admin excludes/replaces a document. The
        service may cache embeddings/candidates, but must recheck serving state
        on every request before returning evidence.
        """
        async with self._semaphore:
            response, _ = await self._post_read("/search/batch", payload)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
                raise httpx.DecodingError("Legal batch retrieval returned an invalid object")
            return self._apply_validity(data, payload)

    async def search_batch_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Perform exactly one physical R28 batch request.

        The general retrieval client supports replica failover. Direct RAG's
        public contract deliberately does not retry retrieval, so it uses this
        method and surfaces a typed transient failure instead.
        """

        async with self._semaphore:
            client = await self._http()
            base_url = self._ordered_base_urls()[0]
            response = await client.post(f"{base_url}/search/batch", json=payload)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
                raise httpx.DecodingError("Legal batch retrieval returned an invalid object")
            return self._apply_validity(data, payload)

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()


_shared_client: LegalSearchClient | None = None


def get_legal_search_client() -> LegalSearchClient:
    global _shared_client
    if _shared_client is None:
        _shared_client = LegalSearchClient()
    return _shared_client
