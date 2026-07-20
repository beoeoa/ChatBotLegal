from datetime import datetime, timedelta, timezone
import itertools

import pytest

from scripts import manage_ask_role_rollout as rollout
from scripts import pilot_quality_gate as quality_gate


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _quality(pass_value: bool = True) -> dict:
    checks = {name: True for name in rollout.REQUIRED_QUALITY_CHECKS}
    if not pass_value:
        checks["full_matrix_coverage"] = False
    return {
        "schema_version": quality_gate.REPORT_SCHEMA_VERSION,
        "pass": pass_value,
        "thresholds": {
            "minimum_group_score": quality_gate.MIN_GROUP_SCORE,
            "minimum_citation_success_rate": quality_gate.MIN_CITATION_SUCCESS,
            "minimum_form_success_rate": quality_gate.MIN_FORM_SUCCESS,
            "maximum_error_rate_exclusive": quality_gate.MAX_ERROR_RATE,
            "maximum_repair_rate_exclusive": quality_gate.MAX_REPAIR_RATE,
        },
        "checks": checks,
        "manifest": {"case_count": 30, "errors": []},
        "expert_review": {"selected_count": 30, "unapproved_review_ids": []},
        "group_scores": {
            f"{domain}:{role}": quality_gate.MIN_GROUP_SCORE
            for domain, role in itertools.product(
                quality_gate.DOMAINS, quality_gate.ROLES
            )
        },
        "coverage": {
            "expected_attempts": 240,
            "actual_results": 240,
            "missing_attempts": [],
            "unexpected_attempts": [],
            "duplicate_attempts": [],
        },
        "metrics": {
            "critical_hallucinations": 0,
            "citation_success_rate": 1.0,
            "form_success_rate": 1.0,
            "error_rate": 0.0,
            "repair_rate": 0.0,
        },
        "artifact_fingerprints": {
            "manifest_sha256": "a" * 64,
            "expert_sha256": "b" * 64,
            "run_sha256": "c" * 64,
        },
    }


def _soak(role: str, hours: float = 24.0, pass_value: bool = True) -> dict:
    return {
        "pass": pass_value,
        "role": role,
        "duration_hours": hours,
        "critical_hallucinations": 0,
        "citation_success_rate": 1.0,
        "form_success_rate": 1.0,
        "error_rate": 0.0,
        "repair_rate": 0.0,
    }


def test_cli_defaults_to_dry_run():
    parser = rollout.build_parser()
    assert parser.parse_args([]).action == "dry-run"
    assert parser.parse_args(["--advance"]).action == "advance"
    assert parser.parse_args(["--rollback"]).action == "rollback"


def test_rollout_order_is_admin_then_officer_then_citizen():
    state = rollout.default_state()
    admin = rollout.advance_state(state, _quality(), soak_report=None, now=NOW)
    assert admin["stage"] == 1
    assert admin["enabled_roles"] == ["admin"]

    admin["stage_started_at"] = (NOW - timedelta(hours=24)).isoformat()
    officer = rollout.advance_state(
        admin, _quality(), soak_report=_soak("admin"), now=NOW
    )
    assert officer["stage"] == 2
    assert officer["enabled_roles"] == ["admin", "officer"]

    officer["stage_started_at"] = (NOW - timedelta(hours=24)).isoformat()
    citizen = rollout.advance_state(
        officer, _quality(), soak_report=_soak("officer"), now=NOW
    )
    assert citizen["stage"] == 3
    assert citizen["enabled_roles"] == ["admin", "officer", "citizen"]


def test_advance_requires_passing_gate_and_full_24_hour_soak():
    with pytest.raises(rollout.RolloutBlocked, match="quality gate"):
        rollout.advance_state(
            rollout.default_state(), _quality(False), soak_report=None, now=NOW
        )

    state = rollout.advance_state(
        rollout.default_state(), _quality(), soak_report=None, now=NOW
    )
    state["stage_started_at"] = (NOW - timedelta(hours=23, minutes=59)).isoformat()
    with pytest.raises(rollout.RolloutBlocked, match="24"):
        rollout.advance_state(
            state, _quality(), soak_report=_soak("admin"), now=NOW
        )


def test_advance_rejects_superficial_or_tampered_quality_evidence():
    with pytest.raises(rollout.RolloutBlocked, match="quality gate"):
        rollout.advance_state(
            rollout.default_state(),
            {"pass": True},
            soak_report=None,
            now=NOW,
        )

    quality = _quality()
    quality["metrics"]["citation_success_rate"] = 0.98
    with pytest.raises(rollout.RolloutBlocked, match="quality gate"):
        rollout.advance_state(
            rollout.default_state(), quality, soak_report=None, now=NOW
        )


def test_quality_report_fingerprints_are_bound_to_current_artifacts():
    manifest = {"version": "1.0", "cases": []}
    expert = {"records": []}
    run = {"results": []}
    quality = _quality()
    quality["artifact_fingerprints"] = {
        "manifest_sha256": rollout.artifact_fingerprint(manifest),
        "expert_sha256": rollout.artifact_fingerprint(expert),
        "run_sha256": rollout.artifact_fingerprint(run),
    }

    assert rollout.quality_artifacts_match(quality, manifest, expert, run) is True
    run["results"].append({"status": "changed"})
    assert rollout.quality_artifacts_match(quality, manifest, expert, run) is False


def test_later_stage_requires_matching_strict_soak_evidence():
    state = rollout.advance_state(
        rollout.default_state(), _quality(), soak_report=None, now=NOW
    )
    state["stage_started_at"] = (NOW - timedelta(hours=24)).isoformat()

    bad_soak = _soak("officer")
    with pytest.raises(rollout.RolloutBlocked, match="soak"):
        rollout.advance_state(state, _quality(), soak_report=bad_soak, now=NOW)

    bad_soak = _soak("admin")
    bad_soak["repair_rate"] = 0.10
    with pytest.raises(rollout.RolloutBlocked, match="soak"):
        rollout.advance_state(state, _quality(), soak_report=bad_soak, now=NOW)


def test_rollback_is_immediate_and_disables_roles_after_target():
    state = rollout.default_state()
    state.update(
        {
            "stage": 3,
            "enabled_roles": ["admin", "officer", "citizen"],
            "stage_started_at": NOW.isoformat(),
        }
    )

    rolled_back = rollout.rollback_state(state, to_stage=1, now=NOW)

    assert rolled_back["stage"] == 1
    assert rolled_back["enabled_roles"] == ["admin"]
    assert rolled_back["history"][-1]["action"] == "rollback"
