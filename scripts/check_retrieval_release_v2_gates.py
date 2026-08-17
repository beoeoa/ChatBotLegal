#!/usr/bin/env python3
"""Evaluate the V2 release prerequisites without changing any live state.

This is a release gate report, not an activation command.  Missing artifacts
are reported as blockers instead of being inferred from a baseline/candidate
artifact or from an incomplete evaluation set.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256, validate_inventory_partition
from api.retrieval_serving_manifest_v3 import load_v3_serving_manifest


DEFAULT_DIR = ROOT / "reports" / "retrieval-release-v2"
DEFAULT_EXPECTED_POINTER = "legal_chunks_vnlegal_lal_haiphong_unified_v1"


def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else None


def _sidecar_matches(path: Path) -> bool:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        return False
    parts = sidecar.read_text(encoding="ascii", errors="replace").strip().split()
    return bool(parts) and parts[0].casefold() == file_sha256(path).casefold()


def _artifact(checks: dict[str, Any], *, name: str, path: Path, required: bool = True) -> dict[str, Any]:
    row = {"path": str(path.resolve()), "exists": path.is_file(), "required": required}
    if path.is_file():
        row["sha256"] = file_sha256(path)
        payload = _read(path) if path.suffix.lower() == ".json" else None
        if payload is not None:
            row["status"] = payload.get("status")
    checks[name] = row
    return row


def _reranker_manifest_artifact_pass(
    path: Path | None, *, requires_custom_code: bool
) -> bool:
    """Validate the release-scoped manifest and its detached checksum."""

    if path is None or not path.is_file():
        return False
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.is_file():
        return False
    sidecar_text = sidecar.read_text(encoding="ascii", errors="replace").strip().split()
    if not sidecar_text or sidecar_text[0].casefold() != file_sha256(path).casefold():
        return False
    payload = _read(path)
    if not payload:
        return False
    if (
        payload.get("schema_version") != "legal-retrieval-v2-reranker-manifest-v1"
        or payload.get("release_scope") != "retrieval-release-v2"
        or payload.get("runtime_download_allowed") is not False
        or payload.get("benchmark_only") is not True
        or not str(payload.get("model_id") or "").strip()
        or not str(payload.get("revision") or "").strip()
    ):
        return False
    files = dict(payload.get("files") or {})
    if not files or any(
        not str((value.get("sha256") if isinstance(value, dict) else value) or "")
        or len(str((value.get("sha256") if isinstance(value, dict) else value) or "")) != 64
        for value in files.values()
    ):
        return False
    custom = dict(payload.get("custom_code") or {})
    if requires_custom_code:
        custom_files = dict(custom.get("files") or {})
        return bool(
            str(custom.get("revision") or "").strip()
            and custom_files
            and {"configuration.py", "modeling.py"}.issubset(custom_files)
            and all(
                len(str((value.get("sha256") if isinstance(value, dict) else value) or "")) == 64
                for value in custom_files.values()
            )
        )
    return not custom


def run(*, inventory: Path, source_snapshot: Path, baseline_lock: Path, chunk_verification: Path, quality_report: Path, approved_manifest: Path, serving_manifest: Path, lexical_report: Path, suite_validation: Path, source_availability: Path, current_vector_report: Path, temporal_vector_report: Path, m5_report: Path, stage_diagnostics: Path, final_acceptance: Path, runtime_fingerprint: Path, official_observations: Path, source_gap_review_manifest: Path, active_pointer: Path, output: Path, expected_pointer: str = DEFAULT_EXPECTED_POINTER, bge_reranker_manifest: Path | None = None, gte_reranker_manifest: Path | None = None) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    blockers: list[str] = []
    inventory_payload = _read(inventory)
    if inventory_payload is None:
        blockers.append("inventory_missing")
    else:
        counts = inventory_payload.get("inventory_counts") or inventory_payload.get("status_counts") or {}
        partition_ok = (
            int(inventory_payload.get("source_snapshot_document_count") or 0) == 12_236
            and not validate_inventory_partition(inventory_payload.get("documents") or [])
        )
        checks["inventory_partition"] = {"pass": partition_ok, "counts": counts, "sha256": file_sha256(inventory)}
        if not partition_ok:
            blockers.append("inventory_partition_12236")

    snapshot_payload = _read(source_snapshot)
    snapshot_documents = (snapshot_payload or {}).get("documents")
    snapshot_content_sha = (
        canonical_sha256(snapshot_documents)
        if isinstance(snapshot_documents, list)
        else None
    )
    snapshot_pass = bool(
        snapshot_payload
        and snapshot_payload.get("schema_version") == "legal-source-snapshot-v2"
        and int(snapshot_payload.get("document_count") or 0) == 12_236
        and isinstance(snapshot_documents, list)
        and len(snapshot_documents) == 12_236
        and snapshot_content_sha == snapshot_payload.get("source_snapshot_sha256")
        and _sidecar_matches(source_snapshot)
        and inventory_payload
        and snapshot_payload.get("source_snapshot_sha256") == inventory_payload.get("source_snapshot_sha256")
    )
    checks["source_snapshot"] = {
        "path": str(source_snapshot.resolve()),
        "exists": source_snapshot.is_file(),
        "pass": snapshot_pass,
        "sha256": file_sha256(source_snapshot) if source_snapshot.is_file() else None,
        "content_sha256": snapshot_content_sha,
        "sidecar_matches": _sidecar_matches(source_snapshot),
    }
    if not snapshot_pass:
        blockers.append("source_snapshot_12236")

    lock_payload = _read(baseline_lock)
    lock_pass = bool(
        lock_payload
        and lock_payload.get("schema_version") == "legal-baseline-write-lock-v1"
        and lock_payload.get("passed") is True
        and (lock_payload.get("checks") or {}).get("active_pointer_is_baseline") is True
        and (lock_payload.get("checks") or {}).get("baseline_write_probe_blocked") is True
        and (lock_payload.get("checks") or {}).get("nonbaseline_write_probe_allowed_then_rolled_back") is True
    )
    checks["baseline_write_lock"] = {
        "path": str(baseline_lock.resolve()),
        "exists": baseline_lock.is_file(),
        "pass": lock_pass,
        "sha256": file_sha256(baseline_lock) if baseline_lock.is_file() else None,
    }
    if not lock_pass:
        blockers.append("baseline_write_lock")

    for name, path in (
        ("chunk_verification", chunk_verification),
        ("quality_policy_release_report", quality_report),
        ("approved_manifest", approved_manifest),
        ("serving_manifest_v3", serving_manifest),
        ("lexical_index_report", lexical_report),
        ("suite_validation", suite_validation),
        ("source_availability", source_availability),
        ("current_vector_report", current_vector_report),
        ("temporal_vector_report", temporal_vector_report),
        ("m5_report", m5_report),
        ("m5_stage_diagnostics", stage_diagnostics),
        ("final_acceptance", final_acceptance),
        ("runtime_fingerprint", runtime_fingerprint),
        ("official_observations", official_observations),
        ("source_gap_review_manifest", source_gap_review_manifest),
        ("bge_reranker_manifest", bge_reranker_manifest) if bge_reranker_manifest is not None else ("bge_reranker_manifest", Path("")),
        ("gte_reranker_manifest", gte_reranker_manifest) if gte_reranker_manifest is not None else ("gte_reranker_manifest", Path("")),
    ):
        if str(path) == ".":
            checks[name] = {"path": None, "exists": False, "required": True}
        else:
            _artifact(checks, name=name, path=path)

    reranker_manifests_pass = _reranker_manifest_artifact_pass(
        bge_reranker_manifest, requires_custom_code=False
    ) and _reranker_manifest_artifact_pass(
        gte_reranker_manifest, requires_custom_code=True
    )
    checks["reranker_manifests"] = {
        "pass": reranker_manifests_pass,
        "bge": str(bge_reranker_manifest.resolve()) if bge_reranker_manifest else None,
        "gte": str(gte_reranker_manifest.resolve()) if gte_reranker_manifest else None,
    }
    if not reranker_manifests_pass:
        blockers.append("reranker_model_manifests")

    chunk = _read(chunk_verification)
    if not chunk or chunk.get("structural_gate_passed") is not True:
        blockers.append("chunk_structural_gate")
    quality = _read(quality_report)
    if not quality or quality.get("quality_gate_passed") is not True:
        blockers.append("quality_policy_v2_release_gate")
    manifest = _read(approved_manifest)
    if not manifest or manifest.get("approved") is not True or manifest.get("legal_review_attestation") is not True:
        blockers.append("approved_manifest_and_legal_attestation")
    serving = _read(serving_manifest)
    if (
        not serving
        or serving.get("schema_version") != "legal-serving-manifest-v3"
        or serving.get("kind") != "release_pointer"
        or serving.get("activation_performed") is not False
        or serving.get("active_pointer_changed") is not False
        or not manifest
        or serving.get("chunk_manifest_sha256") != manifest.get("manifest_sha256")
    ):
        blockers.append("serving_manifest_v3")
    else:
        try:
            load_v3_serving_manifest(
                serving_manifest,
                project_root=ROOT,
                verify_file_artifacts=True,
            )
        except RuntimeError:
            blockers.append("serving_manifest_v3_integrity")
    lexical = _read(lexical_report)
    if not lexical or lexical.get("status") != "STAGING_BUILT":
        blockers.append("exact_lexical_index")
    suite = _read(suite_validation)
    if not suite or suite.get("status") not in {"PASS", "pass"}:
        blockers.append("retrieval_eval_suite_2000")
    availability = _read(source_availability)
    if (
        not availability
        or availability.get("status") != "PASS"
        or float(availability.get("availability_rate") or 0.0) < 1.0
    ):
        blockers.append("retrieval_eval_source_availability")
    for name in ("current_vector_report", "temporal_vector_report"):
        report = _read(Path(checks[name]["path"]))
        vector_pass = bool(
            report
            and (
                report.get("valid") is True
                or (
                    report.get("status") == "PASS"
                    and (report.get("replay_cosine") or {}).get("passed") is True
                    and report.get("active_pointer_changed") is False
                    and report.get("vector_collections_mutated") is False
                    and (
                        report.get("cpu_gpu_parity") is None
                        or (report.get("cpu_gpu_parity") or {}).get("passed") is True
                    )
                )
            )
        )
        if not vector_pass:
            blockers.append(name)
    m5 = _read(m5_report)
    if not m5 or m5.get("status") not in {"pass", "PASS"}:
        blockers.append("m5_release_v2_pass")
    m5_scope_pass = bool(
        m5
        and m5.get("holdout_excluded_from_selection") is True
        and set(m5.get("evaluated_splits") or []) == {"golden-regression", "hard-negative"}
    )
    checks["m5_selection_scope"] = {
        "pass": m5_scope_pass,
        "holdout_excluded_from_selection": (m5 or {}).get("holdout_excluded_from_selection"),
        "evaluated_splits": (m5 or {}).get("evaluated_splits"),
    }
    if not m5_scope_pass:
        blockers.append("m5_holdout_selection_scope")
    diagnostics = _read(stage_diagnostics)
    diagnostics_gate = (diagnostics or {}).get("gate") or {}
    diagnostics_pass = bool(
        diagnostics
        and diagnostics.get("status") == "PASS"
        and diagnostics.get("holdout_excluded") is True
        and not diagnostics.get("limit")
        and diagnostics_gate
        and all(bool(value) for value in diagnostics_gate.values())
    )
    checks["m5_stage_diagnostics"] = {
        "pass": diagnostics_pass,
        "status": (diagnostics or {}).get("status"),
        "gate": diagnostics_gate,
        "holdout_excluded": (diagnostics or {}).get("holdout_excluded"),
    }
    if not diagnostics_pass:
        blockers.append("m5_stage_diagnostics")
    final = _read(final_acceptance)
    if not final or final.get("status") != "PASS":
        blockers.append("retrieval_release_v2_final_acceptance")
    runtime = _read(runtime_fingerprint)
    runtime_retriever = (runtime or {}).get("retriever") or {}
    if (
        not runtime
        or (runtime.get("torch") or {}).get("cuda_available") is not True
        or runtime_retriever.get("active_embedding_device") not in {"cuda", "cuda:0"}
        or runtime_retriever.get("fallback_reason") not in (None, "")
    ):
        blockers.append("cuda_runtime_fingerprint")
    official = _read(official_observations)
    official_pass = bool(
        official
        and official.get("status") == "EVIDENCE_ONLY"
        and official.get("approved_for_import") is False
        and official.get("legal_review_required") is True
        and int(official.get("reference_count") or 0) >= 46
        and official.get("database_mutated") is False
        and official.get("vector_collections_mutated") is False
        and official.get("active_pointer_changed") is False
    )
    checks["official_observations"] = {
        "pass": official_pass,
        "status": (official or {}).get("status"),
        "reference_count": (official or {}).get("reference_count"),
        "approved_for_import": (official or {}).get("approved_for_import"),
    }
    if not official_pass:
        blockers.append("official_source_observation_46")
    source_gap_review = _read(source_gap_review_manifest)
    if (
        not source_gap_review
        or source_gap_review.get("schema_version") != "legal-retrieval-source-gap-review-v2"
        or int(source_gap_review.get("reference_count") or 0) != 46
        or source_gap_review.get("approved_for_import") is not False
    ):
        blockers.append("source_gap_review_manifest_46")
    pointer_before = active_pointer.read_text(encoding="utf-8").strip() if active_pointer.is_file() else None
    pointer_matches_baseline = pointer_before == str(expected_pointer)
    checks["active_pointer"] = {
        "path": str(active_pointer.resolve()),
        "value": pointer_before,
        "expected_baseline": str(expected_pointer),
        "matches_baseline": pointer_matches_baseline,
        "changed": False,
    }
    if not pointer_matches_baseline:
        blockers.append("active_pointer_not_baseline")
    report = {
        "schema_version": "legal-retrieval-v2-gate-report-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
        "checks": checks,
        "active_pointer_changed": False,
        "database_mutated": False,
        "vector_collections_mutated": False,
    }
    report["report_sha256"] = canonical_sha256(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(f"{file_sha256(output)}  {output.name}\n", encoding="ascii")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_DIR / "source-inventory-reconciliation.json")
    parser.add_argument("--source-snapshot", type=Path, default=DEFAULT_DIR / "source-snapshot-12236.json")
    parser.add_argument("--baseline-lock", type=Path, default=DEFAULT_DIR / "baseline-write-lock-verification.json")
    parser.add_argument("--chunk-verification", type=Path, default=DEFAULT_DIR / "legal-retrieval-chunk-manifest-v2-verification-passage-v3.json")
    parser.add_argument("--quality-report", type=Path, default=DEFAULT_DIR / "quality-policy-v2-release-passage-v3.json")
    parser.add_argument("--approved-manifest", type=Path, default=DEFAULT_DIR / "legal-retrieval-chunk-manifest-v2-approved-passage-v3.json")
    parser.add_argument("--serving-manifest", type=Path, default=DEFAULT_DIR / "legal-serving-manifest-v3.json")
    parser.add_argument("--lexical-report", type=Path, default=DEFAULT_DIR / "legal-retrieval-v2-exact-lexical.sqlite3.report.json")
    parser.add_argument("--suite-validation", type=Path, default=DEFAULT_DIR / "eval-suite-validation.json")
    parser.add_argument("--source-availability", type=Path, default=DEFAULT_DIR / "retrieval-eval-source-availability-v2.json")
    parser.add_argument("--current-vector-report", type=Path, default=DEFAULT_DIR / "current-vector-verification.json")
    parser.add_argument("--temporal-vector-report", type=Path, default=DEFAULT_DIR / "temporal-vector-verification.json")
    parser.add_argument("--m5-report", type=Path, default=DEFAULT_DIR / "m5-v2-acceptance.json")
    parser.add_argument("--stage-diagnostics", type=Path, default=DEFAULT_DIR / "m5-v2-stage-diagnostics.json")
    parser.add_argument("--final-acceptance", type=Path, default=DEFAULT_DIR / "retrieval-release-v2-acceptance.json")
    parser.add_argument("--runtime-fingerprint", type=Path, default=DEFAULT_DIR / "runtime-fingerprint-cuda.json")
    parser.add_argument("--official-observations", type=Path, default=DEFAULT_DIR / "official-web-observations-v2.json")
    parser.add_argument("--source-gap-review-manifest", type=Path, default=DEFAULT_DIR / "source-gap-review-manifest-v2-20260816-rerun-r2.json")
    parser.add_argument("--expected-pointer", default=DEFAULT_EXPECTED_POINTER)
    parser.add_argument("--bge-reranker-manifest", type=Path, default=DEFAULT_DIR / "reranker-bge-reranker-v2-m3-manifest.json")
    parser.add_argument("--gte-reranker-manifest", type=Path, default=DEFAULT_DIR / "reranker-gte-multilingual-reranker-base-manifest.json")
    parser.add_argument("--active-pointer", type=Path, default=ROOT / "release-data" / "legal" / "chroma_store" / "active_core_collection.txt")
    parser.add_argument("--output", type=Path, default=DEFAULT_DIR / "retrieval-release-v2-gates.json")
    args = parser.parse_args(argv)
    report = run(
        **{
            key.replace("-", "_"): (value.resolve() if isinstance(value, Path) else value)
            for key, value in vars(args).items()
        }
    )
    print(json.dumps({"status": report["status"], "blockers": report["blockers"], "output": str(args.output.resolve())}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
