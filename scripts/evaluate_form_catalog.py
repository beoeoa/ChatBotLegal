"""Evaluate exact approved-form lookup over the Feature 006 release dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import FormCatalog

DEFAULT_DATASET = ROOT / "reports/feature006/form-lookup-release-dataset.json"
DEFAULT_REPORT = ROOT / "reports/feature006/form-lookup-quality.json"
ROLES = ("citizen", "officer", "admin")
DEFAULT_BINDINGS = ROOT / "notebook_data/forms/procedure_form_bindings_v1.json"
DEFAULT_CATALOG = ROOT / "notebook_data/forms/canonical_forms_catalog_v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_dataset_sources(dataset: dict[str, Any]) -> dict[str, Any]:
    """Prove that the release dataset was built from the current read-only inputs."""

    checks: dict[str, dict[str, Any]] = {}
    source_manifest = str(dataset.get("source_manifest") or "").strip()
    paths = {
        "source_manifest": ROOT / source_manifest if source_manifest else None,
        "bindings": DEFAULT_BINDINGS,
        "catalog": DEFAULT_CATALOG,
    }
    expected_hashes = {
        "source_manifest": str(dataset.get("source_manifest_sha256") or ""),
        "bindings": str(dataset.get("bindings_sha256") or ""),
        "catalog": str(dataset.get("catalog_sha256") or ""),
    }
    for label, path in paths.items():
        expected = expected_hashes[label]
        actual = _sha256(path) if path is not None and path.is_file() else None
        checks[label] = {
            "path": str(path.relative_to(ROOT)).replace("\\", "/") if path else None,
            "expected_sha256": expected or None,
            "actual_sha256": actual,
            "matches": bool(expected and actual and expected == actual),
        }
    stale_sources = [label for label, check in checks.items() if not check["matches"]]
    return {
        "fresh": not stale_sources,
        "stale_sources": stale_sources,
        "checks": checks,
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * percentile))]


def evaluate(
    *,
    catalog: FormCatalog,
    dataset: dict[str, Any],
    legal_as_of: date,
    dataset_integrity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timings: list[float] = []
    exact_hits = 0
    expected_total = 0
    returned_total = 0
    wrong_forms = 0
    missing_forms = 0
    pending_exposure = 0
    expired_exposure = 0
    role_leakage = 0
    procedure_top1_hits = 0
    procedure_top1_total = 0
    wrong_procedure = 0
    state_counts: Counter[str] = Counter()
    domain_rows: dict[str, Counter[str]] = {}

    for case in dataset.get("cases") or []:
        expected = set(case.get("expected_form_ids") or [])
        domain = str(case.get("domain") or "unknown")
        state_counts[str(case.get("expected_state") or "unknown")] += 1
        domain_counter = domain_rows.setdefault(domain, Counter())
        domain_counter["procedure_count"] += 1
        if expected:
            domain_counter["form_bearing_procedure_count"] += 1
        for role in ROLES:
            role_expected = {
                form_id
                for form_id in expected
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
                limit=max(3, len(role_expected)),
            )
            timings.append((time.perf_counter() - started) * 1000)
            actual = {
                str(item.get("form_id") or "")
                for item in result.get("recommended_forms") or []
                if item.get("form_id")
            }
            if role_expected:
                procedure_top1_total += 1
                matches = result.get("procedure_matches") or []
                top_procedure_id = (
                    str(matches[0].get("procedure_id") or "") if matches else ""
                )
                if top_procedure_id == str(case["procedure_id"]):
                    procedure_top1_hits += 1
                else:
                    wrong_procedure += 1
            expected_total += len(role_expected)
            returned_total += len(actual)
            exact_hits += len(actual & role_expected)
            wrong = len(actual - role_expected)
            missing = len(role_expected - actual)
            wrong_forms += wrong
            missing_forms += missing
            domain_counter["expected_form_count"] += len(role_expected)
            domain_counter["returned_form_count"] += len(actual)
            domain_counter["exact_form_hits"] += len(actual & role_expected)
            domain_counter["wrong_form_count"] += wrong
            domain_counter["missing_form_count"] += missing
            for form in result.get("recommended_forms") or []:
                source = catalog._forms_by_id.get(str(form.get("form_id") or ""), {})
                if source.get("review_status") != "approved":
                    pending_exposure += 1
                effective_to = str(source.get("effective_to") or "").strip()
                if (
                    effective_to
                    and date.fromisoformat(effective_to[:10]) < legal_as_of
                ) or source.get("supersedes_form_id"):
                    expired_exposure += 1
                if role == "citizen" and form.get("audience") == "officer":
                    role_leakage += 1

    per_domain: dict[str, dict[str, Any]] = {}
    for domain, counters in sorted(domain_rows.items()):
        expected = counters["expected_form_count"]
        per_domain[domain] = {
            **dict(counters),
            "exact_form_recall": (
                counters["exact_form_hits"] / expected if expected else 1.0
            ),
        }
    report = {
        "schema_version": "feature006-form-lookup-quality-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": legal_as_of.isoformat(),
        "procedure_count": len(dataset.get("cases") or []),
        "role_count": len(ROLES),
        "case_count": len(dataset.get("cases") or []) * len(ROLES),
        "expected_state_counts": dict(state_counts),
        "exact_form_recall": exact_hits / expected_total if expected_total else 1.0,
        "expected_form_count": expected_total,
        "returned_form_count": returned_total,
        "wrong_form_count": wrong_forms,
        "missing_form_count": missing_forms,
        "pending_form_exposure_count": pending_exposure,
        "expired_form_exposure_count": expired_exposure,
        "role_leakage_count": role_leakage,
        "procedure_top1_rate": (
            procedure_top1_hits / procedure_top1_total
            if procedure_top1_total
            else 1.0
        ),
        "wrong_procedure_count": wrong_procedure,
        "lookup_p50_ms": round(statistics.median(timings), 3) if timings else 0.0,
        "lookup_p95_ms": round(_percentile(timings, 0.95), 3),
        "per_domain": per_domain,
        "privacy": {
            "contains_question_text": False,
            "contains_answer_text": False,
            "contains_credentials": False,
        },
        "dataset_integrity": dataset_integrity or {"fresh": True, "stale_sources": []},
    }
    report["technical_pass"] = bool(
        report["dataset_integrity"].get("fresh") is True
        and report["procedure_count"] == 418
        and report["exact_form_recall"] == 1.0
        and wrong_forms == 0
        and missing_forms == 0
        and pending_exposure == 0
        and expired_exposure == 0
        and role_leakage == 0
        and wrong_procedure == 0
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
    dataset_integrity = verify_dataset_sources(dataset)
    report = evaluate(
        catalog=FormCatalog.load_default(),
        dataset=dataset,
        legal_as_of=date.fromisoformat(args.legal_as_of),
        dataset_integrity=dataset_integrity,
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
