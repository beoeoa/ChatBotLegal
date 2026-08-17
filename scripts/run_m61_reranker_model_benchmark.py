"""Checksum-bound M6.1 comparison of local learned reranker models.

The runner is benchmark-only: it never writes a live configuration or active
collection pointer. All runtime models and custom code are local and verified
against pinned SHA-256 manifests before they can be loaded.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_learned_reranker import OptionalCrossEncoderReranker  # noqa: E402
from api.legal_retrieval_evaluation import evaluate_candidate_stage_gate  # noqa: E402
from scripts.benchmark_candidate_golden import (  # noqa: E402
    configure_environment,
    evaluate,
    load_payload,
    sha256,
)

SCHEMA_VERSION = "legal-m61-reranker-model-comparison-v1"
DEFAULT_GOLDEN = ROOT / "reports" / "feature016" / "phase-c" / "hard-negatives-v1.json"
DEFAULT_CANDIDATE = (
    ROOT
    / "reports"
    / "corpus-thinning-remediated-v2"
    / "legal-serving-candidate-3000-v1.json"
)
DEFAULT_CANDIDATE_GATE = (
    ROOT / "reports" / "retrieval-quality-v2" / "stage_diagnostics_hard100_v2.json"
)
DEFAULT_SOURCE_GAP = (
    ROOT / "reports" / "retrieval-quality-v2" / "source_gap_manifest.json"
)
DEFAULT_SOURCE_ARTICLE_INTEGRITY = (
    ROOT / "reports" / "retrieval-quality-v2" / "source_article_integrity_report.json"
)
DEFAULT_BGE_PATH = Path(r"J:\DevCache\HuggingFace\bge-reranker-v2-m3-953dc6f6f85a")
DEFAULT_BGE_MANIFEST = ROOT / "reports" / "feature016" / "phase-c-real" / "model-manifest.json"
DEFAULT_GTE_PATH = Path(r"J:\DevCache\HuggingFace\gte-multilingual-reranker-base-8215cf04918b")
DEFAULT_GTE_CODE_PATH = Path(r"J:\DevCache\HuggingFace\gte-new-impl-40ced75c3017")
DEFAULT_GTE_MANIFEST = ROOT / "reports" / "m61-reranker-model-comparison" / "gte-model-manifest.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "m61-reranker-model-comparison"


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


def _config(*, learned: bool, model_key: str, rerank_top_n: int) -> dict[str, Any]:
    return {
        "candidate_count": 20,
        "lexical_candidate_count": 20,
        "fusion_strategy": "legacy_stack",
        "vector_weight": 0.6,
        "lexical_weight": 0.4,
        "ranking_strategy": "legacy_stack",
        "enable_learned_reranker": learned,
        "model_key": model_key,
        "rerank_top_n": rerank_top_n,
        "enable_parent_expansion": True,
        "enable_neighbor_expansion": True,
        "result_limit": 10,
        "max_length": 512,
        "batch_size": 8,
        "allow_broad_fallback": True,
        "retrieval_tier": "core",
    }


def base_experiment_specs() -> list[dict[str, Any]]:
    return [
        {
            "experiment_id": "m61-e00-control",
            "changed_variable": "learned_reranker_disabled",
            "config": _config(learned=False, model_key="none", rerank_top_n=10),
        },
        {
            "experiment_id": "m61-e01-bge-top10",
            "changed_variable": "learned_reranker_model",
            "config": _config(learned=True, model_key="bge-v2-m3", rerank_top_n=10),
        },
        {
            "experiment_id": "m61-e02-gte-top10",
            "changed_variable": "learned_reranker_model",
            "config": _config(
                learned=True,
                model_key="gte-multilingual-base",
                rerank_top_n=10,
            ),
        },
    ]


def _safe(run: dict[str, Any]) -> bool:
    return all(
        int(run.get(key) or 0) == 0
        for key in (
            "errors",
            "outside_manifest_result_count",
            "invalid_evidence_result_count",
        )
    )


def conditional_gte_top20(gte_top10_run: dict[str, Any]) -> dict[str, Any] | None:
    if float(gte_top10_run.get("p95_retrieval_ms") or 0.0) > 15_000.0:
        return None
    if not _safe(gte_top10_run):
        return None
    return {
        "experiment_id": "m61-e03-gte-top20",
        "changed_variable": "rerank_top_n",
        "config": _config(
            learned=True,
            model_key="gte-multilingual-base",
            rerank_top_n=20,
        ),
    }


def evaluate_gate(control: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    control_mrr = float(control.get("mrr") or 0.0)
    candidate_mrr = float(candidate.get("mrr") or 0.0)
    relative_mrr = (
        (candidate_mrr - control_mrr) / control_mrr if control_mrr else 0.0
    )
    top5_gain = float(candidate.get("direct_source_top5") or 0.0) - float(
        control.get("direct_source_top5") or 0.0
    )
    checks = {
        "p95_retrieval_plus_reranking_le_15000_ms": float(
            candidate.get("p95_retrieval_ms") or 0.0
        )
        <= 15_000.0,
        "recall_at_10_not_below_control": float(
            candidate.get("recall_at_10") or 0.0
        )
        >= float(control.get("recall_at_10") or 0.0),
        "mrr_relative_gain_ge_5pct_or_top5_gain_ge_1pp": (
            relative_mrr >= 0.05 or top5_gain >= 0.01
        ),
        "zero_errors_oom": int(candidate.get("errors") or 0) == 0,
        "zero_outside_manifest": int(
            candidate.get("outside_manifest_result_count") or 0
        )
        == 0,
        "zero_invalid_or_expired_evidence": int(
            candidate.get("invalid_evidence_result_count") or 0
        )
        == 0,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "mrr_relative_gain": round(relative_mrr, 6),
        "top5_absolute_gain": round(top5_gain, 6),
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


def _expected_file_digest(value: Any) -> tuple[str, int | None]:
    if isinstance(value, dict):
        return str(value.get("sha256") or ""), int(value.get("size_bytes") or 0)
    return str(value or ""), None


def _verify_files(root: Path, files: dict[str, Any], *, prefix: str) -> dict[str, Any]:
    verified: dict[str, Any] = {}
    for name, expected_value in files.items():
        expected, expected_size = _expected_file_digest(expected_value)
        path = root / name
        if not path.is_file():
            raise RuntimeError(f"m61_{prefix}_file_missing:{name}")
        if expected_size is not None and path.stat().st_size != expected_size:
            raise RuntimeError(f"m61_{prefix}_size_mismatch:{name}")
        actual = sha256(path)
        if actual.casefold() != expected.casefold():
            raise RuntimeError(f"m61_{prefix}_checksum_mismatch:{name}")
        verified[name] = {"size_bytes": path.stat().st_size, "sha256": actual}
    return verified


def _verify_model(
    model_path: Path,
    manifest_path: Path,
    *,
    custom_code_path: Path | None = None,
) -> dict[str, Any]:
    manifest = load_payload(manifest_path)
    files = _verify_files(
        model_path, dict(manifest.get("files") or {}), prefix="model"
    )
    custom_code_evidence = None
    custom_code = dict(manifest.get("custom_code") or {})
    if custom_code:
        if custom_code_path is None:
            raise RuntimeError("m61_custom_code_path_required")
        code_files = _verify_files(
            custom_code_path,
            dict(custom_code.get("files") or {}),
            prefix="custom_code",
        )
        custom_code_evidence = {
            "repository": custom_code.get("repository"),
            "revision": custom_code.get("revision"),
            "local_path": str(custom_code_path),
            "files": code_files,
        }
    return {
        "model_id": manifest.get("model_id"),
        "revision": manifest.get("revision"),
        "license": manifest.get("license"),
        "local_path": str(model_path),
        "files": files,
        "custom_code": custom_code_evidence,
        "manifest_file_sha256": sha256(manifest_path),
        "verified": True,
    }


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


def _hardware() -> dict[str, Any]:
    result: dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
    }
    try:
        import torch

        result.update(
            {
                "torch": torch.__version__,
                "cuda_available": torch.cuda.is_available(),
                "cuda_version": torch.version.cuda,
                "gpu": torch.cuda.get_device_name(0)
                if torch.cuda.is_available()
                else None,
            }
        )
    except ImportError:
        result["torch"] = None
    return result


def _release_reranker(retriever: Any) -> None:
    retriever._learned_reranker = OptionalCrossEncoderReranker(enabled=False)
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def _make_reranker(
    *, model_key: str, model_path: Path, custom_code_path: Path | None
) -> OptionalCrossEncoderReranker:
    return OptionalCrossEncoderReranker(
        enabled=True,
        model_path=model_path,
        custom_code_path=custom_code_path,
        model_label=model_key,
        max_candidates=20,
        batch_size=8,
        max_length=512,
    )


def _warm_model(
    adapter: OptionalCrossEncoderReranker,
    *,
    query: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    started = perf_counter()
    cold = adapter.rerank(query, candidates, top_n=10)
    cold_wall_ms = (perf_counter() - started) * 1000
    started = perf_counter()
    warmup = adapter.rerank(query, candidates, top_n=10)
    warmup_wall_ms = (perf_counter() - started) * 1000
    if cold.degraded or warmup.degraded:
        raise RuntimeError(
            f"m61_model_warmup_failed:{cold.reason_code or warmup.reason_code}"
        )
    return {
        "cold_start_wall_ms": round(cold_wall_ms, 3),
        "cold_start_reranker_ms": round(cold.latency_ms, 3),
        "warmup_wall_ms": round(warmup_wall_ms, 3),
        "warmup_reranker_ms": round(warmup.latency_ms, 3),
        "device": warmup.device,
        "scored_count": warmup.scored_count,
        "max_length": warmup.max_length,
        "batch_size": warmup.batch_size,
        "degraded": False,
    }


def _run(
    *,
    spec: dict[str, Any],
    retriever: Any,
    cases: list[dict[str, Any]],
    legal_as_of: date,
    domain_docs: dict[str, set[int]],
    candidate_docs: set[int],
    output_dir: Path,
    protocol_sha256: str,
    warmup: dict[str, Any] | None,
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
            print(json.dumps({"experiment": experiment_id, "status": "resumed"}), flush=True)
            return existing
        raise RuntimeError(f"m61_checkpoint_fingerprint_mismatch:{experiment_id}")

    retriever._exact_rows_cache.clear()
    benchmark_config = {
        key: value
        for key, value in config.items()
        if key not in {"model_key", "max_length", "batch_size", "result_limit"}
    }
    run = evaluate(
        experiment_id,
        retriever,
        cases,
        as_of=legal_as_of,
        domain_docs=domain_docs,
        allowed_document_ids=candidate_docs,
        issue_split=False,
        search_config=benchmark_config,
    )
    record = {
        **run,
        "schema_version": "legal-m61-experiment-result-v1",
        "experiment_id": experiment_id,
        "changed_variable": spec["changed_variable"],
        "search_config": config,
        "warmup": warmup,
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
                "direct_source_top5": record["direct_source_top5"],
                "p50_retrieval_ms": record["p50_retrieval_ms"],
                "p95_retrieval_ms": record["p95_retrieval_ms"],
                "p50_reranker_ms": record["p50_reranker_ms"],
                "p95_reranker_ms": record["p95_reranker_ms"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return record


def _calibration_packet(
    retriever: Any, case: dict[str, Any], *, legal_as_of: date
) -> tuple[str, list[dict[str, Any]]]:
    from scripts.legal_search_server import SearchRequest

    query = str((case.get("questions") or {}).get("citizen") or "").strip()
    response = retriever.search(
        SearchRequest(
            query=query,
            limit=10,
            candidate_count=20,
            lexical_candidate_count=20,
            as_of=legal_as_of,
            retrieval_tier="core",
            include_trace=False,
            allow_broad_fallback=True,
            ranking_strategy="legacy_stack",
            fusion_strategy="legacy_stack",
            vector_weight=0.6,
            lexical_weight=0.4,
            enable_learned_reranker=False,
            rerank_top_n=10,
            enable_parent_expansion=True,
            enable_neighbor_expansion=True,
        )
    )
    candidates = [dict(row) for row in response.get("results") or []]
    if len(candidates) < 1:
        raise RuntimeError("m61_warmup_candidates_missing")
    return query, candidates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--bge-path", type=Path, default=DEFAULT_BGE_PATH)
    parser.add_argument("--bge-manifest", type=Path, default=DEFAULT_BGE_MANIFEST)
    parser.add_argument("--gte-path", type=Path, default=DEFAULT_GTE_PATH)
    parser.add_argument("--gte-code-path", type=Path, default=DEFAULT_GTE_CODE_PATH)
    parser.add_argument("--gte-manifest", type=Path, default=DEFAULT_GTE_MANIFEST)
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
        raise RuntimeError("m61_candidate_recall_gate_missing")
    source_gap_path = args.source_gap_manifest.resolve()
    if not source_gap_path.is_file():
        raise RuntimeError("m61_source_gap_manifest_missing")
    article_integrity_path = args.source_article_integrity.resolve()
    if not article_integrity_path.is_file():
        raise RuntimeError("m61_source_article_integrity_missing")
    candidate_gate = evaluate_candidate_stage_gate(
        load_payload(candidate_gate_path),
        load_payload(source_gap_path),
        load_payload(article_integrity_path),
    )
    if not candidate_gate["passed"]:
        raise RuntimeError(
            "m61_candidate_recall_gate_failed:"
            + ",".join(candidate_gate["failed_gates"])
        )

    golden_path = args.golden.resolve()
    candidate_path = args.candidate_manifest.resolve()
    bge_path = args.bge_path.resolve()
    bge_manifest_path = args.bge_manifest.resolve()
    gte_path = args.gte_path.resolve()
    gte_code_path = args.gte_code_path.resolve()
    gte_manifest_path = args.gte_manifest.resolve()
    chroma_path = args.chroma_path.resolve()
    output_dir = args.output_dir.resolve()

    golden = load_payload(golden_path)
    candidate = load_payload(candidate_path)
    cases = _benchmark_cases(golden)
    if args.limit:
        cases = cases[: max(0, args.limit)]
    if not cases:
        raise RuntimeError("m61_golden_cases_required")
    if len(candidate.get("documents") or []) != 3000:
        raise RuntimeError("m61_candidate_document_count_invalid")
    candidate_chunks = {
        int(chunk_id)
        for document in candidate["documents"]
        for chunk_id in (document.get("expected_chunk_ids") or [])
    }
    if len(candidate_chunks) != 88209:
        raise RuntimeError("m61_candidate_chunk_count_invalid")

    model_evidence = {
        "bge-v2-m3": _verify_model(bge_path, bge_manifest_path),
        "gte-multilingual-base": _verify_model(
            gte_path,
            gte_manifest_path,
            custom_code_path=gte_code_path,
        ),
    }
    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip()
    candidate_file_sha = sha256(candidate_path)
    configure_environment(
        candidate_path, str(candidate["candidate_collection"]), candidate_file_sha
    )
    os.environ.pop("LEGAL_SERVING_MANIFEST_POINTER", None)
    os.environ.pop("LEGAL_SERVING_MANIFEST_SHA256", None)
    os.environ["LEGAL_LEARNED_RERANKER_ENABLED"] = "true"
    os.environ["LEGAL_RERANKER_DEVICE"] = "cuda"
    os.environ["LEGAL_RERANKER_MIN_CUDA_FREE_MB"] = "1800"
    os.environ["LEGAL_RERANKER_WINDOW"] = "20"
    os.environ["LEGAL_RERANKER_BATCH_SIZE"] = "8"
    os.environ["LEGAL_RERANKER_MAX_LENGTH"] = "512"
    # Keep the same host while reserving the 6 GB GPU for the cross encoder.
    os.environ["LEGAL_EMBED_DEVICE"] = "cpu"

    import chromadb

    import scripts.legal_search_server as legal_search_server
    from scripts.legal_search_server import LegalRetriever

    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_collection(str(candidate["candidate_collection"]))
    if collection.count() != 88209:
        raise RuntimeError("m61_candidate_vector_count_invalid")
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
        "models": model_evidence,
        "hardware": _hardware(),
        "source_hashes": _source_hashes(),
        "frozen": {
            "vector_top_k": 20,
            "lexical_top_k": 20,
            "fusion": "legacy_stack",
            "final_evidence": 10,
            "max_length": 512,
            "batch_size": 8,
            "parent_expansion": True,
            "neighbor_expansion": True,
            "dataset_prompt_hardware": True,
            "active_pointer_mutation_allowed": False,
            "live_configuration_mutation_allowed": False,
        },
    }
    protocol_sha256 = _json_sha(protocol)
    protocol["protocol_sha256"] = protocol_sha256
    _write_json(output_dir / "m61_protocol.json", protocol)

    legal_as_of = date.fromisoformat(str(candidate["legal_as_of"]))
    query, packet = _calibration_packet(retriever, cases[0], legal_as_of=legal_as_of)
    resume = not args.no_resume
    specs = base_experiment_specs()
    control = _run(
        spec=specs[0],
        retriever=retriever,
        cases=cases,
        legal_as_of=legal_as_of,
        domain_docs=domain_docs,
        candidate_docs=candidate_docs,
        output_dir=output_dir,
        protocol_sha256=protocol_sha256,
        warmup=None,
        resume=resume,
    )

    runs = [control]
    warmups: dict[str, Any] = {}
    for spec, model_path, code_path in (
        (specs[1], bge_path, None),
        (specs[2], gte_path, gte_code_path),
    ):
        _release_reranker(retriever)
        model_key = str(spec["config"]["model_key"])
        adapter = _make_reranker(
            model_key=model_key,
            model_path=model_path,
            custom_code_path=code_path,
        )
        retriever._learned_reranker = adapter
        warmups[model_key] = _warm_model(adapter, query=query, candidates=packet)
        run = _run(
            spec=spec,
            retriever=retriever,
            cases=cases,
            legal_as_of=legal_as_of,
            domain_docs=domain_docs,
            candidate_docs=candidate_docs,
            output_dir=output_dir,
            protocol_sha256=protocol_sha256,
            warmup=warmups[model_key],
            resume=resume,
        )
        runs.append(run)
        if model_key == "gte-multilingual-base":
            top20_spec = conditional_gte_top20(run)
            if top20_spec is not None:
                runs.append(
                    _run(
                        spec=top20_spec,
                        retriever=retriever,
                        cases=cases,
                        legal_as_of=legal_as_of,
                        domain_docs=domain_docs,
                        candidate_docs=candidate_docs,
                        output_dir=output_dir,
                        protocol_sha256=protocol_sha256,
                        warmup=warmups[model_key],
                        resume=resume,
                    )
                )

    _release_reranker(retriever)
    pointer_after = pointer_path.read_text(encoding="utf-8").strip()
    gates = {
        run["experiment_id"]: evaluate_gate(control, run) for run in runs[1:]
    }
    eligible = [run for run in runs[1:] if gates[run["experiment_id"]]["passed"]]
    winner = (
        max(
            eligible,
            key=lambda run: (
                -int(run.get("errors") or 0),
                -int(run.get("outside_manifest_result_count") or 0),
                -int(run.get("invalid_evidence_result_count") or 0),
                float(run.get("recall_at_10") or 0.0),
                float(run.get("mrr") or 0.0),
                float(run.get("direct_source_top5") or 0.0),
                -float(run.get("p95_retrieval_ms") or 0.0),
            ),
        )
        if eligible
        else None
    )
    pointer_unchanged = pointer_before == pointer_after
    execution_pass = (
        len(runs) >= 3
        and all(_safe(run) for run in runs)
        and pointer_unchanged
        and all(
            run.get("search_config", {}).get("candidate_count") == 20
            and run.get("search_config", {}).get("lexical_candidate_count") == 20
            and run.get("search_config", {}).get("fusion_strategy") == "legacy_stack"
            and run.get("search_config", {}).get("result_limit") == 10
            for run in runs
        )
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": "pass" if execution_pass else "fail",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": protocol,
        "experiment_count": len(runs),
        "warmups": warmups,
        "experiments": [
            {
                key: run.get(key)
                for key in (
                    "experiment_id",
                    "changed_variable",
                    "search_config",
                    "case_count",
                    "recall_at_10",
                    "mrr",
                    "direct_source_top5",
                    "p50_retrieval_ms",
                    "p95_retrieval_ms",
                    "p50_reranker_ms",
                    "p95_reranker_ms",
                    "errors",
                    "outside_manifest_result_count",
                    "invalid_evidence_result_count",
                )
            }
            for run in runs
        ],
        "gates": gates,
        "selected_experiment": winner.get("experiment_id") if winner else None,
        "selected_model": (
            winner.get("search_config", {}).get("model_key") if winner else None
        ),
        "decision": (
            "eligible_for_controlled_review"
            if winner
            else "continue_m5_without_learned_reranker"
        ),
        "active_pointer_before": pointer_before,
        "active_pointer_after": pointer_after,
        "active_pointer_changed": not pointer_unchanged,
        "live_configuration_changed": False,
        "activation_performed": False,
        "model_deleted_or_archived": False,
    }
    final_path = output_dir / "m61_reranker_model_benchmark.json"
    _write_json(final_path, summary)
    checksums = {
        "gte-model-manifest.json": sha256(gte_manifest_path),
        "m61_protocol.json": sha256(output_dir / "m61_protocol.json"),
        "m61_reranker_model_benchmark.json": sha256(final_path),
        **{
            f"experiments/{run['experiment_id']}.json": sha256(
                output_dir / "experiments" / f"{run['experiment_id']}.json"
            )
            for run in runs
        },
    }
    _write_json(output_dir / "m61_checksums.json", checksums)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "output": str(final_path),
                "decision": summary["decision"],
                "selected_model": summary["selected_model"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if execution_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
