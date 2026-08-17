"""Run checksum-bound M5 hybrid retrieval experiments on candidate 3,000."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_candidate_golden import (  # noqa: E402
    DOMAIN_MAP,
    configure_environment,
    evaluate,
    load_payload,
    sha256,
)


SCHEMA_VERSION = "legal-m5-hybrid-experiments-v1"
DEFAULT_GOLDEN = (
    ROOT
    / "outputs"
    / "019fe6cd-c481-70c0-8c58-3f69816592fc"
    / "golden-294-live"
    / "golden-1000-residence-remap-proposal.json"
)
DEFAULT_CANDIDATE = (
    ROOT
    / "reports"
    / "corpus-thinning-remediated-v2"
    / "legal-serving-candidate-3000-v1.json"
)
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "m5-hybrid-retrieval"


def _json_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _source_hashes() -> dict[str, str]:
    paths = (
        ROOT / "api" / "legal_retrieval_quality.py",
        ROOT / "api" / "legal_query_understanding.py",
        ROOT / "scripts" / "legal_search_server.py",
        ROOT / "scripts" / "benchmark_candidate_golden.py",
        Path(__file__).resolve(),
    )
    return {
        str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
        for path in paths
    }


def _config(
    *,
    vector_k: int,
    lexical_k: int,
    fusion: str,
    vector_weight: float = 0.6,
    lexical_weight: float = 0.4,
) -> dict[str, Any]:
    return {
        "candidate_count": vector_k,
        "lexical_candidate_count": lexical_k,
        "fusion_strategy": fusion,
        "vector_weight": vector_weight,
        "lexical_weight": lexical_weight,
        "ranking_strategy": "legacy_stack",
        "enable_learned_reranker": False,
        "result_limit": 10,
        "allow_broad_fallback": True,
        "retrieval_tier": "core",
        "parent_expansion": "baseline_enabled",
        "neighbor_expansion": "baseline_enabled",
    }


def _winner(runs: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [
        run
        for run in runs
        if int(run.get("errors") or 0) == 0
        and int(run.get("outside_manifest_result_count") or 0) == 0
    ]
    if not valid:
        raise RuntimeError("m5_no_valid_experiment")
    return max(
        valid,
        key=lambda run: (
            float(run.get("recall_at_10") or 0.0),
            float(run.get("mrr") or 0.0),
            float(run.get("direct_source_top5") or 0.0),
            -float(run.get("p95_retrieval_ms") or 0.0),
        ),
    )


def _load_or_run(
    *,
    experiment_id: str,
    stage: str,
    parent_experiment_id: str | None,
    changed_variable: str,
    config: dict[str, Any],
    retriever: Any,
    cases: list[dict[str, Any]],
    legal_as_of: date,
    domain_docs: dict[str, set[int]],
    allowed_document_ids: set[int],
    output_dir: Path,
    protocol_sha256: str,
    resume: bool,
) -> dict[str, Any]:
    path = output_dir / "experiments" / f"{experiment_id}.json"
    if resume and path.is_file():
        existing = load_payload(path)
        if (
            existing.get("protocol_sha256") == protocol_sha256
            and existing.get("search_config") == config
            and int(existing.get("case_count") or 0) == len(cases)
        ):
            print(
                json.dumps(
                    {"experiment": experiment_id, "status": "resumed"},
                    ensure_ascii=True,
                ),
                flush=True,
            )
            return existing
        raise RuntimeError(f"m5_checkpoint_fingerprint_mismatch:{experiment_id}")

    retriever._exact_rows_cache.clear()
    run = evaluate(
        experiment_id,
        retriever,
        cases,
        as_of=legal_as_of,
        domain_docs=domain_docs,
        allowed_document_ids=allowed_document_ids,
        issue_split=False,
        search_config=config,
    )
    record = {
        **run,
        "search_config": config,
        "schema_version": "legal-m5-experiment-result-v1",
        "experiment_id": experiment_id,
        "stage": stage,
        "parent_experiment_id": parent_experiment_id,
        "changed_variable": changed_variable,
        "protocol_sha256": protocol_sha256,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(path, record)
    print(
        json.dumps(
            {
                "experiment": experiment_id,
                "status": "completed",
                "recall_at_10": record["recall_at_10"],
                "mrr": record["mrr"],
                "p95_retrieval_ms": record["p95_retrieval_ms"],
            },
            ensure_ascii=True,
        ),
        flush=True,
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    golden_path = args.golden.resolve()
    candidate_path = args.candidate_manifest.resolve()
    chroma_path = args.chroma_path.resolve()
    output_dir = args.output_dir.resolve()
    golden = load_payload(golden_path)
    candidate = load_payload(candidate_path)
    cases = list(golden.get("cases") or [])
    if args.limit:
        cases = cases[: max(0, args.limit)]
    if not cases:
        raise RuntimeError("m5_golden_cases_required")
    if len(candidate.get("documents") or []) != 3000:
        raise RuntimeError("m5_candidate_document_count_invalid")
    candidate_chunks = {
        int(chunk_id)
        for document in candidate["documents"]
        for chunk_id in (document.get("expected_chunk_ids") or [])
    }
    if len(candidate_chunks) != 88209:
        raise RuntimeError("m5_candidate_chunk_count_invalid")

    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip()
    candidate_file_sha = sha256(candidate_path)
    configure_environment(
        candidate_path,
        str(candidate["candidate_collection"]),
        candidate_file_sha,
    )
    os.environ.pop("LEGAL_SERVING_MANIFEST_POINTER", None)
    os.environ.pop("LEGAL_SERVING_MANIFEST_SHA256", None)

    import chromadb
    import scripts.legal_search_server as legal_search_server
    from scripts.legal_search_server import LegalRetriever

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(str(candidate["candidate_collection"]))
    if collection.count() != 88209:
        raise RuntimeError("m5_candidate_vector_count_invalid")
    retriever = LegalRetriever()
    legal_search_server.retriever = retriever
    retriever._collection = collection
    retriever._source_collection = collection
    candidate_docs = {int(row["document_id"]) for row in candidate["documents"]}
    retriever._serving_allowed_document_ids = candidate_docs
    retriever._shadow_allowed_chunk_ids = {
        "core": set(candidate_chunks),
        "expanded": set(candidate_chunks),
    }
    domain_docs: dict[str, set[int]] = defaultdict(set)
    for row in candidate["documents"]:
        domain = str(row.get("domain") or "")
        domain_docs[domain].add(int(row["document_id"]))
    retriever._serving_domain_document_ids = domain_docs
    retriever._benchmark_allow_staging = True
    retriever._benchmark_cache_exact_rows = True
    retriever._exact_vector_search_enabled = False
    retriever._hydration_cache_rows = None
    retriever._hydration_cache_parents = None
    retriever._hydration_cache_exact_index = None
    retriever._hydration_cache_article_chunk_counts = None
    retriever._hydration_cache_article_chunks = None
    retriever.prewarm()

    queries = [
        str((case.get("questions") or {}).get("citizen") or "").strip()
        for case in cases
    ]
    retriever.encode_queries(list(dict.fromkeys(query for query in queries if query)))
    source_hashes = _source_hashes()
    protocol = {
        "schema_version": SCHEMA_VERSION,
        "golden_sha256": sha256(golden_path),
        "candidate_manifest_file_sha256": candidate_file_sha,
        "candidate_manifest_sha256": candidate.get("manifest_sha256"),
        "candidate_collection": candidate["candidate_collection"],
        "document_count": len(candidate_docs),
        "chunk_vector_count": len(candidate_chunks),
        "case_count": len(cases),
        "legal_as_of": str(candidate["legal_as_of"]),
        "embedding_fingerprint": retriever._model_fingerprint,
        "source_hashes": source_hashes,
        "frozen": {
            "dataset": True,
            "embedding_model": True,
            "prompt": "not_applicable_retrieval_only",
            "reranker": "baseline_learned_disabled",
            "parent_expansion": "baseline_enabled",
            "neighbor_expansion": "baseline_enabled",
            "result_limit": 10,
            "broad_fallback": True,
            "query_embedding_cache": "prewarmed_for_all_experiments",
            "exact_rows_cache": "cleared_before_each_experiment",
        },
    }
    protocol_sha256 = _json_sha(protocol)
    protocol["protocol_sha256"] = protocol_sha256
    _write_json(output_dir / "m5_protocol.json", protocol)

    resume = not args.no_resume
    legal_as_of = date.fromisoformat(str(candidate["legal_as_of"]))
    all_runs: list[dict[str, Any]] = []

    baseline_config = _config(
        vector_k=150, lexical_k=60, fusion="legacy_stack"
    )
    baseline = _load_or_run(
        experiment_id="m5-e00-baseline-control",
        stage="control",
        parent_experiment_id=None,
        changed_variable="none",
        config=baseline_config,
        retriever=retriever,
        cases=cases,
        legal_as_of=legal_as_of,
        domain_docs=domain_docs,
        allowed_document_ids=candidate_docs,
        output_dir=output_dir,
        protocol_sha256=protocol_sha256,
        resume=resume,
    )
    all_runs.append(baseline)

    vector_runs: list[dict[str, Any]] = []
    for vector_k in (10, 20, 30, 50):
        run = _load_or_run(
            experiment_id=f"m5-e1-vector-k{vector_k}",
            stage="vector_top_k",
            parent_experiment_id=baseline["experiment_id"],
            changed_variable="candidate_count",
            config=_config(
                vector_k=vector_k, lexical_k=60, fusion="legacy_stack"
            ),
            retriever=retriever,
            cases=cases,
            legal_as_of=legal_as_of,
            domain_docs=domain_docs,
            allowed_document_ids=candidate_docs,
            output_dir=output_dir,
            protocol_sha256=protocol_sha256,
            resume=resume,
        )
        vector_runs.append(run)
        all_runs.append(run)
    vector_winner = _winner(vector_runs)
    vector_k = int(vector_winner["search_config"]["candidate_count"])

    lexical_runs: list[dict[str, Any]] = []
    for lexical_k in (10, 20, 30, 50):
        run = _load_or_run(
            experiment_id=f"m5-e2-lexical-k{lexical_k}",
            stage="lexical_top_k",
            parent_experiment_id=vector_winner["experiment_id"],
            changed_variable="lexical_candidate_count",
            config=_config(
                vector_k=vector_k,
                lexical_k=lexical_k,
                fusion="legacy_stack",
            ),
            retriever=retriever,
            cases=cases,
            legal_as_of=legal_as_of,
            domain_docs=domain_docs,
            allowed_document_ids=candidate_docs,
            output_dir=output_dir,
            protocol_sha256=protocol_sha256,
            resume=resume,
        )
        lexical_runs.append(run)
        all_runs.append(run)
    lexical_winner = _winner(lexical_runs)
    lexical_k = int(lexical_winner["search_config"]["lexical_candidate_count"])

    fusion_specs = (
        ("m5-e3-fusion-rrf", "rrf", 0.6, 0.4, lexical_winner["experiment_id"], "fusion_strategy"),
        ("m5-e3-fusion-weighted-060-040", "weighted", 0.6, 0.4, lexical_winner["experiment_id"], "fusion_strategy"),
        ("m5-e3-fusion-weighted-070-030", "weighted", 0.7, 0.3, "m5-e3-fusion-weighted-060-040", "fusion_weights"),
        ("m5-e3-fusion-weighted-050-050", "weighted", 0.5, 0.5, "m5-e3-fusion-weighted-060-040", "fusion_weights"),
    )
    fusion_runs: list[dict[str, Any]] = []
    for experiment_id, fusion, vector_weight, lexical_weight, parent_id, changed in fusion_specs:
        run = _load_or_run(
            experiment_id=experiment_id,
            stage="fusion",
            parent_experiment_id=parent_id,
            changed_variable=changed,
            config=_config(
                vector_k=vector_k,
                lexical_k=lexical_k,
                fusion=fusion,
                vector_weight=vector_weight,
                lexical_weight=lexical_weight,
            ),
            retriever=retriever,
            cases=cases,
            legal_as_of=legal_as_of,
            domain_docs=domain_docs,
            allowed_document_ids=candidate_docs,
            output_dir=output_dir,
            protocol_sha256=protocol_sha256,
            resume=resume,
        )
        fusion_runs.append(run)
        all_runs.append(run)
    final_winner = _winner(fusion_runs)

    pointer_after = pointer_path.read_text(encoding="utf-8").strip()
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass"
        if all(
            int(run.get("errors") or 0) == 0
            and int(run.get("outside_manifest_result_count") or 0) == 0
            for run in all_runs
        )
        and pointer_before == pointer_after
        else "fail",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": protocol,
        "experiment_count": len(all_runs),
        "experiments": [
            {
                key: run.get(key)
                for key in (
                    "experiment_id",
                    "stage",
                    "parent_experiment_id",
                    "changed_variable",
                    "search_config",
                    "case_count",
                    "recall_at_10",
                    "mrr",
                    "direct_source_top5",
                    "p50_retrieval_ms",
                    "p95_retrieval_ms",
                    "errors",
                    "wrong_scope_result_count",
                    "outside_manifest_result_count",
                )
            }
            for run in all_runs
        ],
        "stage_winners": {
            "vector_top_k": vector_winner["experiment_id"],
            "lexical_top_k": lexical_winner["experiment_id"],
            "fusion": final_winner["experiment_id"],
        },
        "recommended_config": final_winner["search_config"],
        "selection_rule": [
            "errors=0 and outside_manifest_result_count=0",
            "max recall_at_10",
            "max mrr",
            "max direct_source_top5",
            "min p95_retrieval_ms",
        ],
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_changed": pointer_before != pointer_after,
        "activation_performed": False,
    }
    final_path = output_dir / "m5_hybrid_experiments_v1.json"
    _write_json(final_path, summary)
    checksums = {
        "m5_protocol.json": sha256(output_dir / "m5_protocol.json"),
        "m5_hybrid_experiments_v1.json": sha256(final_path),
        **{
            f"experiments/{run['experiment_id']}.json": sha256(
                output_dir / "experiments" / f"{run['experiment_id']}.json"
            )
            for run in all_runs
        },
    }
    _write_json(output_dir / "m5_checksums.json", checksums)
    if not args.limit:
        final_path.chmod(final_path.stat().st_mode & ~stat.S_IWRITE)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "output": str(final_path),
                "experiments": len(all_runs),
                "recommended_config": summary["recommended_config"],
                "active_pointer_changed": summary["active_pointer_changed"],
            },
            ensure_ascii=True,
        )
    )
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
