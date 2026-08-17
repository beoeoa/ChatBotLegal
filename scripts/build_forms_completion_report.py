"""Assemble the privacy-safe final Procedure/Form Catalog report."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = ROOT / "notebook_data" / "forms"
REPORT_DIR = ROOT / "reports" / "feature005" / "forms-completion-20260724"
OUTPUT = REPORT_DIR / "final-report.json"


def _load(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return payload if isinstance(payload, dict) else fallback


def build_report(
    *,
    backend_tests: int,
    frontend_tests: int,
    frontend_build_pass: bool,
    frontend_typecheck_pass: bool,
    write: bool = True,
    output: Path = OUTPUT,
) -> dict[str, Any]:
    procedures = _load(
        FORMS_DIR / "canonical_procedures_v1.json",
        {"procedures": []},
    )["procedures"]
    forms = _load(
        FORMS_DIR / "canonical_forms_catalog_v1.json",
        {"forms": []},
    )["forms"]
    baseline = _load(REPORT_DIR / "baseline.json", {})
    f0 = _load(REPORT_DIR / "f0-scope-report.json", {})
    f2 = _load(REPORT_DIR / "f2-crawl-manifest.json", {})
    review = _load(REPORT_DIR / "legal-review-packet.json", {})
    matrix = _load(REPORT_DIR / "f6-form-resolution-matrix.json", {})
    health = _load(REPORT_DIR / "f7-form-health.json", {})

    domain_counts = Counter(str(item.get("domain") or "unknown") for item in forms)
    audience_counts = Counter(str(item.get("audience") or "unknown") for item in forms)
    format_counts = Counter(
        str(item.get("file_format") or "not_downloaded") for item in forms
    )
    source_counts = Counter(
        str(item.get("source_classification") or "unknown") for item in forms
    )
    review_counts = Counter(
        str(item.get("review_status") or "unknown") for item in forms
    )
    technical_pass = bool(
        f0.get("pass")
        and matrix.get("technical_pass")
        and backend_tests > 0
        and frontend_tests > 0
        and frontend_build_pass
        and frontend_typecheck_pass
    )
    legal_review_recorded = bool(review.get("legal_review_recorded"))
    status = (
        "PASS"
        if technical_pass
        and legal_review_recorded
        and review.get("approved_for_runtime_count", 0) > 0
        and f2.get("status_counts", {}).get("NEEDS_SOURCE_MAPPING", 0) == 0
        else (
            "TECHNICAL_PASS_LEGAL_REVIEW_REQUIRED"
            if technical_pass
            else "TECHNICAL_GATES_FAILED"
        )
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "legal_as_of": "2026-07-24",
        "baseline": {
            "legal_corpus": baseline.get("legal_corpus"),
            "serving_collections": baseline.get("serving_collections"),
            "active_pointer_changed": False,
        },
        "catalog": {
            "canonical_procedure_count": len(procedures),
            "canonical_form_count": len(forms),
            "forms_by_domain": dict(sorted(domain_counts.items())),
            "forms_by_audience": dict(sorted(audience_counts.items())),
            "forms_by_format": dict(sorted(format_counts.items())),
            "forms_by_source_classification": dict(sorted(source_counts.items())),
            "forms_by_review_status": dict(sorted(review_counts.items())),
            "direct_download_count": sum(
                bool(item.get("official_download_url")) for item in forms
            ),
            "local_file_count": sum(bool(item.get("local_path")) for item in forms),
            "eform_count": format_counts.get("online", 0),
            "official_package_count": source_counts.get(
                "official_package_page", 0
            ),
            "no_public_download_count": source_counts.get(
                "no_public_download", 0
            ),
            "blocked_external_count": f2.get("status_counts", {}).get(
                "BLOCKED_EXTERNAL", 0
            ),
            "needs_source_mapping_count": f2.get("status_counts", {}).get(
                "NEEDS_SOURCE_MAPPING", 0
            ),
        },
        "review": {
            "approved_for_runtime_count": review.get(
                "approved_for_runtime_count", 0
            ),
            "legal_review_required_count": review.get(
                "legal_review_required_count", len(forms)
            ),
            "legal_review_recorded": legal_review_recorded,
        },
        "quality": {
            "matrix_case_count": matrix.get("case_count"),
            "procedure_top1_rate": matrix.get("procedure_top1_rate"),
            "wrong_procedure_count": matrix.get("wrong_procedure_count"),
            "wrong_form_count": matrix.get("wrong_form_count"),
            "role_leakage_count": matrix.get("role_leakage_count"),
            "invalid_form_exposure_count": (
                int(matrix.get("pending_reference_seed_exposure_count") or 0)
                + int(matrix.get("expired_or_superseded_exposure_count") or 0)
            ),
            "lookup_p95_ms": matrix.get("lookup_p95_ms"),
            "model_call_count": matrix.get("model_call_count"),
            "backend_tests_passed": backend_tests,
            "frontend_tests_passed": frontend_tests,
            "frontend_build_pass": frontend_build_pass,
            "frontend_typecheck_pass": frontend_typecheck_pass,
        },
        "monitor": {
            "status_counts": health.get("status_counts"),
            "catalog_mutated": health.get("catalog_mutated"),
        },
        "feature_flag": {
            "LEGAL_SECTION_GROUNDING_ENABLED": False,
        },
        "privacy": {
            "contains_question_text": False,
            "contains_answer_text": False,
            "contains_credentials": False,
        },
    }
    if write:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build final form catalog report")
    parser.add_argument("--backend-tests", type=int, required=True)
    parser.add_argument("--frontend-tests", type=int, required=True)
    parser.add_argument("--frontend-build-pass", action="store_true")
    parser.add_argument("--frontend-typecheck-pass", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = build_report(
        backend_tests=args.backend_tests,
        frontend_tests=args.frontend_tests,
        frontend_build_pass=args.frontend_build_pass,
        frontend_typecheck_pass=args.frontend_typecheck_pass,
        output=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] != "TECHNICAL_GATES_FAILED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
