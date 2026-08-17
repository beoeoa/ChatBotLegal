#!/usr/bin/env python3
"""Create the immutable Stage C V6R1 acceptance report.

The command only reads already-created local artifacts and the active pointer.
It does not import vectors, mutate Chroma or change any pointer.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import canonical_sha256, file_sha256


EXPECTED_VECTORS = 639_129
EXPECTED_POINTER = "legal_chunks_vnlegal_lal_haiphong_unified_v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required:{path}")
    return value


def finalize(
    *,
    input_manifest_path: Path,
    output_manifest_path: Path,
    structural_report_path: Path,
    parity_report_path: Path,
    kernel_metadata_path: Path,
    kernel_log_path: Path,
    approved_chunk_manifest_path: Path,
    pointer_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    input_manifest = _load(input_manifest_path)
    output_manifest = _load(output_manifest_path)
    structural = _load(structural_report_path)
    parity = _load(parity_report_path)
    kernel_metadata = _load(kernel_metadata_path)

    pointer_before = pointer_path.read_text(encoding="utf-8").strip()
    pointer_after = pointer_path.read_text(encoding="utf-8").strip()
    gates = {
        "input_manifest_release_eligible": input_manifest.get("release_eligible") is True,
        "input_manifest_not_provisional": input_manifest.get("provisional_staging") is False,
        "kernel_private": kernel_metadata.get("is_private") is True,
        "kernel_gpu_enabled": kernel_metadata.get("enable_gpu") is True,
        "kernel_internet_disabled": kernel_metadata.get("enable_internet") is False,
        "structural_verifier": structural.get("valid") is True,
        "vector_count": structural.get("vector_count") == EXPECTED_VECTORS,
        "no_missing_ids": structural.get("missing_ids") == [],
        "no_extra_ids": structural.get("extra_ids") == [],
        "no_duplicate_ids": structural.get("duplicate_ids") == [],
        "finite_vectors": structural.get("non_finite_vectors") == 0,
        "zero_vectors": structural.get("zero_vectors") == 0,
        "norms_valid": structural.get("norm_out_of_range") == 0,
        "output_release_eligible": output_manifest.get("release_eligible") is True,
        "replay_500": (
            parity.get("replay_cosine", {}).get("sample_count") == 500
            and parity.get("replay_cosine", {}).get("passed") is True
        ),
        "cpu_gpu_parity": (
            parity.get("cpu_gpu_parity", {}).get("sample_count") == 100
            and parity.get("cpu_gpu_parity", {}).get("passed") is True
        ),
        "active_pointer_preserved": (
            pointer_before == EXPECTED_POINTER
            and pointer_after == EXPECTED_POINTER
            and parity.get("active_pointer_changed") is False
        ),
        "no_chroma_mutation": (
            structural.get("chroma_mutated") is False
            and parity.get("vector_collections_mutated") is False
        ),
    }
    result: dict[str, Any] = {
        "schema_version": "legal-retrieval-stage-c-report-v1",
        "status": "PASS" if all(gates.values()) else "FAIL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage": "C",
        "release_id": input_manifest.get("release_id"),
        "embedding_job_id": input_manifest.get("embedding_job_id"),
        "kaggle": {
            "dataset": kernel_metadata.get("dataset_sources", [None])[0],
            "model_dataset": kernel_metadata.get("dataset_sources", [None, None])[1],
            "kernel": kernel_metadata.get("id"),
            "accelerator": input_manifest.get("accelerator"),
            "internet_enabled": input_manifest.get("internet_enabled"),
        },
        "counts": {
            "expected_vectors": EXPECTED_VECTORS,
            "observed_vectors": structural.get("vector_count"),
            "embedding_dimension": structural.get("embedding_dimension"),
            "current_chunks": input_manifest.get("document_state_counts", {}).get("current_retrievable"),
            "historical_chunks": input_manifest.get("document_state_counts", {}).get("historical_only"),
        },
        "fingerprints": {
            "model_artifact": input_manifest.get("model_artifact_fingerprint"),
            "tokenizer": input_manifest.get("tokenizer_fingerprint"),
            "embedding_recipe": input_manifest.get("embedding_recipe_fingerprint"),
            "passage_recipe": input_manifest.get("passage_recipe_fingerprint"),
            "splitter": input_manifest.get("splitter_fingerprint"),
            "worker": input_manifest.get("worker_fingerprint"),
            "source_snapshot": input_manifest.get("source_snapshot_sha256"),
            "vector_content": structural.get("vector_content_sha256"),
        },
        "metrics": {
            "replay_cosine": parity.get("replay_cosine"),
            "cpu_gpu_parity": parity.get("cpu_gpu_parity"),
        },
        "gates": gates,
        "artifacts": {
            "input_manifest": {"path": str(input_manifest_path), "sha256": file_sha256(input_manifest_path)},
            "output_manifest": {"path": str(output_manifest_path), "sha256": file_sha256(output_manifest_path)},
            "structural_report": {"path": str(structural_report_path), "sha256": file_sha256(structural_report_path)},
            "parity_report": {"path": str(parity_report_path), "sha256": file_sha256(parity_report_path)},
            "approved_chunk_manifest": {"path": str(approved_chunk_manifest_path), "sha256": file_sha256(approved_chunk_manifest_path)},
            "kernel_metadata": {"path": str(kernel_metadata_path), "sha256": file_sha256(kernel_metadata_path)},
            "kernel_log": {"path": str(kernel_log_path), "sha256": file_sha256(kernel_log_path)},
        },
        "active_pointer": {
            "path": str(pointer_path),
            "before": pointer_before,
            "after": pointer_after,
            "changed": pointer_before != pointer_after,
        },
        "chroma_mutated": False,
        "database_mutated": False,
        "stage_d_started": False,
    }
    result["report_sha256"] = canonical_sha256(result)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.with_suffix(report_path.suffix + ".sha256").write_text(
        f"{file_sha256(report_path)}  {report_path.name}\n", encoding="ascii"
    )
    return result


def main() -> int:
    base = ROOT / "kaggle" / "retrieval-v2"
    reports = ROOT / "reports" / "retrieval-release-v2"
    result = finalize(
        input_manifest_path=base / "input-v6r1-final" / "embedding-input-manifest.json",
        output_manifest_path=base / "output-v6r1-final" / "embedding-output-manifest.json",
        structural_report_path=reports / "kaggle-vector-verification-v6r1.json",
        parity_report_path=reports / "kaggle-vector-parity-v6r1.json",
        kernel_metadata_path=base / "kernel-bundle-v6r1" / "kernel-metadata.json",
        kernel_log_path=base / "output-v6r1-final" / "legal-retrieval-v2-embedding-v6r1-20260816.log",
        approved_chunk_manifest_path=reports / "legal-retrieval-chunk-manifest-v2-approved-passage-v6r1.json",
        pointer_path=ROOT / "release-data" / "legal" / "chroma_store" / "active_core_collection.txt",
        report_path=reports / "stage-c-final-report-v6r1.json",
    )
    print(json.dumps({"status": result["status"], "report": str(reports / "stage-c-final-report-v6r1.json"), "failed_gates": [key for key, value in result["gates"].items() if not value]}, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
