"""Compare baseline and candidate serving manifests on the approved Golden-1000.

The runner is deliberately retrieval-only: it executes the same retrieval
contract, prompt-independent, with lexical SQL and Chroma constrained by the
same manifest. ``--issue-split`` calls the real ``/search/batch`` implementation
used by Ask (including vector prefetch and bounded parallel hydration), rather
than timing a synthetic loop of independent searches. It never changes the
active pointer or PostgreSQL serving scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_retrieval_evaluation import (
    build_retrieval_metrics,
    expected_source_groups,
    expected_sources_for_case,
    match_expected_sources,
    match_expected_source_group_hits,
    normalize_law_number,
    question_for_case,
    validate_case_temporal_alignment,
)


DOMAIN_MAP = {
    "Hộ tịch/chứng thực": "ho_tich_chung_thuc",
    "Đất đai/xây dựng": "dat_dai_xay_dung",
    "Cư trú/an ninh": "cu_tru_an_ninh",
    "Cư trú/căn cước/an ninh": "cu_tru_an_ninh",
    "Đất đai/xây dựng/môi trường": "dat_dai_xay_dung",
    "An sinh/y tế/giáo dục": "an_sinh_y_te_giao_duc",
    "Khiếu nại/tố cáo/xử phạt": "khieu_nai_to_cao_xu_phat",
    "Hành chính công/một cửa": "hanh_chinh_cong",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(float(ordered[index]), 3)


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def article_matches(actual: Any, expected: Any) -> bool:
    actual_norm = norm(actual)
    expected_text = str(expected or "")
    alternatives = [
        norm(item) for item in re.split(r"[,;/|]+", expected_text) if norm(item)
    ]
    return (
        actual_norm in alternatives if alternatives else actual_norm == norm(expected)
    )


def load_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def expected_hit(
    results: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> tuple[int | None, bool]:
    for rank, result in enumerate(results[:10], start=1):
        law = norm(result.get("law_number"))
        article = result.get("article_number")
        for source in sources:
            if law == norm(source.get("law_number")) and article_matches(
                article, source.get("article")
            ):
                return rank, True
    return None, False


def document_hit(
    results: list[dict[str, Any]], sources: list[dict[str, Any]], limit: int
) -> bool:
    expected_laws = {norm(source.get("law_number")) for source in sources}
    return any(
        norm(result.get("law_number")) in expected_laws for result in results[:limit]
    )


def citation_ready(result: dict[str, Any]) -> bool:
    return bool(
        str(result.get("law_number") or "").strip()
        and str(result.get("source_url") or "").strip()
        and str(result.get("article_number") or "").strip()
    )


def configure_environment(manifest: Path, collection: str, manifest_sha: str) -> None:
    os.environ["LEGAL_BENCHMARK_MODE"] = "1"
    os.environ["LEGAL_BENCHMARK_SERVING_MANIFEST"] = str(manifest.resolve())
    os.environ["LEGAL_BENCHMARK_SERVING_MANIFEST_FILE_SHA256"] = manifest_sha
    os.environ["LEGAL_CHROMA_COLLECTION"] = collection
    os.environ["LEGAL_CHROMA_SOURCE_COLLECTION"] = collection
    os.environ.setdefault(
        "LEGAL_CHROMA_PATH", str(ROOT / "release-data" / "legal" / "chroma_store")
    )


def evaluate(
    name: str,
    retriever: Any,
    cases: list[dict[str, Any]],
    *,
    as_of: date,
    domain_docs: dict[str, set[int]],
    allowed_document_ids: set[int] | None = None,
    available_law_numbers: set[str] | None = None,
    issue_split: bool = False,
    search_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    search_config = dict(search_config or {})
    candidate_count = int(search_config.get("candidate_count", 150))
    lexical_candidate_count = int(search_config.get("lexical_candidate_count", 60))
    fusion_strategy = str(search_config.get("fusion_strategy", "legacy_stack"))
    vector_weight = float(search_config.get("vector_weight", 0.6))
    lexical_weight = float(search_config.get("lexical_weight", 0.4))
    ranking_strategy = str(search_config.get("ranking_strategy", "legacy_stack"))
    enable_learned_reranker = bool(search_config.get("enable_learned_reranker", False))
    rerank_top_n = int(search_config.get("rerank_top_n", 40))
    enable_parent_expansion = bool(search_config.get("enable_parent_expansion", True))
    enable_neighbor_expansion = bool(
        search_config.get("enable_neighbor_expansion", True)
    )
    timings: list[float] = []
    retrieval_timings: list[float] = []
    reranker_timings: list[float] = []
    per_domain: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "cases": 0,
            "hits": 0,
            "top5": 0,
            "no_source": 0,
            "citation_ready": 0,
            "latency_ms": [],
            "retrieval_latency_ms": [],
        }
    )
    rows: list[dict[str, Any]] = []
    no_source = 0
    hit_count = 0
    top5_count = 0
    mrr_total = 0.0
    citation_total = 0
    wrong_scope = 0
    outside_manifest = 0
    invalid_evidence = 0
    reranker_scored = 0
    neighbor_candidates = 0
    hydrated_parents = 0
    errors = 0
    import scripts.legal_search_server as legal_search_server
    from api.legal_section_grounding import plan_legal_issues
    from scripts.legal_search_server import (
        BatchSearchIssue,
        BatchSearchRequest,
        SearchRequest,
        _is_current,
    )

    def run_one(case: dict[str, Any]) -> dict[str, Any]:
        query = question_for_case(case)
        domain = DOMAIN_MAP.get(str(case.get("domain") or ""))
        sources = expected_sources_for_case(case)
        source_groups = expected_source_groups(case)
        use_issue_split = issue_split and len(source_groups) > 1
        case_as_of = date.fromisoformat(str(case.get("legal_as_of") or as_of.isoformat()))
        dataset_error = validate_case_temporal_alignment(case)
        if dataset_error:
            expected_refusal = bool(case.get("expected_refusal"))
            return {
                "case_id": case.get("case_id"),
                "domain": domain,
                "expected_law_numbers": [source.get("law_number") for source in sources],
                "expected_articles": [source.get("article") for source in sources],
                "hit_at_10": False,
                "hit_rank": None,
                "answer_required": not expected_refusal,
                "expected_refusal": expected_refusal,
                "correct_refusal": False,
                "response_status": "dataset_error",
                "error_code": dataset_error["code"],
                "intent": "unknown",
                "temporal_scope": "unknown",
                "retrieval_as_of": case_as_of.isoformat(),
                "source_available": None,
                "all_source_documents_available": None,
                "matched_expected_source_keys": [],
                "expected_source_coverage": 0.0,
                "issue_hits": [False for _ in source_groups],
                "direct_source_top5": False,
                "result_count": 0,
                "top_results": [],
                "latency_ms": 0.0,
                "retrieval_latency_ms": 0.0,
                "error": None,
                "dataset_error": dataset_error,
                "issue_count": len(source_groups) or 1,
                "reranker": {},
                "expansion": {},
                "_domain": domain or "unknown",
                "_hit": False,
                "_top5": False,
                "_no_result": True,
                "_citation_count": 0,
                "_wrong_scope": 0,
                "_outside_manifest": 0,
                "_invalid_evidence": 0,
            }
        started = perf_counter()
        issue_count = 1
        retrieval_latency_ms: float | None = None
        reranker_status: dict[str, Any] = {}
        expansion_status: dict[str, Any] = {}
        response_status = "ok"
        response_error_code: str | None = None
        intent = "unknown"
        temporal_scope = "unknown"
        issue_packet_results: list[list[dict[str, Any]]] = []
        try:
            issue_queries = [query]
            if use_issue_split:
                planned = plan_legal_issues(query, max_issues=8)
                planned = [
                    item for item in planned if str(item.query_text or "").strip()
                ]
                issue_queries = [str(item.query_text).strip() for item in planned] or [
                    query
                ]
                # This is the same batch boundary used by the Ask route. Keep
                # the Golden domain as the serving scope because it is the
                # approved five-domain label, while the planner's internal
                # facets are only used to split the question.
                batch_issues = [
                    BatchSearchIssue(
                        issue_id=f"{case.get('case_id')}-{index + 1}",
                        query=issue_query,
                        domain=domain,
                        intent=str(planned[index].intent or "unknown")
                        if index < len(planned)
                        else "unknown",
                    )
                    for index, issue_query in enumerate(issue_queries)
                ]
                batch_request = BatchSearchRequest(
                    request_id=f"golden-{case.get('case_id')}",
                    as_of=case_as_of,
                    as_of_explicit=True,
                    benchmark_today=case_as_of,
                    issues=batch_issues,
                    retrieval_tier="core",
                    include_trace=False,
                    ranking_strategy=ranking_strategy,
                    fusion_strategy=fusion_strategy,
                    vector_weight=vector_weight,
                    lexical_weight=lexical_weight,
                    enable_learned_reranker=enable_learned_reranker,
                    rerank_top_n=rerank_top_n,
                    enable_parent_expansion=enable_parent_expansion,
                    enable_neighbor_expansion=enable_neighbor_expansion,
                )
                # ``search_batch`` is the FastAPI handler, but it is also a
                # deterministic in-process function. Calling it directly
                # avoids network overhead while preserving its production
                # batching/prefetch/parallelism behavior.
                batch_response = legal_search_server.search_batch(batch_request)
                batch_queries = [
                    dict(query_payload)
                    for issue_payload in (batch_response.get("issues") or [])
                    for query_payload in (issue_payload.get("queries") or [])
                ]
                blocked_queries = [
                    query_payload
                    for query_payload in batch_queries
                    if query_payload.get("status") == "clarification_required"
                ]
                if blocked_queries and len(blocked_queries) == len(batch_queries):
                    response_status = "clarification_required"
                    response_error_code = str(blocked_queries[0].get("error_code") or "") or None
                classifications = [
                    query_payload.get("query_classification") or {}
                    for query_payload in batch_queries
                ]
                if classifications:
                    intent = str(classifications[0].get("intent") or "unknown")
                    temporal_scope = str(
                        classifications[0].get("temporal_scope") or "unknown"
                    )
                batch_timing = batch_response.get("timing_ms") or {}
                # Query embedding is common to baseline and candidate and is
                # independent of corpus size. Keep it in ``latency_ms`` (the
                # end-to-end retrieval request), but expose corpus retrieval
                # separately by subtracting only the measured embedding time.
                # This is the metric used by the runbook's "p95 retrieval"
                # gate; it still includes ANN, SQL/hydration and ranking.
                query_totals = [
                    float(query.get("timing_ms", {}).get("total") or 0.0)
                    for issue_payload in (batch_response.get("issues") or [])
                    for query in (issue_payload.get("queries") or [])
                ]
                # Independent issue queries run in the bounded batch pool.
                # Measure the critical retrieval path (ANN plus the slowest
                # query's exact/SQL/hydration/ranking work) separately from
                # response merging/context compaction.
                retrieval_latency_ms = float(batch_timing.get("ann_ms") or 0.0) + (
                    max(query_totals)
                    if query_totals
                    else max(
                        0.0,
                        float(batch_timing.get("total") or 0.0)
                        - float(batch_timing.get("embedding_ms") or 0.0),
                    )
                )
                results = [
                    dict(item)
                    for item in list(batch_response.get("context_results") or [])[:10]
                ]
                issue_packet_results = [
                    [dict(item) for item in (issue_payload.get("results") or [])[:10]]
                    for issue_payload in (batch_response.get("issues") or [])
                ]
                # A diversified context may omit an expected source that is
                # still present in its issue packet. For recall accounting,
                # use the bounded union of issue packets exactly as the Ask
                # evidence collector does before context compaction.
                if not results:
                    for issue_payload in batch_response.get("issues") or []:
                        results.extend(
                            dict(item) for item in issue_payload.get("results") or []
                        )
                    results = results[:10]
                issue_count = len(issue_queries)
            else:
                issue_count = 1
            if not use_issue_split:
                response = retriever.search(
                    SearchRequest(
                        query=query,
                        limit=10,
                        candidate_count=candidate_count,
                        lexical_candidate_count=lexical_candidate_count,
                        as_of=case_as_of,
                        as_of_explicit=True,
                        benchmark_today=case_as_of,
                        domain=domain,
                        retrieval_tier="core",
                        include_trace=False,
                        allow_broad_fallback=True,
                        ranking_strategy=ranking_strategy,
                        fusion_strategy=fusion_strategy,
                        vector_weight=vector_weight,
                        lexical_weight=lexical_weight,
                        enable_learned_reranker=enable_learned_reranker,
                        rerank_top_n=rerank_top_n,
                        enable_parent_expansion=enable_parent_expansion,
                        enable_neighbor_expansion=enable_neighbor_expansion,
                    )
                )
                reranker_status = dict(response.get("reranker") or {})
                expansion_status = dict(response.get("expansion") or {})
                response_status = str(response.get("status") or "ok")
                response_error_code = str(response.get("error_code") or "") or None
                classification = dict(response.get("query_classification") or {})
                intent = str(classification.get("intent") or "unknown")
                temporal_scope = str(classification.get("temporal_scope") or "unknown")
                response_timing = response.get("timing_ms") or {}
                retrieval_latency_ms = max(
                    0.0,
                    float(response_timing.get("total") or 0.0)
                    - float(response_timing.get("embedding") or 0.0),
                )
                results = list(response.get("results") or [])[:10]
        except Exception as exc:  # keep a per-case audit trail and continue
            results = []
            error = f"{type(exc).__name__}:{exc}"
        else:
            error = None
        elapsed = (perf_counter() - started) * 1000
        source_match = match_expected_sources(results, case, top_k=10)
        hit_rank = source_match["hit_rank"]
        hit = bool(source_match["hit_at_10"])
        groups = source_groups
        if use_issue_split and groups:
            issue_hits = match_expected_source_group_hits(
                case,
                issue_packet_results,
                results,
            )
        else:
            issue_hits = list(source_match["issue_hits"])
        top5 = document_hit(results, sources, 5)
        expected_doc_ids = domain_docs.get(domain, set())
        expected_laws = {
            normalize_law_number(source.get("law_number")) for source in sources
        }
        source_available = (
            None
            if available_law_numbers is None
            else any(law in available_law_numbers for law in expected_laws)
        )
        expected_refusal = bool(case.get("expected_refusal"))
        correct_refusal = expected_refusal and not results and response_status in {
            "clarification_required",
            "source_gap",
            "unsupported",
            "ok",
        }
        return {
            "case_id": case.get("case_id"),
            "domain": domain,
            "expected_law_numbers": [source.get("law_number") for source in sources],
            "expected_articles": [source.get("article") for source in sources],
            "hit_at_10": hit,
            "hit_rank": hit_rank,
            "answer_required": not expected_refusal,
            "expected_refusal": expected_refusal,
            "correct_refusal": correct_refusal,
            "response_status": response_status,
            "error_code": response_error_code,
            "intent": intent,
            "temporal_scope": temporal_scope,
            "retrieval_as_of": case_as_of.isoformat(),
            "source_available": source_available,
            "all_source_documents_available": (
                None
                if available_law_numbers is None
                else expected_laws.issubset(available_law_numbers)
            ),
            "matched_expected_source_keys": source_match["matched_expected_source_keys"],
            "expected_source_coverage": source_match["expected_source_coverage"],
            "issue_hits": issue_hits,
            "direct_source_top5": top5,
            "result_count": len(results),
            "top_results": [
                {
                    key: result.get(key)
                    for key in (
                        "document_id",
                        "law_number",
                        "article_number",
                        "domain_slug",
                        "source_url",
                        "document_status",
                    )
                }
                for result in results[:10]
            ],
            "latency_ms": round(elapsed, 3),
            "retrieval_latency_ms": round(
                float(
                    retrieval_latency_ms
                    if retrieval_latency_ms is not None
                    else elapsed
                ),
                3,
            ),
            "error": error,
            "issue_count": issue_count,
            "reranker": reranker_status,
            "expansion": expansion_status,
            "_domain": domain or "unknown",
            "_hit": hit,
            "_top5": top5,
            "_no_result": not results,
            "_citation_count": sum(
                1 for result in results[:10] if citation_ready(result)
            ),
            "_wrong_scope": sum(
                1
                for result in results
                if expected_doc_ids
                and int(result.get("document_id") or 0) not in expected_doc_ids
            ),
            "_outside_manifest": sum(
                1
                for result in results
                if allowed_document_ids is not None
                and int(result.get("document_id") or 0) not in allowed_document_ids
            ),
            "_invalid_evidence": sum(
                1
                for result in results
                if not _is_current(dict(result), case_as_of, allow_staging=True)
            ),
        }

    # ``search_batch`` has its own bounded worker pool and shared CPU model
    # lock. Nesting another three-way pool here measures queue contention
    # between synthetic benchmark clients rather than the request contract;
    # keep issue-split measurements serial so p95 is per-request latency. The
    # non-issue diagnostic retains modest concurrency for throughput comparison.
    benchmark_workers = 1 if issue_split or enable_learned_reranker else 3
    with ThreadPoolExecutor(
        max_workers=benchmark_workers, thread_name_prefix="golden"
    ) as pool:
        futures = [pool.submit(run_one, case) for case in cases]
        for index, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            domain = row.pop("_domain")
            hit = bool(row.pop("_hit"))
            top5 = bool(row.pop("_top5"))
            no_result_flag = bool(row.pop("_no_result"))
            citation_count = int(row.pop("_citation_count"))
            wrong_scope_count = int(row.pop("_wrong_scope"))
            outside_manifest_count = int(row.pop("_outside_manifest"))
            invalid_evidence_count = int(row.pop("_invalid_evidence"))
            rows.append(row)
            if row.get("dataset_error"):
                continue
            timings.append(float(row.get("latency_ms") or 0.0))
            retrieval_timings.append(float(row.get("retrieval_latency_ms") or 0.0))
            hit_count += int(hit)
            top5_count += int(top5)
            no_source += int(no_result_flag)
            citation_total += citation_count
            wrong_scope += wrong_scope_count
            outside_manifest += outside_manifest_count
            invalid_evidence += invalid_evidence_count
            reranker = row.get("reranker") or {}
            expansion = row.get("expansion") or {}
            reranker_timings.append(float(reranker.get("latency_ms") or 0.0))
            reranker_scored += int(reranker.get("scored_count") or 0)
            neighbor_candidates += int(expansion.get("neighbor_candidate_count") or 0)
            hydrated_parents += int(expansion.get("hydrated_parent_count") or 0)
            if hit:
                mrr_total += 1.0 / float(row.get("hit_rank") or 1)
            bucket = per_domain[domain]
            bucket["cases"] += 1
            bucket["hits"] += int(hit)
            bucket["top5"] += int(top5)
            bucket["no_source"] += int(no_result_flag)
            bucket["citation_ready"] += citation_count
            bucket["latency_ms"].append(float(row.get("latency_ms") or 0.0))
            bucket["retrieval_latency_ms"].append(
                float(row.get("retrieval_latency_ms") or 0.0)
            )
            errors += int(bool(row.get("error")))
            progress_every = 5 if enable_learned_reranker else 50
            if index % progress_every == 0:
                print(
                    json.dumps(
                        {"run": name, "processed": index, "total": len(cases)},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    domain_summary: dict[str, Any] = {}
    for domain, bucket in sorted(per_domain.items()):
        n = bucket["cases"]
        domain_summary[domain] = {
            "case_count": n,
            "recall_at_10": round(bucket["hits"] / n, 5) if n else 0.0,
            "direct_source_top5": round(bucket["top5"] / n, 5) if n else 0.0,
            "no_result_rate": round(bucket["no_source"] / n, 5) if n else 0.0,
            "citation_ready_at_10": bucket["citation_ready"],
            "p50_ms": percentile(bucket["latency_ms"], 0.50),
            "p95_ms": percentile(bucket["latency_ms"], 0.95),
            "p50_retrieval_ms": percentile(bucket["retrieval_latency_ms"], 0.50),
            "p95_retrieval_ms": percentile(bucket["retrieval_latency_ms"], 0.95),
        }
    quality_metrics = build_retrieval_metrics(cases, rows)
    for domain, quality in quality_metrics["per_domain"].items():
        domain_summary.setdefault(domain, {}).update(quality)
    return {
        "name": name,
        "search_config": {
            "candidate_count": candidate_count,
            "lexical_candidate_count": lexical_candidate_count,
            "fusion_strategy": fusion_strategy,
            "vector_weight": vector_weight,
            "lexical_weight": lexical_weight,
            "ranking_strategy": ranking_strategy,
            "enable_learned_reranker": enable_learned_reranker,
            "rerank_top_n": rerank_top_n,
            "enable_parent_expansion": enable_parent_expansion,
            "enable_neighbor_expansion": enable_neighbor_expansion,
        },
        "case_count": len(cases),
        "metric_contract_version": quality_metrics["metric_contract_version"],
        "dataset_case_count": quality_metrics["dataset_case_count"],
        "valid_case_count": quality_metrics["valid_case_count"],
        "dataset_error_count": quality_metrics["dataset_error_count"],
        "dataset_errors": quality_metrics["dataset_errors"],
        "answer_required_count": quality_metrics["answer_required_count"],
        "expected_refusal_count": quality_metrics["expected_refusal_count"],
        "recall_at_10": quality_metrics["recall_at_10"] or 0.0,
        "direct_source_top5": round(top5_count / len(cases), 5) if cases else 0.0,
        "mrr": quality_metrics["mrr_at_10"] or 0.0,
        "mrr_at_10": quality_metrics["mrr_at_10"],
        "correct_refusal_rate": quality_metrics["correct_refusal_rate"],
        "false_blocked_answer_count": quality_metrics["false_blocked_answer_count"],
        "issue_count": quality_metrics["issue_count"],
        "issue_recall_at_10": quality_metrics["issue_recall_at_10"],
        "all_required_sources_coverage": quality_metrics[
            "all_required_sources_coverage"
        ],
        "miss_count": quality_metrics["miss_count"],
        "misses": quality_metrics["misses"],
        "per_intent": quality_metrics["per_intent"],
        "per_temporal_scope": quality_metrics["per_temporal_scope"],
        "no_result_rate": round(no_source / len(cases), 5) if cases else 0.0,
        "citation_ready_at_10": citation_total,
        "wrong_scope_result_count": wrong_scope,
        "outside_manifest_result_count": outside_manifest,
        "invalid_evidence_result_count": invalid_evidence,
        "reranker_scored_candidate_count": reranker_scored,
        "neighbor_candidate_count": neighbor_candidates,
        "hydrated_parent_count": hydrated_parents,
        "p50_reranker_ms": percentile(reranker_timings, 0.50),
        "p95_reranker_ms": percentile(reranker_timings, 0.95),
        "errors": errors,
        "p50_ms": percentile(timings, 0.50),
        "p95_ms": percentile(timings, 0.95),
        "p99_ms": percentile(timings, 0.99),
        "p50_retrieval_ms": percentile(retrieval_timings, 0.50),
        "p95_retrieval_ms": percentile(retrieval_timings, 0.95),
        "p99_retrieval_ms": percentile(retrieval_timings, 0.99),
        "per_domain": domain_summary,
        "cases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--warmup-queries",
        type=int,
        default=0,
        help="Warm both HNSW collections with this many non-Golden queries before timing.",
    )
    parser.add_argument(
        "--warm-query-cache",
        action="store_true",
        help="Precompute the same Golden query embeddings before both timed runs; reports warm-cache retrieval separately.",
    )
    parser.add_argument(
        "--exact-flat",
        action="store_true",
        help="Use the opt-in exact cosine matrix backend for both collections; HNSW remains the default.",
    )
    parser.add_argument(
        "--hydration-cache",
        type=Path,
        default=None,
        help="Checksum-bound candidate chunk-row cache for an isolated hydration benchmark.",
    )
    parser.add_argument(
        "--issue-split",
        action="store_true",
        help="Mirror Ask pipeline deterministic issue decomposition before retrieval.",
    )
    args = parser.parse_args()

    golden = load_payload(args.golden.resolve())
    cases = list(golden.get("cases") or [])
    if args.offset < 0:
        raise ValueError("offset_must_be_non_negative")
    if args.offset:
        cases = cases[args.offset :]
    if args.limit:
        cases = cases[: args.limit]
    baseline = load_payload(args.baseline_manifest.resolve())
    candidate = load_payload(args.candidate_manifest.resolve())
    if candidate.get("required_source_blockers") or not candidate.get(
        "prebuild_gate", {}
    ).get("coverage_balanced"):
        raise RuntimeError("candidate_manifest_prebuild_gate_not_clear")
    if candidate.get("status") != "ready_for_candidate_build":
        raise RuntimeError("candidate_manifest_not_ready")
    before_pointer = (
        args.chroma_path.joinpath("active_core_collection.txt")
        .read_text(encoding="utf-8")
        .strip()
    )

    configure_environment(
        args.candidate_manifest.resolve(),
        candidate["candidate_collection"],
        sha256(args.candidate_manifest.resolve()),
    )
    import chromadb

    import scripts.legal_search_server as legal_search_server
    from scripts.legal_search_server import CORE_BATCH_CANDIDATE_COUNT, LegalRetriever

    client = chromadb.PersistentClient(path=str(args.chroma_path.resolve()))
    names = {item.name for item in client.list_collections()}
    for collection in (
        baseline["collection"]["collection_name"],
        candidate["candidate_collection"],
    ):
        if collection not in names:
            raise RuntimeError(f"missing_collection:{collection}")
    retriever = LegalRetriever()
    # The in-process batch handler uses this module-level service, matching the
    # production retrieval endpoint without opening a second server.
    legal_search_server.retriever = retriever
    retriever.prewarm()
    candidate_docs = {int(row["document_id"]) for row in candidate["documents"]}
    baseline_docs = {int(value) for value in baseline["document_ids"]}
    candidate_chunks = {
        int(value)
        for row in candidate["documents"]
        for value in (row.get("expected_chunk_ids") or [])
    }
    baseline_chunks = {int(value) for value in baseline["expected_chunk_ids"]}
    domain_docs: dict[str, set[int]] = defaultdict(set)
    for row in candidate["documents"]:
        domain_docs[str(row.get("domain") or "")].add(int(row["document_id"]))
    for row in baseline["documents"]:
        domain_docs.setdefault(str(row.get("domain") or ""), set()).add(
            int(row["document_id"])
        )

    def configure_run(
        collection_name: str,
        documents: set[int],
        chunks: set[int],
        *,
        allow_staging: bool,
        clear_query_cache: bool = True,
        manifest_documents: list[dict[str, Any]] | None = None,
    ) -> None:
        retriever._collection = client.get_collection(collection_name)
        retriever._source_collection = retriever._collection
        retriever._serving_allowed_document_ids = set(documents)
        domain_document_ids: dict[str, set[int]] = defaultdict(set)
        for row in manifest_documents or []:
            domain_slug = str(row.get("domain") or "").strip()
            document_id = int(row.get("document_id") or 0)
            if domain_slug and document_id:
                domain_document_ids[domain_slug].add(document_id)
        retriever._serving_domain_document_ids = domain_document_ids
        retriever._shadow_allowed_chunk_ids = {
            "core": set(chunks),
            "expanded": set(chunks),
        }
        retriever._benchmark_allow_staging = allow_staging
        # Keep exact identifier lookups on the same bounded in-process cache
        # during the isolated comparison. Public serving leaves this flag
        # unset, so benchmark acceleration cannot silently change production.
        retriever._benchmark_cache_exact_rows = True
        retriever._exact_vector_search_enabled = bool(args.exact_flat)
        retriever._hydration_cache_rows = None
        retriever._hydration_cache_parents = None
        retriever._hydration_cache_exact_index = None
        retriever._hydration_cache_article_chunk_counts = None
        retriever._hydration_cache_article_chunks = None
        retriever._hydration_cache_exact_packet_cache = {}
        retriever._hydration_cache_manifest_sha256 = None
        if allow_staging and args.hydration_cache is not None:
            retriever.load_hydration_cache(
                args.hydration_cache.resolve(),
                manifest_sha256=str(candidate.get("manifest_sha256") or ""),
            )
        if clear_query_cache:
            retriever._query_vector_cache.clear()
        retriever._exact_rows_cache.clear()

    warmup_count = max(0, int(args.warmup_queries))
    if warmup_count:
        # Page-cache/HNSW warm-up must be symmetric. Without this, the second
        # collection is measured after a different OS cache state and a smaller
        # collection can appear slower simply because its graph was cold.
        # ASCII keeps this runner portable across Windows console/code-page
        # settings; these queries only warm Chroma/HNSW pages and are never
        # included in the Golden score.
        warmup_queries = [
            "thu tuc hanh chinh cap xa",
            "ho so dang ky ho tich",
            "tham quyen giai quyet khieu nai",
            "dieu kien cap giay chung nhan dat dai",
            "dang ky cu tru va can cuoc",
            "chinh sach an sinh y te giao duc",
            "phi le phi bieu mau hanh chinh",
            "thoi han giai quyet ho so phuong xa",
        ]
        warmup_queries = (
            warmup_queries
            * ((warmup_count + len(warmup_queries) - 1) // len(warmup_queries))
        )[:warmup_count]
        for collection_name, documents, chunks, allow_staging in (
            (
                baseline["collection"]["collection_name"],
                baseline_docs,
                baseline_chunks,
                False,
            ),
            (candidate["candidate_collection"], candidate_docs, candidate_chunks, True),
        ):
            configure_run(
                collection_name,
                documents,
                chunks,
                allow_staging=allow_staging,
                manifest_documents=(
                    candidate["documents"] if allow_staging else baseline["documents"]
                ),
            )
            for start in range(0, len(warmup_queries), 8):
                retriever.prefetch_batch_vectors(
                    warmup_queries[start : start + 8],
                    retrieval_tier="core",
                    candidate_count=CORE_BATCH_CANDIDATE_COUNT,
                )
            # Force Chroma's lazy HNSW distance layer before timing; a
            # metadata-only probe does not consistently load it.
            warm_vector = retriever.encode_query(warmup_queries[0])
            retriever._collection.query(
                query_embeddings=[warm_vector.tolist()],
                n_results=1,
                include=["distances"],
            )
            retriever._query_vector_cache.clear()

    as_of = date.fromisoformat(str(candidate["legal_as_of"]))
    if args.warm_query_cache:
        from api.legal_section_grounding import plan_legal_issues

        warm_query_set: list[str] = []
        for case in cases:
            query = str((case.get("questions") or {}).get("citizen") or "").strip()
            if args.issue_split:
                planned = [
                    item
                    for item in plan_legal_issues(query, max_issues=8)
                    if str(item.query_text or "").strip()
                ]
                warm_query_set.extend(str(item.query_text).strip() for item in planned)
            else:
                warm_query_set.append(query)
        warm_query_set = list(dict.fromkeys(item for item in warm_query_set if item))
        retriever.encode_queries(warm_query_set)
    configure_run(
        baseline["collection"]["collection_name"],
        baseline_docs,
        baseline_chunks,
        allow_staging=False,
        clear_query_cache=not args.warm_query_cache,
        manifest_documents=baseline["documents"],
    )
    if args.exact_flat:
        retriever.prepare_exact_vector_index()
    baseline_run = evaluate(
        "baseline_7245_issue_split" if args.issue_split else "baseline_7245_cold",
        retriever,
        cases,
        as_of=as_of,
        domain_docs=domain_docs,
        issue_split=args.issue_split,
    )
    configure_run(
        candidate["candidate_collection"],
        candidate_docs,
        candidate_chunks,
        allow_staging=True,
        clear_query_cache=not args.warm_query_cache,
        manifest_documents=candidate["documents"],
    )
    if args.exact_flat:
        retriever.prepare_exact_vector_index()
    candidate_run = evaluate(
        "candidate_3000_issue_split" if args.issue_split else "candidate_3000_cold",
        retriever,
        cases,
        as_of=as_of,
        domain_docs=domain_docs,
        issue_split=args.issue_split,
    )
    after_pointer = (
        args.chroma_path.joinpath("active_core_collection.txt")
        .read_text(encoding="utf-8")
        .strip()
    )

    report = {
        "schema_version": "legal-corpus-golden-benchmark-v1",
        "generated_at": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
        "golden": {
            "path": str(args.golden.resolve()),
            "sha256": sha256(args.golden.resolve()),
            "case_count": len(cases),
            "case_offset": args.offset,
            "status": golden.get("status"),
            "legal_as_of": str(candidate["legal_as_of"]),
        },
        "baseline_manifest": {
            "path": str(args.baseline_manifest.resolve()),
            "file_sha256": sha256(args.baseline_manifest.resolve()),
            "manifest_sha256": baseline.get("manifest_sha256"),
            "collection": baseline["collection"]["collection_name"],
            "document_count": len(baseline_docs),
            "chunk_count": len(baseline_chunks),
        },
        "candidate_manifest": {
            "path": str(args.candidate_manifest.resolve()),
            "file_sha256": sha256(args.candidate_manifest.resolve()),
            "manifest_sha256": candidate.get("manifest_sha256"),
            "collection": candidate["candidate_collection"],
            "document_count": len(candidate_docs),
            "chunk_count": len(candidate_chunks),
        },
        "retrieval_contract": {
            "same_search_request": True,
            "same_model": True,
            "same_chunking": True,
            "same_embedding": True,
            "same_reranker": True,
            "same_prompt": True,
            "lexical_and_vector_same_manifest": True,
            "candidate_staging_benchmark_only": True,
            "issue_split_mirrors_ask_pipeline": bool(args.issue_split),
            "warmup_queries": warmup_count,
            "warmup_symmetric": bool(warmup_count),
            "query_embedding_warmup": bool(args.warm_query_cache),
            "exact_flat_backend": bool(args.exact_flat),
            "candidate_hydration_cache": bool(args.hydration_cache),
            "bounded_exact_rows_cache": True,
            "candidate_exact_packet_cache": True,
            "active_pointer_before": before_pointer,
            "active_pointer_after": after_pointer,
            "active_pointer_changed": before_pointer != after_pointer,
        },
        "runs": {"baseline": baseline_run, "candidate": candidate_run},
        "comparison": {
            "recall_at_10_delta": round(
                candidate_run["recall_at_10"] - baseline_run["recall_at_10"], 5
            ),
            "direct_source_top5_delta": round(
                candidate_run["direct_source_top5"]
                - baseline_run["direct_source_top5"],
                5,
            ),
            "mrr_delta": round(candidate_run["mrr"] - baseline_run["mrr"], 5),
            "p95_ms_delta": round(candidate_run["p95_ms"] - baseline_run["p95_ms"], 3),
            "wrong_scope_delta": candidate_run["wrong_scope_result_count"]
            - baseline_run["wrong_scope_result_count"],
            "errors_delta": candidate_run["errors"] - baseline_run["errors"],
        },
        "activation_decision": {
            "decision": "PENDING_ANSWER_AND_ROLLBACK_GATES",
            "reason": "Retrieval benchmark is recorded without changing active pointer; answer-level and rollback rehearsal gates remain required before activation.",
        },
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "baseline": baseline_run["recall_at_10"],
                "candidate": candidate_run["recall_at_10"],
                "p95_baseline_ms": baseline_run["p95_ms"],
                "p95_candidate_ms": candidate_run["p95_ms"],
                "active_pointer_changed": report["retrieval_contract"][
                    "active_pointer_changed"
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
