"""Evaluate Step 2 issue planning for 167 golden and nine role cases."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_section_grounding import LegalIssue, plan_legal_issues
from api.legal_structured_answer import (
    build_issue_coverage_matrix,
    derive_required_facets_by_issue,
    ensure_required_facet_issues,
)

GOLDEN = ROOT / "notebook_data/legal-golden-set.json"
ROLES = ROOT / "tests/fixtures/feature005_role_matrix.json"


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


def _subject(question: str) -> str:
    clause = re.split(r"[,;?!]", question, maxsplit=1)[0].strip()
    return clause if len(clause.split()) >= 2 else question.strip()


def requested_facets(
    question: str,
    *,
    expected_form: Mapping[str, Any] | None = None,
    declared: Sequence[str] | None = None,
) -> list[str]:
    if declared:
        return list(dict.fromkeys(str(item) for item in declared if str(item).strip()))
    folded = _fold(question)
    markers = {
        "condition": ("neu ", "truong hop", "dieu kien"),
        "authority": ("tham quyen", "co quan", "ubnd", "noi nop", "nop o dau"),
        "documents": ("ho so", "giay to"),
        "procedure": ("thu tuc", "trinh tu", "cac buoc", "lam gi", "ra sao"),
        "deadline": ("thoi han", "bao lau"),
        "fee": ("le phi", "phi"),
        "form": ("bieu mau", "to khai", "mau "),
    }
    facets = [
        facet
        for facet, cues in markers.items()
        if any(cue in folded for cue in cues)
    ]
    if expected_form and "form" not in facets:
        facets.append("form")
    return facets


def _issue_context(case: Mapping[str, Any], question: str) -> dict[str, Any]:
    expected_forms = [
        item for item in (case.get("expected_forms") or []) if isinstance(item, dict)
    ]
    return {
        "subject": _subject(question),
        "location": "",
        "facts": tuple(str(item) for item in (case.get("critical_facts") or [])),
        "applied_date": case.get("event_date") or case.get("legal_as_of"),
        "domain": case.get("domain") or "",
        "expected_sources": tuple(
            case.get("expected_citations")
            or case.get("required_citations_all")
            or ()
        ),
        "expected_form": expected_forms[0] if expected_forms else None,
    }


def evaluate_case(
    *,
    case_id: str,
    question: str,
    context: Mapping[str, Any],
    required_facets: Sequence[str],
) -> dict[str, Any]:
    planned = plan_legal_issues(question, context=context, max_issues=6)
    issues = ensure_required_facet_issues(
        question=question,
        issues=planned,
        required_sections=required_facets,
        max_issues=6,
    )
    assigned = derive_required_facets_by_issue(
        issues=issues,
        required_sections=required_facets,
    )
    matrix = build_issue_coverage_matrix(
        issues=issues,
        evidence_by_id={},
        required_facets_by_issue=assigned,
    )
    assigned_facets = set().union(
        *(set(items) for items in assigned.values())
    ) if assigned else set()
    required = set(required_facets)
    failures: list[str] = []
    if not 1 <= len(issues) <= 6:
        failures.append("ISSUE_COUNT_OUT_OF_RANGE")
    if any(len(issue.query_text.split()) < 2 for issue in issues):
        failures.append("EMPTY_OR_KEYWORD_ONLY_ISSUE")
    if len({issue.query_text.casefold() for issue in issues}) != len(issues):
        failures.append("DUPLICATE_ISSUE")
    if not required.issubset(assigned_facets):
        failures.append("FACET_NOT_PLANNED")
    if set(matrix) != {issue.issue_id for issue in issues}:
        failures.append("COVERAGE_MATRIX_INCOMPLETE")
    for issue in issues:
        if (
            issue.subject != context.get("subject")
            or issue.location != context.get("location")
            or issue.facts != tuple(context.get("facts") or ())
            or issue.applied_date != context.get("applied_date")
            or issue.expected_sources != tuple(context.get("expected_sources") or ())
            or issue.expected_form != context.get("expected_form")
        ):
            failures.append("CONTEXT_NOT_PRESERVED")
            break
        if context.get("domain") and issue.domain != context.get("domain"):
            failures.append("DOMAIN_NOT_PRESERVED")
            break
    return {
        "case_id": case_id,
        "issue_count": len(issues),
        "required_facets": sorted(required),
        "planned_facets": sorted(assigned_facets),
        "matrix_issue_count": len(matrix),
        "failures": sorted(set(failures)),
        "pass": not failures,
    }


def evaluate_all(root: Path = ROOT) -> dict[str, Any]:
    golden_payload = json.loads(
        (root / GOLDEN.relative_to(ROOT)).read_text(encoding="utf-8-sig")
    )
    role_payload = json.loads(
        (root / ROLES.relative_to(ROOT)).read_text(encoding="utf-8-sig")
    )
    golden_results = []
    for case in golden_payload.get("questions") or []:
        question = str(case.get("question_citizen") or "")
        context = _issue_context(case, question)
        facets = requested_facets(
            question,
            expected_form=context.get("expected_form"),
        )
        golden_results.append(
            evaluate_case(
                case_id=str(case.get("id") or ""),
                question=question,
                context=context,
                required_facets=facets,
            )
        )
    role_results = []
    for case in role_payload.get("cases") or []:
        question = str(case.get("question") or "")
        context = _issue_context(case, question)
        role_results.append(
            evaluate_case(
                case_id=str(case.get("id") or ""),
                question=question,
                context=context,
                required_facets=requested_facets(
                    question,
                    expected_form=context.get("expected_form"),
                    declared=case.get("required_facets") or (),
                ),
            )
        )
    all_results = golden_results + role_results
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "golden_count": len(golden_results),
        "role_count": len(role_results),
        "case_count": len(all_results),
        "passed_count": sum(item["pass"] for item in all_results),
        "failed_count": sum(not item["pass"] for item in all_results),
        "max_issue_count": max((item["issue_count"] for item in all_results), default=0),
        "failure_counts": {
            code: sum(code in item["failures"] for item in all_results)
            for code in sorted(
                {
                    code
                    for item in all_results
                    for code in item["failures"]
                }
            )
        },
        "pass": all(item["pass"] for item in all_results),
        "results": all_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate_all()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "golden_count": report["golden_count"],
                "role_count": report["role_count"],
                "passed_count": report["passed_count"],
                "failed_count": report["failed_count"],
                "pass": report["pass"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
