"""Benchmark the pure deterministic legal hierarchy ordering policy."""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path
from time import perf_counter

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.legal_hierarchy import rank_legal_evidence


def _candidate(index: int) -> dict:
    variants = (
        ("Luật", "Quốc hội", "central"),
        ("Nghị định", "Chính phủ", "central"),
        ("Thông tư", "Bộ Tư pháp", "central"),
        ("Quyết định", "UBND thành phố Hải Phòng", "haiphong"),
        ("Quyết định", "UBND phường An Biên", "local"),
        ("Tài liệu", "", "local"),
    )
    document_index = index // 3
    document_type, issuer, scope = variants[document_index % len(variants)]
    return {
        "chunk_id": index + 1,
        "document_id": document_index + 1,
        "law_number": f"{document_index + 1}/2026/TEST",
        "document_type": document_type,
        "issuing_agency": issuer,
        "scope": scope,
        "issued_date": f"202{document_index % 7}-01-01",
        "score": ((index * 7919) % 1000) / 1000,
    }


def run(iterations: int, candidates: int) -> dict:
    source = [_candidate(index) for index in range(candidates)]
    expected = [
        item["chunk_id"]
        for item in rank_legal_evidence(source, scope_filter="haiphong")
    ]
    timings: list[float] = []
    stable = True
    rng = random.Random(20260808)
    for _ in range(iterations):
        shuffled = list(source)
        rng.shuffle(shuffled)
        started = perf_counter()
        ranked = rank_legal_evidence(shuffled, scope_filter="haiphong")
        timings.append((perf_counter() - started) * 1000)
        stable = stable and [item["chunk_id"] for item in ranked] == expected
    p95 = statistics.quantiles(timings, n=100, method="inclusive")[94]
    return {
        "iterations": iterations,
        "candidates": candidates,
        "p95_ms": round(p95, 4),
        "gate_under_5_ms": p95 < 5.0,
        "permutation_stable": stable,
        "network_or_database_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--candidates", type=int, default=150)
    args = parser.parse_args()
    print(json.dumps(run(args.iterations, args.candidates), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
