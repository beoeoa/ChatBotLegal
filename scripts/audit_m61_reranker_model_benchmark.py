"""Independent integrity and safety audit for the M6.1 model benchmark."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "reports" / "m61-reranker-model-comparison"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"m61_audit_invalid_json_object:{path.name}")
    return value


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _frozen_config(config: dict[str, Any]) -> bool:
    return (
        int(config.get("candidate_count") or 0) == 20
        and int(config.get("lexical_candidate_count") or 0) == 20
        and config.get("fusion_strategy") == "legacy_stack"
        and int(config.get("result_limit") or 0) == 10
        and int(config.get("max_length") or 0) == 512
        and int(config.get("batch_size") or 0) == 8
        and bool(config.get("enable_parent_expansion"))
        and bool(config.get("enable_neighbor_expansion"))
    )


def audit(report_dir: Path) -> dict[str, Any]:
    summary_path = report_dir / "m61_reranker_model_benchmark.json"
    protocol_path = report_dir / "m61_protocol.json"
    checksums_path = report_dir / "m61_checksums.json"
    manifest_path = report_dir / "gte-model-manifest.json"
    summary = _load(summary_path)
    protocol = _load(protocol_path)
    checksums = _load(checksums_path)
    manifest = _load(manifest_path)
    experiments = [
        _load(report_dir / "experiments" / f"{row['experiment_id']}.json")
        for row in summary.get("experiments") or []
    ]
    by_id = {str(row.get("experiment_id")): row for row in experiments}
    gte10 = by_id.get("m61-e02-gte-top10", {})
    gte20_required = (
        float(gte10.get("p95_retrieval_ms") or 0.0) <= 15_000.0
        and int(gte10.get("errors") or 0) == 0
        and int(gte10.get("outside_manifest_result_count") or 0) == 0
        and int(gte10.get("invalid_evidence_result_count") or 0) == 0
    )
    checksum_matches = all(
        (report_dir / name).is_file()
        and _sha256(report_dir / name).casefold() == str(expected).casefold()
        for name, expected in checksums.items()
    )
    learned_rows = [
        case
        for experiment in experiments
        if experiment.get("search_config", {}).get("enable_learned_reranker")
        for case in (experiment.get("cases") or [])
    ]
    learned_scored_rows = [
        case for case in learned_rows if int(case.get("result_count") or 0) > 0
    ]
    no_degraded_or_cpu_reranker = all(
        (case.get("reranker") or {}).get("mode") == "learned"
        and (case.get("reranker") or {}).get("device") == "cuda"
        and not bool((case.get("reranker") or {}).get("degraded"))
        and int((case.get("reranker") or {}).get("max_length") or 0) == 512
        and int((case.get("reranker") or {}).get("batch_size") or 0) == 8
        for case in learned_scored_rows
    )
    empty_case_sets = [
        {
            str(case.get("case_id"))
            for case in (experiment.get("cases") or [])
            if int(case.get("result_count") or 0) == 0
        }
        for experiment in experiments
    ]
    checks = {
        "summary_status_pass": summary.get("status") == "pass",
        "golden_100": int(protocol.get("case_count") or 0) == 100,
        "candidate_3000": int(protocol.get("document_count") or 0) == 3000,
        "candidate_chunks_vectors_88209": int(
            protocol.get("chunk_vector_count") or 0
        )
        == 88209,
        "required_experiments_present": {
            "m61-e00-control",
            "m61-e01-bge-top10",
            "m61-e02-gte-top10",
        }.issubset(by_id),
        "conditional_gte_top20_obeyed": (
            ("m61-e03-gte-top20" in by_id) if gte20_required else True
        ),
        "all_experiment_configs_frozen": bool(experiments)
        and all(_frozen_config(dict(row.get("search_config") or {})) for row in experiments),
        "all_cases_completed": bool(experiments)
        and all(int(row.get("case_count") or 0) == 100 for row in experiments),
        "zero_errors_oom": all(int(row.get("errors") or 0) == 0 for row in experiments)
        and all(
            "oom" not in str(case.get("error") or "").casefold()
            and "oom"
            not in str((case.get("reranker") or {}).get("reason_code") or "").casefold()
            for row in experiments
            for case in (row.get("cases") or [])
        ),
        "zero_outside_manifest": all(
            int(row.get("outside_manifest_result_count") or 0) == 0
            for row in experiments
        ),
        "zero_invalid_or_expired_evidence": all(
            int(row.get("invalid_evidence_result_count") or 0) == 0
            for row in experiments
        ),
        "empty_retrieval_bypasses_reranker_consistently": bool(empty_case_sets)
        and all(case_ids == empty_case_sets[0] for case_ids in empty_case_sets[1:]),
        "learned_reranker_cuda_not_degraded": bool(learned_scored_rows)
        and no_degraded_or_cpu_reranker,
        "pinned_gte_model_and_code_verified": bool(
            protocol.get("models", {})
            .get("gte-multilingual-base", {})
            .get("verified")
        )
        and manifest.get("revision")
        == "8215cf04918ba6f7b6a62bb44238ce2953d8831c"
        and manifest.get("custom_code", {}).get("revision")
        == "40ced75c3017eb27626c9d4ea981bde21a2662f4",
        "artifact_checksums_match": checksum_matches,
        "active_pointer_unchanged": not bool(summary.get("active_pointer_changed"))
        and summary.get("active_pointer_before") == summary.get("active_pointer_after"),
        "live_configuration_unchanged": not bool(
            summary.get("live_configuration_changed")
        )
        and not bool(summary.get("activation_performed")),
        "no_model_deleted_or_archived": not bool(
            summary.get("model_deleted_or_archived")
        ),
    }
    return {
        "schema_version": "legal-m61-independent-audit-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "decision": summary.get("decision"),
        "selected_model": summary.get("selected_model"),
        "source_report_sha256": _sha256(summary_path),
        "source_protocol_sha256": _sha256(protocol_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    report_dir = args.report_dir.resolve()
    result = audit(report_dir)
    output = report_dir / "m61_audit.json"
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checksums_path = report_dir / "m61_checksums.json"
    checksums = _load(checksums_path)
    checksums["m61_audit.json"] = _sha256(output)
    checksums_path.write_text(
        json.dumps(checksums, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
