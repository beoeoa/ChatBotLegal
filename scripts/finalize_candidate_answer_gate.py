"""Turn the full shadow answer benchmark into an explicit, fail-closed gate."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    benchmark = load(args.benchmark.resolve())
    baseline = benchmark["baseline"]["summary"]
    candidate = benchmark["candidate"]["summary"]
    # These are comparison gates.  Citation support is additionally required
    # to be 100% for activation; the measured proxy is intentionally fail-closed.
    comparison_gates = {
        "answer_rate_not_decreased_more_than_1pct": candidate["answer_rate"] >= baseline["answer_rate"] - 0.01,
        "grounded_rate_not_decreased_more_than_1pct": candidate["grounded_rate"] >= baseline["grounded_rate"] - 0.01,
        "source_gap_not_increased": candidate["source_gap_rate"] <= baseline["source_gap_rate"],
        "fallback_not_increased": candidate["fallback_rate"] <= baseline["fallback_rate"],
        "citation_support_not_decreased": candidate["citation_verified_rate"] >= baseline["citation_verified_rate"],
        "citation_support_100pct": candidate["citation_verified_rate"] >= 1.0,
        "provider_http_success": candidate["http_200"] == candidate["case_count"],
    }
    report = {
        "schema_version": "legal-candidate-answer-gate-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": str(args.benchmark.resolve()),
        "answer_cases_executed": int(benchmark.get("case_count") or 0),
        "answer_cases_passed": int(candidate.get("fully_grounded") or 0),
        "provider_prerequisites": {"provider_called": True, "credentials_recorded": False},
        "baseline": baseline,
        "candidate": candidate,
        "comparison": benchmark.get("comparison") or {},
        "gates": comparison_gates,
        "gate": {
            "passed": all(comparison_gates.values()),
            "status": "PASS" if all(comparison_gates.values()) else "ANSWER_QUALITY_GATE_FAILED",
            "reason_code": None if all(comparison_gates.values()) else "CITATION_SUPPORT_BELOW_100PCT",
        },
        "safety": {
            "credentials_recorded": False,
            "database_mutated": False,
            "vectors_mutated": False,
            "active_pointer_changed": False,
        },
    }
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "gate": report["gate"], "gates": report["gates"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
