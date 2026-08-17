from __future__ import annotations

import numpy as np

from scripts.legal_retrieval_cache import (
    ExactRowsCache,
    QueryVectorCache,
    exact_rows_cache_key,
    query_vector_cache_key,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def test_query_vector_cache_key_is_stable_and_does_not_expose_question() -> None:
    question = "  Đăng ký   khai sinh cho con  "

    first = query_vector_cache_key(question, "vnlegal-lal:revision-1")
    second = query_vector_cache_key(
        "đăng ký khai sinh cho con",
        "vnlegal-lal:revision-1",
    )

    assert first == second
    assert len(first) == 64
    assert "khai sinh" not in first
    assert first != query_vector_cache_key(question, "vnlegal-lal:revision-2")


def test_query_vector_cache_expires_and_returns_defensive_copies() -> None:
    clock = _Clock()
    cache = QueryVectorCache(max_entries=2, ttl_seconds=5, clock=clock)
    key = query_vector_cache_key("khai sinh", "model-1")
    original = np.array([1.0, 2.0], dtype=np.float32)

    cache.set(key, original)
    first = cache.get(key)
    assert first is not None
    first[0] = 99.0
    assert cache.get(key).tolist() == [1.0, 2.0]

    clock.value += 6
    assert cache.get(key) is None
    assert cache.stats()["expired"] == 1


def test_query_vector_cache_is_lru_bounded_and_reports_sanitized_stats() -> None:
    clock = _Clock()
    cache = QueryVectorCache(max_entries=2, ttl_seconds=60, clock=clock)
    keys = [query_vector_cache_key(f"question-{index}", "model-1") for index in range(3)]

    cache.set(keys[0], np.array([0.0], dtype=np.float32))
    cache.set(keys[1], np.array([1.0], dtype=np.float32))
    assert cache.get(keys[0]) is not None  # make key 0 most recently used
    cache.set(keys[2], np.array([2.0], dtype=np.float32))

    assert cache.get(keys[1]) is None
    assert cache.get(keys[0]) is not None
    assert cache.get(keys[2]) is not None
    stats = cache.stats()
    assert stats["size"] == 2
    assert stats["evictions"] == 1
    assert set(stats) == {
        "size",
        "max_entries",
        "ttl_seconds",
        "hits",
        "misses",
        "expired",
        "evictions",
    }
    assert not any("question" in str(value) for value in stats.values())


def test_exact_rows_cache_is_opaque_bounded_and_returns_defensive_copies() -> None:
    clock = _Clock()
    cache = ExactRowsCache(max_entries=1, ttl_seconds=5, clock=clock)
    key = exact_rows_cache_key(
        law_numbers=["60/2014/QH13"],
        article_numbers=["52"],
        clause_number=None,
        domain="ho_tich_chung_thuc",
        legal_as_of="2026-08-11",
        retrieval_tier="core",
    )
    assert len(key) == 64
    assert "60/2014" not in key

    cache.set(key, [{"chunk_id": 1, "content": "source"}])
    rows = cache.get(key)
    assert rows is not None
    rows[0]["content"] = "changed"
    assert cache.get(key)[0]["content"] == "source"

    clock.value += 6
    assert cache.get(key) is None
    assert cache.stats()["expired"] == 1
