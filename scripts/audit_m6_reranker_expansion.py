"""Audit M6 artifacts without touching collections or serving pointers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit(output_dir: Path) -> dict[str, Any]:
    summary_path = output_dir / "m6_reranker_expansion_v1.json"
    checksums_path = output_dir / "m6_checksums.json"
    summary = _load(summary_path)
    checksums = _load(checksums_path)
    experiments = list(summary.get("experiments") or [])
    rerank_runs = [row for row in experiments if row.get("stage") == "reranker_top_n"]
    expansion_runs = [row for row in experiments if row.get("stage") == "expansion"]
    actual_checksums = {
        relative: _sha256(output_dir / relative) for relative in checksums
    }
    learned_modes: list[str] = []
    for row in experiments:
        path = output_dir / "experiments" / f"{row['experiment_id']}.json"
        payload = _load(path)
        if bool((row.get("search_config") or {}).get("enable_learned_reranker")):
            learned_modes.extend(
                str((case.get("reranker") or {}).get("mode") or "missing")
                for case in (payload.get("cases") or [])
            )

    checks = {
        "summary_status_pass": summary.get("status") == "pass",
        "seven_experiments": len(experiments) == 7,
        "rerank_top_n_matrix": {
            int((row.get("search_config") or {}).get("rerank_top_n") or 0)
            for row in rerank_runs
        }
        == {20, 30, 50, 100},
        "parent_neighbor_isolated": {
            (
                bool((row.get("search_config") or {}).get("enable_parent_expansion")),
                bool((row.get("search_config") or {}).get("enable_neighbor_expansion")),
            )
            for row in expansion_runs
        }
        == {(True, False), (False, True)},
        "parent_expansion_observed": any(
            int(row.get("hydrated_parent_count") or 0) > 0 for row in expansion_runs
        ),
        "neighbor_expansion_observed": any(
            int(row.get("neighbor_candidate_count") or 0) > 0 for row in expansion_runs
        ),
        "learned_mode_observed": "learned" in learned_modes,
        "no_degraded_learned_cases": all(
            mode in {"learned", "exact_article_bypass"} for mode in learned_modes
        ),
        "no_outside_manifest": all(
            int(row.get("outside_manifest_result_count") or 0) == 0
            for row in experiments
        ),
        "no_invalid_evidence": all(
            int(row.get("invalid_evidence_result_count") or 0) == 0
            for row in experiments
        ),
        "pointer_unchanged": not bool(summary.get("active_pointer_changed")),
        "no_activation": summary.get("activation_performed") is False,
        "checksums_match": actual_checksums == checksums,
    }
    return {
        "schema_version": "legal-m6-audit-v1",
        "status": "pass" if all(checks.values()) else "fail",
        "checks": checks,
        "activation_recommendation": summary.get("activation_recommendation"),
        "latency_gate_passed": bool(
            (summary.get("gates") or {}).get("latency_p95_le_3000_ms")
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.output_dir.resolve())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
