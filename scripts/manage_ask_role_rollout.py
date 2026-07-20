"""Manage the offline Ask role-rollout state with fail-closed evidence checks.

The command is read-only by default. ``--advance`` and ``--rollback`` are the
only actions that write state, and this module never calls the Ask API.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.pilot_quality_gate import (
        DEFAULT_EXPERT as DEFAULT_PILOT_EXPERT,
        DEFAULT_MANIFEST as DEFAULT_PILOT_MANIFEST,
        DEFAULT_RUN as DEFAULT_PILOT_RUN,
        DOMAINS,
        MAX_ERROR_RATE,
        MAX_REPAIR_RATE,
        MIN_CITATION_SUCCESS,
        MIN_FORM_SUCCESS,
        MIN_GROUP_SCORE,
        REPORT_SCHEMA_VERSION,
        ROLES,
        artifact_fingerprint,
    )
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from pilot_quality_gate import (  # type: ignore[no-redef]
        DEFAULT_EXPERT as DEFAULT_PILOT_EXPERT,
        DEFAULT_MANIFEST as DEFAULT_PILOT_MANIFEST,
        DEFAULT_RUN as DEFAULT_PILOT_RUN,
        DOMAINS,
        MAX_ERROR_RATE,
        MAX_REPAIR_RATE,
        MIN_CITATION_SUCCESS,
        MIN_FORM_SUCCESS,
        MIN_GROUP_SCORE,
        REPORT_SCHEMA_VERSION,
        ROLES,
        artifact_fingerprint,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / "data" / "pilot" / "ask_role_rollout.json"
DEFAULT_QUALITY_REPORT = ROOT / "reports" / "pilot-quality-gate.json"
DEFAULT_SOAK_REPORT = ROOT / "reports" / "ask-role-soak-latest.json"

ROLLOUT_ROLES = ("admin", "officer", "citizen")
SOAK_HOURS = 24.0
STATE_SCHEMA_VERSION = "1.0"
EXPECTED_ATTEMPTS = 30 * 2 * 4

REQUIRED_QUALITY_CHECKS = (
    "manifest_valid",
    "full_matrix_coverage",
    "metric_inputs_match_expert_records",
    "all_expert_records_approved",
    "all_group_scores_at_least_9",
    "zero_critical_hallucinations",
    "citation_success_at_least_99_percent",
    "form_success_at_least_99_percent",
    "error_rate_below_1_percent",
    "repair_telemetry_complete",
    "repair_rate_below_10_percent",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RolloutBlocked(RuntimeError):
    """Raised when evidence cannot safely authorize a rollout transition."""


def default_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "stage": 0,
        "enabled_roles": [],
        "stage_started_at": None,
        "updated_at": None,
        "history": [],
    }


def _read_json(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists() and not required:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RolloutBlocked(f"cannot read rollout evidence: {path}") from exc
    if not isinstance(payload, dict):
        raise RolloutBlocked(f"rollout evidence must be a JSON object: {path}")
    return payload


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _same_number(value: Any, expected: float) -> bool:
    parsed = _as_number(value)
    return parsed is not None and math.isclose(parsed, expected, abs_tol=1e-12)


def _valid_fingerprints(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    required = {"manifest_sha256", "expert_sha256", "run_sha256"}
    return required.issubset(payload) and all(
        bool(_SHA256.fullmatch(str(payload.get(key) or ""))) for key in required
    )


def valid_quality_report(report: dict[str, Any]) -> bool:
    """Verify the complete gate contract rather than trusting ``pass`` alone."""

    checks = report.get("checks")
    thresholds = report.get("thresholds")
    manifest = report.get("manifest")
    expert = report.get("expert_review")
    coverage = report.get("coverage")
    metrics = report.get("metrics")
    groups = report.get("group_scores")
    expected_groups = {
        f"{domain}:{role}" for domain in DOMAINS for role in ROLES
    }

    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        return False
    if report.get("pass") is not True:
        return False
    if not isinstance(checks, dict) or not all(
        checks.get(name) is True for name in REQUIRED_QUALITY_CHECKS
    ):
        return False
    if not isinstance(thresholds, dict) or not all(
        (
            _same_number(thresholds.get("minimum_group_score"), MIN_GROUP_SCORE),
            _same_number(
                thresholds.get("minimum_citation_success_rate"),
                MIN_CITATION_SUCCESS,
            ),
            _same_number(
                thresholds.get("minimum_form_success_rate"), MIN_FORM_SUCCESS
            ),
            _same_number(
                thresholds.get("maximum_error_rate_exclusive"), MAX_ERROR_RATE
            ),
            _same_number(
                thresholds.get("maximum_repair_rate_exclusive"), MAX_REPAIR_RATE
            ),
        )
    ):
        return False
    if not isinstance(manifest, dict) or not (
        manifest.get("case_count") == 30 and manifest.get("errors") == []
    ):
        return False
    if not isinstance(expert, dict) or not (
        expert.get("selected_count") == 30
        and expert.get("unapproved_review_ids") == []
    ):
        return False
    if not isinstance(coverage, dict) or not (
        coverage.get("expected_attempts") == EXPECTED_ATTEMPTS
        and coverage.get("actual_results") == EXPECTED_ATTEMPTS
        and coverage.get("missing_attempts") == []
        and coverage.get("unexpected_attempts") == []
        and coverage.get("duplicate_attempts") == []
    ):
        return False
    if not isinstance(groups, dict) or set(groups) != expected_groups:
        return False
    if any(
        (score := _as_number(groups.get(group))) is None
        or score < MIN_GROUP_SCORE
        for group in expected_groups
    ):
        return False
    if not isinstance(metrics, dict):
        return False
    critical = _as_number(metrics.get("critical_hallucinations"))
    citation = _as_number(metrics.get("citation_success_rate"))
    forms = _as_number(metrics.get("form_success_rate"))
    errors = _as_number(metrics.get("error_rate"))
    repairs = _as_number(metrics.get("repair_rate"))
    if not (
        critical == 0
        and citation is not None
        and citation >= MIN_CITATION_SUCCESS
        and forms is not None
        and forms >= MIN_FORM_SUCCESS
        and errors is not None
        and errors < MAX_ERROR_RATE
        and repairs is not None
        and repairs < MAX_REPAIR_RATE
    ):
        return False
    return _valid_fingerprints(report.get("artifact_fingerprints"))


def quality_artifacts_match(
    report: dict[str, Any],
    manifest: dict[str, Any],
    expert: dict[str, Any],
    run: dict[str, Any],
) -> bool:
    fingerprints = report.get("artifact_fingerprints")
    if not _valid_fingerprints(fingerprints):
        return False
    return bool(
        fingerprints["manifest_sha256"] == artifact_fingerprint(manifest)
        and fingerprints["expert_sha256"] == artifact_fingerprint(expert)
        and fingerprints["run_sha256"] == artifact_fingerprint(run)
    )


def _valid_soak_report(report: Any, *, role: str) -> bool:
    if not isinstance(report, dict):
        return False
    duration = _as_number(report.get("duration_hours"))
    critical = _as_number(report.get("critical_hallucinations"))
    citation = _as_number(report.get("citation_success_rate"))
    forms = _as_number(report.get("form_success_rate"))
    errors = _as_number(report.get("error_rate"))
    repairs = _as_number(report.get("repair_rate"))
    return bool(
        report.get("pass") is True
        and report.get("role") == role
        and duration is not None
        and duration >= SOAK_HOURS
        and critical == 0
        and citation is not None
        and citation >= MIN_CITATION_SUCCESS
        and forms is not None
        and forms >= MIN_FORM_SUCCESS
        and errors is not None
        and errors < MAX_ERROR_RATE
        and repairs is not None
        and repairs < MAX_REPAIR_RATE
    )


def _aware_utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return _aware_utc(parsed)


def _state_stage(state: dict[str, Any]) -> int:
    stage = state.get("stage")
    if isinstance(stage, bool) or not isinstance(stage, int) or not 0 <= stage <= 3:
        raise RolloutBlocked("invalid rollout state")
    if state.get("enabled_roles") != list(ROLLOUT_ROLES[:stage]):
        raise RolloutBlocked("invalid rollout state")
    return stage


def advance_state(
    state: dict[str, Any],
    quality_report: dict[str, Any],
    soak_report: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    if not valid_quality_report(quality_report):
        raise RolloutBlocked("quality gate evidence is missing, stale, or invalid")

    current = _state_stage(state)
    if current >= len(ROLLOUT_ROLES):
        raise RolloutBlocked("rollout is already complete")
    timestamp = _aware_utc(now)

    if current > 0:
        started = _parse_timestamp(state.get("stage_started_at"))
        if started is None:
            raise RolloutBlocked("24-hour soak start is missing")
        elapsed_hours = (timestamp - started).total_seconds() / 3600
        if elapsed_hours < SOAK_HOURS:
            raise RolloutBlocked("24-hour soak has not completed")
        current_role = ROLLOUT_ROLES[current - 1]
        if not _valid_soak_report(soak_report, role=current_role):
            raise RolloutBlocked(f"strict soak evidence is invalid for role={current_role}")

    next_stage = current + 1
    result = copy.deepcopy(state)
    result.update(
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "stage": next_stage,
            "enabled_roles": list(ROLLOUT_ROLES[:next_stage]),
            "stage_started_at": timestamp.isoformat(),
            "updated_at": timestamp.isoformat(),
        }
    )
    history = list(result.get("history") or [])
    history.append(
        {
            "action": "advance",
            "from_stage": current,
            "to_stage": next_stage,
            "role": ROLLOUT_ROLES[next_stage - 1],
            "at": timestamp.isoformat(),
            "quality_run_sha256": quality_report["artifact_fingerprints"][
                "run_sha256"
            ],
        }
    )
    result["history"] = history
    return result


def rollback_state(
    state: dict[str, Any], *, to_stage: int, now: datetime
) -> dict[str, Any]:
    current = _state_stage(state)
    if isinstance(to_stage, bool) or not isinstance(to_stage, int):
        raise RolloutBlocked("rollback target must be an integer stage")
    if not 0 <= to_stage < current:
        raise RolloutBlocked("rollback target must be lower than the current stage")

    timestamp = _aware_utc(now)
    result = copy.deepcopy(state)
    result.update(
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "stage": to_stage,
            "enabled_roles": list(ROLLOUT_ROLES[:to_stage]),
            "stage_started_at": timestamp.isoformat() if to_stage else None,
            "updated_at": timestamp.isoformat(),
        }
    )
    history = list(result.get("history") or [])
    history.append(
        {
            "action": "rollback",
            "from_stage": current,
            "to_stage": to_stage,
            "at": timestamp.isoformat(),
        }
    )
    result["history"] = history
    return result


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--advance", dest="action", action="store_const", const="advance"
    )
    actions.add_argument(
        "--rollback", dest="action", action="store_const", const="rollback"
    )
    parser.set_defaults(action="dry-run")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument(
        "--quality-report", type=Path, default=DEFAULT_QUALITY_REPORT
    )
    parser.add_argument("--soak-report", type=Path, default=DEFAULT_SOAK_REPORT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_PILOT_MANIFEST)
    parser.add_argument("--expert", type=Path, default=DEFAULT_PILOT_EXPERT)
    parser.add_argument("--run", type=Path, default=DEFAULT_PILOT_RUN)
    parser.add_argument("--to-stage", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        state = _read_json(args.state, required=False) or default_state()
        _state_stage(state)
        if args.action == "dry-run":
            output = {"action": "dry-run", "would_write": False, "state": state}
        elif args.action == "advance":
            quality = _read_json(args.quality_report, required=True)
            manifest = _read_json(args.manifest, required=True)
            expert = _read_json(args.expert, required=True)
            run = _read_json(args.run, required=True)
            if not quality_artifacts_match(quality, manifest, expert, run):
                raise RolloutBlocked(
                    "quality gate fingerprints do not match current artifacts"
                )
            soak = _read_json(args.soak_report, required=False) or None
            state = advance_state(
                state,
                quality,
                soak,
                now=datetime.now(timezone.utc),
            )
            _write_json_atomic(args.state, state)
            output = {"action": "advance", "would_write": True, "state": state}
        else:
            state = rollback_state(
                state,
                to_stage=args.to_stage,
                now=datetime.now(timezone.utc),
            )
            _write_json_atomic(args.state, state)
            output = {"action": "rollback", "would_write": True, "state": state}
    except RolloutBlocked as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
