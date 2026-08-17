"""Run checksum-bound M6 reranker and expansion experiments on candidate 3,000."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_candidate_golden import (  # noqa: E402
    configure_environment,
    evaluate,
    load_payload,
    sha256,
)
from api.legal_retrieval_evaluation import evaluate_candidate_stage_gate  # noqa: E402

SCHEMA_VERSION = "legal-m6-reranker-expansion-v1"
DEFAULT_GOLDEN = ROOT / "reports" / "feature016" / "phase-c" / "hard-negatives-v1.json"
DEFAULT_CANDIDATE = (
    ROOT
    / "reports"
    / "corpus-thinning-remediated-v2"
    / "legal-serving-candidate-3000-v1.json"
)
DEFAULT_MODEL = Path(r"J:\DevCache\HuggingFace\bge-reranker-v2-m3-953dc6f6f85a")
DEFAULT_MODEL_MANIFEST = (
    ROOT / "reports" / "feature016" / "phase-c-real" / "model-manifest.json"
)
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "m6-reranker-expansion"
DEFAULT_CANDIDATE_GATE = (
    ROOT / "reports" / "retrieval-quality-v2" / "stage_diagnostics_hard100_v2.json"
)
DEFAULT_SOURCE_GAP = (
    ROOT / "reports" / "retrieval-quality-v2" / "source_gap_manifest.json"
)
DEFAULT_SOURCE_ARTICLE_INTEGRITY = (
    ROOT / "reports" / "retrieval-quality-v2" / "source_article_integrity_report.json"
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _json_sha(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_hashes() -> dict[str, str]:
    paths = (
        ROOT / "api" / "legal_learned_reranker.py",
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
    learned: bool,
    rerank_top_n: int,
    parent: bool,
    neighbor: bool,
) -> dict[str, Any]:
    return {
        "candidate_count": 20,
        "lexical_candidate_count": 20,
        "fusion_strategy": "legacy_stack",
        "vector_weight": 0.6,
        "lexical_weight": 0.4,
        "ranking_strategy": "legacy_stack",
        "enable_learned_reranker": learned,
        "rerank_top_n": rerank_top_n,
        "enable_parent_expansion": parent,
        "enable_neighbor_expansion": neighbor,
        "result_limit": 10,
        "allow_broad_fallback": True,
        "retrieval_tier": "core",
    }


def experiment_specs(rerank_top_n: int = 40) -> list[dict[str, Any]]:
    """Return the frozen one-variable M6 matrix used by tests and the runner."""

    specs = [
        {
            "experiment_id": "m6-e00-control-no-expansion",
            "stage": "control",
            "changed_variable": "none",
            "config": _config(
                learned=False, rerank_top_n=40, parent=False, neighbor=False
            ),
        }
    ]
    specs.extend(
        {
            "experiment_id": f"m6-e1-rerank-top-{top_n}",
            "stage": "reranker_top_n",
            "changed_variable": "rerank_top_n",
            "config": _config(
                learned=True, rerank_top_n=top_n, parent=False, neighbor=False
            ),
        }
        for top_n in (20, 30, 50, 100)
    )
    specs.extend(
        (
            {
                "experiment_id": "m6-e2-parent-expansion-only",
                "stage": "expansion",
                "changed_variable": "enable_parent_expansion",
                "config": _config(
                    learned=True,
                    rerank_top_n=rerank_top_n,
                    parent=True,
                    neighbor=False,
                ),
            },
            {
                "experiment_id": "m6-e2-neighbor-expansion-only",
                "stage": "expansion",
                "changed_variable": "enable_neighbor_expansion",
                "config": _config(
                    learned=True,
                    rerank_top_n=rerank_top_n,
                    parent=False,
                    neighbor=True,
                ),
            },
        )
    )
    return specs


def _valid(run: dict[str, Any]) -> bool:
    return all(
        int(run.get(key) or 0) == 0
        for key in (
            "errors",
            "outside_manifest_result_count",
            "invalid_evidence_result_count",
        )
    )


def _winner(runs: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [run for run in runs if _valid(run)]
    if not valid:
        raise RuntimeError("m6_no_safe_reranker_experiment")
    return max(
        valid,
        key=lambda run: (
            float(run.get("recall_at_10") or 0.0),
            float(run.get("mrr") or 0.0),
            float(run.get("direct_source_top5") or 0.0),
            -float(run.get("p95_retrieval_ms") or 0.0),
        ),
    )


def _verify_model(model_path: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = load_payload(manifest_path)
    actual: dict[str, str] = {}
    for name, expected in dict(manifest.get("files") or {}).items():
        path = model_path / name
        if not path.is_file():
            raise RuntimeError(f"m6_model_file_missing:{name}")
        digest = sha256(path)
        if digest.casefold() != str(expected).casefold():
            raise RuntimeError(f"m6_model_checksum_mismatch:{name}")
        actual[name] = digest
    return {
        "model_id": manifest.get("model_id"),
        "revision": manifest.get("revision"),
        "local_path": str(model_path),
        "files": actual,
        "manifest_file_sha256": sha256(manifest_path),
        "verified": True,
    }


def _benchmark_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    cases = list(payload.get("cases") or [])
    if cases:
        return cases
    return [
        {
            "case_id": row.get("case_id"),
            "domain": row.get("domain"),
            "legal_as_of": row.get("legal_as_of"),
            "questions": {"citizen": row.get("question")},
            "expected_sources": list(row.get("positive_sources") or []),
            "hard_negatives": list(row.get("hard_negatives") or []),
        }
        for row in (payload.get("examples") or [])
    ]


def _run(
    *,
    spec: dict[str, Any],
    parent_experiment_id: str | None,
    retriever: Any,
    cases: list[dict[str, Any]],
    legal_as_of: date,
    domain_docs: dict[str, set[int]],
    candidate_docs: set[int],
    output_dir: Path,
    protocol_sha256: str,
    resume: bool,
) -> dict[str, Any]:
    experiment_id = str(spec["experiment_id"])
    config = dict(spec["config"])
    path = output_dir / "experiments" / f"{experiment_id}.json"
    if resume and path.is_file():
        existing = load_payload(path)
        if (
            existing.get("protocol_sha256") == protocol_sha256
            and existing.get("search_config") == config
            and int(existing.get("case_count") or 0) == len(cases)
        ):
            print(
                json.dumps({"experiment": experiment_id, "status": "resumed"}),
                flush=True,
            )
            return existing
        raise RuntimeError(f"m6_checkpoint_fingerprint_mismatch:{experiment_id}")

    retriever._exact_rows_cache.clear()
    run = evaluate(
        experiment_id,
        retriever,
        cases,
        as_of=legal_as_of,
        domain_docs=domain_docs,
        allowed_document_ids=candidate_docs,
        issue_split=False,
        search_config=config,
    )
    record = {
        **run,
        "schema_version": "legal-m6-experiment-result-v1",
        "experiment_id": experiment_id,
        "stage": spec["stage"],
        "parent_experiment_id": parent_experiment_id,
        "changed_variable": spec["changed_variable"],
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
                "p95_reranker_ms": record["p95_reranker_ms"],
            }
        ),
        flush=True,
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--model-manifest", type=Path, default=DEFAULT_MODEL_MANIFEST)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--candidate-gate", type=Path, default=DEFAULT_CANDIDATE_GATE)
    parser.add_argument("--source-gap-manifest", type=Path, default=DEFAULT_SOURCE_GAP)
    parser.add_argument(
        "--source-article-integrity",
        type=Path,
        default=DEFAULT_SOURCE_ARTICLE_INTEGRITY,
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()

    candidate_gate_path = args.candidate_gate.resolve()
    if not candidate_gate_path.is_file():
        raise RuntimeError("m6_candidate_recall_gate_missing")
    source_gap_path = args.source_gap_manifest.resolve()
    if not source_gap_path.is_file():
        raise RuntimeError("m6_source_gap_manifest_missing")
    article_integrity_path = args.source_article_integrity.resolve()
    if not article_integrity_path.is_file():
        raise RuntimeError("m6_source_article_integrity_missing")
    candidate_gate = evaluate_candidate_stage_gate(
        load_payload(candidate_gate_path),
        load_payload(source_gap_path),
        load_payload(article_integrity_path),
    )
    if not candidate_gate["passed"]:
        raise RuntimeError(
            "m6_candidate_recall_gate_failed:"
            + ",".join(candidate_gate["failed_gates"])
        )

    golden_path = args.golden.resolve()
    candidate_path = args.candidate_manifest.resolve()
    model_path = args.model_path.resolve()
    model_manifest_path = args.model_manifest.resolve()
    chroma_path = args.chroma_path.resolve()
    output_dir = args.output_dir.resolve()
    golden = load_payload(golden_path)
    candidate = load_payload(candidate_path)
    cases = _benchmark_cases(golden)
    if args.limit:
        cases = cases[: max(0, args.limit)]
    if not cases:
        raise RuntimeError("m6_golden_cases_required")
    if len(candidate.get("documents") or []) != 3000:
        raise RuntimeError("m6_candidate_document_count_invalid")
    candidate_chunks = {
        int(chunk_id)
        for document in candidate["documents"]
        for chunk_id in (document.get("expected_chunk_ids") or [])
    }
    if len(candidate_chunks) != 88209:
        raise RuntimeError("m6_candidate_chunk_count_invalid")

    model_evidence = _verify_model(model_path, model_manifest_path)
    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip()
    candidate_file_sha = sha256(candidate_path)
    configure_environment(
        candidate_path, str(candidate["candidate_collection"]), candidate_file_sha
    )
    os.environ.pop("LEGAL_SERVING_MANIFEST_POINTER", None)
    os.environ.pop("LEGAL_SERVING_MANIFEST_SHA256", None)
    os.environ["LEGAL_LEARNED_RERANKER_ENABLED"] = "true"
    os.environ["LEGAL_RERANKER_MODEL_PATH"] = str(model_path)
    os.environ["LEGAL_RERANKER_DEVICE"] = "cuda"
    os.environ["LEGAL_RERANKER_WINDOW"] = "100"
    os.environ.setdefault("LEGAL_RERANKER_BATCH_SIZE", "8")
    os.environ.setdefault("LEGAL_RERANKER_MAX_LENGTH", "512")

    import chromadb

    import scripts.legal_search_server as legal_search_server
    from scripts.legal_search_server import LegalRetriever

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(str(candidate["candidate_collection"]))
    if collection.count() != 88209:
        raise RuntimeError("m6_candidate_vector_count_invalid")
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
        domain_docs[str(row.get("domain") or "")].add(int(row["document_id"]))
    retriever._serving_domain_document_ids = domain_docs
    retriever._benchmark_allow_staging = True
    retriever._benchmark_cache_exact_rows = True
    retriever._exact_vector_search_enabled = False
    retriever.prewarm()
    queries = [
        str((case.get("questions") or {}).get("citizen") or "").strip()
        for case in cases
    ]
    retriever.encode_queries(list(dict.fromkeys(query for query in queries if query)))

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
        "reranker_model": model_evidence,
        "source_hashes": _source_hashes(),
        "frozen": {
            "vector_top_k": 20,
            "lexical_top_k": 20,
            "fusion": "legacy_stack",
            "result_limit": 10,
            "dataset_model_prompt": True,
            "active_pointer_mutation_allowed": False,
        },
    }
    protocol_sha256 = _json_sha(protocol)
    protocol["protocol_sha256"] = protocol_sha256
    _write_json(output_dir / "m6_protocol.json", protocol)

    resume = not args.no_resume
    legal_as_of = date.fromisoformat(str(candidate["legal_as_of"]))
    initial_specs = experiment_specs()
    control = _run(
        spec=initial_specs[0],
        parent_experiment_id=None,
        retriever=retriever,
        cases=cases,
        legal_as_of=legal_as_of,
        domain_docs=domain_docs,
        candidate_docs=candidate_docs,
        output_dir=output_dir,
        protocol_sha256=protocol_sha256,
        resume=resume,
    )
    rerank_runs = [
        _run(
            spec=spec,
            parent_experiment_id=control["experiment_id"],
            retriever=retriever,
            cases=cases,
            legal_as_of=legal_as_of,
            domain_docs=domain_docs,
            candidate_docs=candidate_docs,
            output_dir=output_dir,
            protocol_sha256=protocol_sha256,
            resume=resume,
        )
        for spec in initial_specs[1:5]
    ]
    rerank_winner = _winner(rerank_runs)
    winner_top_n = int(rerank_winner["search_config"]["rerank_top_n"])
    expansion_runs = [
        _run(
            spec=spec,
            parent_experiment_id=rerank_winner["experiment_id"],
            retriever=retriever,
            cases=cases,
            legal_as_of=legal_as_of,
            domain_docs=domain_docs,
            candidate_docs=candidate_docs,
            output_dir=output_dir,
            protocol_sha256=protocol_sha256,
            resume=resume,
        )
        for spec in experiment_specs(winner_top_n)[5:]
    ]
    all_runs = [control, *rerank_runs, *expansion_runs]
    pointer_after = pointer_path.read_text(encoding="utf-8").strip()

    control_mrr = float(control.get("mrr") or 0.0)
    winner_mrr = float(rerank_winner.get("mrr") or 0.0)
    mrr_relative = (winner_mrr - control_mrr) / control_mrr if control_mrr else 0.0
    quality_gate = float(rerank_winner.get("recall_at_10") or 0.0) >= float(
        control.get("recall_at_10") or 0.0
    ) and (
        mrr_relative >= 0.05
        or float(rerank_winner.get("direct_source_top5") or 0.0)
        - float(control.get("direct_source_top5") or 0.0)
        >= 0.01
    )
    latency_gate = float(rerank_winner.get("p95_retrieval_ms") or 0.0) <= 3000.0
    safety_gate = all(_valid(run) for run in all_runs)
    execution_pass = (
        safety_gate and pointer_before == pointer_after and len(all_runs) == 7
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if execution_pass else "fail",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": protocol,
        "experiment_count": len(all_runs),
        "criteria": {
            "34_rerank_top_n_20_30_50_100_measured": len(rerank_runs) == 4,
            "35_parent_neighbor_measured_separately": len(expansion_runs) == 2,
            "36_recall_gain_vs_latency_recorded": True,
            "37_no_expired_or_outside_manifest_evidence": safety_gate,
        },
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
                    "p95_retrieval_ms",
                    "p95_reranker_ms",
                    "errors",
                    "outside_manifest_result_count",
                    "invalid_evidence_result_count",
                    "neighbor_candidate_count",
                    "hydrated_parent_count",
                )
            }
            for run in all_runs
        ],
        "reranker_winner": rerank_winner["experiment_id"],
        "recommended_config": rerank_winner["search_config"],
        "gates": {
            "quality": quality_gate,
            "latency_p95_le_3000_ms": latency_gate,
            "safety": safety_gate,
            "mrr_relative_gain": round(mrr_relative, 6),
        },
        "activation_recommendation": (
            "eligible_for_controlled_review"
            if quality_gate and latency_gate and safety_gate
            else "keep_learned_reranker_disabled"
        ),
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_changed": pointer_before != pointer_after,
        "activation_performed": False,
    }
    final_path = output_dir / "m6_reranker_expansion_v1.json"
    _write_json(final_path, summary)
    checksums = {
        "m6_protocol.json": sha256(output_dir / "m6_protocol.json"),
        "m6_reranker_expansion_v1.json": sha256(final_path),
        **{
            f"experiments/{run['experiment_id']}.json": sha256(
                output_dir / "experiments" / f"{run['experiment_id']}.json"
            )
            for run in all_runs
        },
    }
    _write_json(output_dir / "m6_checksums.json", checksums)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "output": str(final_path),
                "activation_recommendation": summary["activation_recommendation"],
            }
        )
    )
    return 0 if execution_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
