#!/usr/bin/env python3
"""Run fail-closed M5 candidate-stage diagnostics for Retrieval Release V2.

The diagnostic uses one fixed vector/lexical Top-K=50 control and records the
retrieval stages needed before M6: source availability, exact lookup, vector
and lexical Recall@20/50, fusion Recall@20/50, candidate Recall@50 and final
evidence Recall@10.  It evaluates only Golden regression and Hard-negative
development cases; the sealed holdout is never used here.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_v2_runtime import normalize_exact
from api.retrieval_release_v2_runtime import V2ServingRuntime
from scripts.run_retrieval_release_v2_benchmark import (
    DEVELOPMENT_SPLITS,
    _matches_any,
    _source_matches,
    _sha,
)
from scripts.validate_retrieval_eval_suite_v1 import validate_suite


DEFAULT_DIR = ROOT / "reports" / "retrieval-release-v2"
DEFAULT_MANIFEST = DEFAULT_DIR / "legal-retrieval-chunk-manifest-v2-approved-passage-v3.json"
DEFAULT_SERVING_MANIFEST = DEFAULT_DIR / "legal-serving-manifest-v3.json"
DEFAULT_INDEX = DEFAULT_DIR / "legal-retrieval-v2-exact-lexical.sqlite3"
DEFAULT_SUITE = DEFAULT_DIR / "retrieval-eval-suite-v1.json"
DEFAULT_SOURCE_AVAILABILITY = DEFAULT_DIR / "retrieval-eval-source-availability-v2.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_OUTPUT = DEFAULT_DIR / "m5-v2-stage-diagnostics.json"
DEFAULT_POINTER = "legal_chunks_vnlegal_lal_haiphong_unified_v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _flat_manifest_rows(manifest: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    by_law: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in manifest.get("chunks") or []:
        if not isinstance(row, Mapping) or row.get("eligible") is not True:
            continue
        metadata = dict(row.get("metadata") or {})
        flat = dict(row)
        flat.update({
            "law_number": metadata.get("law_number"),
            "article_number": metadata.get("article_number"),
            "document_serving_state": row.get("document_serving_state"),
        })
        by_law[normalize_exact(metadata.get("law_number"))].append(flat)
    return by_law


def _expected_sources(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        source
        for group in case.get("positive_source_groups") or []
        for source in group.get("sources") or []
        if isinstance(source, Mapping)
    ]


def _stage_hit(items: Sequence[Mapping[str, Any]], sources: Sequence[Mapping[str, Any]], limit: int) -> bool:
    return _matches_any(list(items)[: max(0, int(limit))], sources)


def _root_cause(row: Mapping[str, Any]) -> str | None:
    if not row.get("answer_required"):
        return None
    if row.get("error"):
        return "execution_error"
    if row.get("temporal_blocked"):
        return "temporal_false_block"
    if not row.get("source_available"):
        return "source_absent"
    if not row.get("article_chunk_available"):
        return "article_chunk_absent"
    if not row.get("exact_hit_at_50") and row.get("exact_expected"):
        return "exact_lookup_failure"
    if not row.get("vector_hit_at_50") and not row.get("lexical_hit_at_50"):
        return "candidate_miss"
    if not row.get("fusion_hit_at_50"):
        return "fusion_rank_loss"
    if not row.get("final_hit_at_10"):
        return "fusion_rank_loss"
    return None


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    answers = [row for row in rows if row.get("answer_required")]
    refusals = [row for row in rows if row.get("expected_refusal")]
    exact_answers = [row for row in answers if row.get("exact_expected")]
    def rate(key: str, values: Sequence[Mapping[str, Any]] = answers) -> float:
        return sum(bool(row.get(key)) for row in values) / len(values) if values else 0.0
    domains: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in answers:
        domains[str(row.get("domain") or "")].append(row)
    root_causes = Counter(
        cause for row in rows if (cause := _root_cause(row)) is not None
    )
    latencies = [float(row.get("latency_ms") or 0.0) for row in rows]
    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))] if ordered else 0.0
    return {
        "metric_contract_version": "legal-retrieval-metrics-v2",
        "case_count": len(rows),
        "answer_required_count": len(answers),
        "expected_refusal_count": len(refusals),
        "source_availability_rate": rate("source_available", answers),
        "all_source_documents_available_rate": rate("all_source_documents_available", answers),
        "article_chunk_availability_rate": rate("article_chunk_available", answers),
        "exact_law_article_case_count": len(exact_answers),
        "exact_lookup_recall_at_50": rate("exact_hit_at_50", exact_answers),
        "vector_recall_at_20": rate("vector_hit_at_20", answers),
        "vector_recall_at_50": rate("vector_hit_at_50", answers),
        "lexical_recall_at_20": rate("lexical_hit_at_20", answers),
        "lexical_recall_at_50": rate("lexical_hit_at_50", answers),
        "fusion_recall_at_20": rate("fusion_hit_at_20", answers),
        "fusion_recall_at_50": rate("fusion_hit_at_50", answers),
        "candidate_recall_at_50": rate("candidate_hit_at_50", answers),
        "final_recall_at_10": rate("final_hit_at_10", answers),
        "correct_refusal_rate": (
            sum(bool(row.get("correct_refusal")) for row in refusals) / len(refusals)
            if refusals else None
        ),
        "per_domain": {
            domain: {
                "answer_required_count": len(values),
                "candidate_recall_at_50": rate("candidate_hit_at_50", values),
                "final_recall_at_10": rate("final_hit_at_10", values),
            }
            for domain, values in sorted(domains.items())
        },
        "root_causes": dict(sorted(root_causes.items())),
        "errors": sum(bool(row.get("error")) for row in rows),
        "outside_manifest_count": sum(int(row.get("outside_manifest_count") or 0) for row in rows),
        "invalid_temporal_count": sum(int(row.get("invalid_temporal_count") or 0) for row in rows),
        "latency_ms": {"p50": statistics.median(latencies) if latencies else 0.0, "p95": p95},
    }


def _gate(summary: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "source_availability_100pct": float(summary.get("source_availability_rate") or 0.0) >= 1.0,
        "candidate_recall_at_50": float(summary.get("candidate_recall_at_50") or 0.0) >= 0.99,
        "per_domain_candidate_recall_at_50": bool(summary.get("per_domain")) and all(
            float(item.get("candidate_recall_at_50") or 0.0) >= 0.98
            for item in (summary.get("per_domain") or {}).values()
        ),
        "exact_lookup_cases_100pct": float(summary.get("exact_lookup_recall_at_50") or 0.0) >= 1.0,
        "retrieval_p95_le_3s": float((summary.get("latency_ms") or {}).get("p95") or 0.0) <= 3_000.0,
        "safety": int(summary.get("errors") or 0) == 0
        and int(summary.get("outside_manifest_count") or 0) == 0
        and int(summary.get("invalid_temporal_count") or 0) == 0,
    }


def run(
    *,
    manifest_path: Path,
    serving_manifest_path: Path,
    lexical_index_path: Path,
    suite_path: Path,
    source_availability_path: Path,
    chroma_path: Path,
    current_collection: str,
    temporal_collection: str,
    output: Path,
    limit: int = 0,
    expected_pointer: str = DEFAULT_POINTER,
) -> dict[str, Any]:
    manifest = _load(manifest_path)
    if manifest.get("approved") is not True or manifest.get("legal_review_attestation") is not True:
        raise RuntimeError("approved_v2_manifest_required")
    suite = _load(suite_path)
    validation = validate_suite(suite, require_complete=True)
    if not validation["valid"]:
        raise RuntimeError("retrieval_eval_suite_gate_failed")
    availability = _load(source_availability_path)
    if availability.get("status") != "PASS" or float(availability.get("availability_rate") or 0.0) < 1.0:
        raise RuntimeError("retrieval_eval_source_availability_gate_failed")
    if suite.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256"):
        raise RuntimeError("suite_source_snapshot_mismatch")
    if suite.get("manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("suite_manifest_mismatch")

    cases = [
        dict(case) for case in suite.get("cases") or []
        if isinstance(case, Mapping) and case.get("split") in DEVELOPMENT_SPLITS
    ]
    if limit:
        cases = cases[: max(1, int(limit))]
    if not cases:
        raise RuntimeError("development_cases_required")
    manifest_by_law = _flat_manifest_rows(manifest)
    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else None
    if pointer_before != expected_pointer:
        raise RuntimeError("active_pointer_not_baseline")

    import scripts.legal_search_server as legal_search_server

    encoder = legal_search_server.retriever
    encoder.prewarm()
    if str(getattr(encoder, "_embedding_device", "")) not in {"cuda", "cuda:0"}:
        raise RuntimeError("m5_diagnostics_requires_cuda_embedding_runtime")
    if str(getattr(encoder, "_model_fingerprint", "")) != str(manifest.get("model_artifact_fingerprint") or ""):
        raise RuntimeError("embedding_model_fingerprint_mismatch")
    runtime = V2ServingRuntime(
        manifest_path=manifest_path,
        serving_manifest_path=serving_manifest_path,
        lexical_index_path=lexical_index_path,
        chroma_path=chroma_path,
        current_collection=current_collection,
        temporal_collection=temporal_collection,
        query_encoder=encoder,
    )
    rows: list[dict[str, Any]] = []
    try:
        for case in cases:
            expected = _expected_sources(case)
            laws = {normalize_exact(source.get("law_number")) for source in expected}
            available_rows = [row for law in laws for row in manifest_by_law.get(law, [])]
            article_available = all(
                not source.get("article")
                or any(_source_matches(row, source) for row in available_rows)
                for source in expected
            )
            started = perf_counter()
            record: dict[str, Any] = {
                "case_id": case.get("case_id"),
                "split": case.get("split"),
                "domain": case.get("domain"),
                "answer_required": bool(case.get("answer_required")),
                "expected_refusal": bool(case.get("expected_refusal")),
                "source_available": bool(laws) and all(bool(manifest_by_law.get(law)) for law in laws),
                "all_source_documents_available": bool(laws) and all(bool(manifest_by_law.get(law)) for law in laws),
                "article_chunk_available": article_available,
                "exact_expected": "exact_law_article" in set(case.get("tags") or []),
                "outside_manifest_count": 0,
                "invalid_temporal_count": 0,
            }
            try:
                response = runtime.search(
                    str(case.get("question") or ""),
                    legal_as_of=str(case.get("legal_as_of") or ""),
                    temporal_scope=str(case.get("temporal_scope") or "unknown"),
                    vector_top_k=50,
                    lexical_top_k=50,
                    fusion_strategy="legacy_stack",
                    vector_weight=0.6,
                    lexical_weight=0.4,
                    final_evidence=10,
                    reranker=None,
                    rerank_top_n=50,
                    parent_expansion=False,
                    neighbor_expansion=False,
                    query_classification=case.get("query_classification") or {},
                )
                trace = dict(response.get("trace") or {})
                exact = list(trace.get("exact_candidates") or [])
                vector = list(trace.get("vector_candidates") or [])
                lexical = list(trace.get("lexical_candidates") or [])
                fusion = list(trace.get("fusion_candidates") or [])
                final = list(response.get("results") or [])
                record.update({
                    "response_status": response.get("status"),
                    "exact_hit_at_50": _stage_hit(exact, expected, 50),
                    "vector_hit_at_20": _stage_hit(vector, expected, 20),
                    "vector_hit_at_50": _stage_hit(vector, expected, 50),
                    "lexical_hit_at_20": _stage_hit(lexical, expected, 20),
                    "lexical_hit_at_50": _stage_hit(lexical, expected, 50),
                    "fusion_hit_at_20": _stage_hit(fusion, expected, 20),
                    "fusion_hit_at_50": _stage_hit(fusion, expected, 50),
                    "candidate_hit_at_50": _stage_hit([*exact, *vector[:50], *lexical[:50]], expected, 10**9),
                    "final_hit_at_10": _stage_hit(final, expected, 10),
                    "correct_refusal": (
                        (not case.get("answer_required"))
                        and response.get("status") in {"refusal", "clarification_required", "out_of_scope"}
                    ),
                    "temporal_blocked": response.get("status") == "clarification_required" and bool(case.get("answer_required")),
                    "trace_summary": {
                        "counts": {
                            "exact": len(exact),
                            "vector": len(vector),
                            "lexical": len(lexical),
                            "fusion": len(fusion),
                            "final": len(final),
                        },
                        "stage_latency_ms": trace.get("stage_latency_ms") or {},
                    },
                })
                as_of = str(case.get("legal_as_of") or "")[:10]
                record["outside_manifest_count"] = sum(
                    str(item.get("chunk_revision_id") or "") not in getattr(runtime, "_manifest_chunk_ids", frozenset())
                    for item in final
                )
                record["invalid_temporal_count"] = sum(
                    (str(item.get("effective_from") or "")[:10] and str(item.get("effective_from"))[:10] > as_of)
                    or (str(item.get("effective_to") or "")[:10] and str(item.get("effective_to"))[:10] <= as_of)
                    or (
                        str(case.get("temporal_scope") or "") == "current"
                        and str(item.get("document_serving_state") or "") != "current_retrievable"
                    )
                    for item in final
                )
            except Exception as exc:
                record.update({
                    "error": f"{type(exc).__name__}:{exc}",
                    **{key: False for key in (
                        "exact_hit_at_50", "vector_hit_at_20", "vector_hit_at_50",
                        "lexical_hit_at_20", "lexical_hit_at_50", "fusion_hit_at_20",
                        "fusion_hit_at_50", "candidate_hit_at_50", "final_hit_at_10",
                    )},
                    "correct_refusal": False,
                    "temporal_blocked": False,
                })
            record["latency_ms"] = round((perf_counter() - started) * 1000, 3)
            rows.append(record)
    finally:
        runtime.close()
    summary = _summarize(rows)
    gate = _gate(summary)
    pointer_after = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else None
    gate["active_pointer_unchanged"] = pointer_before == pointer_after == expected_pointer
    report = {
        "schema_version": "legal-retrieval-v2-stage-diagnostics-v1",
        "status": "PASS" if all(gate.values()) and not limit else "FAIL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_file_sha256": _sha(manifest_path),
        "manifest_sha256": manifest.get("manifest_sha256"),
        "suite_file_sha256": _sha(suite_path),
        "suite_sha256": suite.get("suite_sha256"),
        "evaluated_splits": list(DEVELOPMENT_SPLITS),
        "holdout_excluded": True,
        "limit": int(limit),
        "summary": summary,
        "gate": gate,
        "records": rows,
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_changed": pointer_before != pointer_after,
        "database_mutated": False,
        "vector_collections_mutated": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{_sha(output)}  {output.name}\n", encoding="ascii")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--serving-manifest", type=Path, default=DEFAULT_SERVING_MANIFEST)
    parser.add_argument("--lexical-index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--source-availability", type=Path, default=DEFAULT_SOURCE_AVAILABILITY)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--current-collection", required=True)
    parser.add_argument("--temporal-collection", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--expected-pointer", default=DEFAULT_POINTER)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    try:
        report = run(
            manifest_path=args.manifest.resolve(),
            serving_manifest_path=args.serving_manifest.resolve(),
            lexical_index_path=args.lexical_index.resolve(),
            suite_path=args.suite.resolve(),
            source_availability_path=args.source_availability.resolve(),
            chroma_path=args.chroma_path.resolve(),
            current_collection=args.current_collection,
            temporal_collection=args.temporal_collection,
            output=output,
            limit=args.limit,
            expected_pointer=args.expected_pointer,
        )
    except Exception as exc:
        report = {
            "schema_version": "legal-retrieval-v2-stage-diagnostics-v1",
            "status": "BLOCKED",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reason": f"{type(exc).__name__}:{exc}",
            "holdout_excluded": True,
            "active_pointer_changed": False,
            "database_mutated": False,
            "vector_collections_mutated": False,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output.with_suffix(output.suffix + ".sha256").write_text(f"{_sha(output)}  {output.name}\n", encoding="ascii")
        print(json.dumps({"status": "BLOCKED", "output": str(output), "reason": report["reason"]}, ensure_ascii=True))
        return 2
    print(json.dumps({"status": report["status"], "output": str(output), "gate": report["gate"]}, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
