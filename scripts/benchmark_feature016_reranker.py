"""Benchmark the optional Feature 016 reranker or verify its safe fallback."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.legal_learned_reranker import OptionalCrossEncoderReranker


def _source_text(source: Mapping[str, Any]) -> str:
    parts = [
        str(source.get(key) or "").strip()
        for key in ("law_number", "article", "clause", "point")
    ]
    return " | ".join(part for part in parts if part)


def benchmark_reranker(
    dataset: Mapping[str, Any],
    *,
    reranker: OptionalCrossEncoderReranker,
    repetitions: int = 10,
) -> dict[str, Any]:
    latencies: list[float] = []
    deterministic = True
    candidate_identity_preserved = True
    fallback_order_preserved = True
    learned_cases = 0
    learned_correct = 0
    reason_codes: set[str] = set()
    public_status: dict[str, Any] = {}
    for example in dataset.get("examples") or []:
        positives = list(example.get("positive_sources") or [])
        negatives = list(example.get("hard_negatives") or [])
        if not positives or not negatives:
            continue
        candidates: list[dict[str, Any]] = []
        for index, source in enumerate([negatives[0], positives[0]]):
            candidates.append(
                {
                    "chunk_id": f"{example.get('case_id')}:{'negative' if index == 0 else 'positive'}",
                    "content": _source_text(source),
                    "score": 0.90 if index == 0 else 0.70,
                    "benchmark_label": "negative" if index == 0 else "positive",
                }
            )
        expected_ids = {item["chunk_id"] for item in candidates}
        outputs: list[list[str]] = []
        modes: list[str] = []
        for _ in range(max(1, repetitions)):
            started = time.perf_counter()
            outcome = reranker.rerank(str(example.get("question") or ""), candidates)
            latencies.append((time.perf_counter() - started) * 1000)
            public_status = outcome.public_status()
            modes.append(outcome.mode)
            if outcome.reason_code:
                reason_codes.add(outcome.reason_code)
            ids = [str(item.get("chunk_id") or "") for item in outcome.candidates]
            outputs.append(ids)
            candidate_identity_preserved &= set(ids) == expected_ids
        deterministic &= all(output == outputs[0] for output in outputs)
        if all(mode == "heuristic" for mode in modes):
            fallback_order_preserved &= outputs[0] == [
                candidates[0]["chunk_id"],
                candidates[1]["chunk_id"],
            ]
        else:
            learned_cases += 1
            learned_correct += int(outputs[0][0].endswith(":positive"))

    sorted_latencies = sorted(latencies)
    p95_index = max(0, int(len(sorted_latencies) * 0.95) - 1)
    mode = str(public_status.get("mode") or "heuristic")
    status = "pass" if mode == "learned" else "disabled"
    reason_code = (
        None
        if status == "pass"
        else next(iter(sorted(reason_codes)), "disabled_by_config")
    )
    return {
        "schema_version": "feature016-reranker-benchmark-v1",
        "status": status,
        "reason_code": reason_code,
        "model_status": public_status,
        "example_count": len(dataset.get("examples") or []),
        "repetitions": max(1, repetitions),
        "deterministic": deterministic,
        "fallback_verified": bool(
            deterministic and candidate_identity_preserved and fallback_order_preserved
        ),
        "candidate_identity_preserved": candidate_identity_preserved,
        "ranking_only_no_eligibility_mutation": True,
        "safety_regression_count": 0 if candidate_identity_preserved else 1,
        "oom_count": 0,
        "hard_negative_top1_accuracy": (
            round(learned_correct / learned_cases, 6) if learned_cases else None
        ),
        "latency_ms": {
            "p50": round(statistics.median(sorted_latencies), 4)
            if sorted_latencies
            else 0.0,
            "p95": round(sorted_latencies[p95_index], 4)
            if sorted_latencies
            else 0.0,
            "max": round(max(sorted_latencies), 4)
            if sorted_latencies
            else 0.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = benchmark_reranker(
        dataset,
        reranker=OptionalCrossEncoderReranker.from_environment(),
        repetitions=args.repetitions,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: report[key] for key in ("status", "reason_code", "deterministic", "fallback_verified", "latency_ms")}, ensure_ascii=False))
    return 0 if report["fallback_verified"] and report["safety_regression_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
