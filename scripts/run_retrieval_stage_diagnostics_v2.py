"""Measure source/exact/vector/lexical/fusion/expansion stages on hard cases."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_retrieval_evaluation import (
    evaluate_candidate_stage_gate,
    expected_sources_for_case,
    match_expected_sources,
    normalize_law_number,
    question_for_case,
    summarize_stage_diagnostics,
)
from api.legal_exact_retrieval import plan_exact_lookup
from scripts.benchmark_candidate_golden import DOMAIN_MAP, configure_environment, load_payload, sha256
from scripts.backup_legal_retrieval import _database_url
from scripts.run_retrieval_quality_v2 import DEFAULT_CANDIDATE, DEFAULT_CHROMA, DEFAULT_HARD


DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-quality-v2" / "stage_diagnostics_hard100_v2.json"
DEFAULT_SOURCE_GAP = ROOT / "reports" / "retrieval-quality-v2" / "source_gap_manifest.json"
DEFAULT_SOURCE_ARTICLE_INTEGRITY = (
    ROOT / "reports" / "retrieval-quality-v2" / "source_article_integrity_report.json"
)


def _candidate_list(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in (value or []) if isinstance(item, dict)]


def _trace_stage(trace: dict[str, Any], name: str) -> list[dict[str, Any]]:
    if name == "reranked":
        return _candidate_list((trace.get("reranker_candidates") or {}).get("output"))
    if name == "expanded":
        expanded = trace.get("expanded_evidence") or {}
        return (
            _candidate_list(expanded.get("parent_contexts"))
            + _candidate_list(expanded.get("neighbor_candidates"))
            + _candidate_list(expanded.get("fallback_candidates"))
        )
    return _candidate_list(trace.get(name))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--hard-negative", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--source-gap-manifest", type=Path, default=DEFAULT_SOURCE_GAP)
    parser.add_argument(
        "--source-article-integrity",
        type=Path,
        default=DEFAULT_SOURCE_ARTICLE_INTEGRITY,
    )
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    candidate_path = args.candidate_manifest.resolve()
    chroma_path = args.chroma_path.resolve()
    candidate = load_payload(candidate_path)
    documents = list(candidate.get("documents") or [])
    document_ids = {int(row["document_id"]) for row in documents}
    chunk_ids = {
        int(chunk_id)
        for row in documents
        for chunk_id in (row.get("expected_chunk_ids") or row.get("chunk_ids") or [])
    }
    collection_name = str(candidate.get("candidate_collection") or "")
    available_laws = {
        normalize_law_number(row.get("law_number")) for row in documents
    }
    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip()
    os.environ["LEGAL_RETRIEVAL_CACHE_TTL_SECONDS"] = "7200"
    os.environ["LEGAL_RETRIEVAL_CACHE_MAX_ENTRIES"] = "4096"
    os.environ["LEGAL_EXACT_ROWS_CACHE_TTL_SECONDS"] = "7200"
    os.environ["LEGAL_EXACT_ROWS_CACHE_MAX_ENTRIES"] = "4096"
    configure_environment(candidate_path, collection_name, sha256(candidate_path))

    import chromadb
    import scripts.legal_search_server as legal_search_server
    from scripts.legal_search_server import LegalRetriever, SearchRequest, _is_current

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(collection_name)
    if collection.count() != len(chunk_ids):
        raise RuntimeError("stage_diagnostics_candidate_vector_count_invalid")
    retriever = LegalRetriever()
    legal_search_server.retriever = retriever
    retriever._collection = collection
    retriever._source_collection = collection
    retriever._serving_allowed_document_ids = set(document_ids)
    retriever._shadow_allowed_chunk_ids = {"core": set(chunk_ids), "expanded": set(chunk_ids)}
    domain_docs: dict[str, set[int]] = {}
    for row in documents:
        domain_docs.setdefault(str(row.get("domain") or ""), set()).add(int(row["document_id"]))
    retriever._serving_domain_document_ids = domain_docs
    retriever._benchmark_allow_staging = True
    retriever._benchmark_cache_exact_rows = True
    retriever._exact_vector_search_enabled = False
    retriever.prewarm()

    # Read-only article inventory distinguishes absent documents from missing
    # article/chunk structure. It does not alter serving scope.
    from sqlalchemy import bindparam, create_engine, text

    engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            article_inventory = [
                dict(row)
                for row in connection.execute(
                    text(
                        "SELECT DISTINCT d.law_number, a.article_number "
                        "FROM legal_documents d "
                        "JOIN legal_articles a ON a.document_id=d.id "
                        "JOIN legal_article_chunks c ON c.article_id=a.id "
                        "WHERE d.id IN :document_ids"
                    ).bindparams(bindparam("document_ids", expanding=True)),
                    {"document_ids": sorted(document_ids)},
                ).mappings()
            ]
    finally:
        engine.dispose()

    hard = load_payload(args.hard_negative.resolve())
    cases = [dict(item) for item in (hard.get("examples") or [])]
    if args.limit:
        cases = cases[: args.limit]
    retriever.encode_queries([question_for_case(case) for case in cases])
    rows: list[dict[str, Any]] = []
    outside_manifest = 0
    invalid_evidence = 0
    for index, case in enumerate(cases, start=1):
        query = question_for_case(case)
        case_as_of = date.fromisoformat(str(case["legal_as_of"]))
        domain = DOMAIN_MAP.get(str(case.get("domain") or ""))
        started = perf_counter()
        response = retriever.search(
            SearchRequest(
                query=query,
                limit=10,
                candidate_count=50,
                lexical_candidate_count=50,
                as_of=case_as_of,
                as_of_explicit=True,
                benchmark_today=case_as_of,
                domain=domain,
                retrieval_tier="core",
                include_trace=True,
                allow_broad_fallback=True,
                ranking_strategy="legacy_stack",
                fusion_strategy="legacy_stack",
                enable_learned_reranker=False,
                rerank_top_n=50,
                enable_parent_expansion=True,
                enable_neighbor_expansion=True,
            )
        )
        trace = dict(response.get("trace") or {})
        raw_exact_plan = plan_exact_lookup(query)
        vector = _trace_stage(trace, "vector_candidates")
        lexical = _trace_stage(trace, "lexical_candidates")
        fusion = _trace_stage(trace, "fusion_candidates")
        reranked = _trace_stage(trace, "reranked")
        expanded = _trace_stage(trace, "expanded")
        # Safety and final quality are measured on the actual serving payload.
        # The trace projection intentionally omits some validity metadata and
        # must not be passed to ``_is_current``.
        final = _candidate_list(response.get("results"))
        exact = [
            item
            for item in fusion
            if any(
                "exact" in str(source)
                for source in (item.get("retrieval_sources") or [item.get("retrieval_source")])
            )
        ]
        candidate_union = exact + vector[:50] + lexical[:50]
        sources = expected_sources_for_case(case)
        expected_laws = {normalize_law_number(source.get("law_number")) for source in sources}
        planned_laws = {
            normalize_law_number(value)
            for value in (
                raw_exact_plan.law_numbers
                or ((raw_exact_plan.law_number,) if raw_exact_plan.law_number else ())
            )
        }
        exact_expected = bool(
            raw_exact_plan.requires_exact_metadata_lookup
            and planned_laws.intersection(expected_laws)
        )
        source_available = any(law in available_laws for law in expected_laws)
        article_match = match_expected_sources(article_inventory, case, top_k=len(article_inventory))
        classification = response.get("query_classification") or {}
        filtered = _candidate_list(response.get("filtered_candidates"))
        filtered_match = match_expected_sources(filtered, case, top_k=len(filtered))
        row = {
            "case_id": case.get("case_id"),
            "domain": case.get("domain"),
            "source_available": source_available,
            "all_source_documents_available": expected_laws.issubset(available_laws),
            "article_chunk_available": bool(article_match["hit_at_10"]),
            "exact_expected": exact_expected,
            "exact_hit_at_50": bool(match_expected_sources(exact, case, top_k=50)["hit_at_10"]),
            "vector_hit_at_20": bool(match_expected_sources(vector, case, top_k=20)["hit_at_10"]),
            "vector_hit_at_50": bool(match_expected_sources(vector, case, top_k=50)["hit_at_10"]),
            "lexical_hit_at_20": bool(match_expected_sources(lexical, case, top_k=20)["hit_at_10"]),
            "lexical_hit_at_50": bool(match_expected_sources(lexical, case, top_k=50)["hit_at_10"]),
            "candidate_hit_at_50": bool(match_expected_sources(candidate_union, case, top_k=len(candidate_union))["hit_at_10"]),
            "fusion_hit_at_20": bool(match_expected_sources(fusion, case, top_k=20)["hit_at_10"]),
            "fusion_hit_at_50": bool(match_expected_sources(fusion, case, top_k=50)["hit_at_10"]),
            "reranked_hit_at_10": bool(match_expected_sources(reranked, case, top_k=10)["hit_at_10"]),
            "reranker_enabled": False,
            "expanded_hit_at_10": bool(match_expected_sources(expanded, case, top_k=10)["hit_at_10"]),
            "expansion_enabled": True,
            "final_hit_at_10": bool(match_expected_sources(final, case, top_k=10)["hit_at_10"]),
            "filter_rejected": bool(filtered_match["hit_at_10"]),
            "temporal_blocked": classification.get("temporal_error_code") == "TEMPORAL_AS_OF_CONFLICT",
            "response_status": response.get("status"),
            "error_code": response.get("error_code"),
            "latency_ms": round((perf_counter() - started) * 1000, 3),
            "stage_latency_ms": trace.get("stage_latency_ms") or response.get("timing_ms") or {},
        }
        rows.append(row)
        outside_manifest += sum(
            1 for item in final if int(item.get("document_id") or 0) not in document_ids
        )
        invalid_evidence += sum(
            1 for item in final if not _is_current(item, case_as_of, allow_staging=True)
        )
        if index % 10 == 0:
            print(json.dumps({"processed": index, "total": len(cases)}), flush=True)

    summary = summarize_stage_diagnostics(rows)
    pointer_after = pointer_path.read_text(encoding="utf-8").strip()
    summary.update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset_sha256": sha256(args.hard_negative.resolve()),
            "candidate_manifest_file_sha256": sha256(candidate_path),
            "candidate_manifest_sha256": candidate.get("manifest_sha256"),
            "collection": collection_name,
            "search_config": {
                "vector_top_k": 50,
                "lexical_top_k": 50,
                "fusion": "legacy_stack",
                "learned_reranker": False,
                "final_evidence": 10,
            },
            "benchmark_cache": {
                "query_vector_max_entries": 4096,
                "query_vector_ttl_seconds": 7200,
                "exact_rows_max_entries": 4096,
                "exact_rows_ttl_seconds": 7200,
                "process_local_only": True,
            },
            "outside_manifest_result_count": outside_manifest,
            "invalid_evidence_result_count": invalid_evidence,
            "active_pointer_before": pointer_before,
            "active_pointer_after": pointer_after,
            "active_pointer_changed": pointer_before != pointer_after,
            "activation_performed": False,
        }
    )
    source_gap_path = args.source_gap_manifest.resolve()
    source_gap = load_payload(source_gap_path) if source_gap_path.is_file() else None
    article_integrity_path = args.source_article_integrity.resolve()
    article_integrity = (
        load_payload(article_integrity_path)
        if article_integrity_path.is_file()
        else None
    )
    summary["candidate_gate"] = evaluate_candidate_stage_gate(
        summary,
        source_gap,
        article_integrity,
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "candidate_recall_at_50": summary.get("candidate_recall_at_50"),
                "final_recall_at_10": summary.get("final_recall_at_10"),
                "root_causes": summary["root_causes"],
                "active_pointer_changed": summary["active_pointer_changed"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
