"""Privacy-safe local benchmark runner for Feature 005.

The runner sends fixture prompts only to a local API, but never writes their
question, answer, citation body, authorization header or raw error message to
the artifact.  It is intentionally not a release approver: it reports timing
evidence for the human quality gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from collections import Counter
from pathlib import Path
from runpy import run_path
from typing import Any, Iterable, Mapping

import httpx

QUALITY_CASES = run_path(
    str(Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "feature005_grounding_cases.py")
)["QUALITY_CASES"]
BENCHMARK_CASE_IDS = tuple(case_id for case_id, case in QUALITY_CASES.items() if "question" in case)


ALLOWED_ARTIFACT_KEYS = {
    "schema_version",
    "mode",
    "base_url",
    "concurrency",
    "request_count",
    "completed_count",
    "status_counts",
    "section_status_counts",
    "error_category_counts",
    "quality_gate_counts",
    "fallback_count",
    "repair_count",
    "stage_latency_ms",
    "latency_ms",
}
ALLOWED_STAGE_NAMES = (
    "retrieval",
    "provisioning",
    "generation",
    "validation",
    "end_to_end",
)


def _percentile(values: Iterable[float], percentile: float) -> int | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(ordered[index])


def _summary(values: Iterable[float]) -> dict[str, int | None]:
    rows = [float(value) for value in values]
    return {"p50": _percentile(rows, 0.50), "p95": _percentile(rows, 0.95)}


def _extract_stage_timings(payload: Mapping[str, Any]) -> dict[str, float]:
    """Read only aggregate stage timings from the optional admin trace."""

    trace = payload.get("rag_trace")
    section = trace.get("section_orchestration") if isinstance(trace, Mapping) else None
    metric = section.get("metric") if isinstance(section, Mapping) else None
    timings = metric.get("stage_timings_ms") if isinstance(metric, Mapping) else None
    if not isinstance(timings, Mapping):
        return {}
    return {
        stage: float(timings[stage])
        for stage in ALLOWED_STAGE_NAMES
        if isinstance(timings.get(stage), (int, float)) and timings[stage] >= 0
    }


def _extract_runtime_signals(payload: Mapping[str, Any]) -> dict[str, Any]:
    trace = payload.get("rag_trace")
    section = trace.get("section_orchestration") if isinstance(trace, Mapping) else None
    metric = section.get("metric") if isinstance(section, Mapping) else None
    claim_validation = (
        section.get("claim_validation") if isinstance(section, Mapping) else None
    )
    quality_gate = (
        claim_validation.get("quality_gate")
        if isinstance(claim_validation, Mapping)
        else None
    )
    quality_pass = (
        quality_gate.get("pass") if isinstance(quality_gate, Mapping) else None
    )
    return {
        "repair_count": (
            int(metric.get("repair_count") or 0)
            if isinstance(metric, Mapping)
            else 0
        ),
        "error_category": (
            str(metric.get("error_category") or "unknown")
            if isinstance(metric, Mapping)
            else "unknown"
        ),
        "quality_gate_status": (
            "passed"
            if quality_pass is True
            else "failed"
            if quality_pass is False
            else "unavailable"
        ),
    }


def _stage_summaries(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int | None]]:
    values: dict[str, list[float]] = {stage: [] for stage in ALLOWED_STAGE_NAMES}
    for row in rows:
        timings = row.get("stage_timings_ms")
        if not isinstance(timings, Mapping):
            continue
        for stage in ALLOWED_STAGE_NAMES:
            value = timings.get(stage)
            if isinstance(value, (int, float)) and value >= 0:
                values[stage].append(float(value))
    return {stage: _summary(values[stage]) for stage in ALLOWED_STAGE_NAMES if values[stage]}


def _error_category(response: httpx.Response | None, exc: Exception | None) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPError):
        return "network"
    if response is None:
        return "unknown"
    if response.status_code in {401, 403}:
        return "authorization"
    if response.status_code == 429:
        return "rate_limit"
    if response.status_code >= 500:
        return "server"
    if response.status_code >= 400:
        return "request"
    return "none"


async def _one_request(
    client: httpx.AsyncClient,
    *,
    endpoint: str,
    case_id: str,
    case: dict[str, Any],
    role_override: str | None = None,
) -> dict[str, Any]:
    """Return only opaque ID, status and timing; discard response content."""

    started = time.perf_counter()
    response: httpx.Response | None = None
    try:
        response = await client.post(
            endpoint,
            json={
                "question": case["question"],
                "role": role_override or case.get("role", "citizen"),
                "show_rag_trace": True,
                "idempotency_key": f"feature005-{uuid.uuid4().hex}",
            },
        )
        elapsed = round((time.perf_counter() - started) * 1000)
        payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        sections = payload.get("answer_sections") if isinstance(payload, dict) else []
        runtime = _extract_runtime_signals(payload)
        transport_error = _error_category(response, None)
        return {
            "case_id": case_id,
            "status": "completed" if response.is_success else "failed",
            "http_status": response.status_code,
            "duration_ms": elapsed,
            "section_statuses": [
                str(section.get("status"))
                for section in (sections or [])
                if isinstance(section, dict) and section.get("status")
            ],
            "repair_count": runtime["repair_count"],
            "error_category": (
                runtime["error_category"]
                if transport_error == "none"
                and runtime["error_category"] != "unknown"
                else transport_error
            ),
            "quality_gate_status": runtime["quality_gate_status"],
            "stage_timings_ms": _extract_stage_timings(payload),
        }
    except Exception as exc:  # never persist exception text
        return {
            "case_id": case_id,
            "status": "failed",
            "http_status": None,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "section_statuses": [],
            "repair_count": 0,
            "error_category": _error_category(response, exc),
            "quality_gate_status": "unavailable",
            "stage_timings_ms": {},
        }


async def run_benchmark(
    *,
    base_url: str,
    token: str,
    concurrency: int,
    mode: str,
    case_ids: list[str],
    role_override: str | None = None,
) -> dict[str, Any]:
    selected = [(case_id, QUALITY_CASES[case_id]) for case_id in case_ids]
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    semaphore = asyncio.Semaphore(concurrency)
    timeout = httpx.Timeout(90.0, connect=10.0)

    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        async def bounded(case_id: str, case: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                request_kwargs = {
                    "endpoint": f"{base_url.rstrip('/')}/api/search/ask/simple",
                    "case_id": case_id,
                    "case": case,
                }
                if role_override:
                    request_kwargs["role_override"] = role_override
                return await _one_request(client, **request_kwargs)

        if mode == "warm":
            unique_cases = dict(selected)
            for case_id, case in unique_cases.items():
                await bounded(case_id, case)
        rows = await asyncio.gather(*(bounded(case_id, case) for case_id, case in selected))

    durations = [row["duration_ms"] for row in rows]
    status_counts = Counter(row["status"] for row in rows)
    section_statuses = Counter(status for row in rows for status in row["section_statuses"])
    errors = Counter(row["error_category"] for row in rows)
    quality_gates = Counter(row["quality_gate_status"] for row in rows)
    return {
        "schema_version": 1,
        "mode": mode,
        "base_url": base_url.rstrip("/"),
        "concurrency": concurrency,
        "request_count": len(rows),
        "completed_count": status_counts.get("completed", 0),
        "status_counts": dict(status_counts),
        "section_status_counts": dict(section_statuses),
        "error_category_counts": dict(errors),
        "quality_gate_counts": dict(quality_gates),
        "fallback_count": sum(
            1
            for row in rows
            if row["error_category"]
            in {"timeout", "validation_failed", "retrieval_unavailable"}
        ),
        "repair_count": sum(row["repair_count"] for row in rows),
        "latency_ms": _summary(durations),
        "stage_latency_ms": _stage_summaries(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a privacy-safe Feature 005 local benchmark.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--token-env", default="LEGAL_BENCHMARK_TOKEN")
    parser.add_argument("--concurrency", type=int, choices=(1, 5, 10, 20), required=True)
    parser.add_argument("--mode", choices=("cold", "warm"), required=True)
    parser.add_argument("--case", dest="cases", action="append", choices=BENCHMARK_CASE_IDS, required=True)
    parser.add_argument("--role-override", choices=("citizen", "officer", "admin"))
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    if args.concurrency < 1:
        raise SystemExit("Concurrency must be positive.")

    artifact = asyncio.run(
        run_benchmark(
            base_url=args.base_url,
            token=os.getenv(args.token_env, ""),
            concurrency=args.concurrency,
            mode=args.mode,
            case_ids=list(args.cases),
            role_override=args.role_override,
        )
    )
    if set(artifact) != ALLOWED_ARTIFACT_KEYS:
        raise SystemExit("Refusing to write an artifact outside the approved privacy-safe shape.")
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote privacy-safe aggregate benchmark artifact: {args.artifact}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
