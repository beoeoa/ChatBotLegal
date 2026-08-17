#!/usr/bin/env python3
"""Run checksum-bound M5/M6 experiments on Retrieval Release V2.

The runner refuses draft manifests, incomplete evaluation suites, missing
current/temporal collections and fingerprint mismatches.  It never changes an
active pointer.  M5 and M6 are deliberately separate modes so an M6 run
cannot silently bypass the M5 candidate gate.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
import sys
from time import perf_counter
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_v2_runtime import V2ServingRuntime, normalize_exact
from api.legal_learned_reranker import OptionalCrossEncoderReranker
from api.retrieval_holdout_contracts import validate_public_holdout_envelope
from scripts.validate_retrieval_eval_suite_v1 import validate_development_suite


DEFAULT_RELEASE_DIR = ROOT / "reports" / "retrieval-release-v2"
DEFAULT_MANIFEST = DEFAULT_RELEASE_DIR / "legal-retrieval-chunk-manifest-v2-approved-passage-v3.json"
DEFAULT_SERVING_MANIFEST = DEFAULT_RELEASE_DIR / "legal-serving-manifest-v3.json"
DEFAULT_INDEX = DEFAULT_RELEASE_DIR / "legal-retrieval-v2-exact-lexical.sqlite3"
DEFAULT_SUITE = DEFAULT_RELEASE_DIR / "retrieval-eval-suite-v1.json"
DEFAULT_HOLDOUT_ENVELOPE = DEFAULT_RELEASE_DIR / "production-holdout-envelope-v1.json"
DEFAULT_SOURCE_AVAILABILITY = DEFAULT_RELEASE_DIR / "retrieval-eval-source-availability-v2.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_OUTPUT = DEFAULT_RELEASE_DIR / "benchmark-v2.json"
DEFAULT_POINTER = DEFAULT_CHROMA / "active_core_collection.txt"
DEFAULT_M5_REPORT = DEFAULT_RELEASE_DIR / "m5-v2-acceptance.json"
DOMAINS = (
    "Hộ tịch/chứng thực",
    "Đất đai/xây dựng/môi trường",
    "Cư trú/căn cước/an ninh",
    "Khiếu nại/tố cáo/tiếp công dân/xử phạt",
    "An sinh/y tế/giáo dục",
)
DEVELOPMENT_SPLITS = ("golden-regression", "hard-negative")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{path}")
    return value


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_matches(candidate: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    if normalize_exact(candidate.get("law_number")) != normalize_exact(source.get("law_number")):
        return False
    article = normalize_exact(source.get("article"))
    if not article:
        return True
    candidate_article = normalize_exact(candidate.get("article_number"))
    requested_articles = set(re.findall(r"[0-9]+[A-Z]?", article))
    candidate_articles = set(re.findall(r"[0-9]+[A-Z]?", candidate_article))
    if requested_articles & candidate_articles:
        return True
    structural_path = normalize_exact(candidate.get("structural_path"))
    return any(f"DIEU {number}" in structural_path for number in requested_articles)


def _case_groups(case: Mapping[str, Any]) -> list[list[Mapping[str, Any]]]:
    return [list(group.get("sources") or []) for group in case.get("positive_source_groups") or []]


def _matches_any(candidates: Sequence[Mapping[str, Any]], sources: Sequence[Mapping[str, Any]]) -> bool:
    return any(_source_matches(candidate, source) for candidate in candidates for source in sources)


def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    answer_records = [row for row in records if row["answer_required"]]
    refusal_records = [row for row in records if row["expected_refusal"]]
    hits = [row for row in answer_records if row["hit_at_10"]]
    reciprocal = [row["reciprocal_rank_at_10"] for row in answer_records]
    domains: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in answer_records:
        domains[str(row["domain"])].append(row)
    latencies = [float(row["latency_ms"]) for row in records]
    rerank_latencies = [float(row.get("reranker_latency_ms") or 0.0) for row in records]
    reranker_rows = [row for row in records if row.get("reranker_mode")]
    coverage = [float(row["all_required_sources_coverage"]) for row in answer_records]
    exact_records = [
        row for row in answer_records if bool(row.get("exact_law_article_case"))
    ]
    multi_issue_records = [
        row for row in answer_records if bool(row.get("multi_issue_case"))
    ]

    def _segment(values: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "case_count": len(values),
            "answer_required_count": sum(bool(row.get("answer_required")) for row in values),
            "recall_at_10": sum(bool(row.get("hit_at_10")) for row in values if row.get("answer_required")) / sum(bool(row.get("answer_required")) for row in values) if any(row.get("answer_required") for row in values) else 0.0,
            "mrr_at_10": sum(float(row.get("reciprocal_rank_at_10") or 0.0) for row in values if row.get("answer_required")) / sum(bool(row.get("answer_required")) for row in values) if any(row.get("answer_required") for row in values) else 0.0,
            "top5_rate": sum(bool(row.get("top5_hit")) for row in values if row.get("answer_required")) / sum(bool(row.get("answer_required")) for row in values) if any(row.get("answer_required") for row in values) else 0.0,
        }
    intents: dict[str, list[dict[str, Any]]] = defaultdict(list)
    temporal_scopes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        intents[str(row.get("intent") or "UNKNOWN")].append(row)
        temporal_scopes[str(row.get("temporal_scope") or "unknown")].append(row)
    misses = [
        {
            "case_id": row.get("case_id"),
            "domain": row.get("domain"),
            "reason": (
                "execution_error"
                if row.get("error")
                else "candidate_miss"
                if not row.get("stage_hits", {}).get("fusion")
                else "reranker_rank_loss"
                if not row.get("stage_hits", {}).get("reranker")
                else "expansion_loss"
            ),
            "error_code": row.get("error"),
        }
        for row in answer_records
        if not row.get("hit_at_10")
    ]
    return {
        "metric_contract_version": "legal-retrieval-metrics-v2",
        "dataset_case_count": len(records),
        "valid_case_count": len(records) - sum(bool(row.get("error")) for row in records),
        "dataset_error_count": sum(bool(row.get("error")) for row in records),
        "dataset_errors": [
            {"case_id": row.get("case_id"), "code": row.get("error")}
            for row in records
            if row.get("error")
        ],
        "case_count": len(records),
        "answer_required_count": len(answer_records),
        "expected_refusal_count": len(refusal_records),
        "recall_at_10": len(hits) / len(answer_records) if answer_records else 0.0,
        "candidate_recall_at_50": sum(row["candidate_hit_at_50"] for row in answer_records) / len(answer_records) if answer_records else 0.0,
        "candidate_union_recall_at_50": sum(bool(row.get("candidate_union_hit_at_50")) for row in answer_records) / len(answer_records) if answer_records else 0.0,
        "mrr_at_10": sum(reciprocal) / len(reciprocal) if reciprocal else 0.0,
        "top5_rate": sum(bool(row.get("top5_hit")) for row in answer_records) / len(answer_records) if answer_records else 0.0,
        "correct_refusal_rate": sum(row["correct_refusal"] for row in refusal_records) / len(refusal_records) if refusal_records else None,
        "false_blocked_answer_count": sum(bool(row.get("answer_required")) and bool(row.get("error")) for row in records),
        "issue_count": sum(int(row.get("issue_count") or 0) for row in records),
        "issue_recall_at_10": sum(row["issue_recall_at_10"] for row in answer_records) / len(answer_records) if answer_records else 0.0,
        "all_required_sources_coverage": sum(coverage) / len(coverage) if coverage else 0.0,
        "exact_law_article_case_count": len(exact_records),
        "exact_law_article_lookup_recall_at_50": (
            sum(bool(row.get("stage_hits", {}).get("exact")) for row in exact_records)
            / len(exact_records)
            if exact_records
            else 0.0
        ),
        "exact_law_article_final_recall_at_10": (
            sum(bool(row.get("hit_at_10")) for row in exact_records)
            / len(exact_records)
            if exact_records
            else 0.0
        ),
        "multi_issue_case_count": len(multi_issue_records),
        "multi_issue_issue_recall_at_10": (
            sum(float(row.get("issue_recall_at_10") or 0.0) for row in multi_issue_records)
            / len(multi_issue_records)
            if multi_issue_records
            else 0.0
        ),
        "multi_issue_all_required_sources_coverage": (
            sum(float(row.get("all_required_sources_coverage") or 0.0) for row in multi_issue_records)
            / len(multi_issue_records)
            if multi_issue_records
            else 0.0
        ),
        "per_domain": {
            domain: {
                "answer_required_count": len(values),
                "recall_at_10": sum(row["hit_at_10"] for row in values) / len(values) if values else 0.0,
                "mrr_at_10": sum(row["reciprocal_rank_at_10"] for row in values) / len(values) if values else 0.0,
            }
            for domain, values in sorted(domains.items())
        },
        "per_intent": {key: _segment(values) for key, values in sorted(intents.items())},
        "per_temporal_scope": {key: _segment(values) for key, values in sorted(temporal_scopes.items())},
        "latency_ms": {
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
        },
        "reranker_latency_ms": {
            "p50": statistics.median(rerank_latencies) if rerank_latencies else 0.0,
            "p95": _percentile(rerank_latencies, 0.95),
        },
        "reranker_runtime": {
            "scored_count": sum(int(row.get("reranker_scored_count") or 0) for row in reranker_rows),
            "degraded_count": sum(bool(row.get("reranker_degraded")) for row in reranker_rows),
            "non_cuda_count": sum(
                str(row.get("reranker_device") or "") not in {"cuda", "cuda:0", "injected"}
                for row in reranker_rows
            ),
            "modes": sorted({str(row.get("reranker_mode")) for row in reranker_rows}),
        },
        "errors": sum(bool(row.get("error")) for row in records),
        "outside_manifest_count": sum(int(row.get("outside_manifest_count") or 0) for row in records),
        "invalid_temporal_count": sum(int(row.get("invalid_temporal_count") or 0) for row in records),
        "miss_count": len(misses),
        "misses": misses,
        "per_domain_candidate_recall_at_50": {
            domain: sum(row["candidate_hit_at_50"] for row in values) / len(values) if values else 0.0
            for domain, values in sorted(domains.items())
        },
        "stage_recall_at_10": {
            stage: sum(bool(row.get("stage_hits", {}).get(stage)) for row in answer_records) / len(answer_records)
            if answer_records else 0.0
            for stage in ("exact", "vector", "lexical", "candidate_union", "fusion", "reranker", "final")
        },
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * quantile)))
    return ordered[index]


def _evaluate(runtime: V2ServingRuntime, cases: Sequence[Mapping[str, Any]], config: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        sources_groups = _case_groups(case)
        expected_refusal = bool(case.get("expected_refusal"))
        scope = str(case.get("temporal_scope") or "current")
        started = perf_counter()
        try:
            response = runtime.search(
                str(case.get("question") or ""),
                legal_as_of=str(case.get("legal_as_of")),
                temporal_scope=scope,
                vector_top_k=int(config["vector_top_k"]),
                lexical_top_k=int(config["lexical_top_k"]),
                fusion_strategy=str(config["fusion_strategy"]),
                vector_weight=float(config.get("vector_weight", 0.6)),
                lexical_weight=float(config.get("lexical_weight", 0.4)),
                final_evidence=10,
                reranker=config.get("reranker"),
                rerank_top_n=int(config.get("rerank_top_n", 20)),
                parent_expansion=bool(config.get("parent_expansion")),
                neighbor_expansion=bool(config.get("neighbor_expansion")),
                exact_lookup_enabled=bool(config.get("exact_lookup_enabled", True)),
                issue_split_enabled=bool(config.get("issue_split_enabled", True)),
                query_classification=case.get("query_classification") or {},
            )
            final = list(response.get("results") or [])
            trace = dict(response.get("trace") or {})
            top10 = final[:10]
            candidate_union_top50 = list(trace.get("candidate_union") or [])[:50]
            candidate_top50 = list(trace.get("fusion_candidates") or [])[:50]
            expected_sources = [source for group in sources_groups for source in group]
            candidate_hit = _matches_any(candidate_top50, expected_sources)
            reranker_top10 = list((trace.get("reranker_candidates") or {}).get("output") or [])[:10]
            reranker_status = dict((trace.get("reranker_candidates") or {}).get("status") or {})
            stage_hits = {
                "exact": _matches_any(list(trace.get("exact_candidates") or []), expected_sources),
                "vector": _matches_any(list(trace.get("vector_candidates") or []), expected_sources),
                "lexical": _matches_any(list(trace.get("lexical_candidates") or []), expected_sources),
                "candidate_union": _matches_any(candidate_union_top50, expected_sources),
                "fusion": candidate_hit,
                "reranker": _matches_any(reranker_top10, expected_sources),
                "final": _matches_any(top10, expected_sources),
            }
            ranks = [index + 1 for index, item in enumerate(top10) if _matches_any([item], [source for group in sources_groups for source in group])]
            hit = bool(ranks)
            reciprocal = 1.0 / ranks[0] if ranks else 0.0
            top5_hit = bool(ranks and ranks[0] <= 5)
            group_hits = [
                _matches_any(top10, group)
                for group in sources_groups
            ]
            issue_groups = list(case.get("issue_groups") or [])
            required_group_ids = {str(value) for issue in issue_groups for value in issue.get("required_source_group_ids") or []}
            all_group_ids = [str(group.get("group_id")) for group in case.get("positive_source_groups") or []]
            group_hit_map = {
                str(group.get("group_id")): _matches_any(top10, group.get("sources") or [])
                for group in case.get("positive_source_groups") or []
            }
            relevant_hits = [group_hit_map.get(group_id, False) for group_id in required_group_ids] or group_hits
            refusal_status = str(response.get("status") or "")
            expected_reason = str(
                case.get("expected_refusal_reason")
                or case.get("refusal_reason")
                or ""
            ).casefold()
            if expected_reason in {"temporal_unknown", "temporal_conflict", "unknown", "conflict", "insufficient_facts", "missing_facts"}:
                correct_refusal = refusal_status == "clarification_required"
            elif expected_reason in {"out_of_scope", "outside_scope", "jurisdiction", "role_visibility"}:
                correct_refusal = refusal_status in {"refusal", "out_of_scope"}
            else:
                correct_refusal = refusal_status in {"clarification_required", "refusal", "out_of_scope"}
            correct_refusal = correct_refusal if expected_refusal else False
            as_of = str(case.get("legal_as_of") or "")[:10]
            invalid_temporal = sum(
                1
                for item in final
                if (
                    (str(item.get("effective_from") or "")[:10] and str(item.get("effective_from"))[:10] > as_of)
                    or (str(item.get("effective_to") or "")[:10] and str(item.get("effective_to"))[:10] <= as_of)
                    or (
                        scope == "current"
                        and str(item.get("document_serving_state") or "") != "current_retrievable"
                    )
                    or (
                        scope == "historical"
                        and str(item.get("document_serving_state") or "")
                        not in {"current_retrievable", "historical_only"}
                    )
                )
            )
            allowed_ids = getattr(runtime, "_manifest_chunk_ids", frozenset())
            outside_manifest_count = sum(
                1
                for item in final
                if str(item.get("chunk_revision_id") or "") not in allowed_ids
            )
            rows.append({
                "case_id": case.get("case_id"),
                "split": case.get("split"),
                "domain": case.get("domain"),
                "tags": list(case.get("tags") or []),
                "intent": (case.get("query_classification") or {}).get("intent") or "UNKNOWN",
                "temporal_scope": scope,
                "issue_count": len(issue_groups),
                "exact_law_article_case": "exact_law_article" in set(case.get("tags") or []),
                "multi_issue_case": "multi_issue" in set(case.get("tags") or []) or len(issue_groups) > 1,
                "answer_required": bool(case.get("answer_required")),
                "expected_refusal": expected_refusal,
                "hit_at_10": hit,
                "top5_hit": top5_hit,
                "candidate_union_hit_at_50": bool(stage_hits["candidate_union"]),
                "candidate_hit_at_50": candidate_hit,
                "stage_hits": stage_hits,
                "reciprocal_rank_at_10": reciprocal,
                "issue_recall_at_10": sum(relevant_hits) / len(relevant_hits) if relevant_hits else 0.0,
                "all_required_sources_coverage": sum(group_hit_map.get(group_id, False) for group_id in required_group_ids or set(all_group_ids)) / len(required_group_ids or set(all_group_ids)) if (required_group_ids or all_group_ids) else 1.0,
                "correct_refusal": correct_refusal,
                "latency_ms": (perf_counter() - started) * 1000,
                "reranker_latency_ms": float((trace.get("stage_latency_ms") or {}).get("reranking") or 0.0),
                "reranker_mode": reranker_status.get("mode"),
                "reranker_degraded": bool(reranker_status.get("degraded")),
                "reranker_device": reranker_status.get("device"),
                "reranker_scored_count": int(reranker_status.get("scored_count") or 0),
                "outside_manifest_count": outside_manifest_count,
                "invalid_temporal_count": invalid_temporal,
                "trace": trace,
            })
        except Exception as exc:
            rows.append({
                "case_id": case.get("case_id"),
                "split": case.get("split"),
                "domain": case.get("domain"),
                "tags": list(case.get("tags") or []),
                "intent": (case.get("query_classification") or {}).get("intent") or "UNKNOWN",
                "temporal_scope": scope,
                "issue_count": len(case.get("issue_groups") or []),
                "exact_law_article_case": "exact_law_article" in set(case.get("tags") or []),
                "multi_issue_case": "multi_issue" in set(case.get("tags") or []) or len(case.get("issue_groups") or []) > 1,
                "answer_required": bool(case.get("answer_required")),
                "expected_refusal": expected_refusal,
                "hit_at_10": False,
                "top5_hit": False,
                "candidate_hit_at_50": False,
                "candidate_union_hit_at_50": False,
                "stage_hits": {stage: False for stage in ("exact", "vector", "lexical", "candidate_union", "fusion", "reranker", "final")},
                "reciprocal_rank_at_10": 0.0,
                "issue_recall_at_10": 0.0,
                "all_required_sources_coverage": 0.0,
                "correct_refusal": False,
                "latency_ms": (perf_counter() - started) * 1000,
                "reranker_latency_ms": 0.0,
                "reranker_mode": None,
                "reranker_degraded": False,
                "reranker_device": None,
                "reranker_scored_count": 0,
                "outside_manifest_count": 0,
                "invalid_temporal_count": 0,
                "error": f"{type(exc).__name__}:{exc}",
            })
    summary = _metrics(rows)
    summary["records"] = rows
    return summary


def _m5_specs() -> list[dict[str, Any]]:
    specs = [{"id": "m5-control", "changed": "none", "vector_top_k": 20, "lexical_top_k": 20, "fusion_strategy": "legacy_stack", "vector_weight": 0.6, "lexical_weight": 0.4, "exact_lookup_enabled": True, "issue_split_enabled": False}]
    specs.append({"id": "m5-exact-lookup-disabled", "changed": "exact_lookup_enabled", "vector_top_k": 20, "lexical_top_k": 20, "fusion_strategy": "legacy_stack", "vector_weight": 0.6, "lexical_weight": 0.4, "exact_lookup_enabled": False, "issue_split_enabled": False})
    specs.extend({"id": f"m5-vector-k{k}", "changed": "vector_top_k", "vector_top_k": k, "lexical_top_k": 20, "fusion_strategy": "legacy_stack", "vector_weight": 0.6, "lexical_weight": 0.4, "exact_lookup_enabled": True, "issue_split_enabled": False} for k in (10, 20, 30, 50))
    specs.extend({"id": f"m5-lexical-k{k}", "changed": "lexical_top_k", "vector_top_k": 20, "lexical_top_k": k, "fusion_strategy": "legacy_stack", "vector_weight": 0.6, "lexical_weight": 0.4, "exact_lookup_enabled": True, "issue_split_enabled": False} for k in (10, 20, 30, 50))
    specs.extend([
        {"id": "m5-fusion-rrf", "changed": "fusion_strategy", "vector_top_k": 20, "lexical_top_k": 20, "fusion_strategy": "rrf", "vector_weight": 0.6, "lexical_weight": 0.4, "exact_lookup_enabled": True, "issue_split_enabled": False},
        {"id": "m5-fusion-weighted-070-030", "changed": "fusion_weights", "vector_top_k": 20, "lexical_top_k": 20, "fusion_strategy": "weighted", "vector_weight": 0.7, "lexical_weight": 0.3, "exact_lookup_enabled": True, "issue_split_enabled": False},
        {"id": "m5-fusion-weighted-060-040", "changed": "fusion_strategy", "vector_top_k": 20, "lexical_top_k": 20, "fusion_strategy": "weighted", "vector_weight": 0.6, "lexical_weight": 0.4, "exact_lookup_enabled": True, "issue_split_enabled": False},
        {"id": "m5-fusion-weighted-050-050", "changed": "fusion_weights", "vector_top_k": 20, "lexical_top_k": 20, "fusion_strategy": "weighted", "vector_weight": 0.5, "lexical_weight": 0.5, "exact_lookup_enabled": True, "issue_split_enabled": False},
    ])
    specs.append({"id": "m5-issue-splitting", "changed": "issue_split_enabled", "vector_top_k": 20, "lexical_top_k": 20, "fusion_strategy": "legacy_stack", "vector_weight": 0.6, "lexical_weight": 0.4, "exact_lookup_enabled": True, "issue_split_enabled": True})
    return specs


def _m6_parent_config(m5_report: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only retrieval knobs from the frozen M5 winner."""

    selected_id = str(m5_report.get("selected_experiment") or "")
    selected = next(
        (
            item for item in m5_report.get("experiments") or []
            if str(item.get("experiment_id") or "") == selected_id
        ),
        None,
    )
    if not isinstance(selected, Mapping):
        raise RuntimeError("m6_m5_selected_experiment_missing")
    config = dict(selected.get("config") or {})
    return {
        "vector_top_k": int(config.get("vector_top_k") or 20),
        "lexical_top_k": int(config.get("lexical_top_k") or 20),
        "fusion_strategy": str(config.get("fusion_strategy") or "legacy_stack"),
        "vector_weight": float(config.get("vector_weight") or 0.6),
        "lexical_weight": float(config.get("lexical_weight") or 0.4),
        "exact_lookup_enabled": bool(config.get("exact_lookup_enabled", True)),
        "issue_split_enabled": bool(config.get("issue_split_enabled", True)),
    }


def _expected_reranker_digest(value: Any) -> tuple[str, int | None]:
    """Normalize the two pinned-manifest file formats used by local models."""

    if isinstance(value, Mapping):
        size = value.get("size_bytes")
        return str(value.get("sha256") or ""), int(size) if size is not None else None
    return str(value or ""), None


def _verify_reranker_files(
    root: Path, files: Mapping[str, Any], *, prefix: str
) -> dict[str, Any]:
    if not root.is_dir():
        raise RuntimeError(f"reranker_{prefix}_path_missing:{root}")
    verified: dict[str, Any] = {}
    if not files:
        raise RuntimeError(f"reranker_{prefix}_files_required")
    for relative, expected_value in files.items():
        expected, expected_size = _expected_reranker_digest(expected_value)
        path = root / str(relative)
        if not path.is_file():
            raise RuntimeError(f"reranker_{prefix}_file_missing:{relative}")
        actual_size = path.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            raise RuntimeError(f"reranker_{prefix}_size_mismatch:{relative}")
        actual = _sha(path)
        if not expected or actual.casefold() != expected.casefold():
            raise RuntimeError(f"reranker_{prefix}_checksum_mismatch:{relative}")
        verified[str(relative)] = {"size_bytes": actual_size, "sha256": actual}
    return verified


def _verify_reranker_manifest(
    model_path: Path,
    manifest_path: Path,
    *,
    custom_code_path: Path | None = None,
) -> dict[str, Any]:
    """Verify a local-only reranker and any pinned custom implementation.

    GTE's ``auto_map`` points at Alibaba's separate ``new-impl`` repository.
    The V2 runner must therefore verify that repository independently and pass
    its local path to the adapter; otherwise a GTE run could silently fall
    back to a different implementation or attempt a network download.
    """

    manifest = _load(manifest_path)
    if str(manifest.get("model_id") or "").strip() == "":
        raise RuntimeError("reranker_model_id_required")
    if str(manifest.get("revision") or "").strip() == "":
        raise RuntimeError("reranker_revision_required")
    if manifest.get("runtime_download_allowed") is not False:
        raise RuntimeError("reranker_runtime_download_must_be_disabled")
    model_files = _verify_reranker_files(
        model_path, dict(manifest.get("files") or {}), prefix="model"
    )
    custom_code = dict(manifest.get("custom_code") or {})
    verified_custom_code: dict[str, Any] | None = None
    resolved_custom_code: Path | None = None
    if custom_code:
        if str(custom_code.get("revision") or "").strip() == "":
            raise RuntimeError("reranker_custom_code_revision_required")
        declared_path = str(custom_code.get("local_path") or "").strip()
        if custom_code_path is not None:
            resolved_custom_code = custom_code_path.resolve()
        elif declared_path:
            resolved_custom_code = Path(declared_path).resolve()
        if resolved_custom_code is None:
            raise RuntimeError("reranker_custom_code_path_required")
        custom_files = _verify_reranker_files(
            resolved_custom_code,
            dict(custom_code.get("files") or {}),
            prefix="custom_code",
        )
        required = {"configuration.py", "modeling.py"}
        if not required.issubset(custom_files):
            raise RuntimeError("reranker_custom_code_loader_files_missing")
        verified_custom_code = {
            "repository": custom_code.get("repository"),
            "revision": custom_code.get("revision"),
            "manifest_local_path": declared_path or None,
            "local_path": str(resolved_custom_code),
            "files": custom_files,
        }
    elif custom_code_path is not None:
        raise RuntimeError("reranker_custom_code_not_declared")
    return {
        "model_id": manifest.get("model_id"),
        "revision": manifest.get("revision"),
        "manifest_sha256": _sha(manifest_path),
        "model_path": str(model_path.resolve()),
        "files": model_files,
        "custom_code_path": str(resolved_custom_code) if resolved_custom_code else None,
        "custom_code": verified_custom_code,
    }


def _base_config(spec: Mapping[str, Any]) -> dict[str, Any]:
    return {**dict(spec), "parent_expansion": False, "neighbor_expansion": False, "reranker": None, "rerank_top_n": 20}


def _experiment_quality(
    experiment: Mapping[str, Any], *, mode: str = "m5"
) -> tuple[float, ...]:
    """Return the deterministic winner order required by the release plan.

    Safety is deliberately the first key.  A high-quality-looking run with
    invalid temporal evidence, an out-of-manifest result or a degraded
    learned reranker must therefore lose to a safe run (and still fails the
    final gate if no safe run exists).  M5 keeps the candidate Recall@50 gate
    ahead of answer ranking; M6 ranks Recall@10, MRR and multi-issue coverage
    after safety.
    """

    summaries = list((experiment.get("splits") or {}).values())
    answer_count = sum(int(item.get("answer_required_count") or 0) for item in summaries)

    def weighted(key: str) -> float:
        return (
            sum(
                float(item.get(key) or 0.0)
                * int(item.get("answer_required_count") or 0)
                for item in summaries
            )
            / answer_count
            if answer_count
            else 0.0
        )

    safety = int(
        all(
            int(item.get("errors") or 0) == 0
            and int(item.get("outside_manifest_count") or 0) == 0
            and int(item.get("invalid_temporal_count") or 0) == 0
            for item in summaries
        )
    )
    if mode == "m6":
        runtime_safe = int(
            all(
                (item.get("reranker_runtime") or {}).get("modes") == ["learned"]
                and int((item.get("reranker_runtime") or {}).get("degraded_count") or 0) == 0
                and int((item.get("reranker_runtime") or {}).get("non_cuda_count") or 0) == 0
                for item in summaries
            )
        )
        p95 = max(
            float((item.get("latency_ms") or {}).get("p95") or 0.0)
            for item in summaries
        ) if summaries else float("inf")
        return (
            float(safety and runtime_safe),
            weighted("recall_at_10"),
            weighted("mrr_at_10"),
            weighted("all_required_sources_coverage"),
            weighted("top5_rate"),
            -p95,
        )

    p95 = max(
        float((item.get("latency_ms") or {}).get("p95") or 0.0)
        for item in summaries
    ) if summaries else float("inf")
    return (
        float(safety),
        weighted("candidate_recall_at_50"),
        weighted("recall_at_10"),
        weighted("mrr_at_10"),
        weighted("all_required_sources_coverage"),
        weighted("top5_rate"),
        -p95,
    )


def _experiment_metric(experiment: Mapping[str, Any], key: str) -> float:
    summaries = list((experiment.get("splits") or {}).values())
    denominator = sum(int(item.get("answer_required_count") or 0) for item in summaries)
    if not denominator:
        return 0.0
    return sum(
        float(item.get(key) or 0.0) * int(item.get("answer_required_count") or 0)
        for item in summaries
    ) / denominator


def _compact_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Keep acceptance evidence small while retaining M3 trace evidence."""

    compact = {key: value for key, value in summary.items() if key != "records"}
    records = list(summary.get("records") or [])
    compact["misses"] = [
        {
            "case_id": row.get("case_id"),
            "answer_required": row.get("answer_required"),
            "error": row.get("error"),
            "hit_at_10": row.get("hit_at_10"),
            "top5_hit": row.get("top5_hit"),
            "candidate_hit_at_50": row.get("candidate_hit_at_50"),
            "reciprocal_rank_at_10": row.get("reciprocal_rank_at_10"),
        }
        for row in records
        if row.get("answer_required") and not row.get("hit_at_10")
    ][:200]
    for row in records:
        if row.get("trace"):
            compact["trace_sample"] = row["trace"]
            break
    return compact


def _median(values: Sequence[float]) -> float:
    return statistics.median([float(value) for value in values]) if values else 0.0


def _aggregate_summaries(summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate repeated runs without averaging away a safety failure."""

    if not summaries:
        return {}
    first = summaries[0]
    numeric = (
        "recall_at_10",
        "candidate_recall_at_50",
        "candidate_union_recall_at_50",
        "mrr_at_10",
        "top5_rate",
        "issue_recall_at_10",
        "all_required_sources_coverage",
        "exact_law_article_lookup_recall_at_50",
        "exact_law_article_final_recall_at_10",
        "multi_issue_issue_recall_at_10",
        "multi_issue_all_required_sources_coverage",
    )
    result = {
        key: value
        for key, value in first.items()
        if key not in {"records", "per_domain", "latency_ms", "reranker_latency_ms", "misses", "trace_sample", "stage_recall_at_10"}
    }
    for key in numeric:
        result[key] = _median([float(item.get(key) or 0.0) for item in summaries])
    refusal_values = [item.get("correct_refusal_rate") for item in summaries if item.get("correct_refusal_rate") is not None]
    result["correct_refusal_rate"] = _median([float(value) for value in refusal_values]) if refusal_values else None
    result["per_domain"] = {}
    domains = set().union(*(set((item.get("per_domain") or {}).keys()) for item in summaries))
    for domain in sorted(domains):
        rows = [(item.get("per_domain") or {}).get(domain) or {} for item in summaries]
        result["per_domain"][domain] = {
            "answer_required_count": int(rows[0].get("answer_required_count") or 0),
            "recall_at_10": _median([float(row.get("recall_at_10") or 0.0) for row in rows]),
            "mrr_at_10": _median([float(row.get("mrr_at_10") or 0.0) for row in rows]),
        }
    candidate_domains = set().union(*(set((item.get("per_domain_candidate_recall_at_50") or {}).keys()) for item in summaries))
    result["per_domain_candidate_recall_at_50"] = {
        domain: _median([
            float((item.get("per_domain_candidate_recall_at_50") or {}).get(domain) or 0.0)
            for item in summaries
        ])
        for domain in sorted(candidate_domains)
    }
    stages = set().union(*(set((item.get("stage_recall_at_10") or {}).keys()) for item in summaries))
    result["stage_recall_at_10"] = {
        stage: _median([
            float((item.get("stage_recall_at_10") or {}).get(stage) or 0.0)
            for item in summaries
        ])
        for stage in sorted(stages)
    }
    for latency_key in ("latency_ms", "reranker_latency_ms"):
        result[latency_key] = {
            metric: _median([
                float(((item.get(latency_key) or {}).get(metric)) or 0.0)
                for item in summaries
            ])
            for metric in ("p50", "p95")
        }
    result["errors"] = max(int(item.get("errors") or 0) for item in summaries)
    result["exact_law_article_case_count"] = int(
        summaries[0].get("exact_law_article_case_count") or 0
    )
    result["multi_issue_case_count"] = int(
        summaries[0].get("multi_issue_case_count") or 0
    )
    result["outside_manifest_count"] = max(int(item.get("outside_manifest_count") or 0) for item in summaries)
    result["invalid_temporal_count"] = max(int(item.get("invalid_temporal_count") or 0) for item in summaries)
    miss_map: dict[str, dict[str, Any]] = {}
    for item in summaries:
        for row in (item.get("misses") or []):
            if isinstance(row, dict):
                miss_map[str(row.get("case_id"))] = dict(row)
    result["misses"] = list(miss_map.values())[:200]
    result["miss_count"] = len(result["misses"])
    result["reranker_runtime"] = {
        "scored_count": max(
            int((item.get("reranker_runtime") or {}).get("scored_count") or 0)
            for item in summaries
        ),
        "degraded_count": max(
            int((item.get("reranker_runtime") or {}).get("degraded_count") or 0)
            for item in summaries
        ),
        "non_cuda_count": max(
            int((item.get("reranker_runtime") or {}).get("non_cuda_count") or 0)
            for item in summaries
        ),
        "modes": sorted({
            str(mode)
            for item in summaries
            for mode in (item.get("reranker_runtime") or {}).get("modes") or []
        }),
    }
    for item in summaries:
        sample = _compact_summary(item).get("trace_sample")
        if sample:
            result["trace_sample"] = sample
            break
    return result


def _run_repeated(
    runtime: V2ServingRuntime,
    grouped: Mapping[str, Sequence[Mapping[str, Any]]],
    config: Mapping[str, Any],
    *,
    warm_runs: int,
) -> dict[str, Any]:
    cold = {split: _evaluate(runtime, values, config) for split, values in grouped.items()}
    warm = [
        {split: _evaluate(runtime, values, config) for split, values in grouped.items()}
        for _ in range(max(1, int(warm_runs)))
    ]
    return {
        "splits": {
            split: _aggregate_summaries([run[split] for run in warm])
            for split in grouped
        },
        "cold_start": {
            split: _compact_summary(summary)
            for split, summary in cold.items()
        },
        "warm_runs": [
            {
                split: _compact_summary(summary)
                for split, summary in run.items()
            }
            for run in warm
        ],
    }


def _development_cases(cases: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Return only cases allowed to influence M5/M6 configuration selection.

    The sealed production holdout remains part of the assembled suite and is
    validated by preflight, but it must not affect experiment ranking. It is
    scored later by an independent frozen acceptance run.
    """

    return [
        case
        for case in cases
        if str(case.get("split") or "") in DEVELOPMENT_SPLITS
    ]


def _run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("m5", "m6"), required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--serving-manifest", type=Path, default=DEFAULT_SERVING_MANIFEST)
    parser.add_argument("--lexical-index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--holdout-envelope", type=Path, default=DEFAULT_HOLDOUT_ENVELOPE)
    parser.add_argument("--source-availability", type=Path, default=DEFAULT_SOURCE_AVAILABILITY)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--current-collection", required=True)
    parser.add_argument("--temporal-collection", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--m5-report", type=Path, default=DEFAULT_M5_REPORT)
    parser.add_argument("--reranker-model", type=Path)
    parser.add_argument("--reranker-manifest", type=Path)
    parser.add_argument(
        "--reranker-custom-code",
        type=Path,
        help="Optional override for a pinned custom-code snapshot declared by the model manifest.",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--experiment-id",
        action="append",
        default=[],
        help="Run only the named experiment(s); intended for a partial smoke run.",
    )
    parser.add_argument("--warm-runs", type=int, default=3, choices=(3, 4, 5))
    args = parser.parse_args(argv)

    manifest_path = args.manifest.resolve()
    index_path = args.lexical_index.resolve()
    suite_path = args.suite.resolve()
    manifest = _load(manifest_path)
    if manifest.get("approved") is not True or manifest.get("legal_review_attestation") is not True:
        raise RuntimeError("approved_v2_manifest_required")
    suite = _load(suite_path)
    validation = validate_development_suite(suite)
    if not validation["valid"]:
        raise RuntimeError("retrieval_eval_suite_gate_failed:" + ",".join(validation["errors"][:10]))
    holdout_envelope = _load(args.holdout_envelope.resolve())
    envelope_errors = validate_public_holdout_envelope(holdout_envelope, require_unused=True)
    if envelope_errors:
        raise RuntimeError("production_holdout_envelope_gate_failed:" + ",".join(envelope_errors[:10]))
    source_availability = _load(args.source_availability.resolve())
    if (
        source_availability.get("status") != "PASS"
        or float(source_availability.get("availability_rate") or 0.0) < 1.0
    ):
        raise RuntimeError("retrieval_eval_source_availability_gate_failed")
    if suite.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256") or suite.get("manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("suite_manifest_fingerprint_mismatch")
    if (
        holdout_envelope.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256")
        or holdout_envelope.get("manifest_sha256") != manifest.get("manifest_sha256")
    ):
        raise RuntimeError("holdout_envelope_manifest_fingerprint_mismatch")
    suite_cases = list(suite.get("cases") or [])
    holdout_case_count = int(holdout_envelope.get("case_count") or 0)
    cases = _development_cases(suite_cases)
    partial_run = bool(args.limit or args.experiment_id)
    if args.limit:
        cases = cases[: max(1, int(args.limit))]
    pointer_path = (args.chroma_path.resolve() / "active_core_collection.txt")
    pointer_before = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else ""

    # M5/M6 acceptance is a GTX 1660 CUDA benchmark.  Set the embedding
    # device before importing legal_search_server so its singleton cannot be
    # initialized on CPU by the surrounding shell environment.  The runner
    # still fails closed below if CUDA is unavailable or an OOM fallback occurs.
    os.environ.setdefault("LEGAL_EMBED_DEVICE", "cuda")
    os.environ.setdefault("LEGAL_RERANKER_DEVICE", "cuda")
    m5_baseline_report: dict[str, Any] | None = None
    if args.mode == "m6":
        m5_report_path = args.m5_report.resolve()
        if not m5_report_path.is_file():
            raise RuntimeError("m6_requires_m5_report")
        m5_baseline_report = _load(m5_report_path)
        if m5_baseline_report.get("status") != "PASS":
            raise RuntimeError("m6_requires_passed_m5_gate")
        if not args.reranker_model or not args.reranker_manifest:
            raise RuntimeError("m6_reranker_model_and_manifest_required")
        m6_parent_config = _m6_parent_config(m5_baseline_report)
        model_evidence = _verify_reranker_manifest(
            args.reranker_model.resolve(),
            args.reranker_manifest.resolve(),
            custom_code_path=(
                args.reranker_custom_code.resolve()
                if args.reranker_custom_code
                else None
            ),
        )
    else:
        model_evidence = None

    import scripts.legal_search_server as legal_search_server
    encoder = legal_search_server.retriever
    encoder_prewarm_started = perf_counter()
    encoder.prewarm()
    encoder_prewarm_ms = round((perf_counter() - encoder_prewarm_started) * 1000, 3)
    if str(getattr(encoder, "_embedding_device", "")) not in {"cuda", "cuda:0"}:
        raise RuntimeError("v2_benchmark_requires_cuda_embedding_runtime")
    if str(getattr(encoder, "_model_fingerprint", "")) != str(manifest.get("model_artifact_fingerprint") or ""):
        raise RuntimeError("embedding_model_fingerprint_mismatch")
    runtime = V2ServingRuntime(
        manifest_path=manifest_path,
        serving_manifest_path=args.serving_manifest.resolve(),
        lexical_index_path=index_path,
        chroma_path=args.chroma_path.resolve(),
        current_collection=args.current_collection,
        temporal_collection=args.temporal_collection,
        query_encoder=encoder,
    )
    try:
        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for case in cases:
            grouped[str(case.get("split"))].append(case)
        experiments: list[dict[str, Any]] = []
        if args.mode == "m5":
            specs = _m5_specs()
            if args.experiment_id:
                requested = set(args.experiment_id)
                specs = [spec for spec in specs if str(spec.get("id")) in requested]
                missing = requested - {str(spec.get("id")) for spec in specs}
                if missing:
                    raise RuntimeError("unknown_m5_experiment:" + ",".join(sorted(missing)))
            for spec in specs:
                config = _base_config(spec)
                result = {
                    "experiment_id": spec["id"],
                    "changed_variable": spec["changed"],
                    "config": {key: value for key, value in config.items() if key != "reranker"},
                    **_run_repeated(runtime, grouped, config, warm_runs=args.warm_runs),
                }
                experiments.append(result)
        else:
            if model_evidence is None:
                raise RuntimeError("m6_model_evidence_missing")
            for top_n in (20, 30, 50, 100):
                reranker = OptionalCrossEncoderReranker(
                    enabled=True,
                    model_path=args.reranker_model.resolve(),
                    model_label=str(model_evidence.get("model_id") or "reranker"),
                    custom_code_path=model_evidence.get("custom_code_path"),
                    max_candidates=top_n,
                    batch_size=8,
                    max_length=512,
                )
                spec = {"id": f"m6-rerank-top-{top_n}", "changed": "rerank_top_n", **m6_parent_config, "rerank_top_n": top_n, "reranker": reranker, "parent_expansion": False, "neighbor_expansion": False}
                result = {
                    "experiment_id": spec["id"],
                    "changed_variable": spec["changed"],
                    "config": {key: value for key, value in spec.items() if key != "reranker"},
                    **_run_repeated(runtime, grouped, spec, warm_runs=args.warm_runs),
                }
                experiments.append(result)
            # Expansion is intentionally measured separately with the best
            # reranker window; no mixed expansion result is used as a gate.
            best_rerank = max(
                experiments, key=lambda item: _experiment_quality(item, mode="m6")
            )
            best_top_n = int((best_rerank.get("config") or {}).get("rerank_top_n") or 20)
            for kind in ("parent_expansion", "neighbor_expansion"):
                reranker = OptionalCrossEncoderReranker(
                    enabled=True,
                    model_path=args.reranker_model.resolve(),
                    model_label=str(model_evidence.get("model_id") or "reranker"),
                    custom_code_path=model_evidence.get("custom_code_path"),
                    max_candidates=best_top_n,
                    batch_size=8,
                    max_length=512,
                )
                spec = {"id": f"m6-{kind}", "changed": kind, **m6_parent_config, "rerank_top_n": best_top_n, "reranker": reranker, "parent_expansion": kind == "parent_expansion", "neighbor_expansion": kind == "neighbor_expansion"}
                experiments.append({"experiment_id": spec["id"], "changed_variable": kind, "config": {key: value for key, value in spec.items() if key != "reranker"}, **_run_repeated(runtime, grouped, spec, warm_runs=args.warm_runs)})
        pointer_after = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else ""
        selected = (
            max(
                experiments,
                key=lambda item: _experiment_quality(item, mode=args.mode),
            )
            if experiments
            else {}
        )
        selected_splits = list((selected.get("splits") or {}).values())
        m5_control_comparison: dict[str, Any] | None = None
        if args.mode == "m5":
            selected_gate = {
                "candidate_recall_at_50": all(float(item.get("candidate_recall_at_50") or 0.0) >= 0.99 for item in selected_splits),
                "per_domain_candidate_recall_at_50": all(
                    float(value) >= 0.98
                    for item in selected_splits
                    for value in (item.get("per_domain_candidate_recall_at_50") or {}).values()
                ),
                "warm_p95_retrieval_le_3000_ms": all(float((item.get("latency_ms") or {}).get("p95") or 0.0) <= 3000.0 for item in selected_splits),
                "exact_law_article_lookup_100pct": all(
                    int(item.get("exact_law_article_case_count") or 0) > 0
                    and float(item.get("exact_law_article_lookup_recall_at_50") or 0.0) >= 1.0
                    for item in selected_splits
                ),
                "exact_law_article_final_recall_100pct": all(
                    int(item.get("exact_law_article_case_count") or 0) > 0
                    and float(item.get("exact_law_article_final_recall_at_10") or 0.0) >= 1.0
                    for item in selected_splits
                ),
                "safety": all(int(item.get("errors") or 0) == 0 and int(item.get("outside_manifest_count") or 0) == 0 and int(item.get("invalid_temporal_count") or 0) == 0 for item in selected_splits),
            }
        else:
            selected_gate = {
                "recall_at_10": all(float(item.get("recall_at_10") or 0.0) >= 0.95 for item in selected_splits),
                "mrr_at_10": all(float(item.get("mrr_at_10") or 0.0) >= 0.90 for item in selected_splits),
                "correct_refusal_rate": all(
                    item.get("correct_refusal_rate") is not None
                    and float(item.get("correct_refusal_rate") or 0.0) >= 0.99
                    for item in selected_splits
                ),
                "per_domain_recall_at_10": all(
                    float(value.get("recall_at_10") or 0.0) >= 0.95
                    for item in selected_splits
                    for value in (item.get("per_domain") or {}).values()
                    if int(value.get("answer_required_count") or 0) > 0
                ),
                "warm_p95_retrieval_plus_reranking_le_15000_ms": all(float((item.get("latency_ms") or {}).get("p95") or 0.0) <= 15000.0 for item in selected_splits),
                "multi_issue_required_source_coverage": all(
                    int(item.get("multi_issue_case_count") or 0) > 0
                    and float(item.get("multi_issue_all_required_sources_coverage") or 0.0) >= 0.95
                    for item in selected_splits
                ),
                "safety": all(int(item.get("errors") or 0) == 0 and int(item.get("outside_manifest_count") or 0) == 0 and int(item.get("invalid_temporal_count") or 0) == 0 for item in selected_splits),
                "learned_reranker_active_on_cuda": all(
                    (item.get("reranker_runtime") or {}).get("modes") == ["learned"]
                    and int((item.get("reranker_runtime") or {}).get("degraded_count") or 0) == 0
                    and int((item.get("reranker_runtime") or {}).get("non_cuda_count") or 0) == 0
                    and int((item.get("reranker_runtime") or {}).get("scored_count") or 0) > 0
                    for item in selected_splits
                ),
            }
            control = next(
                (
                    item
                    for item in (m5_baseline_report or {}).get("experiments") or []
                    if item.get("experiment_id") == "m5-control"
                ),
                None,
            )
            if control is None:
                m5_control_comparison = {"available": False}
                selected_gate.update({
                    "recall_not_below_m5_control": False,
                    "mrr_gain_relative_or_top5_gain": False,
                })
            else:
                control_recall = _experiment_metric(control, "recall_at_10")
                control_mrr = _experiment_metric(control, "mrr_at_10")
                control_top5 = _experiment_metric(control, "top5_rate")
                selected_recall = _experiment_metric(selected, "recall_at_10")
                selected_mrr = _experiment_metric(selected, "mrr_at_10")
                selected_top5 = _experiment_metric(selected, "top5_rate")
                mrr_relative_gain = (selected_mrr / control_mrr - 1.0) if control_mrr > 0 else 0.0
                top5_delta = selected_top5 - control_top5
                m5_control_comparison = {
                    "available": True,
                    "control_recall_at_10": control_recall,
                    "selected_recall_at_10": selected_recall,
                    "recall_delta": selected_recall - control_recall,
                    "control_mrr_at_10": control_mrr,
                    "selected_mrr_at_10": selected_mrr,
                    "mrr_relative_gain": mrr_relative_gain,
                    "control_top5_rate": control_top5,
                    "selected_top5_rate": selected_top5,
                    "top5_delta": top5_delta,
                    "required_mrr_relative_gain": 0.05,
                    "required_top5_delta": 0.01,
                }
                selected_gate.update({
                    "recall_not_below_m5_control": selected_recall >= control_recall,
                    "mrr_gain_relative_or_top5_gain": mrr_relative_gain >= 0.05 or top5_delta >= 0.01,
                })
        report = {
            "schema_version": f"legal-retrieval-release-v2-{args.mode}-benchmark-v1",
            "status": "PASS" if not partial_run and pointer_before == pointer_after and all(selected_gate.values()) else "FAIL",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": args.mode,
            "manifest_file_sha256": _sha(manifest_path),
            "manifest_sha256": manifest.get("manifest_sha256"),
            "suite_file_sha256": _sha(suite_path),
            "suite_sha256": suite.get("suite_sha256"),
            "embedding_model_fingerprint": getattr(encoder, "_model_fingerprint", None),
            "embedding_prewarm_ms": encoder_prewarm_ms,
            "warm_runs": args.warm_runs,
            "partial_run": partial_run,
            "experiment_filter": list(args.experiment_id),
            "full_suite_required": True,
            "suite_case_count": len(suite_cases),
            "evaluated_case_count": len(cases),
            "evaluated_splits": list(DEVELOPMENT_SPLITS),
            "holdout_case_count": holdout_case_count,
            "holdout_excluded_from_selection": True,
            "holdout_acceptance": "separate_frozen_run_required",
            "reranker_model": model_evidence,
            "experiments": experiments,
            "selected_experiment": selected.get("experiment_id"),
            "selection_policy": (
                [
                    "safety",
                    "candidate_recall_at_50",
                    "recall_at_10",
                    "mrr_at_10",
                    "all_required_sources_coverage",
                    "top5_rate",
                    "latency_p95_ascending",
                ]
                if args.mode == "m5"
                else [
                    "safety_and_learned_cuda",
                    "recall_at_10",
                    "mrr_at_10",
                    "all_required_sources_coverage",
                    "top5_rate",
                    "latency_p95_ascending",
                ]
            ),
            "selected_gates": selected_gate,
            "m5_control_comparison": m5_control_comparison,
            "active_pointer_before": pointer_before,
            "active_pointer_after": pointer_after,
            "active_pointer_changed": pointer_before != pointer_after,
            "activation_performed": False,
            "database_mutated": False,
            "vector_collections_mutated": False,
        }
    finally:
        runtime.close()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{_sha(output)}  {output.name}\n", encoding="ascii")
    print(json.dumps({"status": report["status"], "mode": args.mode, "experiment_count": len(report["experiments"]), "output": str(output)}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark and persist a blocker report on preflight failure."""

    tokens = list(argv if argv is not None else sys.argv[1:])
    mode = "unknown"
    if "--mode" in tokens:
        index = tokens.index("--mode")
        if index + 1 < len(tokens):
            mode = tokens[index + 1]
    output = DEFAULT_OUTPUT
    for index, token in enumerate(tokens):
        if token == "--output" and index + 1 < len(tokens):
            output = Path(tokens[index + 1])
        elif token.startswith("--output="):
            output = Path(token.split("=", 1)[1])
    output = output.resolve()
    try:
        return _run_cli(argv)
    except Exception as exc:
        report = {
            "schema_version": f"legal-retrieval-release-v2-{mode}-benchmark-v1",
            "status": "BLOCKED",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "reason": f"{type(exc).__name__}:{exc}",
            "full_suite_required": True,
            "partial_run": False,
            "active_pointer_changed": False,
            "activation_performed": False,
            "database_mutated": False,
            "vector_collections_mutated": False,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output.with_suffix(output.suffix + ".sha256").write_text(
            f"{_sha(output)}  {output.name}\n", encoding="ascii"
        )
        print(json.dumps({"status": "BLOCKED", "mode": mode, "output": str(output), "reason": report["reason"]}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
