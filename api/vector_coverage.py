"""Count distinct SQL chunk identities present in serving collections, never sums."""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable


def chunk_fingerprint(ids: Iterable[int]) -> str:
    return hashlib.sha256("\n".join(f"chunk-{value}" for value in sorted(set(ids))).encode()).hexdigest()


def present_chunk_ids(collection: Any, chunk_ids: Iterable[int]) -> set[int]:
    """Support the numeric batch index and prefixed incremental index identities.

    Both are exact SQL identities; duplicate encodings count only once.
    Never infer coverage from the collection's total vector count.
    """
    expected = set(int(value) for value in chunk_ids)
    aliases = {alias: value for value in expected for alias in (str(value), f"chunk-{value}")}
    ids = sorted(aliases)
    present: set[int] = set()
    for start in range(0, len(ids), 1000):
        result = collection.get(ids=ids[start:start + 1000], include=[])
        present.update(aliases[item] for item in (result.get("ids") or ()) if item in aliases)
    return present


def measure_coverage(collections: Iterable[Any], chunk_ids: Iterable[int]) -> dict[str, Any]:
    expected = sorted(set(int(value) for value in chunk_ids))
    wanted = set(expected)
    present: set[int] = set()
    failures = 0
    handles = list({id(handle): handle for handle in collections if handle is not None}.values())
    for handle in handles:
        try:
            present.update(present_chunk_ids(handle, wanted - present))
        except Exception:
            failures += 1
    complete_probe = bool(handles) and failures == 0
    return {
        "status": "available" if complete_probe else "unavailable",
        "expected": len(wanted), "present": len(present),
        "coverage_percent": round(100 * len(present) / len(wanted), 1) if complete_probe and wanted else None,
        "chunk_ids_sha256": chunk_fingerprint(expected),
        "verification_source": "serving_collection_chunk_identity",
    }


class CoverageSnapshots:
    """Bound repeated probes without making dashboard callers queue.

    The SQL identity set and collection handles are part of the key, so an
    imported document or reloaded collection cannot reuse an old denominator.
    Vector-only changes become visible within the observation TTL.  Measuring a
    large Chroma collection can take seconds, so the mutex protects only the
    snapshot state; it is deliberately *not* held while scanning collections.
    """

    def __init__(self, ttl_seconds: float = 300, clock=time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._lock = threading.Lock()
        self._snapshot: tuple[Any, float, dict[str, Any]] | None = None
        self._refreshing_key: Any | None = None

    @staticmethod
    def _in_progress_payload(*, key: Any, manifest: str, expected: tuple[int, ...]) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "reason_code": "coverage_probe_in_progress",
            "expected": len(expected),
            "present": 0,
            "coverage_percent": None,
            "chunk_ids_sha256": key[1],
            "verification_source": "serving_collection_chunk_identity",
            "manifest_sha256": manifest,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    def read(self, collections: Iterable[Any], chunk_ids: Iterable[int], manifest: str) -> dict[str, Any]:
        handles = tuple(collections)
        expected = tuple(sorted(set(chunk_ids)))
        key = (manifest, chunk_fingerprint(expected), tuple(id(handle) for handle in handles))
        with self._lock:
            if self._snapshot:
                previous_key, expires, payload = self._snapshot
                if previous_key == key and self.clock() < expires:
                    return dict(payload)
            if self._refreshing_key is not None:
                # An expired successful snapshot remains more useful than
                # blocking every dashboard request behind the active probe.
                if self._snapshot and self._snapshot[0] == key:
                    stale = dict(self._snapshot[2])
                    stale.update(stale=True, refreshing=True)
                    return stale
                return self._in_progress_payload(key=key, manifest=manifest, expected=expected)
            self._refreshing_key = key

        try:
            result = measure_coverage(handles, expected)
            result.update(
                manifest_sha256=manifest,
                observed_at=datetime.now(timezone.utc).isoformat(),
                stale=False,
                refreshing=False,
            )
            with self._lock:
                # Outages are not cached, allowing immediate recovery on retry.
                # Keep an older successful snapshot only as a concurrency
                # fallback; never present it as the result of this failed probe.
                if result["status"] == "available":
                    self._snapshot = (key, self.clock() + self.ttl_seconds, dict(result))
            return result
        finally:
            with self._lock:
                if self._refreshing_key == key:
                    self._refreshing_key = None
