"""Assemble read-only Golden 100 coverage, retrieval and review outputs."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--coverage", required=True)
    p.add_argument("--cache", required=True)
    p.add_argument("--benchmark", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--out-dir", required=True)
    return p.parse_args()


def articles(value: Any) -> set[str]:
    if value is None:
        return set()
    return {part.strip() for part in str(value).replace(";", ",").split(",") if part.strip()}


def source_match(candidate: dict[str, Any], source: dict[str, Any]) -> bool:
    if str(candidate.get("law_number") or "").strip().lower() != str(source.get("law_number") or "").strip().lower():
        return False
    expected = articles(source.get("article"))
    return not expected or str(candidate.get("article_number") or "").strip() in expected


def forbidden_match(candidate: dict[str, Any], source: dict[str, Any]) -> bool:
    return str(candidate.get("law_number") or "").strip().lower() == str(source.get("law_number") or "").strip().lower()


def rank_stats(candidates: list[dict[str, Any]], expected: list[dict[str, Any]], forbidden: list[dict[str, Any]]) -> dict[str, Any]:
    relevant = [index + 1 for index, item in enumerate(candidates) if any(source_match(item, source) for source in expected)]
    forbidden_ranks = [index + 1 for index, item in enumerate(candidates) if any(forbidden_match(item, source) for source in forbidden)]
    ineligible_ranks = [
        index + 1 for index, item in enumerate(candidates)
        if (item.get("validity_sync") or {}).get("current_answer_eligible") is False
        or str(item.get("document_status") or "active").lower() in {"expired", "repealed", "inactive"}
    ]
    first = relevant[0] if relevant else None
    return {
        "candidate_count": len(candidates),
        "relevant_ranks": relevant,
        "first_relevant_rank": first,
        "top1_hit": first == 1,
        "top5_hit": first is not None and first <= 5,
        "top10_hit": first is not None and first <= 10,
        "top30_hit": first is not None and first <= 30,
        "reciprocal_rank": round(1 / first, 6) if first else 0.0,
        "forbidden_top1": any(rank == 1 for rank in forbidden_ranks),
        "forbidden_top5": any(rank <= 5 for rank in forbidden_ranks),
        "ineligible_top10": sum(1 for rank in ineligible_ranks if rank <= 10),
        "forbidden_ranks": forbidden_ranks,
        "ineligible_ranks": ineligible_ranks,
    }


def main() -> int:
    a = args()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(Path(a.dataset).read_text(encoding="utf-8"))
    coverage = json.loads(Path(a.coverage).read_text(encoding="utf-8"))
    cache = json.loads(Path(a.cache).read_text(encoding="utf-8"))
    prior = json.loads(Path(a.benchmark).read_text(encoding="utf-8"))
    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    split_map = {item["case_id"]: item.get("evaluation_split") for item in manifest.get("case_metadata", [])}
    coverage_sources = coverage.get("sources", [])
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in coverage_sources:
        by_case.setdefault(row["case_id"], []).append(row)
    retrieval_cases: list[dict[str, Any]] = []
    for case in dataset["cases"]:
        case_id = case["case_id"]
        entry = (cache.get("cases") or {}).get(case_id, {})
        candidates = entry.get("candidates") or []
        expected = case.get("expected_sources") or []
        forbidden = case.get("forbidden_sources") or []
        stats = rank_stats(candidates, expected, forbidden)
        source_reasons = Counter(row.get("reason_code") for row in by_case.get(case_id, []))
        if source_reasons.get("document_missing"):
            case_reason = "document_missing"
        elif source_reasons.get("article_missing"):
            case_reason = "article_missing"
        elif source_reasons.get("embedding_missing"):
            case_reason = "embedding_missing"
        elif not stats["top10_hit"]:
            case_reason = "candidate_generated_but_ranked_low" if candidates else "exact_lookup_failed"
        else:
            case_reason = "ok"
        manifest_split = split_map.get(case_id)
        split = {"development": "dev", "validation": "validation", "held_out": "heldout"}.get(manifest_split)
        if split is None:
            number = int(case_id.split("-")[-1])
            split = "dev" if number <= 60 else "validation" if number <= 80 else "heldout"
        retrieval_cases.append({
            "case_id": case_id,
            "domain": case.get("domain"),
            "split": split,
            "question": (case.get("questions") or {}).get("citizen"),
            "expected_source_count": len(expected),
            "expected_law_numbers": sorted({str(item.get("law_number")) for item in expected}),
            "source_reason_counts": dict(source_reasons),
            "reason_code": case_reason,
            "retrieval_ms": entry.get("retrieval_ms"),
            **stats,
        })
    def avg(key: str, rows: list[dict[str, Any]]) -> float:
        return round(sum(float(row.get(key) or 0) for row in rows) / len(rows), 6) if rows else 0.0
    def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "case_count": len(rows),
            "top1_accuracy": round(sum(row["top1_hit"] for row in rows) / len(rows), 6) if rows else 0.0,
            "top5_accuracy": round(sum(row["top5_hit"] for row in rows) / len(rows), 6) if rows else 0.0,
            "top10_accuracy": round(sum(row["top10_hit"] for row in rows) / len(rows), 6) if rows else 0.0,
            "recall_at_30": round(sum(row["top30_hit"] for row in rows) / len(rows), 6) if rows else 0.0,
            "mrr": avg("reciprocal_rank", rows),
            "forbidden_top1_count": sum(row["forbidden_top1"] for row in rows),
            "forbidden_top5_count": sum(row["forbidden_top5"] for row in rows),
            "ineligible_top10_count": sum(row["ineligible_top10"] for row in rows),
            "p95_retrieval_ms": sorted(float(row["retrieval_ms"] or 0) for row in rows)[max(0, math.ceil(len(rows) * .95) - 1)] if rows else 0.0,
        }
    dev = [row for row in retrieval_cases if row["split"] == "dev"]
    validation = [row for row in retrieval_cases if row["split"] == "validation"]
    heldout = [row for row in retrieval_cases if row["split"] == "heldout"]
    retrieval = {
        "schema_version": "feature016-golden100-retrieval-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_source": "reports/feature016/phase-c-real/candidate-cache.json (read-only live retrieval cache)",
        "pipeline": prior.get("pipeline_baseline"),
        "active_collection": coverage.get("observed_active_collection"),
        "dataset_case_count": len(retrieval_cases),
        "candidate_limit_observed_max": max((row["candidate_count"] for row in retrieval_cases), default=0),
        "all": aggregate(retrieval_cases),
        "dev_60": aggregate(dev),
        "validation_20": aggregate(validation),
        "heldout_20": aggregate(heldout),
        "safety": {
            "forbidden_top1": sum(row["forbidden_top1"] for row in retrieval_cases),
            "forbidden_top5": sum(row["forbidden_top5"] for row in retrieval_cases),
            "ineligible_top10": sum(row["ineligible_top10"] for row in retrieval_cases),
            "reranker_activation": "disabled; BGE p95 25,384 ms exceeded 3,000 ms gate",
        },
        "cases": retrieval_cases,
    }
    answer = {
        "schema_version": "feature016-golden100-answer-evaluation-v1",
        "generated_at": retrieval["generated_at"],
        "status": "not_run",
        "reason_code": "no_generated_answer_artifact_in_scope",
        "explanation": "This run is a read-only source/database/retrieval audit. It does not invent or score final chatbot answers without a captured answer artifact and official evidence spans.",
        "required_next_inputs": ["captured final answer per Case ID", "official quote/page or character-span proof"],
        "cases": [{"case_id": case["case_id"], "status": "pending_answer_artifact"} for case in dataset["cases"]],
    }
    unresolved = [
        {
            "case_id": row["case_id"],
            "law_number": row["law_number"],
            "article": row["article"],
            "reason_code": row["reason_code"],
            "document_id": row.get("document_id"),
            "db_source_url": row.get("db_source_url"),
            "next_action": "official source review/import through existing approval workflow" if row["reason_code"] != "ok" else "none",
        }
        for row in coverage_sources if row.get("reason_code") != "ok"
    ]
    Path(out / "retrieval-benchmark.json").write_text(json.dumps(retrieval, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(out / "answer-evaluation.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(out / "unresolved-legal-review.json").write_text(json.dumps({"schema_version":"feature016-golden100-unresolved-v1","generated_at":retrieval["generated_at"],"rows":unresolved}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"all": retrieval["all"], "dev_60": retrieval["dev_60"], "validation_20": retrieval["validation_20"], "heldout_20": retrieval["heldout_20"], "unresolved_rows": len(unresolved)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
