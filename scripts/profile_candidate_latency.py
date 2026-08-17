"""Profile real issue-split batch retrieval for baseline/candidate collections.

This is a diagnostic only: it never changes the active pointer or PostgreSQL
serving scope.  It mirrors ``benchmark_candidate_golden.py`` but enables the
production trace for a bounded list of Golden case IDs, so ANN, embedding and
hydration pressure can be compared before making an optimization.
"""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import os
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def configure(
    manifest: dict,
    collection: str,
    chroma_path: Path,
    *,
    staging: bool,
    hydration_cache: Path | None = None,
) -> dict:
    os.environ["LEGAL_BENCHMARK_MODE"] = "1"
    os.environ["LEGAL_BENCHMARK_SERVING_MANIFEST"] = str(manifest["_path"])
    os.environ["LEGAL_BENCHMARK_SERVING_MANIFEST_FILE_SHA256"] = str(manifest.get("manifest_file_sha256") or "")
    os.environ["LEGAL_CHROMA_COLLECTION"] = collection
    os.environ["LEGAL_CHROMA_SOURCE_COLLECTION"] = collection
    os.environ["LEGAL_CHROMA_PATH"] = str(chroma_path)
    from scripts.legal_search_server import retriever
    client = __import__("chromadb").PersistentClient(path=str(chroma_path))
    retriever._collection = client.get_collection(collection)
    retriever._source_collection = retriever._collection
    retriever._serving_allowed_document_ids = {
        int(row["document_id"]) for row in manifest.get("documents", [])
    } if manifest.get("documents") else {
        int(value) for value in manifest.get("document_ids", [])
    }
    domain_document_ids: dict[str, set[int]] = {}
    for row in manifest.get("documents") or []:
        slug = str(row.get("domain") or "").strip()
        document_id = int(row.get("document_id") or 0)
        if slug and document_id:
            domain_document_ids.setdefault(slug, set()).add(document_id)
    retriever._serving_domain_document_ids = domain_document_ids
    chunks = {
        int(value)
        for row in manifest.get("documents", [])
        for value in (row.get("expected_chunk_ids") or [])
    } if manifest.get("documents") else {
        int(value) for value in manifest.get("expected_chunk_ids", [])
    }
    retriever._shadow_allowed_chunk_ids = {"core": chunks, "expanded": chunks}
    retriever._benchmark_allow_staging = staging
    retriever._benchmark_cache_exact_rows = bool(staging)
    retriever._hydration_cache_rows = None
    retriever._hydration_cache_parents = None
    retriever._hydration_cache_exact_index = None
    retriever._hydration_cache_article_chunk_counts = None
    retriever._hydration_cache_article_chunks = None
    if staging and hydration_cache is not None:
        retriever.load_hydration_cache(
            hydration_cache.resolve(),
            manifest_sha256=str(manifest.get("manifest_sha256") or ""),
        )
    retriever._query_vector_cache.clear()
    retriever._exact_rows_cache.clear()
    return retriever


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--candidate-manifest", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, required=True)
    parser.add_argument("--hydration-cache", type=Path, default=None)
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    golden = load(args.golden.resolve())
    by_id = {str(row.get("case_id")): row for row in golden.get("cases") or []}
    cases = [by_id[item] for item in args.case_id if item in by_id]
    if len(cases) != len(args.case_id):
        missing = sorted(set(args.case_id) - set(by_id))
        raise SystemExit(f"unknown_case_ids:{missing}")
    baseline = load(args.baseline_manifest.resolve())
    candidate = load(args.candidate_manifest.resolve())
    baseline["_path"] = str(args.baseline_manifest.resolve())
    candidate["_path"] = str(args.candidate_manifest.resolve())

    import scripts.legal_search_server as server
    from scripts.legal_search_server import BatchSearchIssue, BatchSearchRequest
    from api.legal_section_grounding import plan_legal_issues

    results: list[dict] = []
    for label, manifest, collection, staging in (
        ("baseline", baseline, baseline["collection"]["collection_name"], False),
        ("candidate", candidate, candidate["candidate_collection"], True),
    ):
        retriever = configure(
            manifest,
            collection,
            args.chroma_path.resolve(),
            staging=staging,
            hydration_cache=args.hydration_cache,
        )
        server.retriever = retriever
        retriever.prewarm()
        # Chroma lazily materializes the HNSW distance layer. A distances-only
        # probe is the same index warm-up production needs; asking for
        # metadata first can hide this cost behind the first citizen query.
        warm_vector = retriever.encode_query("thu tuc hanh chinh cap xa")
        retriever._collection.query(
            query_embeddings=[warm_vector.tolist()],
            n_results=30,
            include=["distances"],
        )
        for case in cases:
            query = str((case.get("questions") or {}).get("citizen") or "").strip()
            planned = [item for item in plan_legal_issues(query, max_issues=8) if str(item.query_text or "").strip()]
            issue_queries = [str(item.query_text).strip() for item in planned] or [query]
            issues = [
                BatchSearchIssue(
                    issue_id=f"{case['case_id']}-{index + 1}",
                    query=item,
                    domain=DOMAIN_MAP.get(str(case.get("domain") or ""), str(case.get("domain") or "")),
                    intent=str(planned[index].intent or "unknown") if index < len(planned) else "unknown",
                )
                for index, item in enumerate(issue_queries)
            ]
            request = BatchSearchRequest(
                request_id=f"profile-{label}-{case['case_id']}",
                as_of=date.fromisoformat(str(case.get("legal_as_of") or golden.get("legal_as_of") or date.today())),
                issues=issues,
                retrieval_tier="core",
                include_trace=True,
                ranking_strategy="legacy_stack",
                enable_learned_reranker=False,
            )
            for repeat in range(max(1, args.repeat)):
                started = perf_counter()
                response = server.search_batch(request)
                elapsed = (perf_counter() - started) * 1000
                results.append({
                    "collection": label,
                    "case_id": case["case_id"],
                    "repeat": repeat + 1,
                    "issue_count": len(issues),
                    "elapsed_ms": round(elapsed, 3),
                    "timing_ms": response.get("timing_ms"),
                    "batch_telemetry": response.get("batch_telemetry"),
                    "issue_timings": [
                        {
                            "issue_id": item.get("issue_id"),
                            "timing_ms": item.get("timing_ms"),
                            "queries": [
                                {
                                    "query_id": query.get("query_id"),
                                    "timing_ms": query.get("timing_ms"),
                                }
                                for query in (item.get("queries") or [])
                            ],
                        }
                        for item in (response.get("issues") or [])
                    ],
                    "issue_traces": [item.get("trace") for item in response.get("issues") or []],
                })
    print(json.dumps({"cases": args.case_id, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
