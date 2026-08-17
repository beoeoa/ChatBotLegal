"""Compare answer-level behaviour for baseline and candidate APIs.

The script is intentionally a shadow/diagnostic evaluator.  It never changes
the database, active pointer, Chroma collections, or provider configuration.
Passwords are read only from the process environment and are not written to
the report; question and answer bodies are represented by SHA-256 hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


DOMAIN_ALIASES = {
    "ho_tich_chung_thuc": ("hộ tịch", "chứng thực"),
    "cu_tru_an_ninh": ("cư trú", "căn cước", "an ninh"),
    "dat_dai_xay_dung": ("đất đai", "xây dựng", "môi trường"),
    "an_sinh_y_te_giao_duc": ("an sinh", "y tế", "giáo dục"),
    "khieu_nai_to_cao_xu_phat": ("khiếu nại", "tố cáo", "xử phạt"),
}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_domain(value: str) -> str:
    lowered = (value or "").casefold()
    for domain, aliases in DOMAIN_ALIASES.items():
        if any(alias in lowered for alias in aliases):
            return domain
    return "unmapped"


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)


def select_cases(path: Path, limit: int, balanced: bool) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("cases", payload if isinstance(payload, list) else [])
    usable = [row for row in rows if (row.get("questions") or {}).get("citizen")]
    if not balanced:
        return usable[:limit]

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in usable:
        buckets[canonical_domain(str(row.get("domain") or ""))].append(row)
    domains = [domain for domain in DOMAIN_ALIASES if buckets.get(domain)]
    if not domains:
        return usable[:limit]
    selected: list[dict[str, Any]] = []
    index = 0
    while len(selected) < limit:
        made_progress = False
        for domain in domains:
            bucket = buckets[domain]
            if index < len(bucket) and len(selected) < limit:
                selected.append(bucket[index])
                made_progress = True
        if not made_progress:
            break
        index += 1
    return selected


def login(session: requests.Session, base_url: str, password: str) -> str:
    response = session.post(
        f"{base_url.rstrip('/')}/api/auth/login",
        json={"identifier": "citizen01", "password": password, "role": "citizen"},
        timeout=20,
    )
    response.raise_for_status()
    token = response.json().get("token")
    if not token:
        raise RuntimeError(f"login_missing_token:{base_url}")
    return str(token)


def run_endpoint(
    base_url: str,
    cases: list[dict[str, Any]],
    password: str,
    *,
    request_timeout_seconds: float,
    checkpoint_path: Path | None = None,
    concurrency: int = 1,
) -> dict[str, Any]:
    def run_one(case: dict[str, Any]) -> dict[str, Any]:
        session = requests.Session()
        token = login(session, base_url, password)
        question = str(case["questions"]["citizen"])
        started = time.perf_counter()
        item: dict[str, Any] = {
            "case_id": case.get("case_id"),
            "domain": canonical_domain(str(case.get("domain") or "")),
            "question_sha256": sha256_text(question),
        }
        try:
            response = session.post(
                f"{base_url.rstrip('/')}/api/search/ask/simple",
                json={
                    "question": question,
                    "role": "citizen",
                    "legal_as_of": case.get("legal_as_of"),
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=request_timeout_seconds,
            )
            item["http_status"] = response.status_code
            body = response.json()
            answer = str(body.get("answer") or "")
            non_200 = response.status_code != 200
            citations = body.get("citations") or []
            claim_validation = body.get("claim_validation") or []
            evidence_coverage = body.get("evidence_coverage") or {}
            verified_claims = sum(
                1 for claim in claim_validation if claim.get("status") == "verified"
            )
            item.update(
                {
                    "answer_present": bool(answer.strip()),
                    "answer_sha256": sha256_text(answer) if answer else None,
                    "answer_mode": body.get("answer_mode"),
                    "grounding_status": body.get("grounding_status"),
                    "source_gap": bool(body.get("source_gap")) or non_200,
                    "fallback_tier": body.get("fallback_tier"),
                    "fallback_or_blocked": non_200 or bool(
                        body.get("blocked_reason")
                        or body.get("error")
                        or str(body.get("fallback_tier") or "").casefold()
                        not in {"", "exact", "normal", "verified"}
                    ),
                    "citation_count": len(citations),
                    "verified_claim_count": verified_claims,
                    "claim_count": len(claim_validation),
                    "citation_coverage_verified": (
                        (evidence_coverage.get("citations") or {}).get("status")
                        == "verified"
                    ),
                    "quality_flags": sorted(
                        str(flag) for flag in (body.get("quality_flags") or [])
                    ),
                    "error": body.get("error") or (f"HTTP_{response.status_code}" if non_200 else None),
                    "latency_ms_api": body.get("latency_ms"),
                    "timing_summary": body.get("timing_summary") or {},
                }
            )
        except Exception as exc:  # keep a failed case auditable and continue
            item.update(
                {
                    "http_status": None,
                    "answer_present": False,
                    "source_gap": True,
                    "fallback_or_blocked": True,
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
        item["elapsed_ms_client"] = round((time.perf_counter() - started) * 1000, 3)
        return item

    result_rows: list[dict[str, Any]] = []
    if concurrency <= 1:
        completed = (run_one(case) for case in cases)
        for item in completed:
            result_rows.append(item)
            _write_checkpoint(checkpoint_path, base_url, cases, result_rows, item)
    else:
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
            futures = [executor.submit(run_one, case) for case in cases]
            for future in as_completed(futures):
                item = future.result()
                result_rows.append(item)
                _write_checkpoint(checkpoint_path, base_url, cases, result_rows, item)

    # Keep output deterministic even when concurrent requests complete out of order.
    result_rows.sort(key=lambda row: cases.index(next(case for case in cases if case.get("case_id") == row.get("case_id"))))

    latencies = [float(row["elapsed_ms_client"]) for row in result_rows]
    by_domain: dict[str, dict[str, Any]] = {}
    for domain in sorted({row["domain"] for row in result_rows}):
        subset = [row for row in result_rows if row["domain"] == domain]
        by_domain[domain] = summarize_rows(subset)
    return {
        "base_url": base_url.rstrip("/"),
        "case_count": len(result_rows),
        "summary": summarize_rows(result_rows),
        "per_domain": by_domain,
        "cases": result_rows,
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
    }


def _write_checkpoint(
    checkpoint_path: Path | None,
    base_url: str,
    cases: list[dict[str, Any]],
    result_rows: list[dict[str, Any]],
    item: dict[str, Any],
) -> None:
    if checkpoint_path:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(
            json.dumps(
                {
                    "schema_version": "legal-candidate-answer-benchmark-checkpoint-v1",
                    "base_url": base_url.rstrip("/"),
                    "completed_cases": len(result_rows),
                    "total_cases": len(cases),
                    "last_case_id": item.get("case_id"),
                    "summary": summarize_rows(result_rows),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    status_200 = sum(row.get("http_status") == 200 for row in rows)
    answers = sum(bool(row.get("answer_present")) for row in rows)
    grounded = sum(row.get("grounding_status") == "fully_grounded" for row in rows)
    source_gap = sum(bool(row.get("source_gap")) for row in rows)
    fallback = sum(bool(row.get("fallback_or_blocked")) for row in rows)
    citation_verified = sum(bool(row.get("citation_coverage_verified")) for row in rows)
    return {
        "case_count": count,
        "http_200": status_200,
        "answer_present": answers,
        "fully_grounded": grounded,
        "source_gap": source_gap,
        "fallback_or_blocked": fallback,
        "citation_coverage_verified": citation_verified,
        "answer_rate": round(answers / count, 6) if count else None,
        "grounded_rate": round(grounded / count, 6) if count else None,
        "source_gap_rate": round(source_gap / count, 6) if count else None,
        "fallback_rate": round(fallback / count, 6) if count else None,
        "citation_verified_rate": round(citation_verified / count, 6) if count else None,
        "answer_modes": dict(Counter(str(row.get("answer_mode")) for row in rows)),
        "errors": dict(Counter(str(row.get("error")) for row in rows if row.get("error"))),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--baseline-url", required=True)
    parser.add_argument("--candidate-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--request-timeout-seconds", type=float, default=45.0)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument(
        "--checkpoint-dir",
        type=Path,
        help="Optional directory receiving per-endpoint progress checkpoints.",
    )
    parser.add_argument("--legal-as-of", default=None)
    parser.add_argument("--password-env", default="CANDIDATE_ANSWER_PASSWORD")
    parser.add_argument("--balanced", action="store_true")
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Run only the named Golden case id(s); may be repeated."
    )
    args = parser.parse_args()

    password = os.getenv(args.password_env, "")
    if not password:
        raise SystemExit(f"missing_password_env:{args.password_env}")
    all_selected = select_cases(
        args.golden,
        max(args.limit + args.offset, args.limit),
        args.balanced,
    )
    if args.case_ids:
        wanted = set(args.case_ids)
        payload = json.loads(args.golden.read_text(encoding="utf-8"))
        rows = payload.get("cases", payload if isinstance(payload, list) else [])
        cases = [row for row in rows if row.get("case_id") in wanted]
        missing = sorted(wanted - {str(row.get("case_id")) for row in cases})
        if missing:
            raise SystemExit("missing_case_ids:" + ",".join(missing))
    else:
        cases = all_selected[args.offset : args.offset + args.limit]
    if args.legal_as_of:
        for case in cases:
            case["legal_as_of"] = args.legal_as_of
    checkpoint_dir = args.checkpoint_dir.resolve() if args.checkpoint_dir else None
    baseline_checkpoint = (
        checkpoint_dir / "baseline.json" if checkpoint_dir else None
    )
    candidate_checkpoint = (
        checkpoint_dir / "candidate.json" if checkpoint_dir else None
    )
    baseline = run_endpoint(
        args.baseline_url,
        cases,
        password,
        request_timeout_seconds=args.request_timeout_seconds,
        checkpoint_path=baseline_checkpoint,
        concurrency=args.concurrency,
    )
    candidate = run_endpoint(
        args.candidate_url,
        cases,
        password,
        request_timeout_seconds=args.request_timeout_seconds,
        checkpoint_path=candidate_checkpoint,
        concurrency=args.concurrency,
    )

    output = {
        "schema_version": "legal-candidate-answer-benchmark-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "golden": str(args.golden.resolve()),
        "case_count": len(cases),
        "offset": args.offset,
        "request_timeout_seconds": args.request_timeout_seconds,
        "concurrency": args.concurrency,
        "balanced": bool(args.balanced),
        "credentials_recorded": False,
        "question_bodies_recorded": False,
        "answer_bodies_recorded": False,
        "baseline": baseline,
        "candidate": candidate,
        "comparison": {
            "answer_rate_delta": round(
                (candidate["summary"]["answer_rate"] or 0)
                - (baseline["summary"]["answer_rate"] or 0),
                6,
            ),
            "grounded_rate_delta": round(
                (candidate["summary"]["grounded_rate"] or 0)
                - (baseline["summary"]["grounded_rate"] or 0),
                6,
            ),
            "source_gap_rate_delta": round(
                (candidate["summary"]["source_gap_rate"] or 0)
                - (baseline["summary"]["source_gap_rate"] or 0),
                6,
            ),
            "fallback_rate_delta": round(
                (candidate["summary"]["fallback_rate"] or 0)
                - (baseline["summary"]["fallback_rate"] or 0),
                6,
            ),
            "citation_verified_rate_delta": round(
                (candidate["summary"]["citation_verified_rate"] or 0)
                - (baseline["summary"]["citation_verified_rate"] or 0),
                6,
            ),
            "client_p95_ms_delta": round(
                (candidate["latency_ms"]["p95"] or 0)
                - (baseline["latency_ms"]["p95"] or 0),
                3,
            ),
        },
        "safety": {
            "database_mutated": False,
            "vectors_mutated": False,
            "active_pointer_changed": False,
        },
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output.resolve()), "comparison": output["comparison"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
