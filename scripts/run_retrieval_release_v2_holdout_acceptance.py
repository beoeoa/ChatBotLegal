#!/usr/bin/env python3
"""Score the sealed production holdout with an already-frozen V2 config.

This command is intentionally separate from M5/M6 experiment selection.  It
loads the selected experiment from a passing benchmark report, refuses any
configuration override, and evaluates only the sealed 500-case
``production-holdout`` split.  It never changes a pointer, collection, model
configuration or dataset.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_v2_runtime import V2ServingRuntime
from api.legal_learned_reranker import OptionalCrossEncoderReranker
from api.retrieval_holdout_contracts import (
    aggregate_holdout_receipt,
    validate_public_holdout_envelope,
)
from api.retrieval_release_contracts import canonical_sha256
from scripts.run_retrieval_release_v2_benchmark import (
    _aggregate_summaries,
    _evaluate,
    _sha,
    _verify_reranker_manifest,
)
from scripts.validate_retrieval_eval_suite_v1 import validate_suite


DEFAULT_DIR = ROOT / "reports" / "retrieval-release-v2"
DEFAULT_MANIFEST = DEFAULT_DIR / "legal-retrieval-chunk-manifest-v2-approved-passage-v3.json"
DEFAULT_SERVING_MANIFEST = DEFAULT_DIR / "legal-serving-manifest-v3.json"
DEFAULT_INDEX = DEFAULT_DIR / "legal-retrieval-v2-exact-lexical.sqlite3"
DEFAULT_SUITE = DEFAULT_DIR / "retrieval-eval-suite-v1.json"
DEFAULT_HOLDOUT_ENVELOPE = DEFAULT_DIR / "production-holdout-envelope-v1.json"
DEFAULT_SOURCE_AVAILABILITY = DEFAULT_DIR / "retrieval-eval-source-availability-v2.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"
DEFAULT_OUTPUT = DEFAULT_DIR / "production-holdout-acceptance-v1.json"
DEFAULT_POINTER = "legal_chunks_vnlegal_lal_haiphong_unified_v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def _require_external_custody_bundle(path: Path) -> Path:
    """Prevent hidden holdout content from being stored in this workspace."""

    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError:
        return resolved
    raise RuntimeError("holdout_custody_bundle_must_be_outside_repository")


def _selected_config(report: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    if str(report.get("status") or "").upper() != "PASS":
        raise RuntimeError("selected_benchmark_report_must_be_passed")
    if report.get("active_pointer_changed") is not False:
        raise RuntimeError("selected_benchmark_pointer_changed")
    selected_id = str(report.get("selected_experiment") or "")
    if not selected_id:
        raise RuntimeError("selected_experiment_missing")
    selected = next(
        (
            item for item in report.get("experiments") or []
            if str(item.get("experiment_id") or "") == selected_id
        ),
        None,
    )
    if not isinstance(selected, Mapping):
        raise RuntimeError("selected_experiment_not_found")
    config = dict(selected.get("config") or {})
    if mode == "m5":
        config.update({"reranker": None, "parent_expansion": False, "neighbor_expansion": False})
    elif mode == "m6":
        config["parent_expansion"] = False
        config["neighbor_expansion"] = False
        if int(config.get("rerank_top_n") or 0) not in {20, 30, 50, 100}:
            raise RuntimeError("selected_m6_rerank_top_n_invalid")
    else:  # pragma: no cover - CLI constrains this
        raise RuntimeError("unsupported_holdout_mode")
    return config


def holdout_gates(summary: Mapping[str, Any], *, mode: str) -> dict[str, bool]:
    domains = list((summary.get("per_domain") or {}).values())
    latency_limit = 3_000.0 if mode == "m5" else 15_000.0
    return {
        "case_count_500": int(summary.get("case_count") or 0) == 500,
        "recall_at_10": float(summary.get("recall_at_10") or 0.0) >= 0.95,
        "mrr_at_10": float(summary.get("mrr_at_10") or 0.0) >= 0.90,
        "per_domain_recall_at_10": bool(domains) and all(
            float(item.get("recall_at_10") or 0.0) >= 0.95
            for item in domains
            if int(item.get("answer_required_count") or 0) > 0
        ),
        "correct_refusal_rate": (
            summary.get("correct_refusal_rate") is not None
            and float(summary.get("correct_refusal_rate") or 0.0) >= 0.99
        ),
        "multi_issue_required_source_coverage": (
            int(summary.get("multi_issue_case_count") or 0) > 0
            and float(summary.get("multi_issue_all_required_sources_coverage") or 0.0) >= 0.95
        ),
        "exact_law_article_lookup_100pct": (
            int(summary.get("exact_law_article_case_count") or 0) > 0
            and float(summary.get("exact_law_article_lookup_recall_at_50") or 0.0) >= 1.0
        ),
        "exact_law_article_final_recall_100pct": (
            int(summary.get("exact_law_article_case_count") or 0) > 0
            and float(summary.get("exact_law_article_final_recall_at_10") or 0.0) >= 1.0
        ),
        "latency_p95": float((summary.get("latency_ms") or {}).get("p95") or 0.0) <= latency_limit,
        "safety": (
            int(summary.get("errors") or 0) == 0
            and int(summary.get("outside_manifest_count") or 0) == 0
            and int(summary.get("invalid_temporal_count") or 0) == 0
        ),
    }


def run(
    *,
    mode: str,
    benchmark_report_path: Path,
    manifest_path: Path,
    serving_manifest_path: Path,
    lexical_index_path: Path,
    suite_path: Path,
    holdout_envelope_path: Path,
    source_availability_path: Path,
    chroma_path: Path,
    current_collection: str,
    temporal_collection: str,
    output: Path,
    reranker_model: Path | None = None,
    reranker_manifest: Path | None = None,
    reranker_custom_code: Path | None = None,
    expected_pointer: str = DEFAULT_POINTER,
) -> dict[str, Any]:
    suite_path = _require_external_custody_bundle(suite_path)
    benchmark = _load(benchmark_report_path)
    config = _selected_config(benchmark, mode=mode)
    manifest = _load(manifest_path)
    if manifest.get("approved") is not True or manifest.get("legal_review_attestation") is not True:
        raise RuntimeError("approved_v2_manifest_required")
    envelope = _load(holdout_envelope_path)
    envelope_errors = validate_public_holdout_envelope(envelope, require_unused=True)
    if envelope_errors:
        raise RuntimeError("holdout_envelope_gate_failed:" + ",".join(envelope_errors))
    if _sha(suite_path) != envelope.get("content_sha256"):
        raise RuntimeError("holdout_custody_bundle_checksum_mismatch")
    suite = _load(suite_path)
    validation = validate_suite(suite, require_complete=True)
    if not validation["valid"]:
        raise RuntimeError("retrieval_eval_suite_gate_failed")
    if (suite.get("review_policy") or {}).get("holdout_sealed") is not True:
        raise RuntimeError("production_holdout_not_sealed")
    if suite.get("source_snapshot_sha256") != manifest.get("source_snapshot_sha256"):
        raise RuntimeError("suite_source_snapshot_mismatch")
    if suite.get("manifest_sha256") != manifest.get("manifest_sha256"):
        raise RuntimeError("suite_manifest_mismatch")
    availability = _load(source_availability_path)
    if availability.get("status") != "PASS" or float(availability.get("availability_rate") or 0.0) < 1.0:
        raise RuntimeError("retrieval_eval_source_availability_gate_failed")
    holdout = [
        case for case in suite.get("cases") or []
        if isinstance(case, Mapping) and case.get("split") == "production-holdout"
    ]
    if len(holdout) != 500:
        raise RuntimeError(f"production_holdout_case_count:{len(holdout)}!=500")
    holdout_case_ids_sha256 = canonical_sha256(
        sorted(str(case.get("case_id") or "") for case in holdout)
    )
    if holdout_case_ids_sha256 != envelope.get("case_ids_sha256"):
        raise RuntimeError("holdout_case_ids_checksum_mismatch")

    pointer_path = chroma_path / "active_core_collection.txt"
    pointer_before = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else None
    if pointer_before != expected_pointer:
        raise RuntimeError("active_pointer_not_baseline")

    import scripts.legal_search_server as legal_search_server

    encoder = legal_search_server.retriever
    prewarm_started = perf_counter()
    encoder.prewarm()
    prewarm_ms = round((perf_counter() - prewarm_started) * 1000, 3)
    if str(getattr(encoder, "_embedding_device", "")) not in {"cuda", "cuda:0"}:
        raise RuntimeError("holdout_requires_cuda_embedding_runtime")
    if str(getattr(encoder, "_model_fingerprint", "")) != str(manifest.get("model_artifact_fingerprint") or ""):
        raise RuntimeError("embedding_model_fingerprint_mismatch")

    if mode == "m6":
        if not reranker_model or not reranker_manifest:
            raise RuntimeError("holdout_m6_reranker_required")
        model_evidence = _verify_reranker_manifest(
            reranker_model.resolve(),
            reranker_manifest.resolve(),
            custom_code_path=reranker_custom_code.resolve() if reranker_custom_code else None,
        )
        reranker = OptionalCrossEncoderReranker(
            enabled=True,
            model_path=reranker_model.resolve(),
            model_label=str(model_evidence.get("model_id") or "reranker"),
            custom_code_path=model_evidence.get("custom_code_path"),
            max_candidates=int(config["rerank_top_n"]),
            batch_size=8,
            max_length=512,
        )
        config["reranker"] = reranker
    else:
        model_evidence = None

    runtime = V2ServingRuntime(
        manifest_path=manifest_path,
        serving_manifest_path=serving_manifest_path,
        lexical_index_path=lexical_index_path,
        chroma_path=chroma_path,
        current_collection=current_collection,
        temporal_collection=temporal_collection,
        query_encoder=encoder,
    )
    try:
        cold = _evaluate(runtime, holdout, config)
        warm_runs = [_evaluate(runtime, holdout, config) for _ in range(3)]
        summary = _aggregate_summaries(warm_runs)
        gates = holdout_gates(summary, mode=mode)
        pointer_after = pointer_path.read_text(encoding="utf-8").strip() if pointer_path.is_file() else None
        gates["active_pointer_unchanged"] = pointer_before == pointer_after == expected_pointer
        candidate_sha256 = canonical_sha256({
            "benchmark_report_sha256": _sha(benchmark_report_path),
            "selected_experiment": benchmark.get("selected_experiment"),
            "config": {key: value for key, value in config.items() if key != "reranker"},
            "manifest_sha256": manifest.get("manifest_sha256"),
        })
        report = aggregate_holdout_receipt(
            envelope=envelope,
            candidate_sha256=candidate_sha256,
            summary=summary,
            gates=gates,
        )
    finally:
        runtime.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{_sha(output)}  {output.name}\n", encoding="ascii")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("m5", "m6"), required=True)
    parser.add_argument("--benchmark-report", type=Path, required=True)
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
    parser.add_argument("--reranker-model", type=Path)
    parser.add_argument("--reranker-manifest", type=Path)
    parser.add_argument("--reranker-custom-code", type=Path)
    parser.add_argument("--expected-pointer", default=DEFAULT_POINTER)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    try:
        report = run(
            mode=args.mode,
            benchmark_report_path=args.benchmark_report.resolve(),
            manifest_path=args.manifest.resolve(),
            serving_manifest_path=args.serving_manifest.resolve(),
            lexical_index_path=args.lexical_index.resolve(),
            suite_path=args.suite.resolve(),
            holdout_envelope_path=args.holdout_envelope.resolve(),
            source_availability_path=args.source_availability.resolve(),
            chroma_path=args.chroma_path.resolve(),
            current_collection=args.current_collection,
            temporal_collection=args.temporal_collection,
            output=output,
            reranker_model=args.reranker_model.resolve() if args.reranker_model else None,
            reranker_manifest=args.reranker_manifest.resolve() if args.reranker_manifest else None,
            reranker_custom_code=args.reranker_custom_code.resolve() if args.reranker_custom_code else None,
            expected_pointer=args.expected_pointer,
        )
    except Exception as exc:
        report = {
            "schema_version": "legal-retrieval-production-holdout-acceptance-v1",
            "status": "BLOCKED",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "reason": f"{type(exc).__name__}:{exc}",
            "active_pointer_changed": False,
            "activation_performed": False,
            "database_mutated": False,
            "vector_collections_mutated": False,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output.with_suffix(output.suffix + ".sha256").write_text(f"{_sha(output)}  {output.name}\n", encoding="ascii")
        print(json.dumps({"status": "BLOCKED", "output": str(output), "reason": report["reason"]}, ensure_ascii=True))
        return 2
    print(json.dumps({"status": report["status"], "output": str(output), "gates": report["gates"]}, ensure_ascii=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
