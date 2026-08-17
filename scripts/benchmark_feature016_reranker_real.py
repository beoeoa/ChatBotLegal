"""Compare production BM25/RRF ranking with a local BGE reranker on real chunks.

The retrieval service remains the authority for eligibility.  This script only
reranks the exact candidates returned after its validity/domain gates and never
writes to PostgreSQL, Chroma, or the active index pointer.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import re
import statistics
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.legal_hierarchy import rank_legal_evidence
from api.legal_learned_reranker import OptionalCrossEncoderReranker


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    return re.sub(r"[^0-9A-ZÀ-ỸĐ]+", "", text)


def _articles(source: Mapping[str, Any]) -> set[str]:
    value = str(source.get("article") or source.get("article_number") or "")
    return {
        _normalized(part) for part in re.split(r"[,;|/]", value) if _normalized(part)
    }


def source_matches(candidate: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    if _normalized(candidate.get("law_number")) != _normalized(
        source.get("law_number")
    ):
        return False
    expected_articles = _articles(source)
    return (
        not expected_articles
        or _normalized(candidate.get("article_number")) in expected_articles
    )


def _rank_metrics(
    ranked: Sequence[Mapping[str, Any]],
    positives: Sequence[Mapping[str, Any]],
    forbidden: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    first_rank = next(
        (
            rank
            for rank, candidate in enumerate(ranked, start=1)
            if any(source_matches(candidate, source) for source in positives)
        ),
        None,
    )
    covered_at_10 = sum(
        any(source_matches(candidate, source) for candidate in ranked[:10])
        for source in positives
    )

    def forbidden_hit(limit: int) -> bool:
        return any(
            source_matches(candidate, source)
            for candidate in ranked[:limit]
            for source in forbidden
        )

    def is_ineligible(candidate: Mapping[str, Any]) -> bool:
        status = _normalized(
            candidate.get("article_status") or candidate.get("document_status")
        )
        validity = candidate.get("validity_sync")
        validity = validity if isinstance(validity, Mapping) else {}
        return bool(
            status in {"EXPIRED", "REPEALED", "INACTIVE", "HETHIEULUC"}
            or validity.get("serving_action") == "block"
            or validity.get("current_answer_eligible") is False
        )

    return {
        "top1_hit": bool(first_rank == 1),
        "top5_hit": bool(first_rank is not None and first_rank <= 5),
        "reciprocal_rank": 0.0 if first_rank is None else 1.0 / first_rank,
        "recall_at_10": (covered_at_10 / len(positives) if positives else 0.0),
        "forbidden_top1": forbidden_hit(1),
        "forbidden_top5": forbidden_hit(5),
        "ineligible_top10": sum(is_ineligible(item) for item in ranked[:10]),
        "first_relevant_rank": first_rank,
    }


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> dict[str, float | int]:
    count = len(rows)
    if not count:
        return {
            "case_count": 0,
            "top1_accuracy": 0.0,
            "top5_accuracy": 0.0,
            "mrr": 0.0,
            "recall_at_10": 0.0,
            "forbidden_top1_count": 0,
            "forbidden_top5_count": 0,
            "ineligible_top10_count": 0,
        }
    return {
        "case_count": count,
        "top1_accuracy": round(sum(bool(row["top1_hit"]) for row in rows) / count, 6),
        "top5_accuracy": round(sum(bool(row["top5_hit"]) for row in rows) / count, 6),
        "mrr": round(sum(float(row["reciprocal_rank"]) for row in rows) / count, 6),
        "recall_at_10": round(
            sum(float(row["recall_at_10"]) for row in rows) / count, 6
        ),
        "forbidden_top1_count": sum(bool(row["forbidden_top1"]) for row in rows),
        "forbidden_top5_count": sum(bool(row["forbidden_top5"]) for row in rows),
        "ineligible_top10_count": sum(int(row["ineligible_top10"]) for row in rows),
    }


def activation_decision(
    baseline: Mapping[str, Any],
    learned: Mapping[str, Any],
    *,
    evaluated_cases: int,
    required_cases: int,
    learned_mode_count: int,
    identity_preserved: bool,
    oom_count: int,
    latency_p95_ms: float,
    latency_target_ms: float = 3000.0,
) -> dict[str, Any]:
    recall_ok = float(learned["recall_at_10"]) >= float(baseline["recall_at_10"])
    mrr_ok = float(learned["mrr"]) >= float(baseline["mrr"]) * 1.05
    top5_ok = float(learned["top5_accuracy"]) >= float(baseline["top5_accuracy"]) + 0.01
    safety_ok = bool(
        identity_preserved
        and oom_count == 0
        and int(learned["forbidden_top1_count"])
        <= int(baseline["forbidden_top1_count"])
        and int(learned["forbidden_top5_count"])
        <= int(baseline["forbidden_top5_count"])
        and int(learned["ineligible_top10_count"])
        <= int(baseline["ineligible_top10_count"])
    )
    coverage_ok = (
        evaluated_cases == required_cases and learned_mode_count == evaluated_cases
    )
    latency_ok = latency_p95_ms <= latency_target_ms
    passed = (
        recall_ok and (mrr_ok or top5_ok) and safety_ok and coverage_ok and latency_ok
    )
    failed = [
        name
        for name, value in {
            "recall_at_10_non_decrease": recall_ok,
            "mrr_plus_5pct_or_top5_plus_1pp": mrr_ok or top5_ok,
            "safety_no_regression": safety_ok,
            "full_evaluation_coverage": coverage_ok,
            "latency_p95_target": latency_ok,
        }.items()
        if not value
    ]
    return {
        "activate": passed,
        "status": "pass" if passed else "fail",
        "failed_gates": failed,
        "gates": {
            "recall_at_10_non_decrease": recall_ok,
            "mrr_relative_plus_5pct": mrr_ok,
            "top5_absolute_plus_1pp": top5_ok,
            "safety_no_regression": safety_ok,
            "full_evaluation_coverage": coverage_ok,
            "latency_p95_target": latency_ok,
        },
    }


def compare_rankings(
    dataset: Mapping[str, Any],
    candidate_cache: Mapping[str, Any],
    *,
    reranker: OptionalCrossEncoderReranker,
    latency_target_ms: float = 3000.0,
) -> dict[str, Any]:
    baseline_rows: list[dict[str, Any]] = []
    learned_rows: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    latencies: list[float] = []
    learned_mode_count = 0
    oom_count = 0
    identity_preserved = True
    retrieved_case_count = 0
    cache_cases = candidate_cache.get("cases") or {}
    examples = list(dataset.get("examples") or [])

    for example in examples:
        case_id = str(example.get("case_id") or "")
        cached = cache_cases.get(case_id) or {}
        candidates = [dict(item) for item in cached.get("candidates") or []]
        positives = list(example.get("positive_sources") or [])
        forbidden = list(example.get("hard_negatives") or [])
        if not candidates or cached.get("error"):
            empty_metrics = _rank_metrics([], positives, forbidden)
            baseline_rows.append(empty_metrics)
            learned_rows.append(dict(empty_metrics))
            cases.append(
                {
                    "case_id": case_id,
                    "candidate_count": 0,
                    "candidate_status": (
                        "error" if cached.get("error") else "empty"
                    ),
                    "candidate_error": cached.get("error"),
                    "exact_article_packet": cached.get("exact_article_packet"),
                    "validity_sync": cached.get("validity_sync"),
                    "baseline": empty_metrics,
                    "learned": dict(empty_metrics),
                    "reranker": {
                        "mode": "not_run",
                        "reason_code": "retrieval_unavailable",
                        "degraded": True,
                        "candidate_count": 0,
                        "scored_count": 0,
                        "latency_ms": 0.0,
                    },
                    "baseline_top": [],
                    "learned_top": [],
                }
            )
            continue
        retrieved_case_count += 1
        score_order = sorted(
            candidates,
            key=lambda item: (
                -float(item.get("score") or 0.0),
                str(item.get("chunk_id") or ""),
            ),
        )
        baseline_ranked = rank_legal_evidence(score_order)
        outcome = reranker.rerank(str(example.get("question") or ""), score_order)
        learned_ranked = rank_legal_evidence(outcome.candidates)
        expected_ids = {str(item.get("chunk_id") or "") for item in score_order}
        actual_ids = {str(item.get("chunk_id") or "") for item in learned_ranked}
        identity_preserved &= expected_ids == actual_ids
        learned_mode_count += int(outcome.mode == "learned")
        oom_count += int(outcome.reason_code == "inference_oom")
        latencies.append(outcome.latency_ms)
        baseline_metrics = _rank_metrics(baseline_ranked, positives, forbidden)
        learned_metrics = _rank_metrics(learned_ranked, positives, forbidden)
        baseline_rows.append(baseline_metrics)
        learned_rows.append(learned_metrics)
        cases.append(
            {
                "case_id": case_id,
                "candidate_count": len(candidates),
                "baseline": baseline_metrics,
                "learned": learned_metrics,
                "reranker": outcome.public_status(),
                "baseline_top": [
                    str(item.get("chunk_id") or "") for item in baseline_ranked[:10]
                ],
                "learned_top": [
                    str(item.get("chunk_id") or "") for item in learned_ranked[:10]
                ],
            }
        )

    baseline = _aggregate(baseline_rows)
    learned = _aggregate(learned_rows)
    sorted_latencies = sorted(latencies)
    p95_index = max(0, int(len(sorted_latencies) * 0.95) - 1)
    latency = {
        "p50": round(statistics.median(sorted_latencies), 3)
        if sorted_latencies
        else 0.0,
        "p95": round(sorted_latencies[p95_index], 3) if sorted_latencies else 0.0,
        "max": round(max(sorted_latencies), 3) if sorted_latencies else 0.0,
    }
    decision = activation_decision(
        baseline,
        learned,
        evaluated_cases=len(cases),
        required_cases=len(examples),
        learned_mode_count=learned_mode_count,
        identity_preserved=identity_preserved,
        oom_count=oom_count,
        latency_p95_ms=float(latency["p95"]),
        latency_target_ms=latency_target_ms,
    )
    retrieval_gap_reasons: Counter[str] = Counter()
    for item in cases:
        if int(item.get("candidate_count") or 0) > 0:
            continue
        if item.get("candidate_error"):
            retrieval_gap_reasons["retrieval_error"] += 1
        packet = item.get("exact_article_packet") or {}
        for reason in packet.get("reason_codes") or []:
            retrieval_gap_reasons[str(reason)] += 1
        validity = item.get("validity_sync") or {}
        for reason, count in (validity.get("filtered_reasons") or {}).items():
            retrieval_gap_reasons[f"validity:{reason}"] += int(count or 0)
        if not item.get("candidate_error") and not packet and not validity:
            retrieval_gap_reasons["empty_without_diagnostics"] += 1
    return {
        "schema_version": "feature016-real-reranker-benchmark-v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset_case_count": len(examples),
        "evaluated_case_count": len(cases),
        "retrieved_case_count": retrieved_case_count,
        "retrieval_gap_reasons": dict(sorted(retrieval_gap_reasons.items())),
        "candidate_source": "live_retrieval_after_validity_domain_gates",
        "pipeline_baseline": "vector+BM25+RRF+heuristics+legal_hierarchy",
        "pipeline_candidate": "vector+BM25+RRF+heuristics+BGE-cross-encoder+legal_hierarchy",
        "baseline": baseline,
        "learned": learned,
        "delta": {
            key: round(float(learned[key]) - float(baseline[key]), 6)
            for key in ("top1_accuracy", "top5_accuracy", "mrr", "recall_at_10")
        },
        "reranker_runtime": {
            "learned_mode_count": learned_mode_count,
            "oom_count": oom_count,
            "candidate_identity_preserved": identity_preserved,
            "latency_ms": latency,
        },
        "activation_decision": decision,
        "cases": cases,
    }


def _post_search(
    url: str,
    example: Mapping[str, Any],
    timeout: float,
    retrieval_tier: str = "core",
) -> dict[str, Any]:
    started = time.perf_counter()
    tiers = ("core", "expanded") if retrieval_tier == "adaptive" else (retrieval_tier,)
    last_body: dict[str, Any] = {}
    attempted: list[str] = []
    for tier in tiers:
        attempted.append(tier)
        payload = json.dumps(
            {
                "query": example.get("question"),
                "limit": 30,
                "candidate_count": 150,
                "lexical_candidate_count": 60,
                "as_of": example.get("legal_as_of") or date.today().isoformat(),
                "include_trace": False,
                "allow_broad_fallback": True,
                "retrieval_tier": tier,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            url.rstrip("/") + "/search",
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            last_body = json.loads(response.read().decode("utf-8"))
        if last_body.get("results"):
            break
    return {
        "candidates": last_body.get("results") or [],
        "retrieval_ms": round((time.perf_counter() - started) * 1000, 3),
        "error": None,
        "retrieval_tier_used": attempted[-1],
        "retrieval_tiers_attempted": attempted,
        "exact_article_packet": last_body.get("exact_article_packet"),
        "validity_sync": last_body.get("validity_sync"),
    }


def collect_candidates(
    dataset: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    *,
    retrieval_url: str,
    workers: int,
    timeout: float,
    retrieval_tier: str = "core",
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    existing_cases = (
        dict((existing or {}).get("cases") or {})
        if str((existing or {}).get("retrieval_tier") or "core") == retrieval_tier
        else {}
    )
    cache: dict[str, Any] = {
        "schema_version": "feature016-real-candidate-cache-v1",
        "retrieval_url": retrieval_url,
        "retrieval_tier": retrieval_tier,
        "cases": existing_cases,
    }
    examples = {
        str(item.get("case_id") or ""): item for item in dataset.get("examples") or []
    }
    missing = [
        (case_id, example)
        for case_id, example in examples.items()
        if not (cache["cases"].get(case_id) or {}).get("candidates")
    ]

    def fetch(case_id: str, example: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        last_error = "unknown_error"
        for attempt in range(3):
            try:
                return case_id, _post_search(
                    retrieval_url,
                    example,
                    timeout,
                    retrieval_tier,
                )
            except (
                OSError,
                TimeoutError,
                urllib.error.URLError,
                json.JSONDecodeError,
            ) as exc:
                last_error = f"{type(exc).__name__}:{str(exc)[:160]}"
                if attempt < 2:
                    time.sleep(1.0 + attempt)
        return case_id, {"candidates": [], "retrieval_ms": None, "error": last_error}

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as executor:
        futures = [
            executor.submit(fetch, case_id, example) for case_id, example in missing
        ]
        for future in as_completed(futures):
            case_id, result = future.result()
            cache["cases"][case_id] = result
            if checkpoint_path:
                _atomic_json(checkpoint_path, cache)
    return cache


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--candidate-cache", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--retrieval-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--retrieval-tier",
        choices=("core", "expanded", "adaptive"),
        default="core",
        help=(
            "Reviewed commune core, active source corpus, or the production-like "
            "core-then-expanded policy."
        ),
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--latency-target-ms", type=float, default=3000.0)
    parser.add_argument("--collect-only", action="store_true")
    args = parser.parse_args()

    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    existing = (
        json.loads(args.candidate_cache.read_text(encoding="utf-8"))
        if args.candidate_cache.exists()
        else None
    )
    cache = collect_candidates(
        dataset,
        existing,
        retrieval_url=args.retrieval_url,
        workers=args.workers,
        timeout=args.timeout,
        retrieval_tier=args.retrieval_tier,
        checkpoint_path=args.candidate_cache,
    )
    _atomic_json(args.candidate_cache, cache)
    collected = sum(
        bool((row or {}).get("candidates")) for row in cache["cases"].values()
    )
    if args.collect_only:
        print(
            json.dumps(
                {"collected": collected, "required": len(dataset.get("examples") or [])}
            )
        )
        return 0 if collected == len(dataset.get("examples") or []) else 1

    report = compare_rankings(
        dataset,
        cache,
        reranker=OptionalCrossEncoderReranker.from_environment(),
        latency_target_ms=args.latency_target_ms,
    )
    _atomic_json(args.output, report)
    print(
        json.dumps(
            {
                "evaluated": report["evaluated_case_count"],
                "baseline": report["baseline"],
                "learned": report["learned"],
                "delta": report["delta"],
                "activation_decision": report["activation_decision"],
                "latency_ms": report["reranker_runtime"]["latency_ms"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["activation_decision"]["activate"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
