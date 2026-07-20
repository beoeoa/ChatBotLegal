"""Small in-memory caches used by the local legal retrieval service.

Only opaque SHA-256 keys are retained.  The user's raw question is never kept
as a cache key or exposed through cache statistics.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Callable

import numpy as np


def _normalized_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def query_vector_cache_key(query: str, model_fingerprint: str) -> str:
    """Return an opaque, stable key for a model/query pair.

    Domain, legal date, retrieval tier and corpus revision intentionally do not
    participate: they do not change the embedding and would prevent a core
    query vector from being reused by the expanded retrieval tier.
    """

    payload = f"v1\0{model_fingerprint}\0{_normalized_query(query)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _VectorEntry:
    value: np.ndarray
    expires_at: float


class QueryVectorCache:
    """Thread-safe bounded TTL/LRU cache for query embeddings."""

    def __init__(
        self,
        *,
        max_entries: int = 1024,
        ttl_seconds: float = 300,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if int(max_entries) < 1:
            raise ValueError("max_entries must be at least 1")
        if float(ttl_seconds) <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        self._max_entries = int(max_entries)
        self._ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._entries: OrderedDict[str, _VectorEntry] = OrderedDict()
        self._lock = Lock()
        self._hits = 0
        self._misses = 0
        self._expired = 0
        self._evictions = 0

    def get(self, key: str) -> np.ndarray | None:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.expires_at <= now:
                self._entries.pop(key, None)
                self._expired += 1
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry.value.copy()

    def set(self, key: str, value: np.ndarray) -> None:
        vector = np.asarray(value, dtype=np.float32).copy()
        with self._lock:
            self._entries[key] = _VectorEntry(
                value=vector,
                expires_at=self._clock() + self._ttl_seconds,
            )
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
                self._evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self._max_entries,
                "ttl_seconds": self._ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "expired": self._expired,
                "evictions": self._evictions,
            }

