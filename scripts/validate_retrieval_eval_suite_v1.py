#!/usr/bin/env python3
"""Validate the locked Retrieval Release V2 evaluation suite.

This validator intentionally fails closed.  Existing Golden-1000 and
Hard-negative-100 files are legacy development inputs; they are not silently
promoted to the new 2,000-case production acceptance suite.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
import unicodedata

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # pragma: no cover - the repository test environment has it
    Draft202012Validator = None
    FormatChecker = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.retrieval_release_contracts import (
    DOMAINS,
    EVAL_SUITE_SCHEMA_VERSION,
    SPLIT_COUNTS,
    DOMAIN_BLOCK_SIZE,
    canonical_sha256,
    file_sha256,
)


SCHEMA = ROOT / "specs" / "018-production-release-readiness" / "contracts" / "retrieval-eval-suite-v1.schema.json"
TAG_TARGETS = {
    "exact_law_article": 20,
    "procedure": 20,
    "multi_issue": 15,
    "validity_trap": 20,
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("suite_root_must_be_object")
    return value


def _schema_errors(payload: dict[str, Any]) -> list[str]:
    if Draft202012Validator is None:
        return ["jsonschema_dependency_missing"]
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        f"{'.'.join(str(part) for part in error.absolute_path)}:{error.message}"
        for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    ]


def _scaled_target(base: int, split: str) -> int:
    size = DOMAIN_BLOCK_SIZE[split]
    return base * size // 100


def _normalized_question(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def validate_suite(payload: dict[str, Any], *, require_complete: bool = True) -> dict[str, Any]:
    errors = _schema_errors(payload)
    cases = [item for item in payload.get("cases", []) if isinstance(item, dict)]
    ids = [str(item.get("case_id") or "") for item in cases]
    if len(ids) != len(set(ids)):
        errors.append("duplicate_case_id")
    question_keys: dict[str, list[tuple[str, str]]] = {}
    for item in cases:
        normalized = _normalized_question(item.get("question"))
        if normalized:
            question_keys.setdefault(normalized, []).append(
                (str(item.get("case_id") or ""), str(item.get("split") or ""))
            )
        declared_case_sha256 = str(item.get("case_sha256") or "")
        case_body = {key: value for key, value in item.items() if key != "case_sha256"}
        if declared_case_sha256 != canonical_sha256(case_body):
            errors.append(f"case_sha256_mismatch:{item.get('case_id')}")
    for values in question_keys.values():
        if len(values) > 1:
            ids_for_question = ",".join(sorted(case_id for case_id, _ in values))
            errors.append(f"normalized_question_duplicate:{ids_for_question}")
    if require_complete and len(cases) != sum(SPLIT_COUNTS.values()):
        errors.append(f"total_case_count:{len(cases)}!={sum(SPLIT_COUNTS.values())}")

    split_counts = {split: sum(item.get("split") == split for item in cases) for split in SPLIT_COUNTS}
    for split, expected in SPLIT_COUNTS.items():
        if require_complete and split_counts[split] != expected:
            errors.append(f"{split}_case_count:{split_counts[split]}!={expected}")
    domain_counts: dict[str, dict[str, int]] = {
        split: {domain: 0 for domain in DOMAINS} for split in SPLIT_COUNTS
    }
    for item in cases:
        split = item.get("split")
        domain = item.get("domain")
        if split in domain_counts and domain in domain_counts[split]:
            domain_counts[split][domain] += 1

    quota: dict[str, dict[str, dict[str, int]]] = {}
    for split in SPLIT_COUNTS:
        quota[split] = {}
        for domain in DOMAINS:
            block = [
                item for item in cases
                if item.get("split") == split and item.get("domain") == domain
            ]
            if require_complete and len(block) != DOMAIN_BLOCK_SIZE[split]:
                errors.append(
                    f"domain_block_count:{split}:{domain}:{len(block)}!={DOMAIN_BLOCK_SIZE[split]}"
                )
            actual = {
                "current_answer": sum(
                    bool(item.get("answer_required")) and item.get("temporal_scope") == "current"
                    for item in block
                ),
                "historical_answer": sum(
                    bool(item.get("answer_required")) and item.get("temporal_scope") == "historical"
                    for item in block
                ),
                "temporal_refusal": sum(
                    item.get("refusal_category") in {"temporal_unknown", "temporal_conflict"}
                    and bool(item.get("expected_refusal"))
                    for item in block
                ),
                "insufficient_facts_refusal": sum(
                    item.get("refusal_category") == "insufficient_facts"
                    and bool(item.get("expected_refusal"))
                    for item in block
                ),
                "out_of_scope_refusal": sum(
                    item.get("refusal_category") == "out_of_scope"
                    and bool(item.get("expected_refusal"))
                    for item in block
                ),
            }
            target = {
                "current_answer": _scaled_target(65, split),
                "historical_answer": _scaled_target(15, split),
                # Each 100-case domain block contains 5 temporal
                # unknown/conflict refusals; the remaining refusal quota is
                # 5 insufficient-facts + 10 out-of-scope.
                "temporal_refusal": _scaled_target(5, split),
                "insufficient_facts_refusal": _scaled_target(5, split),
                "out_of_scope_refusal": _scaled_target(10, split),
            }
            if sum(target.values()) != DOMAIN_BLOCK_SIZE[split]:
                errors.append(f"quota_contract_total:{split}:{domain}:{sum(target.values())}!={DOMAIN_BLOCK_SIZE[split]}")
            quota[split][domain] = {"actual": actual, "target": target}
            if require_complete:
                for name, expected in target.items():
                    if actual[name] != expected:
                        errors.append(
                            f"quota:{split}:{domain}:{name}:{actual[name]}!={expected}"
                        )
            for tag, minimum in TAG_TARGETS.items():
                count = sum(tag in set(item.get("tags") or []) for item in block)
                expected_min = _scaled_target(minimum, split)
                if require_complete and count < expected_min:
                    errors.append(f"tag_quota:{split}:{domain}:{tag}:{count}<{expected_min}")

    for item in cases:
        expected_refusal = bool(item.get("expected_refusal"))
        if expected_refusal == bool(item.get("answer_required")):
            errors.append(f"refusal_contract:{item.get('case_id')}")
        if not expected_refusal and not item.get("positive_source_groups"):
            errors.append(f"positive_sources_missing:{item.get('case_id')}")
        if item.get("split") == "hard-negative" and not expected_refusal and not item.get("hard_negative_sources"):
            errors.append(f"hard_negative_source_missing:{item.get('case_id')}")
        group_ids = {str(group.get("group_id")) for group in item.get("positive_source_groups") or []}
        for issue in item.get("issue_groups") or []:
            if not set(issue.get("required_source_group_ids") or []) <= group_ids:
                errors.append(f"issue_group_reference_missing:{item.get('case_id')}:{issue.get('issue_id')}")
        approval = item.get("reviewer_approval") or {}
        if item.get("split") == "production-holdout" and not approval:
            errors.append(f"holdout_review_missing:{item.get('case_id')}")
        if expected_refusal and item.get("refusal_category") in {"out_of_scope", "insufficient_facts"}:
            classification = item.get("query_classification") or {}
            if not classification:
                errors.append(f"refusal_query_classification_missing:{item.get('case_id')}")

    policy = payload.get("review_policy") or {}
    if require_complete and not bool(policy.get("holdout_sealed")):
        errors.append("holdout_not_sealed")
    if require_complete and bool(policy.get("golden_development_allowed")) is False:
        errors.append("golden_development_policy_missing")
    if require_complete and bool(policy.get("hard_negative_development_allowed")) is False:
        errors.append("hard_negative_development_policy_missing")
    if require_complete:
        body = {key: value for key, value in payload.items() if key != "suite_sha256"}
        declared = str(payload.get("suite_sha256") or "")
        if declared and canonical_sha256(body) != declared:
            errors.append("suite_sha256_mismatch")
    return {
        "schema_version": payload.get("schema_version"),
        "valid": not errors,
        "errors": sorted(set(errors)),
        "case_count": len(cases),
        "split_counts": split_counts,
        "domain_counts": domain_counts,
        "quota": quota,
        "holdout_sealed": bool(policy.get("holdout_sealed")),
    }


def validate_development_suite(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate the 1,500 visible cases used for configuration selection.

    Production holdout content is forbidden here. Its public envelope is
    checked separately and the hidden cases are opened only by the custodian.
    """

    report = validate_suite(payload, require_complete=False)
    errors = list(report["errors"])
    cases = [item for item in payload.get("cases") or [] if isinstance(item, dict)]
    if any(item.get("split") == "production-holdout" for item in cases):
        errors.append("production_holdout_content_forbidden_in_development_suite")
    expected_splits = {"golden-regression": 1_000, "hard-negative": 500}
    for split, expected in expected_splits.items():
        actual = sum(item.get("split") == split for item in cases)
        if actual != expected:
            errors.append(f"development_split_count:{split}:{actual}!={expected}")
        for domain in DOMAINS:
            block = [
                item for item in cases
                if item.get("split") == split and item.get("domain") == domain
            ]
            expected_block = DOMAIN_BLOCK_SIZE[split]
            if len(block) != expected_block:
                errors.append(f"development_domain_count:{split}:{domain}:{len(block)}!={expected_block}")
            quota = ((report.get("quota") or {}).get(split) or {}).get(domain) or {}
            for name, target in (quota.get("target") or {}).items():
                actual_value = (quota.get("actual") or {}).get(name)
                if actual_value != target:
                    errors.append(
                        f"development_quota:{split}:{domain}:{name}:{actual_value}!={target}"
                    )
            for tag, minimum in TAG_TARGETS.items():
                count = sum(tag in set(item.get("tags") or []) for item in block)
                expected_min = _scaled_target(minimum, split)
                if count < expected_min:
                    errors.append(
                        f"development_tag_quota:{split}:{domain}:{tag}:{count}<{expected_min}"
                    )
    if len(cases) != 1_500:
        errors.append(f"development_total_case_count:{len(cases)}!=1500")
    policy = payload.get("review_policy") or {}
    if policy.get("golden_development_allowed") is not True:
        errors.append("golden_development_policy_missing")
    if policy.get("hard_negative_development_allowed") is not True:
        errors.append("hard_negative_development_policy_missing")
    body = {key: value for key, value in payload.items() if key != "suite_sha256"}
    if str(payload.get("suite_sha256") or "") != canonical_sha256(body):
        errors.append("suite_sha256_mismatch")
    report.update({
        "valid": not errors,
        "errors": sorted(set(errors)),
        "case_count": len(cases),
        "holdout_content_present": any(
            item.get("split") == "production-holdout" for item in cases
        ),
    })
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument("--development-only", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    payload = _load(args.suite.resolve())
    report = (
        validate_development_suite(payload)
        if args.development_only
        else validate_suite(payload, require_complete=not args.allow_draft)
    )
    report.update({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "suite_path": str(args.suite.resolve()),
        "suite_file_sha256": file_sha256(args.suite),
        "status": "PASS" if report["valid"] else "FAIL",
        "mutation": {"dataset_mutated": False, "active_pointer_changed": False},
    })
    if args.report:
        output = args.report.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output.with_suffix(output.suffix + ".sha256").write_text(
            f"{file_sha256(output)}  {output.name}\n", encoding="ascii"
        )
    # Windows PowerShell may expose a legacy CP1252 stdout.  Keep the CLI
    # machine-readable and avoid a UnicodeEncodeError on Vietnamese domains;
    # the report file, when requested, remains UTF-8 with native characters.
    # Keep stdout bounded for CI/PowerShell.  The complete error list is
    # written to --report; console output is a stable summary plus samples.
    console_report = {
        "schema_version": report.get("schema_version"),
        "status": report.get("status"),
        "valid": report.get("valid"),
        "case_count": report.get("case_count"),
        "split_counts": report.get("split_counts"),
        "holdout_sealed": report.get("holdout_sealed"),
        "error_count": len(report.get("errors") or []),
        "first_errors": (report.get("errors") or [])[:20],
        "suite_path": report.get("suite_path"),
    }
    print(json.dumps(console_report, ensure_ascii=True))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
