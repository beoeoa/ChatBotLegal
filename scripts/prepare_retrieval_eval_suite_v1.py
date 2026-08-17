#!/usr/bin/env python3
"""Prepare an auditable plan for the 2,000-case suite without fabricating law.

The repository currently contains the approved Golden-1000 development set
and Hard-negative-100 development set.  This command records their coverage
and the exact missing work for Hard-negative-500 and sealed Holdout-500.  It
does not synthesize questions, sources, dates or legal answers.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import (
    DOMAINS,
    SPLIT_COUNTS,
    canonical_sha256,
    file_sha256,
)


DEFAULT_GOLDEN = ROOT / "notebook_data" / "feature016-golden-1000-approved.json"
DEFAULT_HARD = ROOT / "reports" / "feature016" / "phase-c" / "hard-negatives-v1.json"
DEFAULT_OUTPUT = ROOT / "reports" / "retrieval-release-v2" / "eval-suite-v1-planning-report.json"
DOMAIN_MAP = {
    "Hộ tịch/chứng thực": "Hộ tịch/chứng thực",
    "Đất đai/xây dựng": "Đất đai/xây dựng/môi trường",
    "Cư trú/an ninh": "Cư trú/căn cước/an ninh",
    "Khiếu nại/tố cáo/xử phạt": "Khiếu nại/tố cáo/tiếp công dân/xử phạt",
    "An sinh/y tế/giáo dục": "An sinh/y tế/giáo dục",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"dataset_object_required:{path}")
    return value


def _cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("cases")
    if values is None:
        values = payload.get("examples")
    return [dict(item) for item in values or [] if isinstance(item, dict)]


def _domain_counts(cases: list[dict[str, Any]]) -> dict[str, int]:
    counts = {domain: 0 for domain in DOMAINS}
    for case in cases:
        domain = DOMAIN_MAP.get(str(case.get("domain") or ""))
        if domain in counts:
            counts[domain] += 1
    return counts


def _field_audit(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Audit legacy inputs without converting or inventing v1 fields."""

    required_case_fields = (
        "case_id",
        "domain",
        "question",
        "legal_as_of",
        "answer_required",
        "expected_refusal",
        "positive_source_groups",
        "hard_negative_sources",
        "reviewer_approval",
        "case_sha256",
    )
    missing_case_fields = {}
    for field in required_case_fields:
        if field == "question":
            missing_case_fields[field] = sum(
                not case.get("question")
                and not (case.get("questions") or {}).get("citizen")
                for case in cases
            )
        elif field == "positive_source_groups":
            missing_case_fields[field] = sum(
                not any(case.get(name) for name in ("positive_source_groups", "expected_sources", "positive_sources"))
                for case in cases
            )
        elif field == "hard_negative_sources":
            missing_case_fields[field] = sum(
                not any(case.get(name) for name in ("hard_negative_sources", "hard_negatives"))
                for case in cases
            )
        elif field == "answer_required":
            missing_case_fields[field] = sum(
                field not in case and "expected_refusal" not in case
                for case in cases
            )
        else:
            missing_case_fields[field] = sum(field not in case or case.get(field) is None for case in cases)
    source_rows: list[dict[str, Any]] = []
    for case in cases:
        groups = (
            case.get("positive_source_groups")
            or case.get("expected_sources")
            or case.get("positive_sources")
            or case.get("hard_negative_sources")
            or case.get("hard_negatives")
            or []
        )
        if isinstance(groups, dict):
            groups = [groups]
        for group in groups:
            sources = group.get("sources") if isinstance(group, dict) else None
            if sources is None:
                sources = [group]
            for source in sources or []:
                if isinstance(source, dict):
                    source_rows.append(source)
    required_source_fields = (
        "law_number",
        "article",
        "official_url",
        "jurisdiction",
        "validity_from",
        "validity_to",
    )
    missing_source_fields = {
        field: sum(not source.get(field) for source in source_rows)
        for field in required_source_fields
    }
    return {
        "case_count": len(cases),
        "source_count": len(source_rows),
        "missing_case_fields": missing_case_fields,
        "missing_source_fields": missing_source_fields,
        "promotable_without_manual_mapping": False,
    }


def build(*, golden: Path, hard: Path) -> dict[str, Any]:
    golden_payload = _load(golden)
    hard_payload = _load(hard)
    golden_cases = _cases(golden_payload)
    hard_cases = _cases(hard_payload)
    existing = {
        "golden-regression": len(golden_cases),
        "hard-negative": len(hard_cases),
        "production-holdout": 0,
    }
    deficits = {
        split: max(0, expected - existing[split])
        for split, expected in SPLIT_COUNTS.items()
    }
    report = {
        "schema_version": "retrieval-eval-suite-v1-planning",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "BLOCKED_PENDING_LEGAL_DATASET_WORK",
        "policy": {
            "do_not_fabricate_legal_cases": True,
            "holdout_sealed": False,
            "implementer_can_read_holdout_content": False,
        },
        "target": {
            "total": sum(SPLIT_COUNTS.values()),
            "splits": SPLIT_COUNTS,
            "domains": list(DOMAINS),
            "domain_balance": "Golden 200/domain; Hard-negative 100/domain; Holdout 100/domain",
        },
        "observed_inputs": {
            "golden-regression": {
                "path": str(golden.resolve()),
                "sha256": file_sha256(golden),
                "case_count": len(golden_cases),
                "domain_counts": _domain_counts(golden_cases),
                "contract_audit": _field_audit(golden_cases),
            },
            "hard-negative": {
                "path": str(hard.resolve()),
                "sha256": file_sha256(hard),
                "case_count": len(hard_cases),
                "domain_counts": _domain_counts(hard_cases),
                "contract_audit": _field_audit(hard_cases),
            },
        },
        "deficits": deficits,
        "blocking_reasons": [
            "legacy_inputs_do_not_satisfy_retrieval-eval-suite-v1_schema",
            "hard_negative_500_not_frozen",
            "production_holdout_500_not_present_or_sealed",
            "official_url_validity_jurisdiction_review_fields_missing_from_legacy_cases",
        ],
        "next_actions": [
            "Legal QA produce 500 hard-negative cases with explicit positive groups and hard-negative sources.",
            "Independent legal QA produce and seal a new 500-case holdout before final benchmark.",
            "Run validate_retrieval_eval_suite_v1.py only after schema, quotas and SHA-256 are complete.",
        ],
        "mutation": {"datasets_mutated": False, "active_pointer_changed": False},
    }
    report["report_sha256"] = canonical_sha256(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--hard-negative", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    report = build(golden=args.golden.resolve(), hard=args.hard_negative.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
    )
    print(json.dumps({
        "status": report["status"],
        "existing": {key: value["case_count"] for key, value in report["observed_inputs"].items()},
        "deficits": report["deficits"],
        "report_sha256": report["report_sha256"],
        "output": str(output),
    }, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
