from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import Request

from api.optimized_profile_rollout import (
    ROLLOUT_ROLES,
    STATE_SCHEMA_VERSION,
    OptimizedProfileRolloutError,
    enabled_roles,
    enforce,
    load_state,
)
from scripts.manage_optimized_profile_rollout import (
    advance_state,
    default_state,
    rollback_state,
)


NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def _evidence(role="citizen", **overrides):
    value = {
        "role": role,
        "quality_pass": True,
        "performance_pass": True,
        "p95_ms": 24000,
        "fallback_rate": 0.01,
        "provider_error_rate": 0.001,
        "completed_samples": 100,
        "soak_hours": 24,
    }
    value.update(overrides)
    return value


def test_canary_order_is_citizen_then_officer():
    stage_one = advance_state(default_state(), now=NOW, evidence=_evidence())
    assert stage_one["stage"] == 1
    assert stage_one["enabled_roles"] == ["citizen"]
    stage_two = advance_state(stage_one, now=NOW, evidence=_evidence())
    assert stage_two["stage"] == 2
    assert stage_two["enabled_roles"] == ["citizen", "officer"]
    assert ROLLOUT_ROLES == ("citizen", "officer")


def test_canary_rollback_returns_to_stage_zero():
    state = advance_state(advance_state(default_state(), now=NOW, evidence=_evidence()), now=NOW, evidence=_evidence())
    rolled = rollback_state(state, to_stage=0, now=NOW)
    assert rolled["stage"] == 0
    assert rolled["enabled_roles"] == []
    assert rolled["stage_started_at"] is None


def test_state_reader_rejects_wrong_schema_or_role_order(tmp_path):
    path = tmp_path / "rollout.json"
    path.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
    with pytest.raises(OptimizedProfileRolloutError, match="schema mismatch"):
        load_state(path)

    state = default_state()
    state["enabled_roles"] = ["officer"]
    path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(OptimizedProfileRolloutError, match="roles do not match"):
        load_state(path)


def test_transition_requires_quality_and_performance_evidence():
    with pytest.raises(OptimizedProfileRolloutError, match="evidence is required"):
        advance_state(default_state(), now=NOW)
    with pytest.raises(OptimizedProfileRolloutError, match="P95"):
        advance_state(default_state(), now=NOW, evidence=_evidence(p95_ms=25001))
    stage_one = advance_state(default_state(), now=NOW, evidence=_evidence())
    with pytest.raises(OptimizedProfileRolloutError, match="100 samples and 24 hours"):
        advance_state(stage_one, now=NOW, evidence=_evidence(completed_samples=99, soak_hours=23.9))

def test_enforcement_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LEGAL_ANSWER_OPTIMIZED_PROFILE_ENFORCED", raising=False)
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    request.state.user_role = "officer"
    enforce(request)


def test_enabled_roles_reads_only_valid_state(monkeypatch, tmp_path):
    state = default_state()
    state["stage"] = 1
    state["enabled_roles"] = ["citizen"]
    state["stage_started_at"] = NOW.isoformat()
    state["updated_at"] = NOW.isoformat()
    path = tmp_path / "rollout.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("LEGAL_ANSWER_OPTIMIZED_PROFILE_STATE_PATH", str(path))
    assert enabled_roles() == ("citizen",)


def test_enforced_canary_state_controls_profile_selection(monkeypatch, tmp_path):
    from api.legal_structured_answer import optimized_profile_enabled

    state = default_state()
    state["stage"] = 1
    state["enabled_roles"] = ["citizen"]
    state["stage_started_at"] = NOW.isoformat()
    state["updated_at"] = NOW.isoformat()
    path = tmp_path / "rollout.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv("LEGAL_ANSWER_OPTIMIZED_PROFILE_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ANSWER_OPTIMIZED_PROFILE_ENFORCED", "true")
    monkeypatch.setenv("LEGAL_ANSWER_OPTIMIZED_PROFILE_STATE_PATH", str(path))
    assert optimized_profile_enabled("citizen") is True
    assert optimized_profile_enabled("officer") is False
