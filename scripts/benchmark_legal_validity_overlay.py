"""Offline micro-benchmark for the legal-validity serving overlay.

The script performs no network or database calls. Pass the independently
measured retrieval p95 to calculate the estimated end-to-end delta.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from statistics import quantiles
from time import perf_counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.legal_validity_registry import apply_validity_overlay


def _fixture(size: int) -> tuple[dict, dict]:
    documents = {}
    rows = []
    for index in range(size):
        law_number = f"{index + 1}/2024/QH15"
        status = "expired" if index % 10 == 0 else "active"
        action = "historical_only" if status == "expired" else "allow"
        documents[law_number] = {
            "document_id": str(index + 1),
            "normalized_status": status,
            "serving_action": action,
            "source_url": f"https://vbpl.vn/van-ban/benchmark-{index + 1}",
            "verified_at": "2026-08-08T00:00:00+00:00",
            "effective_from": "2024-08-01",
            "effective_to": "2026-01-01" if status == "expired" else None,
            "affected_provisions": [],
            "identity_status": "exact",
            "evidence_status": "sufficient",
            "warning_code": None,
            "reason_code": "expired" if status == "expired" else "active",
        }
        rows.append(
            {
                "doc_id": str(index + 1),
                "law_number": law_number,
                "article_number": str((index % 20) + 1),
                "score": 1 - (index / max(size, 1) / 2),
            }
        )
    return (
        {"results": rows},
        {
            "schema_version": "legal-validity-serving-v1",
            "mode": "protect",
            "documents": documents,
            "document_ids": {str(index + 1): law for index, law in enumerate(documents)},
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--results", type=int, default=100)
    parser.add_argument("--baseline-retrieval-p95-ms", type=float, default=200.0)
    args = parser.parse_args()
    iterations = min(max(args.iterations, 100), 100_000)
    result_count = min(max(args.results, 1), 500)
    payload, snapshot = _fixture(result_count)

    for _ in range(25):
        apply_validity_overlay(
            payload,
            snapshot=snapshot,
            as_of=date(2026, 8, 8),
            mode="protect",
        )

    timings_ms = []
    for _ in range(iterations):
        started = perf_counter()
        result = apply_validity_overlay(
            payload,
            snapshot=snapshot,
            as_of=date(2026, 8, 8),
            mode="protect",
        )
        timings_ms.append((perf_counter() - started) * 1000)
    p95_ms = quantiles(timings_ms, n=100, method="inclusive")[94]
    baseline_ms = max(float(args.baseline_retrieval_p95_ms), 0.001)
    retrieval_delta_percent = (p95_ms / baseline_ms) * 100
    report = {
        "iterations": iterations,
        "results_per_response": result_count,
        "kept_results": len(result["results"]),
        "filtered_results": result["validity_sync"]["filtered_count"],
        "overlay_p95_ms": round(p95_ms, 4),
        "baseline_retrieval_p95_ms": round(baseline_ms, 4),
        "estimated_retrieval_delta_percent": round(retrieval_delta_percent, 4),
        "overlay_gate_under_10_ms": p95_ms < 10,
        "retrieval_delta_gate_under_5_percent": retrieval_delta_percent <= 5,
        "network_or_database_calls": 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["overlay_gate_under_10_ms"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
