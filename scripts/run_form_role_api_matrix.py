"""Run the deterministic citizen/officer/admin form authorization matrix."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import FormCatalog


DEFAULT_DATASET = ROOT / "reports/feature006/form-lookup-release-dataset.json"
DEFAULT_REPORT = ROOT / "reports/feature006/form-role-api-matrix.json"
ROLES = ("citizen", "officer", "admin")


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * 0.95))]


def run_matrix(
    *,
    catalog: FormCatalog,
    dataset: dict[str, Any],
    legal_as_of: date,
) -> dict[str, Any]:
    role_rows: dict[str, dict[str, Any]] = {}
    all_timings: list[float] = []
    for role in ROLES:
        counters = {
            "case_count": 0,
            "pass_count": 0,
            "wrong_form_count": 0,
            "missing_form_count": 0,
            "pending_form_exposure_count": 0,
            "expired_form_exposure_count": 0,
            "role_leakage_count": 0,
        }
        timings: list[float] = []
        for case in dataset.get("cases") or []:
            counters["case_count"] += 1
            expected = {
                form_id
                for form_id in case.get("expected_form_ids") or []
                if (
                    role != "citizen"
                    or catalog._forms_by_id.get(form_id, {}).get("audience")
                    in {"citizen", "both"}
                )
            }
            started = time.perf_counter()
            result = catalog.resolve_forms(
                str(case.get("query") or ""),
                role=role,
                as_of=legal_as_of,
                procedure_ids=[str(case["procedure_id"])],
                limit=max(3, len(expected)),
            )
            elapsed = (time.perf_counter() - started) * 1000
            timings.append(elapsed)
            all_timings.append(elapsed)
            actual = {
                str(item.get("form_id") or "")
                for item in result.get("recommended_forms") or []
                if item.get("form_id")
            }
            wrong = actual - expected
            missing = expected - actual
            pending = 0
            expired = 0
            leakage = 0
            for item in result.get("recommended_forms") or []:
                source = catalog._forms_by_id.get(str(item.get("form_id") or ""), {})
                pending += int(source.get("review_status") != "approved")
                end = str(source.get("effective_to") or "").strip()
                expired += int(
                    bool(end and date.fromisoformat(end[:10]) < legal_as_of)
                    or bool(source.get("supersedes_form_id"))
                )
                leakage += int(
                    role == "citizen" and source.get("audience") == "officer"
                )
            counters["wrong_form_count"] += len(wrong)
            counters["missing_form_count"] += len(missing)
            counters["pending_form_exposure_count"] += pending
            counters["expired_form_exposure_count"] += expired
            counters["role_leakage_count"] += leakage
            if not wrong and not missing and not pending and not expired and not leakage:
                counters["pass_count"] += 1
        counters["lookup_p95_ms"] = round(_p95(timings), 3)
        role_rows[role] = counters

    case_count = sum(row["case_count"] for row in role_rows.values())
    pass_count = sum(row["pass_count"] for row in role_rows.values())
    report = {
        "schema_version": "feature006-form-role-api-matrix-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": legal_as_of.isoformat(),
        "procedure_count": len(dataset.get("cases") or []),
        "role_count": len(ROLES),
        "case_count": case_count,
        "pass_count": pass_count,
        "roles": role_rows,
        "lookup_median_ms": round(statistics.median(all_timings), 3),
        "lookup_p95_ms": round(_p95(all_timings), 3),
        "privacy": {
            "contains_question_text": False,
            "contains_answer_text": False,
            "contains_credentials": False,
        },
    }
    report["technical_pass"] = bool(
        report["procedure_count"] == 418
        and case_count == 1254
        and pass_count == case_count
        and report["lookup_p95_ms"] <= 200
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--legal-as-of", default="2026-07-30")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8-sig"))
    report = run_matrix(
        catalog=FormCatalog.load_default(),
        dataset=dataset,
        legal_as_of=date.fromisoformat(args.legal_as_of),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["technical_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
