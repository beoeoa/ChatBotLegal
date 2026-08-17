"""Aggregate resumable answer-benchmark segments without rerunning the API."""

from __future__ import annotations

import argparse
import copy
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from benchmark_candidate_answer import percentile, summarize_rows


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def project(endpoint: str, rows: list[dict]) -> dict:
    normalized_rows = []
    for original in rows:
        row = copy.deepcopy(original)
        if row.get("http_status") != 200:
            row["fallback_or_blocked"] = True
            row["source_gap"] = True
            if not row.get("error"):
                row["error"] = f"HTTP_{row.get('http_status')}"
        normalized_rows.append(row)
    rows = normalized_rows
    latencies = [float(row.get("elapsed_ms_client") or 0.0) for row in rows]
    per_domain: dict[str, dict] = {}
    for domain in sorted({str(row.get("domain") or "unmapped") for row in rows}):
        per_domain[domain] = summarize_rows(
            [row for row in rows if str(row.get("domain") or "unmapped") == domain]
        )
    return {
        "base_url": endpoint,
        "case_count": len(rows),
        "summary": summarize_rows(rows),
        "per_domain": per_domain,
        "cases": rows,
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-case-count", type=int, default=1000)
    args = parser.parse_args()
    paths = sorted(args.segment_dir.glob("answer-golden-1000-segment-*-v2.json"))
    if not paths:
        raise SystemExit("no_segment_reports")
    baseline_rows: list[dict] = []
    candidate_rows: list[dict] = []
    for path in paths:
        payload = load(path)
        baseline_rows.extend(payload.get("baseline", {}).get("cases") or [])
        candidate_rows.extend(payload.get("candidate", {}).get("cases") or [])
    for label, rows in (("baseline", baseline_rows), ("candidate", candidate_rows)):
        ids = [str(row.get("case_id")) for row in rows]
        if len(rows) != args.expected_case_count or len(set(ids)) != len(ids):
            raise SystemExit(f"{label}_segment_coverage_invalid:{len(rows)}:{len(set(ids))}")
    baseline = project("http://127.0.0.1:5055", baseline_rows)
    candidate = project("http://127.0.0.1:5056", candidate_rows)
    comparison = {
        "answer_rate_delta": round(candidate["summary"]["answer_rate"] - baseline["summary"]["answer_rate"], 6),
        "grounded_rate_delta": round(candidate["summary"]["grounded_rate"] - baseline["summary"]["grounded_rate"], 6),
        "source_gap_rate_delta": round(candidate["summary"]["source_gap_rate"] - baseline["summary"]["source_gap_rate"], 6),
        "fallback_rate_delta": round(candidate["summary"]["fallback_rate"] - baseline["summary"]["fallback_rate"], 6),
        "citation_verified_rate_delta": round(candidate["summary"]["citation_verified_rate"] - baseline["summary"]["citation_verified_rate"], 6),
        "client_p95_ms_delta": round(candidate["latency_ms"]["p95"] - baseline["latency_ms"]["p95"], 3),
    }
    output = {
        "schema_version": "legal-candidate-answer-benchmark-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "segment_count": len(paths),
        "segments": [str(path.resolve()) for path in paths],
        "case_count": args.expected_case_count,
        "balanced": True,
        "credentials_recorded": False,
        "question_bodies_recorded": False,
        "answer_bodies_recorded": False,
        "baseline": baseline,
        "candidate": candidate,
        "comparison": comparison,
        "safety": {
            "database_mutated": False,
            "vectors_mutated": False,
            "active_pointer_changed": False,
        },
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "comparison": comparison}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
