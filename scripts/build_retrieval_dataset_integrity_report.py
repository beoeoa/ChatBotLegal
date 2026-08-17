"""Audit retrieval benchmark dates and refusal routing without retrieval I/O."""

from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_query_understanding import classify_legal_query
from api.legal_retrieval_evaluation import (
    METRIC_CONTRACT_VERSION,
    question_for_case,
    validate_case_temporal_alignment,
)


DEFAULT_GOLDEN = ROOT / "notebook_data" / "feature016-golden-1000-approved.json"
DEFAULT_HARD = ROOT / "reports" / "feature016" / "phase-c" / "hard-negatives-v1.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-quality-v2" / "dataset_integrity_report.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_cases(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("cases")
    if values is None:
        values = payload.get("examples")
    return [dict(item) for item in (values or [])]


def _audit_dataset(path: Path) -> dict[str, Any]:
    cases = _load_cases(path)
    temporal_errors = [
        error
        for case in cases
        if (error := validate_case_temporal_alignment(case)) is not None
    ]
    refusal_rows: list[dict[str, Any]] = []
    for case in cases:
        if not bool(case.get("expected_refusal")):
            continue
        legal_as_of = date.fromisoformat(str(case["legal_as_of"]))
        classification = classify_legal_query(
            question_for_case(case),
            as_of=legal_as_of,
            as_of_explicit=True,
            today=legal_as_of,
        )
        refusal_rows.append(
            {
                "case_id": case.get("case_id"),
                "retrieval_allowed": classification["retrieval_allowed"],
                "intent": classification["intent"],
                "error_code": classification["temporal_error_code"],
            }
        )
    correct_refusal_routes = sum(
        not bool(row["retrieval_allowed"]) for row in refusal_rows
    )
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "case_count": len(cases),
        "valid_case_count": len(cases) - len(temporal_errors),
        "temporal_contract_error_count": len(temporal_errors),
        "temporal_contract_errors": temporal_errors,
        "expected_refusal_count": len(refusal_rows),
        "refusal_route_correct_count": correct_refusal_routes,
        "refusal_route_rate": (
            round(correct_refusal_routes / len(refusal_rows), 6)
            if refusal_rows
            else None
        ),
        "refusal_routes": refusal_rows,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--hard-negative", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    datasets = {
        "golden-1000": _audit_dataset(args.golden.resolve()),
        "hard-negative-100": _audit_dataset(args.hard_negative.resolve()),
    }
    report = {
        "schema_version": "legal-retrieval-dataset-integrity-v1",
        "metric_contract_version": METRIC_CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "pass"
        if all(
            row["temporal_contract_error_count"] == 0
            and (
                row["refusal_route_rate"] is None
                or row["refusal_route_rate"] >= 0.99
            )
            for row in datasets.values()
        )
        else "fail",
        "datasets": datasets,
        "dataset_mutated": False,
        "active_pointer_changed": False,
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
        f"{_sha256(output)}  {output.name}\n",
        encoding="ascii",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(output),
                "datasets": {
                    name: {
                        "temporal_contract_error_count": row[
                            "temporal_contract_error_count"
                        ],
                        "refusal_route_rate": row["refusal_route_rate"],
                    }
                    for name, row in datasets.items()
                },
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
