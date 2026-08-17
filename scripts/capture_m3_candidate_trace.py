#!/usr/bin/env python3
"""Capture immutable criterion-29 traces on the isolated 3,000-doc candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_retrieval_trace import (
    M3_REQUIRED_TRACE_FIELDS,
    M3_TRACE_SCHEMA_VERSION,
    validate_m3_retrieval_trace,
)
from api.legal_serving_scope import file_sha256

DEFAULT_MANIFEST = ROOT / "reports" / "m1-freeze" / "candidate_manifest.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_HISTORICAL = (
    ROOT / "reports" / "corpus-thinning-remediated-v2" / "golden-benchmark-1000.json"
)
DEFAULT_OUTPUT = (
    ROOT / "reports" / "m3-retrieval-baseline"
    / "candidate-3000-retrieval-baseline-traces-v1.json"
)
DEFAULT_REPORT = ROOT / "reports" / "m3-retrieval-baseline" / "m3_acceptance_report.json"

TRACE_QUERIES = (
    {
        "query_id": "m3-ho-tich-001",
        "domain": "ho_tich_chung_thuc",
        "query": "Thủ tục đăng ký khai sinh cần giấy tờ gì và nộp tại đâu?",
    },
    {
        "query_id": "m3-dat-dai-001",
        "domain": "dat_dai_xay_dung",
        "query": "Thủ tục cấp giấy chứng nhận quyền sử dụng đất lần đầu gồm những bước nào?",
    },
    {
        "query_id": "m3-cu-tru-001",
        "domain": "cu_tru_an_ninh",
        "query": "Người dân đăng ký tạm trú cần hồ sơ gì và thời hạn giải quyết bao lâu?",
    },
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _canonical_sha256(payload: dict[str, Any]) -> str:
    value = dict(payload)
    value.pop("artifact_sha256", None)
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _immutable_json(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError("m3_trace_artifact_version_already_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
    path.chmod(stat.S_IREAD)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * ratio))))
    return round(ordered[index], 3)


def _configure_candidate(manifest_path: Path, collection: str, device: str) -> None:
    os.environ["LEGAL_BENCHMARK_MODE"] = "true"
    os.environ["LEGAL_BENCHMARK_SERVING_MANIFEST"] = str(manifest_path.resolve())
    os.environ["LEGAL_BENCHMARK_SERVING_MANIFEST_FILE_SHA256"] = file_sha256(manifest_path)
    os.environ["LEGAL_CHROMA_COLLECTION"] = collection
    os.environ["LEGAL_CHROMA_SOURCE_COLLECTION"] = collection
    os.environ["LEGAL_EMBED_DEVICE"] = device
    os.environ.pop("LEGAL_SERVING_MANIFEST", None)
    os.environ.pop("LEGAL_SERVING_MANIFEST_POINTER", None)
    os.environ.pop("LEGAL_SERVING_MANIFEST_REQUIRED", None)
    os.environ.pop("LEGAL_HYDRATION_CACHE_PATH", None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--historical-baseline", type=Path, default=DEFAULT_HISTORICAL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args()

    manifest = _load(args.manifest.resolve())
    collection_name = str(manifest.get("candidate_collection") or "")
    if manifest.get("selected_document_count") != 3000:
        raise RuntimeError("m3_candidate_document_count_not_3000")
    if not collection_name:
        raise RuntimeError("m3_candidate_collection_missing")
    before_pointer = args.chroma_path.joinpath("active_core_collection.txt").read_text(
        encoding="utf-8"
    ).strip()
    _configure_candidate(args.manifest.resolve(), collection_name, args.device)

    import chromadb
    from scripts.legal_search_server import LegalRetriever, SearchRequest

    client = chromadb.PersistentClient(path=str(args.chroma_path.resolve()))
    collection = client.get_collection(collection_name)
    if int(collection.count()) != int(manifest.get("expected_vector_count") or 0):
        raise RuntimeError("m3_candidate_vector_count_mismatch")

    historical = _load(args.historical_baseline.resolve())
    historical_candidate = (historical.get("runs") or {}).get("candidate") or {}
    retriever = LegalRetriever()
    retriever.prewarm()
    legal_as_of = date.fromisoformat(str(manifest.get("legal_as_of") or "2026-08-14"))

    records: list[dict[str, Any]] = []
    total_latencies: list[float] = []
    for query in TRACE_QUERIES:
        started = perf_counter()
        response = retriever.search(SearchRequest(
            query=query["query"],
            limit=10,
            candidate_count=150,
            lexical_candidate_count=60,
            as_of=legal_as_of,
            domain=query["domain"],
            retrieval_tier="core",
            include_trace=True,
            allow_broad_fallback=True,
            ranking_strategy="legacy_stack",
            enable_learned_reranker=False,
            audience="system",
            request_id="m3-candidate-3000-v1",
            issue_id=query["query_id"],
            query_id=f"{query['query_id']}-q1",
            issue_domain=query["domain"],
        ))
        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        trace = response.get("trace") or {}
        validation = validate_m3_retrieval_trace(trace)
        if not trace["vector_candidates"]:
            raise RuntimeError(f"m3_vector_candidates_empty:{query['query_id']}")
        if not trace["lexical_candidates"]:
            raise RuntimeError(f"m3_lexical_candidates_empty:{query['query_id']}")
        if not trace["fusion_candidates"]:
            raise RuntimeError(f"m3_fusion_candidates_empty:{query['query_id']}")
        if not trace["reranker_candidates"]["input"]:
            raise RuntimeError(f"m3_reranker_candidates_empty:{query['query_id']}")
        if not trace["final_evidence"]:
            raise RuntimeError(f"m3_final_evidence_empty:{query['query_id']}")
        records.append({
            "query_id": query["query_id"],
            "domain": query["domain"],
            "trace_validation": validation,
            "elapsed_ms": elapsed_ms,
            "trace": trace,
        })
        total_latencies.append(float(trace["stage_latency_ms"]["total"]))

    after_pointer = args.chroma_path.joinpath("active_core_collection.txt").read_text(
        encoding="utf-8"
    ).strip()
    pipeline_files = (
        ROOT / "scripts" / "legal_search_server.py",
        ROOT / "api" / "legal_retrieval_quality.py",
        ROOT / "api" / "legal_retrieval_trace.py",
    )
    payload: dict[str, Any] = {
        "schema_version": "legal-m3-candidate-retrieval-baseline-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "criterion_29_trace_baseline_before_future_m3_optimization",
        "candidate": {
            "manifest_path": str(args.manifest.resolve()),
            "manifest_file_sha256": file_sha256(args.manifest.resolve()),
            "manifest_sha256": manifest.get("manifest_sha256"),
            "collection": collection_name,
            "document_count": 3000,
            "chunk_vector_count": int(collection.count()),
        },
        "pipeline_contract": {
            "ranking_strategy": "legacy_stack",
            "learned_reranker": False,
            "candidate_count": 150,
            "lexical_candidate_count": 60,
            "result_limit": 10,
            "retrieval_tier": "core",
            "broad_fallback": True,
            "exact_flat_backend": False,
            "hydration_cache": False,
            "trace_only_change_no_ranking_change": True,
            "source_files": [
                {"path": str(path.relative_to(ROOT)), "sha256": file_sha256(path)}
                for path in pipeline_files
            ],
        },
        "historical_full_baseline": {
            "path": str(args.historical_baseline.resolve()),
            "file_sha256": file_sha256(args.historical_baseline.resolve()),
            "candidate_case_count": historical_candidate.get("case_count"),
            "candidate_recall_at_10": historical_candidate.get("recall_at_10"),
            "candidate_mrr": historical_candidate.get("mrr"),
            "candidate_p50_ms": historical_candidate.get("p50_ms"),
            "candidate_p95_ms": historical_candidate.get("p95_ms"),
        },
        "trace_contract": {
            "schema_version": M3_TRACE_SCHEMA_VERSION,
            "required_fields": list(M3_REQUIRED_TRACE_FIELDS),
            "query_count": len(records),
            "all_queries_pass": all(
                row["trace_validation"]["status"] == "pass" for row in records
            ),
        },
        "latency_summary_ms": {
            "p50_total": round(median(total_latencies), 3),
            "p95_total": _percentile(total_latencies, 0.95),
            "max_total": round(max(total_latencies), 3),
        },
        "active_pointer_before": before_pointer,
        "active_pointer_after": after_pointer,
        "active_pointer_changed": before_pointer != after_pointer,
        "queries": records,
    }
    payload["artifact_sha256"] = _canonical_sha256(payload)
    if payload["active_pointer_changed"]:
        raise RuntimeError("m3_active_pointer_changed")
    _immutable_json(args.output.resolve(), payload)

    gates = {
        "candidate_document_count_3000": manifest.get("selected_document_count") == 3000,
        "candidate_chunk_vector_count_88209": int(collection.count()) == 88209,
        "raw_query": all(bool(row["trace"]["raw_query"]) for row in records),
        "normalized_query": all(bool(row["trace"]["normalized_query"]) for row in records),
        "query_classification": all(bool(row["trace"]["query_classification"]) for row in records),
        "vector_candidates": all(bool(row["trace"]["vector_candidates"]) for row in records),
        "lexical_candidates": all(bool(row["trace"]["lexical_candidates"]) for row in records),
        "fusion_candidates": all(bool(row["trace"]["fusion_candidates"]) for row in records),
        "reranker_candidates": all(bool(row["trace"]["reranker_candidates"]["input"]) for row in records),
        "expanded_evidence": all(isinstance(row["trace"]["expanded_evidence"], dict) for row in records),
        "final_evidence": all(bool(row["trace"]["final_evidence"]) for row in records),
        "latency_each_stage": all(bool(row["trace"]["stage_latency_ms"]) for row in records),
        "active_pointer_unchanged": before_pointer == after_pointer,
        "immutable_trace_artifact": not bool(args.output.resolve().stat().st_mode & stat.S_IWRITE),
    }
    report = {
        "schema_version": "legal-m3-criterion-29-acceptance-v1",
        "status": "pass" if all(gates.values()) else "fail",
        "trace_artifact": str(args.output.resolve()),
        "trace_artifact_file_sha256": file_sha256(args.output.resolve()),
        "trace_artifact_content_sha256": payload["artifact_sha256"],
        "gates": gates,
    }
    _atomic_json(args.report.resolve(), report)
    _atomic_json(args.report.resolve().with_name("m3_checksums.json"), {
        args.output.name: file_sha256(args.output.resolve()),
        args.report.name: file_sha256(args.report.resolve()),
        args.manifest.name: file_sha256(args.manifest.resolve()),
        args.historical_baseline.name: file_sha256(args.historical_baseline.resolve()),
    })
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
