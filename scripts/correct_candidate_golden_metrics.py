"""Add source-backed/gap-aware metrics to a completed candidate benchmark."""

from __future__ import annotations

from collections import defaultdict
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LABELS = [
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
    "an_sinh_y_te_giao_duc",
]
ALIASES = {
    "ho_tich_chung_thuc": {"ho_tich_chung_thuc", "tu_phap_ho_tich"},
    "dat_dai_xay_dung": {
        "dat_dai_xay_dung",
        "xay_dung",
        "moi_truong",
        "dat_dai_moi_truong",
        "xay_dung_do_thi",
    },
    "cu_tru_an_ninh": {"cu_tru_an_ninh", "cu_tru"},
    "khieu_nai_to_cao_xu_phat": {"khieu_nai_to_cao_xu_phat", "noi_vu_hanh_chinh", "trat_tu_do_thi"},
    "an_sinh_y_te_giao_duc": {
        "an_sinh_y_te_giao_duc",
        "an_sinh_y_te",
        "giao_duc_van_hoa",
    },
}


def label_for_case(case_id: str) -> str:
    number = int(str(case_id).rsplit("-", 1)[-1])
    return LABELS[min(len(LABELS) - 1, (number - 1) // 200)]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(float(ordered[index]), 3)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.resolve().read_text(encoding="utf-8"))
    evaluation: dict[str, dict] = {}
    for name, run in report["runs"].items():
        by_domain: dict[str, list[dict]] = defaultdict(list)
        wrong_scope = 0
        for row in run["cases"]:
            domain = label_for_case(str(row["case_id"]))
            by_domain[domain].append(row)
            allowed = ALIASES[domain]
            wrong_scope += sum(
                1
                for result in row.get("top_results") or []
                if result.get("domain_slug") and result.get("domain_slug") not in allowed
            )
        source_cases = [row for row in run["cases"] if row.get("expected_law_numbers")]
        source_hits = sum(bool(row.get("hit_at_10")) for row in source_cases)
        source_top5 = sum(bool(row.get("direct_source_top5")) for row in source_cases)
        domains: dict[str, dict] = {}
        for domain, rows in sorted(by_domain.items()):
            eligible = [row for row in rows if row.get("expected_law_numbers")]
            gaps = [row for row in rows if not row.get("expected_law_numbers")]
            latencies = [float(row.get("latency_ms") or 0.0) for row in rows]
            retrieval_latencies = [float(row.get("retrieval_latency_ms") or row.get("latency_ms") or 0.0) for row in rows]
            domains[domain] = {
                "case_count": len(rows),
                "source_case_count": len(eligible),
                "gap_case_count": len(gaps),
                "source_recall_at_10": round(sum(bool(row.get("hit_at_10")) for row in eligible) / len(eligible), 5) if eligible else None,
                "source_direct_source_top5": round(sum(bool(row.get("direct_source_top5")) for row in eligible) / len(eligible), 5) if eligible else None,
                "gap_result_rate": round(sum(bool(row.get("result_count")) for row in gaps) / len(gaps), 5) if gaps else None,
                "p50_ms": percentile(latencies, 0.50),
                "p95_ms": percentile(latencies, 0.95),
                "p50_retrieval_ms": percentile(retrieval_latencies, 0.50),
                "p95_retrieval_ms": percentile(retrieval_latencies, 0.95),
            }
        evaluation[name] = {
            "case_count": len(run["cases"]),
            "source_case_count": len(source_cases),
            "verified_gap_case_count": len(run["cases"]) - len(source_cases),
            "source_recall_at_10": round(source_hits / len(source_cases), 5) if source_cases else 0.0,
            "source_direct_source_top5": round(source_top5 / len(source_cases), 5) if source_cases else 0.0,
            "all_case_result_rate": round(sum(bool(row.get("result_count")) for row in run["cases"]) / len(run["cases"]), 5) if run["cases"] else 0.0,
            "verified_gap_result_rate": round(sum(bool(row.get("result_count")) for row in run["cases"] if not row.get("expected_law_numbers")) / (len(run["cases"]) - len(source_cases)), 5) if len(run["cases"]) > len(source_cases) else 0.0,
            "wrong_scope_result_count_corrected": wrong_scope,
            "p50_ms": run.get("p50_ms"),
            "p95_ms": run.get("p95_ms"),
            "p99_ms": run.get("p99_ms"),
            "p50_retrieval_ms": run.get("p50_retrieval_ms", run.get("p50_ms")),
            "p95_retrieval_ms": run.get("p95_retrieval_ms", run.get("p95_ms")),
            "p99_retrieval_ms": run.get("p99_retrieval_ms", run.get("p99_ms")),
            "per_domain": domains,
        }
    baseline = evaluation["baseline"]
    candidate = evaluation["candidate"]
    report["evaluation"] = evaluation
    report["comparison"] = {
        "source_recall_at_10_delta": round(candidate["source_recall_at_10"] - baseline["source_recall_at_10"], 5),
        "source_direct_source_top5_delta": round(candidate["source_direct_source_top5"] - baseline["source_direct_source_top5"], 5),
        "p95_ms_delta": round(candidate["p95_ms"] - baseline["p95_ms"], 3),
        "p95_improvement_ratio": round((baseline["p95_ms"] - candidate["p95_ms"]) / baseline["p95_ms"], 5) if baseline["p95_ms"] else 0.0,
        "retrieval_p95_ms_delta": round(candidate["p95_retrieval_ms"] - baseline["p95_retrieval_ms"], 3),
        "retrieval_p95_improvement_ratio": round((baseline["p95_retrieval_ms"] - candidate["p95_retrieval_ms"]) / baseline["p95_retrieval_ms"], 5) if baseline["p95_retrieval_ms"] else 0.0,
        "full_pipeline_p95_not_increased": candidate["p95_ms"] <= baseline["p95_ms"],
        "wrong_scope_delta_corrected": candidate["wrong_scope_result_count_corrected"] - baseline["wrong_scope_result_count_corrected"],
        "all_case_result_rate_delta": round(candidate["all_case_result_rate"] - baseline["all_case_result_rate"], 5),
    }
    report["activation_decision"] = {
        "decision": "NO_GO_PENDING_ANSWER_AND_ROLLBACK_GATES",
        "reason": "Candidate retrieval source recall/latency/coverage are measured and improved, but answer-level grounding and rollback rehearsal are not completed; active pointer remains unchanged.",
        "threshold_observations": {
            "overall_source_recall_drop_exceeds_1pct": candidate["source_recall_at_10"] < baseline["source_recall_at_10"] - 0.01,
            "all_domain_source_recall_drop_exceeds_2pct": any(
                candidate["per_domain"][domain]["source_recall_at_10"] < baseline["per_domain"][domain]["source_recall_at_10"] - 0.02
                for domain in candidate["per_domain"]
                if candidate["per_domain"].get(domain, {}).get("source_recall_at_10") is not None and baseline["per_domain"].get(domain, {}).get("source_recall_at_10") is not None
            ),
            "retrieval_p95_improvement_at_least_20pct": report["comparison"]["retrieval_p95_improvement_ratio"] >= 0.20,
            "full_pipeline_p95_not_increased": report["comparison"]["full_pipeline_p95_not_increased"],
            "active_pointer_changed": report["retrieval_contract"]["active_pointer_changed"],
        },
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "evaluation": report["evaluation"], "comparison": report["comparison"], "activation_decision": report["activation_decision"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
