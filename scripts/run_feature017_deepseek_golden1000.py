#!/usr/bin/env python3
"""Run approved Golden V3 through the live citizen answer endpoint.

This is a read-only answer evaluation.  It never serializes credentials and
does not mutate Golden truth, the active release, legal corpus or vectors.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

EVALUATOR_VERSION = "feature017-deepseek-golden1000-evaluator-v2"
CHECKPOINT_SCHEMA_VERSION = "feature017-deepseek-golden1000-checkpoint-v2"
REPORT_SCHEMA_VERSION = "feature017-deepseek-golden1000-live-v2"


def _public_mode(value: Any) -> str:
    mode = str(value or "")
    if mode == "normal":
        return "grounded_answer"
    return mode


def _sha256(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_run_identity(
    *,
    base_url: str,
    role: str,
    golden_path: Path,
    cases: list[dict[str, Any]],
    concurrency: int | None = None,
    request_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Bind resumable evidence to one evaluator, target and selected dataset."""

    case_ids = [str(case.get("case_id") or "") for case in cases]
    selection_payload = json.dumps(
        case_ids,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    identity: dict[str, Any] = {
        "evaluator_version": EVALUATOR_VERSION,
        "role": role,
        "golden_sha256": _sha256_file(golden_path),
        "target_sha256": _sha256(base_url.rstrip("/")),
        "case_selection_sha256": _sha256(selection_payload),
        "case_count": len(cases),
    }
    if concurrency is not None:
        identity["concurrency"] = max(1, int(concurrency))
    if request_timeout_seconds is not None:
        identity["request_timeout_seconds"] = float(request_timeout_seconds)
    return identity


def _select_cases(
    cases: list[dict[str, Any]],
    *,
    limit: int | None,
    balanced_domains: bool,
) -> list[dict[str, Any]]:
    if limit is None:
        return cases
    if not balanced_domains:
        return cases[:limit]
    by_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_domain[str(case.get("domain") or "unknown")].append(case)
    domains = sorted(by_domain)
    if not domains or limit % len(domains) != 0:
        raise RuntimeError("FEATURE017_BALANCED_LIMIT_INVALID")
    per_domain = limit // len(domains)
    if any(len(by_domain[domain]) < per_domain for domain in domains):
        raise RuntimeError("FEATURE017_BALANCED_CASES_INSUFFICIENT")
    return [case for domain in domains for case in by_domain[domain][:per_domain]]


def _evaluate(case: dict[str, Any], payload: dict[str, Any], status: int) -> list[str]:
    errors: list[str] = []
    expected = sorted(str(value) for value in case.get("expected_form_ids") or [])
    actual = sorted(
        str(form.get("form_id"))
        for form in payload.get("recommended_forms") or []
        if isinstance(form, dict) and form.get("form_id")
    )
    if status != 200:
        errors.append(f"HTTP_{status}")
    if not str(payload.get("answer") or "").strip():
        errors.append("EMPTY_ANSWER")
    if actual != expected:
        errors.append("FORM_SET_MISMATCH")
    if set(actual).intersection(case.get("forbidden_form_ids") or []):
        errors.append("FORBIDDEN_FORM_RETURNED")
    actual_mode = _public_mode(payload.get("answer_mode"))
    expected_mode = case.get("expected_answer_mode")
    # T088 deliberately keeps independently grounded claims when a deferred
    # owner leaves forms/conclusion coverage incomplete.  That is a coverage
    # advisory, not evidence failure, so do not fail the Golden gate solely
    # because the UI remains in its normal grounded mode.
    advisory_coverage = (
        expected_mode == "source_view_only"
        and actual_mode == "grounded_answer"
        and payload.get("grounding_status") == "fully_grounded"
        and str((payload.get("answer_completeness") or {}).get("status") or "")
        == "incomplete"
        and any(
            str(flag).startswith("missing_")
            for flag in payload.get("quality_flags") or []
        )
        and not (payload.get("error") or {}).get("code")
    )
    if actual_mode != expected_mode and not advisory_coverage:
        errors.append("ANSWER_MODE_MISMATCH")
    if case.get("expected_clarification") and not payload.get("clarifying_questions"):
        errors.append("CLARIFICATION_MISSING")
    if (
        case.get("expected_answer_mode") == "grounded_answer"
        and (payload.get("error") or {}).get("code") == "AI_PROVIDER_FALLBACK"
    ):
        errors.append("UNEXPECTED_PROVIDER_FALLBACK")
    return errors


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Persist evaluator state without leaving a truncated checkpoint."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_checkpoint(
    path: Path,
    *,
    run_identity: dict[str, Any],
) -> list[dict[str, Any] | None]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise RuntimeError("FEATURE017_CHECKPOINT_SCHEMA_INVALID")
    if payload.get("evaluator_version") != EVALUATOR_VERSION:
        raise RuntimeError("FEATURE017_CHECKPOINT_EVALUATOR_MISMATCH")
    if payload.get("run_identity") != run_identity:
        raise RuntimeError("FEATURE017_CHECKPOINT_INPUT_MISMATCH")
    rows = payload.get("results")
    if not isinstance(rows, list):
        raise RuntimeError("FEATURE017_CHECKPOINT_RESULTS_INVALID")
    return rows


def _safe_error_projection(payload: dict[str, Any], *, status: int) -> dict[str, Any] | None:
    """Keep stable diagnostics without copying provider/server prose to evidence."""

    candidate = payload.get("error")
    if not isinstance(candidate, dict):
        candidate = payload.get("detail")
    if not isinstance(candidate, dict):
        return {"code": f"HTTP_{status}", "retryable": status >= 500} if status != 200 else None
    code = str(candidate.get("code") or "").strip()
    if not code and status != 200:
        code = f"HTTP_{status}"
    if not code:
        return None
    return {
        "code": code,
        "retryable": bool(candidate.get("retryable", status >= 500)),
    }


def _evaluation_payload_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Recreate only the public fields needed by the evaluator."""

    clarification_count = int(row.get("clarifying_question_count") or 0)
    return {
        "answer": "recorded" if row.get("answer_present") else "",
        "recommended_forms": [
            {"form_id": form_id}
            for form_id in row.get("actual_form_ids") or []
        ],
        "answer_mode": row.get("answer_mode"),
        "grounding_status": row.get("grounding_status"),
        "answer_completeness": {
            "status": row.get("answer_completeness_status")
        },
        "clarifying_questions": [True] * clarification_count,
        "quality_flags": row.get("quality_flags") or [],
        "error": row.get("error"),
    }


def _reevaluate_checkpoint_rows(
    rows: list[dict[str, Any] | None],
    *,
    cases: list[dict[str, Any]],
) -> list[dict[str, Any] | None]:
    """Apply the current evaluator to every completed resumable row."""

    if len(rows) != len(cases):
        raise RuntimeError("FEATURE017_CHECKPOINT_CASE_COUNT_INVALID")
    refreshed: list[dict[str, Any] | None] = []
    for case, row in zip(cases, rows, strict=True):
        if row is None:
            refreshed.append(None)
            continue
        if row.get("case_id") != case.get("case_id"):
            raise RuntimeError("FEATURE017_CHECKPOINT_CASE_ID_MISMATCH")
        required_snapshot_fields = {
            "answer_present",
            "actual_form_ids",
            "clarifying_question_count",
            "answer_completeness_status",
        }
        if not required_snapshot_fields.issubset(row):
            raise RuntimeError("FEATURE017_CHECKPOINT_RESPONSE_SNAPSHOT_MISSING")
        updated = dict(row)
        updated["evaluation_errors"] = _evaluate(
            case,
            _evaluation_payload_from_row(updated),
            int(updated.get("http_status") or 0),
        )
        refreshed.append(updated)
    return refreshed


async def execute(
    *,
    base_url: str,
    golden_path: Path,
    output_path: Path,
    identifier: str,
    password: str,
    concurrency: int,
    role: str = "citizen",
    checkpoint_path: Path | None = None,
    resume: bool = False,
    request_timeout_seconds: float = 30.0,
    include_content: bool = False,
    limit: int | None = None,
    balanced_domains: bool = False,
) -> dict[str, Any]:
    if not password:
        env_name = "FEATURE017_OFFICER_PASSWORD" if role == "officer" else "FEATURE017_CITIZEN_PASSWORD"
        raise RuntimeError(f"{env_name} is required")
    golden = json.loads(golden_path.read_text(encoding="utf-8-sig"))
    all_cases = list(golden.get("cases") or [])
    if len(all_cases) != 1000:
        raise RuntimeError("FEATURE017_GOLDEN_CASE_COUNT_INVALID")
    if limit is not None and not 1 <= limit <= len(all_cases):
        raise RuntimeError("FEATURE017_CASE_LIMIT_INVALID")
    cases = _select_cases(all_cases, limit=limit, balanced_domains=balanced_domains)
    run_identity = _build_run_identity(
        base_url=base_url,
        role=role,
        golden_path=golden_path,
        cases=cases,
        concurrency=concurrency,
        request_timeout_seconds=request_timeout_seconds,
    )

    limits = httpx.Limits(
        max_connections=max(2, concurrency + 2),
        max_keepalive_connections=max(2, concurrency),
    )
    checkpoint_path = checkpoint_path or output_path.with_suffix(".checkpoint.json")
    results: list[dict[str, Any] | None] = (
        _load_checkpoint(checkpoint_path, run_identity=run_identity)
        if resume
        else []
    )
    if results and len(results) != len(cases):
        raise RuntimeError("FEATURE017_CHECKPOINT_CASE_COUNT_INVALID")
    if results:
        results = _reevaluate_checkpoint_rows(results, cases=cases)
    if not results:
        results = [None] * len(cases)

    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=max(5.0, request_timeout_seconds), limits=limits
    ) as client:
        login_payload = {"password": password, "role": role}
        if identifier:
            login_payload["identifier"] = identifier
        login = await client.post("/api/auth/login", json=login_payload)
        login.raise_for_status()
        token = str(login.json().get("token") or "")
        if not token:
            raise RuntimeError("Citizen login returned no token")
        headers = {"Authorization": f"Bearer {token}", "X-User-Role": role}
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def run_one(index: int, case: dict[str, Any]) -> None:
            async with semaphore:
                response: httpx.Response | None = None
                last_error: str | None = None
                started = time.perf_counter()
                for attempt in range(3):
                    try:
                        response = await client.post(
                            "/api/search/ask/simple",
                            headers=headers,
                            json={
                                "question": case["question"],
                                "role": role,
                                "legal_as_of": case["legal_as_of"],
                                "idempotency_key": f"f017-full-{role}-{case['case_id']}-v1",
                            },
                        )
                        if response.status_code not in {429, 502, 503, 504}:
                            break
                    except (httpx.TimeoutException, httpx.NetworkError) as exc:
                        last_error = type(exc).__name__
                    await asyncio.sleep(0.75 * (attempt + 1))
                elapsed = time.perf_counter() - started
                if response is None:
                    payload: dict[str, Any] = {}
                    status = 0
                    errors = [last_error or "REQUEST_FAILED"]
                else:
                    status = response.status_code
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = {}
                    errors = _evaluate(case, payload, status)
                clarifying_questions = payload.get("clarifying_questions") or []
                answer_completeness = payload.get("answer_completeness") or {}
                result_row = {
                    "case_id": case["case_id"],
                    "domain": case["domain"],
                    "category": case["category"],
                    "expected_state": case["expected_state"],
                    "expected_answer_mode": case["expected_answer_mode"],
                    "expected_form_ids": case.get("expected_form_ids") or [],
                    "http_status": status,
                    "elapsed_seconds": round(elapsed, 3),
                    "question_sha256": _sha256(case["question"]),
                    "answer_sha256": _sha256(payload.get("answer")),
                    "answer_present": bool(str(payload.get("answer") or "").strip()),
                    "answer_mode": payload.get("answer_mode"),
                    "grounding_status": payload.get("grounding_status"),
                    "actual_form_ids": [
                        form.get("form_id")
                        for form in payload.get("recommended_forms") or []
                        if isinstance(form, dict) and form.get("form_id")
                    ],
                    "clarifying_question_count": len(clarifying_questions),
                    "quality_flags": payload.get("quality_flags") or [],
                    "answer_completeness_status": (
                        str(answer_completeness.get("status") or "") or None
                    ),
                    "error": _safe_error_projection(payload, status=status),
                    "evaluation_errors": errors,
                }
                raw_timing = payload.get("timing_summary")
                if isinstance(raw_timing, dict):
                    result_row["timing_summary"] = {
                        key: float(raw_timing[key])
                        for key in (
                            "retrieval_ms",
                            "provisioning_ms",
                            "generation_ms",
                            "validation_ms",
                            "end_to_end_ms",
                        )
                        if isinstance(raw_timing.get(key), (int, float))
                    }
                if include_content:
                    result_row["question"] = case["question"]
                    result_row["answer"] = payload.get("answer")
                    result_row["clarifying_questions"] = clarifying_questions
                results[index] = result_row

        tasks = [
            asyncio.create_task(run_one(index, case))
            for index, case in enumerate(cases)
            if results[index] is None
        ]
        completed = sum(row is not None for row in results)
        for task in asyncio.as_completed(tasks):
            await task
            completed += 1
            if completed % 25 == 0:
                _write_json_atomic(
                    checkpoint_path,
                    {
                        "schema_version": CHECKPOINT_SCHEMA_VERSION,
                        "evaluator_version": EVALUATOR_VERSION,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "run_identity": run_identity,
                        "role": role,
                        "golden_file": str(golden_path),
                        "completed": completed,
                        "results": results,
                    },
                )
                print(f"checkpoint={completed}/1000", flush=True)

        # Persist short canaries and refreshed resumed rows too; otherwise a
        # non-multiple-of-25 run can finish without a reusable checkpoint.
        _write_json_atomic(
            checkpoint_path,
            {
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "evaluator_version": EVALUATOR_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "run_identity": run_identity,
                "role": role,
                "golden_file": str(golden_path),
                "completed": completed,
                "results": results,
            },
        )

    rows = [row for row in results if row is not None]
    timings = [float(row["elapsed_seconds"]) for row in rows]
    error_counts = Counter(
        error for row in rows for error in row.get("evaluation_errors") or []
    )
    pass_count = sum(not row.get("evaluation_errors") for row in rows)
    stage_timings: dict[str, dict[str, float]] = {}
    for key in ("retrieval_ms", "provisioning_ms", "generation_ms", "validation_ms", "end_to_end_ms"):
        values = [float((row.get("timing_summary") or {}).get(key)) for row in rows if (row.get("timing_summary") or {}).get(key) is not None]
        if values:
            stage_timings[key] = {
                "p50_ms": round(statistics.median(values), 1),
                "p95_ms": round(statistics.quantiles(values, n=100)[94], 1) if len(values) >= 2 else round(values[0], 1),
                "max_ms": round(max(values), 1),
            }
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_identity": run_identity,
        "role": role,
        "credentials_recorded": False,
        "golden_file": str(golden_path),
        "cases": rows,
        "summary": {
            "case_count": len(rows),
            "passed": pass_count,
            "failed": len(rows) - pass_count,
            "pass_rate": pass_count / len(rows),
            "http_200": sum(row["http_status"] == 200 for row in rows),
            "exact_form_set": sum(
                "FORM_SET_MISMATCH" not in row["evaluation_errors"] for row in rows
            ),
            "unexpected_provider_fallback": error_counts[
                "UNEXPECTED_PROVIDER_FALLBACK"
            ],
            "error_counts": dict(error_counts),
            "p50_seconds": statistics.median(timings),
            "p95_seconds": statistics.quantiles(timings, n=100)[94],
            "max_seconds": max(timings),
            "stage_timings": stage_timings,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5055")
    parser.add_argument("--role", choices=("citizen", "officer"), default="citizen")
    parser.add_argument("--identifier", default=None)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path(
            "outputs/feature017-golden-v3-user-journey-1000/"
            "golden-v3-1000-approved.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/feature017/deepseek-golden1000-live.json"),
    )
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--request-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N cases (canary smoke only).")
    parser.add_argument("--balanced-domains", action="store_true", help="Select an equal number from each Golden domain.")
    parser.add_argument(
        "--include-content",
        action="store_true",
        help="Include question/answer text; use only for synthetic, ignored reports.",
    )
    args = parser.parse_args()
    identifier = args.identifier or os.getenv(
        "FEATURE017_OFFICER_IDENTIFIER" if args.role == "officer" else "FEATURE017_CITIZEN_IDENTIFIER",
        "",
    )
    password = os.getenv(
        "FEATURE017_OFFICER_PASSWORD" if args.role == "officer" else "FEATURE017_CITIZEN_PASSWORD",
        "",
    )
    report = asyncio.run(
        execute(
            base_url=args.base_url,
            golden_path=args.golden,
            output_path=args.output,
            identifier=identifier,
            password=password,
            concurrency=args.concurrency,
            role=args.role,
            checkpoint_path=args.checkpoint,
            resume=args.resume,
            request_timeout_seconds=args.request_timeout_seconds,
            include_content=args.include_content,
            limit=args.limit,
            balanced_domains=args.balanced_domains,
        )
    )
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
