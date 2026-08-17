"""Run the corrected M5 control on Golden-1000 and Hard-negative-100.

The runner is staging-only and never changes the active collection pointer.
It uses per-case legal_as_of, M5 vector/lexical Top-K 20, legacy fusion,
learned reranking disabled, and issue splitting only for explicitly grouped
multi-issue cases.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_retrieval_evaluation import (
    normalize_law_number,
    question_for_case,
    validate_case_temporal_alignment,
)
from scripts.benchmark_candidate_golden import (
    configure_environment,
    evaluate,
    load_payload,
    sha256,
)


DEFAULT_CANDIDATE = ROOT / "reports" / "m1-freeze" / "candidate_manifest.json"
DEFAULT_GOLDEN = ROOT / "notebook_data" / "feature016-golden-1000-approved.json"
DEFAULT_HARD = ROOT / "reports" / "feature016" / "phase-c" / "hard-negatives-v1.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-quality-v2" / "control_report.json"

CONTROL_CONFIG = {
    "candidate_count": 20,
    "lexical_candidate_count": 20,
    "fusion_strategy": "legacy_stack",
    "vector_weight": 0.6,
    "lexical_weight": 0.4,
    "ranking_strategy": "legacy_stack",
    "enable_learned_reranker": False,
    "rerank_top_n": 20,
    "enable_parent_expansion": True,
    "enable_neighbor_expansion": True,
}


def _cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("cases")
    if values is None:
        values = payload.get("examples")
    return [dict(item) for item in (values or [])]


def _json_sha(value: Any) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _quality_gates(run: dict[str, Any]) -> dict[str, bool]:
    per_domain = run.get("per_domain") or {}
    answer_domains = [
        metrics
        for metrics in per_domain.values()
        if int(metrics.get("answer_required_count") or 0) > 0
    ]
    refusal_rate = run.get("correct_refusal_rate")
    return {
        "dataset_integrity": int(run.get("dataset_error_count") or 0) == 0,
        "recall_at_10": float(run.get("recall_at_10") or 0.0) >= 0.95,
        "mrr_at_10": float(run.get("mrr_at_10") or 0.0) >= 0.90,
        "per_domain_recall_at_10": bool(answer_domains)
        and all(float(metrics.get("recall_at_10") or 0.0) >= 0.95 for metrics in answer_domains),
        "correct_refusal_rate": refusal_rate is None or float(refusal_rate) >= 0.99,
        "safety": int(run.get("outside_manifest_result_count") or 0) == 0
        and int(run.get("invalid_evidence_result_count") or 0) == 0,
        "errors": int(run.get("errors") or 0) == 0,
        "warm_p95": float(run.get("p95_retrieval_ms") or 0.0) <= 15_000.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--hard-negative", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Replay only matching case IDs across the supplied datasets.",
    )
    args = parser.parse_args()

    candidate_path = args.candidate_manifest.resolve()
    chroma_path = args.chroma_path.resolve()
    candidate = load_payload(candidate_path)
    documents = list(candidate.get("documents") or [])
    candidate_docs = {int(row["document_id"]) for row in documents}
    candidate_chunks = {
        int(chunk_id)
        for row in documents
        for chunk_id in (row.get("expected_chunk_ids") or row.get("chunk_ids") or [])
    }
    available_laws = {
        normalize_law_number(row.get("law_number")) for row in documents
    }
    collection_name = str(
        candidate.get("candidate_collection") or candidate.get("collection_name") or ""
    )
    if not collection_name or len(candidate_docs) != 3000 or len(candidate_chunks) != 88209:
        raise RuntimeError("retrieval_quality_candidate_manifest_invalid")

    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip()
    # The full benchmark warms close to one thousand unique questions.  The
    # production defaults (1,024 entries / 300 seconds) can expire early
    # vectors before measurement begins on local hardware, turning a declared
    # warm run into a mixed cold/warm run. These overrides are process-local.
    os.environ["LEGAL_RETRIEVAL_CACHE_TTL_SECONDS"] = "7200"
    os.environ["LEGAL_RETRIEVAL_CACHE_MAX_ENTRIES"] = "4096"
    os.environ["LEGAL_EXACT_ROWS_CACHE_TTL_SECONDS"] = "7200"
    os.environ["LEGAL_EXACT_ROWS_CACHE_MAX_ENTRIES"] = "4096"
    configure_environment(candidate_path, collection_name, sha256(candidate_path))
    os.environ.pop("LEGAL_SERVING_MANIFEST_POINTER", None)
    os.environ.pop("LEGAL_SERVING_MANIFEST_SHA256", None)

    import chromadb
    import scripts.legal_search_server as legal_search_server
    from scripts.legal_search_server import LegalRetriever

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(collection_name)
    if collection.count() != len(candidate_chunks):
        raise RuntimeError("retrieval_quality_candidate_vector_count_invalid")
    retriever = LegalRetriever()
    legal_search_server.retriever = retriever
    retriever._collection = collection
    retriever._source_collection = collection
    retriever._serving_allowed_document_ids = set(candidate_docs)
    retriever._shadow_allowed_chunk_ids = {
        "core": set(candidate_chunks),
        "expanded": set(candidate_chunks),
    }
    domain_docs: dict[str, set[int]] = {}
    for row in documents:
        domain_docs.setdefault(str(row.get("domain") or ""), set()).add(int(row["document_id"]))
    retriever._serving_domain_document_ids = domain_docs
    retriever._benchmark_allow_staging = True
    retriever._benchmark_cache_exact_rows = True
    retriever._exact_vector_search_enabled = False
    retriever.prewarm()

    dataset_paths = {
        "golden-1000": args.golden.resolve(),
        "hard-negative-100": args.hard_negative.resolve(),
    }
    loaded = {name: load_payload(path) for name, path in dataset_paths.items()}
    case_sets = {name: _cases(payload) for name, payload in loaded.items()}
    if args.case_id:
        selected_ids = set(args.case_id)
        case_sets = {
            name: [case for case in values if str(case.get("case_id")) in selected_ids]
            for name, values in case_sets.items()
        }
        case_sets = {name: values for name, values in case_sets.items() if values}
        dataset_paths = {
            name: path for name, path in dataset_paths.items() if name in case_sets
        }
        if not case_sets:
            raise RuntimeError("retrieval_quality_requested_case_ids_not_found")
    if args.limit:
        case_sets = {name: values[: args.limit] for name, values in case_sets.items()}
    warm_queries = list(
        dict.fromkeys(
            question_for_case(case)
            for values in case_sets.values()
            for case in values
            if question_for_case(case)
            and not bool(case.get("expected_refusal"))
            and validate_case_temporal_alignment(case) is None
        )
    )
    retriever.encode_queries(warm_queries)

    runs: dict[str, Any] = {}
    for name, cases in case_sets.items():
        retriever._exact_rows_cache.clear()
        runs[name] = evaluate(
            name,
            retriever,
            cases,
            as_of=date.fromisoformat(str(candidate.get("legal_as_of"))),
            domain_docs=domain_docs,
            allowed_document_ids=candidate_docs,
            available_law_numbers=available_laws,
            issue_split=True,
            search_config=CONTROL_CONFIG,
        )
        runs[name]["gates"] = _quality_gates(runs[name])
        runs[name]["status"] = (
            "pass" if all(runs[name]["gates"].values()) else "fail"
        )

    pointer_after = pointer_path.read_text(encoding="utf-8").strip()
    protocol = {
        "metric_contract_version": "legal-retrieval-metrics-v2",
        "candidate_manifest_file_sha256": sha256(candidate_path),
        "candidate_manifest_sha256": candidate.get("manifest_sha256"),
        "candidate_collection": collection_name,
        "document_count": len(candidate_docs),
        "chunk_vector_count": len(candidate_chunks),
        "datasets": {
            name: {"path": str(path), "sha256": sha256(path), "case_count": len(case_sets[name])}
            for name, path in dataset_paths.items()
        },
        "search_config": CONTROL_CONFIG,
        "per_case_legal_as_of": True,
        "multi_issue_split": True,
        "learned_reranker": False,
        "benchmark_cache": {
            "query_vector_max_entries": 4096,
            "query_vector_ttl_seconds": 7200,
            "exact_rows_max_entries": 4096,
            "exact_rows_ttl_seconds": 7200,
            "process_local_only": True,
        },
        "active_pointer_before": pointer_before,
    }
    protocol["protocol_sha256"] = _json_sha(protocol)
    report = {
        "schema_version": "legal-retrieval-quality-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass"
        if all(run["status"] == "pass" for run in runs.values())
        and pointer_before == pointer_after
        else "fail",
        "protocol": protocol,
        "runs": runs,
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_changed": pointer_before != pointer_after,
        "activation_performed": False,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    checksum_path.write_text(f"{sha256(output)}  {output.name}\n", encoding="ascii")
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(output),
                "active_pointer_changed": report["active_pointer_changed"],
                "runs": {
                    name: {
                        "status": run["status"],
                        "recall_at_10": run["recall_at_10"],
                        "mrr_at_10": run["mrr_at_10"],
                        "p95_retrieval_ms": run["p95_retrieval_ms"],
                    }
                    for name, run in runs.items()
                },
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
