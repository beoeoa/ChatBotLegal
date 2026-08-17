"""Assemble immutable candidate evidence into an activation gate summary."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--rollback", type=Path, required=True)
    parser.add_argument("--answer-audit", type=Path, required=True)
    parser.add_argument(
        "--answer-shadow",
        type=Path,
        help="Optional bounded baseline/candidate answer canary report; does not close the full gate.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = load(args.manifest.resolve())
    build = load(args.build_report.resolve())
    benchmark = load(args.benchmark.resolve())
    rollback = load(args.rollback.resolve())
    answer_audit = load(args.answer_audit.resolve())
    answer_shadow = load(args.answer_shadow.resolve()) if args.answer_shadow else None
    evaluation = benchmark.get("evaluation") or {}
    baseline_eval = evaluation.get("baseline") or {}
    candidate_eval = evaluation.get("candidate") or {}
    if not evaluation and benchmark.get("runs"):
        # The live benchmark runner emits ``runs`` (one row per case), while
        # older corrected reports used an ``evaluation`` projection. Normalize
        # both schemas here so the gate cannot silently report zero cases.
        def project(run: dict) -> dict:
            cases = list(run.get("cases") or [])
            source_cases = [row for row in cases if row.get("expected_law_numbers")]
            source_hits = sum(bool(row.get("hit_at_10")) for row in source_cases)
            per_domain: dict[str, dict] = {}
            for row in source_cases:
                domain = str(row.get("domain") or "unknown")
                bucket = per_domain.setdefault(domain, {"cases": 0, "hits": 0})
                bucket["cases"] += 1
                bucket["hits"] += int(bool(row.get("hit_at_10")))
            for bucket in per_domain.values():
                bucket["source_recall_at_10"] = (
                    bucket["hits"] / bucket["cases"] if bucket["cases"] else 0.0
                )
            return {
                "case_count": len(cases),
                "source_case_count": len(source_cases),
                "source_recall_at_10": source_hits / len(source_cases) if source_cases else 0.0,
                "p95_ms": float(run.get("p95_ms") or 0.0),
                "p95_retrieval_ms": float(run.get("p95_retrieval_ms") or run.get("p95_ms") or 0.0),
                "per_domain": per_domain,
            }
        evaluation = {"baseline": project(benchmark["runs"].get("baseline") or {}), "candidate": project(benchmark["runs"].get("candidate") or {})}
        baseline_eval = evaluation["baseline"]
        candidate_eval = evaluation["candidate"]
    recall_delta = float(candidate_eval.get("source_recall_at_10") or 0.0) - float(baseline_eval.get("source_recall_at_10") or 0.0)
    per_domain_drops_ok = all(
        float(candidate_eval.get("per_domain", {}).get(domain, {}).get("source_recall_at_10") or 0.0)
        >= float(baseline_eval.get("per_domain", {}).get(domain, {}).get("source_recall_at_10") or 0.0) - 0.02
        for domain in candidate_eval.get("per_domain", {})
    )
    p95_ms = float(candidate_eval.get("p95_ms") or 0.0)
    baseline_p95_ms = float(baseline_eval.get("p95_ms") or 0.0)
    retrieval_p95_ms = float(candidate_eval.get("p95_retrieval_ms") or p95_ms)
    baseline_retrieval_p95_ms = float(baseline_eval.get("p95_retrieval_ms") or baseline_p95_ms)
    comparison = benchmark.get("comparison", {})
    p95_improvement_ratio = float(comparison.get("retrieval_p95_improvement_ratio") or 0.0)
    if not p95_improvement_ratio and baseline_retrieval_p95_ms:
        p95_improvement_ratio = (baseline_retrieval_p95_ms - retrieval_p95_ms) / baseline_retrieval_p95_ms
    full_pipeline_p95_not_increased = comparison.get("full_pipeline_p95_not_increased")
    if full_pipeline_p95_not_increased is None:
        full_pipeline_p95_not_increased = p95_ms <= baseline_p95_ms
    retrieval_gates = {
        "source_recall_not_decreased_more_than_1pct": recall_delta >= -0.01,
        "per_domain_recall_not_decreased_more_than_2pct": per_domain_drops_ok,
        "absolute_p95_retrieval_under_3s": retrieval_p95_ms <= 3000.0,
        "p95_improved_at_least_20pct": p95_improvement_ratio >= 0.20,
        "full_pipeline_p95_not_increased": bool(full_pipeline_p95_not_increased),
        "wrong_scope_not_increased": int(comparison.get("wrong_scope_delta_corrected", comparison.get("wrong_scope_delta", 0)) or 0) <= 0,
    }
    answer_gate = summary_answer_gate = {
        "completed": int(answer_audit.get("answer_cases_executed") or 0) > 0,
        "passed": bool(answer_audit.get("gate", {}).get("passed")),
        "status": answer_audit.get("gate", {}).get("status"),
        "reason_code": answer_audit.get("gate", {}).get("reason_code"),
        "report": str(args.answer_audit.resolve()),
    }
    if answer_shadow:
        shadow_summary = answer_shadow.get("candidate", {}).get("summary") or {}
        baseline_shadow_summary = answer_shadow.get("baseline", {}).get("summary") or {}
        answer_gate["shadow_canary"] = {
            "completed": True,
            "case_count": int(answer_shadow.get("case_count") or 0),
            "balanced": bool(answer_shadow.get("balanced")),
            "report": str(args.answer_shadow.resolve()),
            "baseline_answer_rate": baseline_shadow_summary.get("answer_rate"),
            "candidate_answer_rate": shadow_summary.get("answer_rate"),
            "baseline_grounded_rate": baseline_shadow_summary.get("grounded_rate"),
            "candidate_grounded_rate": shadow_summary.get("grounded_rate"),
            "baseline_source_gap_rate": baseline_shadow_summary.get("source_gap_rate"),
            "candidate_source_gap_rate": shadow_summary.get("source_gap_rate"),
            "baseline_fallback_rate": baseline_shadow_summary.get("fallback_rate"),
            "candidate_fallback_rate": shadow_summary.get("fallback_rate"),
            "full_answer_gate_closed": False,
        }
    if answer_gate["reason_code"] == "NO_PROVIDER_AND_API_DEPENDENCY_UNAVAILABLE":
        answer_reason = "answer-level/provider gate is blocked by unavailable environment"
    elif not answer_gate["completed"]:
        answer_reason = "answer-level Golden has not been executed"
    elif not answer_gate["passed"]:
        answer_reason = "answer-level/provider gate failed"
    else:
        answer_reason = "answer-level/provider gate passed"
    summary = {
        "schema_version": "legal-candidate-3000-gate-summary-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest": {"path": str(args.manifest.resolve()), "file_sha256": sha256(args.manifest.resolve()), "manifest_sha256": manifest.get("manifest_sha256")},
        "prebuild": {"allowed": bool(manifest.get("prebuild_gate", {}).get("allowed")), "coverage_balanced": bool(manifest.get("prebuild_gate", {}).get("coverage_balanced")), "required_source_blockers": list(manifest.get("required_source_blockers") or [])},
        "collection": {"name": build.get("candidate_collection"), "expected_vectors": build.get("expected_vector_count"), "actual_vectors": build.get("actual_vector_count"), "exact_chunk_set_match": build.get("exact_chunk_set_match"), "missing_vector_ids": build.get("missing_vector_ids"), "orphan_vector_ids": build.get("orphan_vector_ids"), "active_pointer_changed": build.get("active_pointer_changed"), "baseline_collection_mutated": build.get("baseline_collection_mutated"), "benchmark_only": build.get("benchmark_only"), "embedding_fingerprint": build.get("embedding_fingerprint")},
        "retrieval_isolation": {"completed": bool(benchmark.get("retrieval_contract", {}).get("lexical_and_vector_same_manifest")), "active_pointer_changed": bool(benchmark.get("retrieval_contract", {}).get("active_pointer_changed")), "same_pipeline_contract": all(bool(benchmark.get("retrieval_contract", {}).get(key)) for key in ("same_search_request", "same_model", "same_chunking", "same_embedding", "same_reranker", "same_prompt"))},
        "golden_retrieval": {"completed": bool(evaluation), "case_count": int(candidate_eval.get("case_count") or 0), "source_case_count": int(candidate_eval.get("source_case_count") or 0), "baseline_source_recall_at_10": baseline_eval.get("source_recall_at_10"), "candidate_source_recall_at_10": candidate_eval.get("source_recall_at_10"), "baseline_p95_ms": baseline_eval.get("p95_ms"), "candidate_p95_ms": candidate_eval.get("p95_ms"), "baseline_p95_retrieval_ms": baseline_retrieval_p95_ms, "candidate_p95_retrieval_ms": retrieval_p95_ms, "retrieval_p95_improvement_ratio": p95_improvement_ratio, "gates": retrieval_gates},
        "rollback_rehearsal": {"completed": bool(rollback.get("passed")), "passed": bool(rollback.get("passed")), "report": str(args.rollback.resolve())},
        "answer_gate": answer_gate,
        "not_completed": ["answer_level_grounding_and_provider_metrics", "activation_approval"],
        "decision": "NO_GO_KEEP_BASELINE_ACTIVE",
        "reason": f"Candidate retrieval gates are evaluated above; {answer_reason}, so baseline remains active.",
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
