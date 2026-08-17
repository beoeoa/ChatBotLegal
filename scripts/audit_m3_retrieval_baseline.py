#!/usr/bin/env python3
"""Independently verify the immutable M3 candidate trace baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_retrieval_trace import validate_m3_retrieval_trace
from api.legal_serving_scope import file_sha256

DEFAULT_ARTIFACT = (
    ROOT / "reports" / "m3-retrieval-baseline"
    / "candidate-3000-retrieval-baseline-traces-v1.json"
)
DEFAULT_REPORT = ROOT / "reports" / "m3-retrieval-baseline" / "m3_acceptance_report.json"
DEFAULT_CHROMA = ROOT / "release-data" / "legal" / "chroma_store"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _canonical_sha256(payload: dict[str, Any]) -> str:
    value = dict(payload)
    value.pop("artifact_sha256", None)
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _expanded_count(trace: dict[str, Any]) -> int:
    return sum(
        len(value) for value in trace["expanded_evidence"].values()
        if isinstance(value, list)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA)
    args = parser.parse_args()

    artifact_path = args.artifact.resolve()
    payload = _load(artifact_path)
    records = list(payload.get("queries") or [])
    validations = [validate_m3_retrieval_trace(row.get("trace") or {}) for row in records]
    candidate = payload.get("candidate") or {}
    pipeline = payload.get("pipeline_contract") or {}
    historical = payload.get("historical_full_baseline") or {}
    collection = chromadb.PersistentClient(path=str(args.chroma_path.resolve())).get_collection(
        str(candidate.get("collection") or "")
    )
    active_pointer = args.chroma_path.joinpath("active_core_collection.txt").read_text(
        encoding="utf-8"
    ).strip()
    trace_fields = {
        "raw_query": all(bool(row["trace"]["raw_query"]) for row in records),
        "normalized_query": all(bool(row["trace"]["normalized_query"]) for row in records),
        "query_classification": all(bool(row["trace"]["query_classification"]) for row in records),
        "vector_candidates": all(bool(row["trace"]["vector_candidates"]) for row in records),
        "lexical_candidates": all(bool(row["trace"]["lexical_candidates"]) for row in records),
        "fusion_candidates": all(bool(row["trace"]["fusion_candidates"]) for row in records),
        "reranker_candidates": all(
            bool(row["trace"]["reranker_candidates"]["input"])
            and bool(row["trace"]["reranker_candidates"]["output"])
            for row in records
        ),
        "expanded_evidence": all(_expanded_count(row["trace"]) > 0 for row in records),
        "final_evidence": all(bool(row["trace"]["final_evidence"]) for row in records),
        "latency_each_stage": all(bool(row["trace"]["stage_latency_ms"]) for row in records),
    }
    pipeline_hashes_match = all(
        file_sha256(ROOT / row["path"]) == row["sha256"]
        for row in pipeline.get("source_files") or []
    )
    gates = {
        "criterion_29_all_trace_fields": all(trace_fields.values()),
        "all_trace_records_validate": len(records) >= 3
        and len(validations) == len(records)
        and all(item["status"] == "pass" for item in validations),
        "candidate_collection_3000_documents": candidate.get("document_count") == 3000,
        "candidate_collection_88209_vectors": candidate.get("chunk_vector_count") == 88209
        and int(collection.count()) == 88209,
        "historical_full_1000_query_baseline_linked": historical.get("candidate_case_count") == 1000
        and Path(str(historical.get("path") or "")).is_file()
        and file_sha256(Path(historical["path"])) == historical.get("file_sha256"),
        "baseline_pipeline_configuration_frozen": pipeline.get("ranking_strategy") == "legacy_stack"
        and pipeline.get("learned_reranker") is False
        and pipeline.get("candidate_count") == 150
        and pipeline.get("lexical_candidate_count") == 60
        and pipeline.get("exact_flat_backend") is False
        and pipeline.get("hydration_cache") is False
        and pipeline_hashes_match,
        "artifact_content_checksum_valid": _canonical_sha256(payload)
        == payload.get("artifact_sha256"),
        "artifact_file_read_only": not bool(artifact_path.stat().st_mode & stat.S_IWRITE),
        "active_baseline_pointer_unchanged": payload.get("active_pointer_changed") is False
        and payload.get("active_pointer_before") == payload.get("active_pointer_after")
        and active_pointer == payload.get("active_pointer_after"),
    }
    report = {
        "schema_version": "legal-m3-criterion-29-acceptance-v2",
        "status": "pass" if all(gates.values()) else "fail",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "artifact": {
            "path": str(artifact_path),
            "file_sha256": file_sha256(artifact_path),
            "content_sha256": payload.get("artifact_sha256"),
            "query_count": len(records),
        },
        "trace_fields": trace_fields,
        "gates": gates,
    }
    _atomic_json(args.report.resolve(), report)
    checksum_path = args.report.resolve().with_name("m3_checksums.json")
    checksums = {
        artifact_path.name: file_sha256(artifact_path),
        args.report.name: file_sha256(args.report.resolve()),
        Path(candidate["manifest_path"]).name: candidate["manifest_file_sha256"],
        Path(historical["path"]).name: historical["file_sha256"],
    }
    _atomic_json(checksum_path, checksums)
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
