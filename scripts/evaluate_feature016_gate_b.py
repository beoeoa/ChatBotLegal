"""Run the non-browser Gate-B deterministic safety audit on 100 questions.

This audit intentionally separates implementation evidence from legal expert
approval.  It never promotes proposed golden cases or calls retrieval/browser.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.legal_problem_map import build_legal_intent


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def evaluate_gate_b(
    baseline: dict[str, Any], golden: dict[str, Any]
) -> dict[str, Any]:
    proposed_by_id = {
        str(case.get("case_id")): case for case in golden.get("cases") or []
    }
    cases: list[dict[str, Any]] = []
    deterministic_count = 0
    fail_closed_count = 0
    states: Counter[str] = Counter()
    for observed in baseline.get("cases") or []:
        intent_runs = [
            build_legal_intent(
                observed["question"],
                legal_as_of=(proposed_by_id.get(observed["case_id"]) or {}).get(
                    "legal_as_of"
                ),
            ).model_dump(mode="json")
            for _ in range(10)
        ]
        deterministic = all(item == intent_runs[0] for item in intent_runs)
        deterministic_count += int(deterministic)
        state = str(intent_runs[0].get("state") or "unsupported")
        states[state] += 1
        fail_closed = state != "resolved" or bool(
            intent_runs[0].get("procedure_family")
            or intent_runs[0].get("domain") != "unknown"
        )
        fail_closed_count += int(fail_closed)
        cases.append(
            {
                "case_id": observed["case_id"],
                "question_hash": observed["question_hash"],
                "intent": intent_runs[0],
                "deterministic_10_of_10": deterministic,
                "safe_route_or_clarification": fail_closed,
                "review_status": (
                    proposed_by_id.get(observed["case_id"]) or {}
                ).get("review_status", "missing"),
            }
        )
    review_counts = Counter(
        str(case.get("review_status") or "missing")
        for case in golden.get("cases") or []
    )
    legal_acceptance_ready = bool(cases) and review_counts.get("approved", 0) == len(
        cases
    )
    implementation_pass = (
        len(cases) == 100
        and deterministic_count == len(cases)
        and fail_closed_count == len(cases)
    )
    return {
        "schema_version": "feature016-gate-b-v1",
        "status": (
            "pass"
            if implementation_pass and legal_acceptance_ready
            else "implementation_pass_legal_review_pending"
            if implementation_pass
            else "fail"
        ),
        "implementation_pass": implementation_pass,
        "legal_acceptance_ready": legal_acceptance_ready,
        "legal_review_counts": dict(sorted(review_counts.items())),
        "summary": {
            "case_count": len(cases),
            "deterministic_10_of_10_count": deterministic_count,
            "safe_route_or_clarification_count": fail_closed_count,
            "intent_states": dict(sorted(states.items())),
        },
        "limitations": [
            "Proposed cases are not legal ground truth and are not scored as correct answers."
        ] if not legal_acceptance_ready else [],
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--golden", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    result = evaluate_gate_b(baseline, golden)
    result["inputs"] = {
        "baseline_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
        "golden_sha256": hashlib.sha256(args.golden.read_bytes()).hexdigest(),
    }
    _atomic_json(args.output, result)
    print(json.dumps({key: result[key] for key in ("status", "implementation_pass", "legal_acceptance_ready", "summary")}, ensure_ascii=False))
    return 0 if result["implementation_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
