#!/usr/bin/env python3
"""Create a compact, auditable subset summary from a retrieval report."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 3)


def summarize(
    *, report_path: Path, selector_path: Path, validity_audit_path: Path
) -> dict[str, Any]:
    report = _load(report_path)
    selector = _load(selector_path)
    validity_audit = _load(validity_audit_path)
    blocked_rows = selector.get("blocked_expected_sources") or []
    blocked_by_case = {
        str(row["case_id"]): {
            "law_number": str(row.get("law_number") or ""),
            "article_number": str(row.get("article_number") or ""),
            "reason_code": str(row.get("reason_code") or ""),
        }
        for row in blocked_rows
        if row.get("case_id")
    }
    cases_by_id = {
        str(row.get("case_id")): row
        for row in report.get("cases") or []
        if row.get("case_id")
    }
    missing_case_ids = sorted(set(blocked_by_case) - set(cases_by_id))
    selected = [
        cases_by_id[case_id] for case_id in blocked_by_case if case_id in cases_by_id
    ]
    classification_counts: dict[str, int] = {}
    for case in selected:
        key = str(case.get("classification") or "UNKNOWN")
        classification_counts[key] = classification_counts.get(key, 0) + 1
    timings = [float(case.get("timing_ms") or 0.0) for case in selected]
    wrong_field_count = sum(int(case.get("wrong_field_count") or 0) for case in selected)
    expired_selection_count = sum(
        int(case.get("expired_selection_count") or 0) for case in selected
    )
    validity_summary = validity_audit.get("summary") or {}
    validity_audit_passed = (
        validity_audit.get("status") == "pass"
        and int(validity_summary.get("blocked_expected_source_count") or 0) == 0
    )
    passed = (
        len(selected) == len(blocked_by_case) == 294
        and not missing_case_ids
        and wrong_field_count == 0
        and expired_selection_count == 0
        and validity_audit_passed
    )
    return {
        "schema_version": "retrieval-subset-validity-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_report": str(report_path),
        "selector": str(selector_path),
        "post_correction_validity_audit": str(validity_audit_path),
        "status": "PASS" if passed else "FAIL",
        "case_count": len(selected),
        "expected_case_count": len(blocked_by_case),
        "classification_counts": classification_counts,
        "wrong_field_count": wrong_field_count,
        "expired_selection_count": expired_selection_count,
        "post_correction_validity_audit_passed": validity_audit_passed,
        "post_correction_blocked_expected_source_count": int(
            validity_summary.get("blocked_expected_source_count") or 0
        ),
        "missing_case_ids": missing_case_ids,
        "latency_ms": {
            "p50": _percentile(timings, 0.50),
            "p95": _percentile(timings, 0.95),
        },
        "production_corpus_mutated": False,
        "production_vectors_mutated": False,
        "validity_snapshot_mutated": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--selector", type=Path, required=True)
    parser.add_argument("--validity-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = summarize(
        report_path=args.report.resolve(),
        selector_path=args.selector.resolve(),
        validity_audit_path=args.validity_audit.resolve(),
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    public_keys = (
        "status",
        "case_count",
        "post_correction_validity_audit_passed",
        "expired_selection_count",
        "latency_ms",
    )
    print(json.dumps({key: summary[key] for key in public_keys}, ensure_ascii=False))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
