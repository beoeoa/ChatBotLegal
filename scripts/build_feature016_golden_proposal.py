"""Create a legally-unreviewed golden-v2 proposal from the regression baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any


def build_proposal(
    baseline_path: str | Path,
    output_path: str | Path,
    *,
    legal_as_of: date,
) -> dict[str, Any]:
    baseline_file = Path(baseline_path).resolve()
    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    cases = []
    for observed in baseline.get("cases") or []:
        cases.append(
            {
                "case_id": observed["case_id"],
                "schema_version": "2.0",
                "domain": observed["domain"],
                "procedure_family": None,
                "legal_as_of": legal_as_of.isoformat(),
                "questions": {
                    "citizen": observed["question"],
                    "officer": None,
                },
                "expected_sources": [],
                "forbidden_sources": [],
                "required_claims": [],
                "expected_answer_mode": "source_view_only",
                "expected_refusal": True,
                "risk_tags": ["awaiting_legal_review"],
                "review_status": "proposed",
            }
        )
    payload = {
        "schema_version": "2.0",
        "dataset_kind": "golden_proposal_not_legal_ground_truth",
        "review_notice": (
            "Automation must not use proposed cases as correctness ground truth "
            "or promote their review status."
        ),
        "source": {
            "baseline": baseline_file.name,
            "baseline_sha256": hashlib.sha256(baseline_file.read_bytes()).hexdigest(),
        },
        "cases": cases,
    }
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_name, output)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--legal-as-of", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    payload = build_proposal(
        args.baseline, args.output, legal_as_of=args.legal_as_of
    )
    print(
        json.dumps(
            {"output": str(args.output), "proposed_cases": len(payload["cases"])},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
