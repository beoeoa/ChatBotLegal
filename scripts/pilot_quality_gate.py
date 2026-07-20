"""Fail-closed quality gate for the 30-case Ask pilot matrix.

This module only reads expert-review and runner artifacts. It never updates an
expert record, approves a review, calls Ask, or changes the legal corpus.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data" / "pilot" / "ask_quality_manifest.json"
DEFAULT_EXPERT = ROOT / "notebook_data" / "legal-golden-expert-review.json"
DEFAULT_RUN = ROOT / "reports" / "pilot-ask-matrix-latest.json"
DEFAULT_ISOLATION = ROOT / "reports" / "pilot-role-isolation-latest.json"
DEFAULT_REPORT = ROOT / "reports" / "pilot-quality-gate.json"

DOMAINS = (
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "cu_tru_an_ninh",
    "khieu_nai_to_cao_xu_phat",
    "an_sinh_y_te_giao_duc",
)
ROLES = ("citizen", "officer")
SCENARIO_CLASSES = ("routine", "complex", "boundary")
ENDPOINTS = ("/api/search/ask/simple", "/api/search/ask/progress")
CONCURRENCY_LEVELS = (1, 5, 10, 20)

MIN_GROUP_SCORE = 9.0
MIN_CITATION_SUCCESS = 0.99
MIN_FORM_SUCCESS = 0.99
MAX_ERROR_RATE = 0.01
MAX_REPAIR_RATE = 0.10
REPORT_SCHEMA_VERSION = "1.0"
ISOLATION_RESOURCE_TYPES = {
    "conversation",
    "profile",
    "notebook",
    "legal_dossier",
}
ISOLATION_DENIAL_STATUSES = {403, 404}
_ISOLATION_FORBIDDEN_KEYS = {
    "answer",
    "authorization",
    "body",
    "citation",
    "cookie",
    "credential",
    "exception",
    "message",
    "password",
    "prompt",
    "question",
    "response",
    "secret",
    "token",
}


def artifact_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _case_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("domain") or ""),
        str(item.get("role") or ""),
        str(item.get("scenario_class") or ""),
    )


def validate_manifest(
    manifest: dict[str, Any], expert_payload: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    if manifest.get("version") != "1.0":
        errors.append("manifest_version_mismatch")
    if tuple(manifest.get("domains") or ()) != DOMAINS:
        errors.append("manifest_domains_mismatch")
    if tuple(manifest.get("roles") or ()) != ROLES:
        errors.append("manifest_roles_mismatch")
    if tuple(manifest.get("scenario_classes") or ()) != SCENARIO_CLASSES:
        errors.append("manifest_scenario_classes_mismatch")
    cases = [item for item in manifest.get("cases") or [] if isinstance(item, dict)]
    expected_combinations = set(
        itertools.product(DOMAINS, ROLES, SCENARIO_CLASSES)
    )
    actual_combinations = {_case_key(item) for item in cases}
    review_ids = [str(item.get("review_id") or "") for item in cases]

    if len(cases) != len(expected_combinations):
        errors.append(f"manifest_case_count:{len(cases)}")
    if actual_combinations != expected_combinations:
        errors.append("manifest_cartesian_product_mismatch")
    duplicates = sorted(
        review_id
        for review_id, count in Counter(review_ids).items()
        if not review_id or count > 1
    )
    if duplicates:
        errors.append("duplicate_or_blank_review_id:" + ",".join(duplicates))

    expert_records = [
        item
        for item in expert_payload.get("records") or []
        if isinstance(item, dict)
    ]
    expert_by_id = {
        str(item.get("review_id")): item
        for item in expert_records
        if item.get("review_id")
    }
    for item in cases:
        review_id = str(item.get("review_id") or "")
        review = expert_by_id.get(review_id)
        if review is None:
            errors.append(f"unknown_review_id:{review_id}")
            continue
        if str(review.get("domain") or "") != str(item.get("domain") or ""):
            errors.append(f"review_domain_mismatch:{review_id}")
        if str(review.get("role") or "") != str(item.get("role") or ""):
            errors.append(f"review_role_mismatch:{review_id}")
    return errors


def _approved_expert_record(item: dict[str, Any]) -> bool:
    score = item.get("expert_score")
    return bool(
        item.get("expert_review_status") == "approved"
        and str(item.get("expert_name") or "").strip()
        and str(item.get("reviewed_at") or "").strip()
        and isinstance(score, (int, float))
        and math.isfinite(float(score))
        and 0 <= float(score) <= 10
    )


def _attempt_key(item: dict[str, Any]) -> tuple[str, str, int]:
    try:
        concurrency = int(item.get("concurrency"))
    except (TypeError, ValueError):
        concurrency = -1
    return (
        str(item.get("review_id") or ""),
        str(item.get("endpoint") or ""),
        concurrency,
    )


def _critical_hallucination(item: dict[str, Any]) -> bool:
    if bool(item.get("critical_hallucination")):
        return True
    for error in item.get("critical_errors") or []:
        if isinstance(error, dict):
            severity = str(error.get("severity") or "critical").casefold()
            if severity == "critical":
                return True
        elif str(error).strip():
            return True
    return False


def _safe_rate(good: int, total: int) -> float:
    return good / total if total > 0 else 0.0


def evaluate_quality_gate(
    manifest: dict[str, Any],
    expert_payload: dict[str, Any],
    run_payload: dict[str, Any],
) -> dict[str, Any]:
    manifest_errors = validate_manifest(manifest, expert_payload)
    cases = [item for item in manifest.get("cases") or [] if isinstance(item, dict)]
    manifest_ids = {str(item.get("review_id") or "") for item in cases}
    expert_by_id = {
        str(item.get("review_id")): item
        for item in expert_payload.get("records") or []
        if isinstance(item, dict) and item.get("review_id")
    }
    selected_reviews = [
        expert_by_id[review_id]
        for review_id in sorted(manifest_ids)
        if review_id in expert_by_id
    ]
    unapproved = sorted(
        str(item.get("review_id") or "")
        for item in selected_reviews
        if not _approved_expert_record(item)
    )

    grouped_scores: dict[str, list[float]] = defaultdict(list)
    for item in selected_reviews:
        score = item.get("expert_score")
        if isinstance(score, (int, float)) and math.isfinite(float(score)):
            grouped_scores[f"{item.get('domain')}:{item.get('role')}"].append(
                float(score)
            )
    group_scores = {
        key: round(sum(values) / len(values), 5)
        for key, values in sorted(grouped_scores.items())
        if values
    }
    expected_groups = {
        f"{domain}:{role}" for domain, role in itertools.product(DOMAINS, ROLES)
    }

    expected_attempt_keys = {
        (review_id, endpoint, concurrency)
        for review_id, endpoint, concurrency in itertools.product(
            manifest_ids, ENDPOINTS, CONCURRENCY_LEVELS
        )
    }
    results = [
        item for item in run_payload.get("results") or [] if isinstance(item, dict)
    ]
    actual_keys = [_attempt_key(item) for item in results]
    key_counts = Counter(actual_keys)
    duplicate_attempts = sorted(
        key for key, count in key_counts.items() if count > 1
    )
    actual_key_set = set(actual_keys)
    missing_attempts = sorted(expected_attempt_keys - actual_key_set)
    unexpected_attempts = sorted(actual_key_set - expected_attempt_keys)
    full_coverage = bool(
        expected_attempt_keys
        and not missing_attempts
        and not unexpected_attempts
        and not duplicate_attempts
        and len(results) == len(expected_attempt_keys)
    )

    missing_count = len(missing_attempts)
    error_count = missing_count + sum(
        1 for item in results if item.get("status") != "completed"
    )
    expected_attempt_count = len(expected_attempt_keys)
    error_rate = _safe_rate(error_count, expected_attempt_count)
    repair_rate = _safe_rate(
        sum(1 for item in results if bool(item.get("repair_used"))),
        expected_attempt_count,
    )
    repair_telemetry_complete = bool(results) and all(
        item.get("repair_observed") is True for item in results
    )
    critical_count = sum(1 for item in results if _critical_hallucination(item))

    metric_inputs_consistent = True
    for item in results:
        expert = expert_by_id.get(str(item.get("review_id") or ""), {})
        expected_citation_applicable = bool(
            expert.get("expected_documents") or expert.get("expected_articles")
        )
        expected_form_applicable = bool(expert.get("official_form_ids"))
        if item.get("citation_applicable") is not expected_citation_applicable:
            metric_inputs_consistent = False
        if item.get("form_applicable") is not expected_form_applicable:
            metric_inputs_consistent = False

    citation_rows = [item for item in results if item.get("citation_applicable") is True]
    form_rows = [item for item in results if item.get("form_applicable") is True]
    citation_rate = _safe_rate(
        sum(1 for item in citation_rows if item.get("citation_ok") is True),
        len(citation_rows),
    )
    form_rate = _safe_rate(
        sum(1 for item in form_rows if item.get("forms_ok") is True),
        len(form_rows),
    )

    checks = {
        "manifest_valid": not manifest_errors,
        "full_matrix_coverage": full_coverage,
        "metric_inputs_match_expert_records": metric_inputs_consistent,
        "all_expert_records_approved": (
            len(selected_reviews) == len(cases) and not unapproved
        ),
        "all_group_scores_at_least_9": (
            set(group_scores) == expected_groups
            and all(score >= MIN_GROUP_SCORE for score in group_scores.values())
        ),
        "zero_critical_hallucinations": critical_count == 0,
        "citation_success_at_least_99_percent": (
            bool(citation_rows) and citation_rate >= MIN_CITATION_SUCCESS
        ),
        "form_success_at_least_99_percent": (
            bool(form_rows) and form_rate >= MIN_FORM_SUCCESS
        ),
        "error_rate_below_1_percent": error_rate < MAX_ERROR_RATE,
        "repair_telemetry_complete": repair_telemetry_complete,
        "repair_rate_below_10_percent": repair_rate < MAX_REPAIR_RATE,
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "pass": all(checks.values()),
        "artifact_fingerprints": {
            "manifest_sha256": artifact_fingerprint(manifest),
            "expert_sha256": artifact_fingerprint(expert_payload),
            "run_sha256": artifact_fingerprint(run_payload),
        },
        "thresholds": {
            "minimum_group_score": MIN_GROUP_SCORE,
            "minimum_citation_success_rate": MIN_CITATION_SUCCESS,
            "minimum_form_success_rate": MIN_FORM_SUCCESS,
            "maximum_error_rate_exclusive": MAX_ERROR_RATE,
            "maximum_repair_rate_exclusive": MAX_REPAIR_RATE,
        },
        "checks": checks,
        "manifest": {
            "case_count": len(cases),
            "errors": manifest_errors,
        },
        "expert_review": {
            "selected_count": len(selected_reviews),
            "unapproved_review_ids": unapproved,
        },
        "group_scores": group_scores,
        "coverage": {
            "expected_attempts": expected_attempt_count,
            "actual_results": len(results),
            "missing_attempts": [list(key) for key in missing_attempts],
            "unexpected_attempts": [list(key) for key in unexpected_attempts],
            "duplicate_attempts": [list(key) for key in duplicate_attempts],
        },
        "metrics": {
            "critical_hallucinations": critical_count,
            "citation_success_rate": round(citation_rate, 5),
            "form_success_rate": round(form_rate, 5),
            "error_rate": round(error_rate, 5),
            "repair_rate": round(repair_rate, 5),
        },
    }


def validate_role_isolation_evidence(payload: dict[str, Any]) -> list[str]:
    """Validate the separate 8-attempt cross-account evidence artifact."""

    errors: list[str] = []

    def scan(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).casefold() in _ISOLATION_FORBIDDEN_KEYS:
                    errors.append(f"forbidden_isolation_field:{key}")
                scan(item)
        elif isinstance(value, list):
            for item in value:
                scan(item)

    scan(payload)
    if payload.get("schema_version") != "1.0":
        errors.append("isolation_schema_version_mismatch")
    if payload.get("evidence_type") != "cross_account_role_isolation":
        errors.append("isolation_evidence_type_mismatch")
    if payload.get("quality_matrix_attempts") != 0:
        errors.append("isolation_must_not_count_as_quality_attempt")
    if payload.get("expected_attempts") != 8:
        errors.append("isolation_expected_attempt_count_mismatch")
    if payload.get("executed_attempts") != 8:
        errors.append("isolation_executed_attempt_count_mismatch")
    results = [
        item for item in payload.get("results") or [] if isinstance(item, dict)
    ]
    if len(results) != 8:
        errors.append(f"isolation_result_count:{len(results)}")
    attempt_ids = [str(item.get("attempt_id") or "") for item in results]
    if len(set(attempt_ids)) != 8 or any(not value for value in attempt_ids):
        errors.append("isolation_attempt_ids_not_unique")
    expected_pairs = {
        (direction, resource_type)
        for direction, resource_type in itertools.product(
            ("a_to_b", "b_to_a"), ISOLATION_RESOURCE_TYPES
        )
    }
    actual_pairs = {
        (str(item.get("direction") or ""), str(item.get("resource_type") or ""))
        for item in results
    }
    if actual_pairs != expected_pairs:
        errors.append("isolation_direction_resource_coverage_mismatch")
    if any(
        item.get("status_code") not in ISOLATION_DENIAL_STATUSES
        or item.get("passed") is not True
        for item in results
    ):
        errors.append("isolation_access_not_denied")
    if any(
        str(item.get("actor_account_id") or "")
        == str(item.get("target_account_id") or "")
        for item in results
    ):
        errors.append("isolation_actor_target_not_distinct")
    if payload.get("pass") is not True:
        errors.append("isolation_report_not_passing")
    return sorted(set(errors))


def attach_role_isolation_gate(
    quality_report: dict[str, Any],
    isolation_payload: dict[str, Any],
) -> dict[str, Any]:
    """Require role-isolation evidence without changing the evaluator API."""

    report = copy.deepcopy(quality_report)
    isolation_errors = validate_role_isolation_evidence(isolation_payload)
    checks = report.setdefault("checks", {})
    checks["role_isolation_evidence_passes"] = not isolation_errors
    fingerprints = report.setdefault("artifact_fingerprints", {})
    fingerprints["role_isolation_sha256"] = artifact_fingerprint(isolation_payload)
    report["role_isolation"] = {
        "expected_attempts": 8,
        "actual_attempts": len(isolation_payload.get("results") or []),
        "quality_matrix_attempts": isolation_payload.get(
            "quality_matrix_attempts"
        ),
        "errors": isolation_errors,
    }
    report["pass"] = bool(report.get("pass") and not isolation_errors)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate the offline 30-case pilot quality artifacts."
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expert", type=Path, default=DEFAULT_EXPERT)
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--isolation", type=Path, default=DEFAULT_ISOLATION)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = attach_role_isolation_gate(
        evaluate_quality_gate(
            _read_json(args.manifest),
            _read_json(args.expert),
            _read_json(args.run),
        ),
        _read_json(args.isolation),
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
