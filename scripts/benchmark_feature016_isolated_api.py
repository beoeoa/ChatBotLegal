"""Privacy-safe isolated API load gate for Feature 016.

The benchmark mounts the real Ask HTTP route in-process and replaces only the
external retrieval/generation dependencies with deterministic local work.  It
therefore measures FastAPI routing, validation, response serialization and the
Feature 016 trust primitives without touching a live corpus or paid provider.

Artifacts contain aggregate timings and counters only.  Questions, answers,
credentials, citations and exception text are intentionally never persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

import httpx
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_citation_provenance import verify_citation
from api.legal_provider_privacy import prepare_provider_egress
from api.model_modality import validate_embedding_output
from api.models import AskResponse
from api.routers import search


CONCURRENCY_LEVELS = (1, 5, 10, 20, 30)
STAGES = ("retrieval", "provisioning", "generation", "validation", "end_to_end")
FIXTURE_QUESTION = "Cho biết cách tra cứu quy định đang có hiệu lực."
FIXTURE_SOURCE = "Điều 1. Phạm vi điều chỉnh."
FIXTURE_QUOTE = "Phạm vi điều chỉnh"
FORBIDDEN_ARTIFACT_KEYS = {
    "answer",
    "authorization",
    "citation",
    "citations",
    "cookie",
    "credential",
    "password",
    "prompt",
    "question",
    "secret",
    "token",
}


def _percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return round(ordered[index], 3)


def _timing_summary(values: Iterable[float]) -> dict[str, float | None]:
    rows = [float(value) for value in values]
    return {
        "p50": _percentile(rows, 0.50),
        "p95": _percentile(rows, 0.95),
        "max": round(max(rows), 3) if rows else None,
    }


def _stage_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, float | None]]:
    materialized = list(rows)
    return {
        stage: _timing_summary(
            float(row["stages"][stage])
            for row in materialized
            if isinstance(row.get("stages"), Mapping)
            and isinstance(row["stages"].get(stage), (int, float))
        )
        for stage in STAGES
    }


def _assert_privacy_safe(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in FORBIDDEN_ARTIFACT_KEYS:
                raise ValueError(f"privacy_forbidden_key:{path}.{normalized}")
            _assert_privacy_safe(child, f"{path}.{normalized}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_privacy_safe(child, f"{path}[{index}]")


async def _isolated_execute(
    ask_request,
    _request,
    *,
    progress=None,
    trace_id_override=None,
) -> AskResponse:
    started = time.perf_counter()

    stage_started = time.perf_counter()
    proof = verify_citation(
        source_text=FIXTURE_SOURCE,
        support_quote=FIXTURE_QUOTE,
        provenance={"verification_status": "verified"},
        internal_url="/legal-documents/isolated-fixture",
    )
    if proof.status != "verified":
        raise RuntimeError("isolated_citation_verification_failed")
    retrieval_ms = (time.perf_counter() - stage_started) * 1000

    stage_started = time.perf_counter()
    egress = prepare_provider_egress(
        ask_request.question,
        provider="huggingface-local",
        model="isolated-fixture",
    )
    if egress.mode != "local":
        raise RuntimeError("isolated_provider_mode_failed")
    provisioning_ms = (time.perf_counter() - stage_started) * 1000

    stage_started = time.perf_counter()
    # Deterministic local delay creates genuine overlap without an LLM or I/O.
    await asyncio.sleep(0.008)
    generation_ms = (time.perf_counter() - stage_started) * 1000

    stage_started = time.perf_counter()
    validate_embedding_output([[0.1, 0.2, 0.3]], expected_count=1)
    validation_ms = (time.perf_counter() - stage_started) * 1000
    end_to_end_ms = (time.perf_counter() - started) * 1000

    if progress is not None:
        await progress("status", {"stage": "validated"})
    return AskResponse(
        answer="Nguồn thử nghiệm cô lập đã được xác minh.",
        question=ask_request.question,
        grounding_status="fully_grounded",
        answer_mode="normal",
        trace_id=trace_id_override,
        rag_trace={
            "section_orchestration": {
                "metric": {
                    "stage_timings_ms": {
                        "retrieval": retrieval_ms,
                        "provisioning": provisioning_ms,
                        "generation": generation_ms,
                        "validation": validation_ms,
                        "end_to_end": end_to_end_ms,
                    },
                    "repair_count": 0,
                    "error_category": "none",
                }
            }
        },
    )


def _isolated_app() -> FastAPI:
    app = FastAPI()
    app.include_router(search.router, prefix="/api")
    return app


async def _one_request(client: httpx.AsyncClient, ordinal: int) -> dict[str, Any]:
    started = time.perf_counter()
    response: httpx.Response | None = None
    category = "none"
    stages: dict[str, float] = {}
    try:
        response = await client.post(
            "/api/search/ask/simple",
            json={
                "question": FIXTURE_QUESTION,
                "role": "citizen",
                "show_rag_trace": True,
                "idempotency_key": f"feature016-isolated-{ordinal}",
            },
        )
        if response.status_code >= 500:
            category = "server"
        elif response.status_code >= 400:
            category = "request"
        else:
            payload = response.json()
            trace = payload.get("rag_trace") if isinstance(payload, dict) else None
            section = trace.get("section_orchestration") if isinstance(trace, Mapping) else None
            metric = section.get("metric") if isinstance(section, Mapping) else None
            raw_stages = metric.get("stage_timings_ms") if isinstance(metric, Mapping) else None
            if isinstance(raw_stages, Mapping):
                stages = {
                    stage: round(float(raw_stages[stage]), 3)
                    for stage in STAGES
                    if isinstance(raw_stages.get(stage), (int, float))
                    and float(raw_stages[stage]) >= 0
                }
    except httpx.TimeoutException:
        category = "timeout"
    except httpx.HTTPError:
        category = "network"
    except Exception:
        category = "unexpected"
    duration_ms = (time.perf_counter() - started) * 1000
    return {
        "ok": bool(response is not None and response.is_success and category == "none"),
        "status": response.status_code if response is not None else None,
        "category": category,
        "duration_ms": round(duration_ms, 3),
        "stages": stages,
    }


def _aggregate(rows: list[dict[str, Any]], elapsed_seconds: float) -> dict[str, Any]:
    completed = sum(1 for row in rows if row["ok"])
    error_count = len(rows) - completed
    return {
        "request_count": len(rows),
        "completed_count": completed,
        "error_count": error_count,
        "error_rate": round(error_count / len(rows), 6) if rows else 0.0,
        "status_counts": dict(Counter(str(row["status"]) for row in rows)),
        "error_category_counts": dict(Counter(row["category"] for row in rows)),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "achieved_rps": round(len(rows) / elapsed_seconds, 3) if elapsed_seconds else None,
        "latency_ms": _timing_summary(row["duration_ms"] for row in rows),
        "stage_latency_ms": _stage_summary(rows),
    }


async def _matrix_run(
    client: httpx.AsyncClient,
    *,
    requests_per_level: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    ordinal = 0
    for concurrency in CONCURRENCY_LEVELS:
        semaphore = asyncio.Semaphore(concurrency)

        async def bounded(current: int) -> dict[str, Any]:
            async with semaphore:
                return await _one_request(client, current)

        started = time.perf_counter()
        rows = await asyncio.gather(
            *(bounded(ordinal + index) for index in range(requests_per_level))
        )
        ordinal += requests_per_level
        aggregate = _aggregate(list(rows), time.perf_counter() - started)
        aggregate["concurrency"] = concurrency
        results.append(aggregate)
    return results


async def _sustained_run(
    client: httpx.AsyncClient,
    *,
    rps: float,
    duration_seconds: float,
) -> dict[str, Any]:
    request_count = max(1, int(math.floor(rps * duration_seconds)))
    interval = 1.0 / rps
    semaphore = asyncio.Semaphore(30)
    started = time.perf_counter()

    async def scheduled(index: int) -> dict[str, Any]:
        target = started + (index * interval)
        delay = target - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        async with semaphore:
            return await _one_request(client, 1_000_000 + index)

    rows = await asyncio.gather(*(scheduled(index) for index in range(request_count)))
    elapsed = time.perf_counter() - started
    aggregate = _aggregate(list(rows), elapsed)
    aggregate.update(
        {
            "target_rps": rps,
            "target_duration_seconds": duration_seconds,
            "scheduler_concurrency_limit": 30,
        }
    )
    return aggregate


def _evaluate_gates(matrix: list[dict[str, Any]], sustained: dict[str, Any]) -> dict[str, bool]:
    all_rows = [*matrix, sustained]
    api_p95_values = [
        float(row["latency_ms"]["p95"])
        for row in all_rows
        if row.get("latency_ms", {}).get("p95") is not None
    ]
    retrieval_p95_values = [
        float(row["stage_latency_ms"]["retrieval"]["p95"])
        for row in all_rows
        if row.get("stage_latency_ms", {}).get("retrieval", {}).get("p95") is not None
    ]
    return {
        "matrix_1_5_10_20_30_complete": [row["concurrency"] for row in matrix]
        == list(CONCURRENCY_LEVELS),
        "all_requests_completed": all(row["completed_count"] == row["request_count"] for row in all_rows),
        "error_rate_below_0_5_percent": all(float(row["error_rate"]) < 0.005 for row in all_rows),
        "api_p95_at_most_25_seconds": bool(api_p95_values) and max(api_p95_values) <= 25_000,
        "retrieval_p95_at_most_3_seconds": bool(retrieval_p95_values) and max(retrieval_p95_values) <= 3_000,
        "sustained_3_rps_duration_met": (
            float(sustained["target_rps"]) == 3.0
            and float(sustained["elapsed_seconds"]) >= 599.0
            and float(sustained["achieved_rps"]) >= 2.99
        ),
        "privacy_safe_aggregate_only": True,
    }


async def run_benchmark(
    *,
    requests_per_level: int,
    duration_seconds: float,
    rps: float,
) -> dict[str, Any]:
    original = search._execute_ask_simple
    search._execute_ask_simple = _isolated_execute
    try:
        transport = httpx.ASGITransport(app=_isolated_app())
        timeout = httpx.Timeout(30.0)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://feature016.isolated",
            timeout=timeout,
        ) as client:
            matrix = await _matrix_run(client, requests_per_level=requests_per_level)
            sustained = await _sustained_run(
                client,
                rps=rps,
                duration_seconds=duration_seconds,
            )
    finally:
        search._execute_ask_simple = original

    report: dict[str, Any] = {
        "schema_version": "1.0",
        "benchmark": "feature016_isolated_ask_api",
        "execution_mode": "in_process_asgi_real_route_stubbed_external_dependencies",
        "scope_note": (
            "Measures the real Ask HTTP boundary and trust primitives; it is not "
            "a production model/database capacity claim."
        ),
        "concurrency_matrix": matrix,
        "sustained_rate": sustained,
    }
    report["gates"] = _evaluate_gates(matrix, sustained)
    report["status"] = "pass" if all(report["gates"].values()) else "fail"
    _assert_privacy_safe(report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--requests-per-level", type=int, default=60)
    parser.add_argument("--duration-seconds", type=float, default=600.0)
    parser.add_argument("--rps", type=float, default=3.0)
    parser.add_argument(
        "--allow-short-test",
        action="store_true",
        help="Allow a duration below 600 seconds for unit tests only.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.requests_per_level < max(CONCURRENCY_LEVELS):
        raise SystemExit("requests-per-level must be at least 30")
    if args.rps != 3.0:
        raise SystemExit("The final gate requires exactly 3 RPS")
    if args.duration_seconds < 600 and not args.allow_short_test:
        raise SystemExit("The final gate requires a 600-second sustained run")
    report = asyncio.run(
        run_benchmark(
            requests_per_level=args.requests_per_level,
            duration_seconds=args.duration_seconds,
            rps=args.rps,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    # Byte-exact output keeps the printed digest identical on Windows/Linux.
    args.output.write_bytes(payload.encode("utf-8"))
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest().upper()
    print(f"status={report['status']} sha256={digest} output={args.output}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
