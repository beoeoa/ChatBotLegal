"""Derive a corrected control report from an immutable full run and replay."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_retrieval_evaluation import (
    build_retrieval_metrics,
    normalize_domain,
)
from scripts.benchmark_candidate_golden import load_payload, percentile, sha256
from scripts.run_retrieval_quality_v2 import _quality_gates


DEFAULT_BASE = ROOT / "reports" / "retrieval-quality-v2" / "control-full-v2.json"
DEFAULT_REPLAY = (
    ROOT / "reports" / "retrieval-quality-v2" / "control-temporal-replay-5-v3.json"
)
DEFAULT_OUTPUT = (
    ROOT / "reports" / "retrieval-quality-v2" / "control-full-v3-derived.json"
)


def _cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("cases")
    if values is None:
        values = payload.get("examples")
    return [dict(item) for item in (values or [])]


def _json_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _rescore_run(run: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [dict(row) for row in (run.get("cases") or [])]
    metrics = build_retrieval_metrics(cases, rows)
    valid_rows = [row for row in rows if not row.get("dataset_error")]
    latency = [float(row.get("latency_ms") or 0.0) for row in valid_rows]
    retrieval_latency = [
        float(row.get("retrieval_latency_ms") or 0.0) for row in valid_rows
    ]
    run.update(
        {
            "metric_contract_version": metrics["metric_contract_version"],
            "dataset_case_count": metrics["dataset_case_count"],
            "valid_case_count": metrics["valid_case_count"],
            "dataset_error_count": metrics["dataset_error_count"],
            "dataset_errors": metrics["dataset_errors"],
            "answer_required_count": metrics["answer_required_count"],
            "expected_refusal_count": metrics["expected_refusal_count"],
            "recall_at_10": metrics["recall_at_10"] or 0.0,
            "mrr": metrics["mrr_at_10"] or 0.0,
            "mrr_at_10": metrics["mrr_at_10"],
            "correct_refusal_rate": metrics["correct_refusal_rate"],
            "false_blocked_answer_count": metrics[
                "false_blocked_answer_count"
            ],
            "issue_count": metrics["issue_count"],
            "issue_recall_at_10": metrics["issue_recall_at_10"],
            "all_required_sources_coverage": metrics[
                "all_required_sources_coverage"
            ],
            "miss_count": metrics["miss_count"],
            "misses": metrics["misses"],
            "per_intent": metrics["per_intent"],
            "per_temporal_scope": metrics["per_temporal_scope"],
            "p50_ms": percentile(latency, 0.50),
            "p95_ms": percentile(latency, 0.95),
            "p99_ms": percentile(latency, 0.99),
            "p50_retrieval_ms": percentile(retrieval_latency, 0.50),
            "p95_retrieval_ms": percentile(retrieval_latency, 0.95),
            "p99_retrieval_ms": percentile(retrieval_latency, 0.99),
            "errors": sum(bool(row.get("error")) for row in valid_rows),
            "cases": rows,
        }
    )

    case_by_id = {str(case.get("case_id")): case for case in cases}
    domain_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid_rows:
        case = case_by_id.get(str(row.get("case_id")), {})
        domain_rows[normalize_domain(case.get("domain") or row.get("domain"))].append(row)
    domain_summary: dict[str, Any] = {}
    for domain, quality in metrics["per_domain"].items():
        values = domain_rows.get(domain, [])
        domain_latency = [float(row.get("latency_ms") or 0.0) for row in values]
        domain_retrieval = [
            float(row.get("retrieval_latency_ms") or 0.0) for row in values
        ]
        domain_summary[domain] = {
            **quality,
            "p50_ms": percentile(domain_latency, 0.50),
            "p95_ms": percentile(domain_latency, 0.95),
            "p50_retrieval_ms": percentile(domain_retrieval, 0.50),
            "p95_retrieval_ms": percentile(domain_retrieval, 0.95),
        }
    run["per_domain"] = domain_summary
    run["gates"] = _quality_gates(run)
    run["status"] = "pass" if all(run["gates"].values()) else "fail"
    return run


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    base_path = args.base.resolve()
    replay_path = args.replay.resolve()
    base = load_payload(base_path)
    replay = load_payload(replay_path)
    runs = {name: dict(run) for name, run in (base.get("runs") or {}).items()}
    replaced: dict[str, list[str]] = {}
    for name, replay_run in (replay.get("runs") or {}).items():
        if name not in runs:
            raise RuntimeError(f"control_replay_unknown_run:{name}")
        replacements = {
            str(row.get("case_id")): dict(row)
            for row in (replay_run.get("cases") or [])
        }
        original_rows = [dict(row) for row in (runs[name].get("cases") or [])]
        known_ids = {str(row.get("case_id")) for row in original_rows}
        unknown = sorted(set(replacements).difference(known_ids))
        if unknown:
            raise RuntimeError("control_replay_unknown_case_ids:" + ",".join(unknown))
        runs[name]["cases"] = [
            replacements.get(str(row.get("case_id")), row) for row in original_rows
        ]
        replaced[name] = sorted(replacements)

    dataset_paths = {
        name: Path(str(details["path"]))
        for name, details in (base.get("protocol", {}).get("datasets") or {}).items()
    }
    for name, run in runs.items():
        runs[name] = _rescore_run(run, _cases(load_payload(dataset_paths[name])))

    pointer_before = str(base.get("active_pointer_before") or "")
    pointer_after = str(base.get("active_pointer_after") or "")
    derivation = {
        "method": "replace_case_rows_and_rescore_metric_contract_v2",
        "base_path": str(base_path),
        "base_sha256": sha256(base_path),
        "replay_path": str(replay_path),
        "replay_sha256": sha256(replay_path),
        "replaced_case_ids": replaced,
        "retrieval_rerun_scope": sum(len(values) for values in replaced.values()),
        "unchanged_case_rows_reused": sum(
            len(run.get("cases") or []) for run in runs.values()
        )
        - sum(len(values) for values in replaced.values()),
    }
    derivation["derivation_sha256"] = _json_sha(derivation)
    report = {
        **base,
        "schema_version": "legal-retrieval-quality-v3-derived",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass"
        if all(run["status"] == "pass" for run in runs.values())
        and pointer_before == pointer_after
        else "fail",
        "derivation": derivation,
        "runs": runs,
        "active_pointer_changed": pointer_before != pointer_after,
        "activation_performed": False,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{sha256(output)}  {output.name}\n",
        encoding="ascii",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(output),
                "runs": {
                    name: {
                        "status": run["status"],
                        "recall_at_10": run["recall_at_10"],
                        "mrr_at_10": run["mrr_at_10"],
                        "correct_refusal_rate": run["correct_refusal_rate"],
                    }
                    for name, run in runs.items()
                },
                "active_pointer_changed": report["active_pointer_changed"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
