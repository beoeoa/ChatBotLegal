"""Safely merge disjoint retrieval-only Golden benchmark segments.

The full approved Golden can be expensive on local PostgreSQL/Chroma. This
utility combines independently completed segments without averaging rounded
percentiles or silently accepting duplicate/missing case IDs.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from typing import Any


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(float(ordered[index]), 3)


def expected_hit(row: dict[str, Any]) -> bool:
    return bool(row.get("hit_at_10"))


def aggregate_run(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    per_domain: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        per_domain[str(row.get("domain") or "unknown")].append(row)
    source_rows = [row for row in rows if row.get("expected_law_numbers")]
    top5 = sum(bool(row.get("direct_source_top5")) for row in source_rows)
    mrr = sum(1.0 / int(row["hit_rank"]) for row in source_rows if row.get("hit_rank"))
    citation = sum(
        1
        for row in rows
        if sum(
            1
            for result in row.get("top_results") or []
            if result.get("law_number")
            and result.get("article_number")
            and result.get("source_url")
        )
    )
    timings = [float(row.get("latency_ms") or 0.0) for row in rows]
    domains: dict[str, dict[str, Any]] = {}
    for domain, domain_rows in sorted(per_domain.items()):
        eligible = [row for row in domain_rows if row.get("expected_law_numbers")]
        gaps = [row for row in domain_rows if not row.get("expected_law_numbers")]
        domain_timings = [float(row.get("latency_ms") or 0.0) for row in domain_rows]
        domains[domain] = {
            "case_count": len(domain_rows),
            "source_case_count": len(eligible),
            "gap_case_count": len(gaps),
            "source_recall_at_10": round(
                sum(expected_hit(row) for row in eligible) / len(eligible), 5
            )
            if eligible
            else None,
            "source_direct_source_top5": round(
                sum(bool(row.get("direct_source_top5")) for row in eligible) / len(eligible), 5
            )
            if eligible
            else None,
            "gap_result_rate": round(
                sum(bool(row.get("result_count")) for row in gaps) / len(gaps), 5
            )
            if gaps
            else None,
            "p50_ms": percentile(domain_timings, 0.5),
            "p95_ms": percentile(domain_timings, 0.95),
        }
    return {
        "name": name,
        "case_count": len(rows),
        "recall_at_10": round(sum(expected_hit(row) for row in rows) / len(rows), 5) if rows else 0.0,
        "direct_source_top5": round(top5 / len(source_rows), 5) if source_rows else 0.0,
        "mrr": round(mrr / len(source_rows), 5) if source_rows else 0.0,
        "no_result_rate": round(sum(not row.get("result_count") for row in rows) / len(rows), 5) if rows else 0.0,
        "citation_ready_at_10": round(citation / len(rows), 5) if rows else 0.0,
        "wrong_scope_result_count": sum(
            int(row.get("_wrong_scope_count") or 0) for row in rows
        ),
        "errors": sum(bool(row.get("error")) for row in rows),
        "p50_ms": percentile(timings, 0.5),
        "p95_ms": percentile(timings, 0.95),
        "p99_ms": percentile(timings, 0.99),
        "per_domain": domains,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=1000)
    args = parser.parse_args()
    if len(args.report) < 2:
        raise ValueError("at_least_two_segment_reports_required")
    payloads = [json.loads(path.resolve().read_text(encoding="utf-8")) for path in args.report]
    reference = payloads[0]
    all_rows: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}
    seen: set[str] = set()
    for payload in payloads:
        if payload.get("golden", {}).get("sha256") != reference.get("golden", {}).get("sha256"):
            raise ValueError("golden_sha256_mismatch")
        for run_name in all_rows:
            rows = list(payload.get("runs", {}).get(run_name, {}).get("cases") or [])
            ids = {str(row.get("case_id")) for row in rows}
            overlap = seen.intersection(ids)
            if overlap:
                raise ValueError(f"duplicate_case_ids:{sorted(overlap)[:5]}")
            all_rows[run_name].extend(rows)
        segment_ids = {str(row.get("case_id")) for row in payload.get("runs", {}).get("baseline", {}).get("cases") or []}
        if seen.intersection(segment_ids):
            raise ValueError("duplicate_segment_case_ids")
        seen.update(segment_ids)
    if len(seen) != args.expected_count:
        raise ValueError(f"case_union_count:{len(seen)} expected:{args.expected_count}")
    report = {
        "schema_version": "legal-corpus-golden-benchmark-v1",
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "golden": {**reference["golden"], "case_count": len(seen), "segmented": True, "segment_count": len(payloads)},
        "baseline_manifest": reference["baseline_manifest"],
        "candidate_manifest": reference["candidate_manifest"],
        "retrieval_contract": {
            **reference["retrieval_contract"],
            "segmented_execution": True,
            "segment_count": len(payloads),
            "case_union_complete": True,
            "case_union_count": len(seen),
        },
        "runs": {
            "baseline": aggregate_run(all_rows["baseline"], "baseline_7245_issue_split"),
            "candidate": aggregate_run(all_rows["candidate"], "candidate_3000_issue_split"),
        },
        "comparison": {},
        "activation_decision": {
            "decision": "PENDING_ANSWER_AND_ROLLBACK_GATES",
            "reason": "Segmented retrieval results are complete and require corrected source/gap metrics before gate evaluation.",
        },
    }
    baseline = report["runs"]["baseline"]
    candidate = report["runs"]["candidate"]
    report["comparison"] = {
        "recall_at_10_delta": round(candidate["recall_at_10"] - baseline["recall_at_10"], 5),
        "direct_source_top5_delta": round(candidate["direct_source_top5"] - baseline["direct_source_top5"], 5),
        "mrr_delta": round(candidate["mrr"] - baseline["mrr"], 5),
        "p95_ms_delta": round(candidate["p95_ms"] - baseline["p95_ms"], 3),
        "wrong_scope_delta": candidate["wrong_scope_result_count"] - baseline["wrong_scope_result_count"],
        "errors_delta": candidate["errors"] - baseline["errors"],
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "case_count": len(seen), "segments": len(payloads)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
