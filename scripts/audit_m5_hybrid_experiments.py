"""Independently audit the frozen M5 hybrid retrieval experiment artifacts."""

from __future__ import annotations

import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_m5_hybrid_experiments import _json_sha, _winner


REPORT_DIR = ROOT / "reports" / "m5-hybrid-retrieval"
SUMMARY_PATH = REPORT_DIR / "m5_hybrid_experiments_v1.json"
PROTOCOL_PATH = REPORT_DIR / "m5_protocol.json"
CHECKSUM_PATH = REPORT_DIR / "m5_checksums.json"
SCHEMA_PATH = (
    ROOT
    / "specs"
    / "018-production-release-readiness"
    / "contracts"
    / "legal-m5-hybrid-experiments-v1.schema.json"
)
CANDIDATE_PATH = (
    ROOT
    / "reports"
    / "corpus-thinning-remediated-v2"
    / "legal-serving-candidate-3000-v1.json"
)
GOLDEN_PATH = (
    ROOT
    / "outputs"
    / "019fe6cd-c481-70c0-8c58-3f69816592fc"
    / "golden-294-live"
    / "golden-1000-residence-remap-proposal.json"
)
POINTER_PATH = (
    ROOT / "release-data" / "legal" / "chroma_store" / "active_core_collection.txt"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _config_diff(left: dict[str, Any], right: dict[str, Any]) -> set[str]:
    return {key for key in set(left) | set(right) if left.get(key) != right.get(key)}


def main() -> int:
    summary = _read(SUMMARY_PATH)
    protocol = _read(PROTOCOL_PATH)
    checksums = _read(CHECKSUM_PATH)
    schema = _read(SCHEMA_PATH)
    jsonschema.validate(summary, schema)

    experiments = {row["experiment_id"]: row for row in summary["experiments"]}
    full_runs = {
        experiment_id: _read(REPORT_DIR / "experiments" / f"{experiment_id}.json")
        for experiment_id in experiments
    }
    vector_ids = {f"m5-e1-vector-k{k}" for k in (10, 20, 30, 50)}
    lexical_ids = {f"m5-e2-lexical-k{k}" for k in (10, 20, 30, 50)}
    fusion_ids = {
        "m5-e3-fusion-rrf",
        "m5-e3-fusion-weighted-060-040",
        "m5-e3-fusion-weighted-070-030",
        "m5-e3-fusion-weighted-050-050",
    }
    expected_ids = {"m5-e00-baseline-control"} | vector_ids | lexical_ids | fusion_ids

    embedded_protocol = dict(summary["protocol"])
    embedded_protocol_sha = embedded_protocol.pop("protocol_sha256")
    protocol_file_without_sha = dict(protocol)
    protocol_file_sha = protocol_file_without_sha.pop("protocol_sha256")
    source_hashes_valid = all(
        _sha256(ROOT / relative_path) == expected_sha
        for relative_path, expected_sha in protocol["source_hashes"].items()
    )
    artifact_checksums_valid = all(
        (REPORT_DIR / relative_path).is_file()
        and _sha256(REPORT_DIR / relative_path) == expected_sha
        for relative_path, expected_sha in checksums.items()
    )

    baseline = experiments["m5-e00-baseline-control"]
    vector_single_variable = all(
        experiments[item]["parent_experiment_id"] == baseline["experiment_id"]
        and _config_diff(experiments[item]["search_config"], baseline["search_config"])
        == {"candidate_count"}
        for item in vector_ids
    )
    vector_winner = _winner([experiments[item] for item in vector_ids])
    lexical_single_variable = all(
        experiments[item]["parent_experiment_id"] == vector_winner["experiment_id"]
        and _config_diff(
            experiments[item]["search_config"], vector_winner["search_config"]
        )
        == {"lexical_candidate_count"}
        for item in lexical_ids
    )
    lexical_winner = _winner([experiments[item] for item in lexical_ids])
    weighted_060 = experiments["m5-e3-fusion-weighted-060-040"]
    fusion_strategy_single_variable = all(
        experiments[item]["parent_experiment_id"] == lexical_winner["experiment_id"]
        and _config_diff(
            experiments[item]["search_config"], lexical_winner["search_config"]
        )
        == {"fusion_strategy"}
        for item in ("m5-e3-fusion-rrf", "m5-e3-fusion-weighted-060-040")
    )
    fusion_weight_single_variable = all(
        experiments[item]["parent_experiment_id"] == weighted_060["experiment_id"]
        and _config_diff(
            experiments[item]["search_config"], weighted_060["search_config"]
        )
        == {"vector_weight", "lexical_weight"}
        for item in (
            "m5-e3-fusion-weighted-070-030",
            "m5-e3-fusion-weighted-050-050",
        )
    )
    fusion_winner = _winner([experiments[item] for item in fusion_ids])
    current_pointer = POINTER_PATH.read_text(encoding="utf-8").strip()
    candidate = _read(CANDIDATE_PATH)
    candidate_chunks = {
        int(chunk_id)
        for document in candidate["documents"]
        for chunk_id in document.get("expected_chunk_ids") or []
    }

    gates = {
        "summary_schema_valid": True,
        "summary_status_pass": summary["status"] == "pass",
        "exact_13_experiments": set(experiments) == expected_ids,
        "all_full_runs_match_summary_protocol": all(
            run["protocol_sha256"] == protocol["protocol_sha256"]
            and run["case_count"] == 1000
            for run in full_runs.values()
        ),
        "all_runs_error_free": all(row["errors"] == 0 for row in experiments.values()),
        "all_results_manifest_bound": all(
            row["outside_manifest_result_count"] == 0 for row in experiments.values()
        ),
        "vector_top_k_matrix_complete": {
            experiments[item]["search_config"]["candidate_count"] for item in vector_ids
        }
        == {10, 20, 30, 50},
        "lexical_top_k_matrix_complete": {
            experiments[item]["search_config"]["lexical_candidate_count"]
            for item in lexical_ids
        }
        == {10, 20, 30, 50},
        "fusion_matrix_complete": {
            (
                experiments[item]["search_config"]["fusion_strategy"],
                experiments[item]["search_config"]["vector_weight"],
                experiments[item]["search_config"]["lexical_weight"],
            )
            for item in fusion_ids
        }
        == {
            ("rrf", 0.6, 0.4),
            ("weighted", 0.6, 0.4),
            ("weighted", 0.7, 0.3),
            ("weighted", 0.5, 0.5),
        },
        "vector_experiments_change_one_variable": vector_single_variable,
        "lexical_experiments_change_one_variable": lexical_single_variable,
        "fusion_strategy_changes_one_variable": fusion_strategy_single_variable,
        "fusion_weights_change_one_conceptual_variable": fusion_weight_single_variable,
        "stage_winners_recomputed": summary["stage_winners"]
        == {
            "vector_top_k": vector_winner["experiment_id"],
            "lexical_top_k": lexical_winner["experiment_id"],
            "fusion": fusion_winner["experiment_id"],
        },
        "recommended_config_recomputed": summary["recommended_config"]
        == fusion_winner["search_config"],
        "protocol_sha256_valid": embedded_protocol_sha == _json_sha(embedded_protocol)
        and protocol_file_sha == _json_sha(protocol_file_without_sha)
        and embedded_protocol_sha == protocol_file_sha,
        "source_hashes_valid": source_hashes_valid,
        "artifact_checksums_valid": artifact_checksums_valid,
        "golden_checksum_valid": protocol["golden_sha256"] == _sha256(GOLDEN_PATH),
        "candidate_checksum_valid": protocol["candidate_manifest_file_sha256"]
        == _sha256(CANDIDATE_PATH),
        "candidate_shape_valid": len(candidate["documents"]) == 3000
        and len(candidate_chunks) == 88209
        and protocol["document_count"] == 3000
        and protocol["chunk_vector_count"] == 88209,
        "active_pointer_unchanged": not summary["active_pointer_changed"]
        and not summary["activation_performed"]
        and summary["active_pointer_before"] == summary["active_pointer_after"]
        == current_pointer,
        "summary_is_read_only": not bool(SUMMARY_PATH.stat().st_mode & stat.S_IWRITE),
    }
    status_value = "pass" if all(gates.values()) else "fail"
    report = {
        "schema_version": "legal-m5-hybrid-acceptance-v1",
        "status": status_value,
        "gates": gates,
        "stage_winners": summary["stage_winners"],
        "recommended_config": summary["recommended_config"],
        "active_pointer": current_pointer,
        "diagnostic_wrong_scope_result_counts": {
            key: value["wrong_scope_result_count"] for key, value in experiments.items()
        },
    }
    report_path = REPORT_DIR / "m5_acceptance_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    audit_artifacts = (
        SUMMARY_PATH,
        PROTOCOL_PATH,
        CHECKSUM_PATH,
        report_path,
        SCHEMA_PATH,
        Path(__file__).resolve(),
        ROOT / "tests" / "test_m5_hybrid_retrieval.py",
    )
    audit_checksums = {
        str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path)
        for path in audit_artifacts
    }
    (REPORT_DIR / "m5_audit_checksums.json").write_text(
        json.dumps(audit_checksums, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=True))
    return 0 if status_value == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
