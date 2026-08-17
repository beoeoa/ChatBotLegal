"""Manage Feature 017 optimized-profile canary state without touching legal data."""

from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from api.optimized_profile_rollout import (
    DEFAULT_STATE_PATH,
    ROLLOUT_ROLES,
    STATE_SCHEMA_VERSION,
    OptimizedProfileRolloutError,
    load_state,
)


MAX_P95_MS = 25_000
MAX_FALLBACK_RATE = 0.03
MAX_PROVIDER_ERROR_RATE = 0.005
MIN_CANARY_SAMPLES = 100
MIN_SOAK_HOURS = 24


def default_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "stage": 0,
        "enabled_roles": [],
        "profile_version": "optimized-v1",
        "stage_started_at": None,
        "updated_at": None,
        "history": [],
    }


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_transition_evidence(
    evidence: Mapping[str, Any] | None,
    *,
    current_stage: int,
) -> dict[str, Any]:
    """Require auditable quality/performance evidence before a role is opened."""
    if not isinstance(evidence, Mapping):
        raise OptimizedProfileRolloutError("optimized-profile transition evidence is required")
    target_role = ROLLOUT_ROLES[current_stage]
    expected_observation_role = "citizen" if current_stage else target_role
    if evidence.get("role") != expected_observation_role:
        raise OptimizedProfileRolloutError("optimized-profile evidence role does not match transition")
    for field in ("quality_pass", "performance_pass"):
        if evidence.get(field) is not True:
            raise OptimizedProfileRolloutError(f"optimized-profile {field} is not proven")
    try:
        p95_ms = float(evidence["p95_ms"])
        fallback_rate = float(evidence["fallback_rate"])
        provider_error_rate = float(evidence["provider_error_rate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OptimizedProfileRolloutError("optimized-profile performance evidence is incomplete") from exc
    if not 0 <= p95_ms <= MAX_P95_MS:
        raise OptimizedProfileRolloutError("optimized-profile P95 exceeds 25 seconds")
    if not 0 <= fallback_rate <= MAX_FALLBACK_RATE:
        raise OptimizedProfileRolloutError("optimized-profile fallback rate exceeds 3 percent")
    if not 0 <= provider_error_rate < MAX_PROVIDER_ERROR_RATE:
        raise OptimizedProfileRolloutError("optimized-profile provider error rate is too high")
    if current_stage >= 1:
        try:
            samples = int(evidence["completed_samples"])
            soak_hours = float(evidence["soak_hours"])
        except (KeyError, TypeError, ValueError) as exc:
            raise OptimizedProfileRolloutError("optimized-profile soak evidence is incomplete") from exc
        if samples < MIN_CANARY_SAMPLES or soak_hours < MIN_SOAK_HOURS:
            raise OptimizedProfileRolloutError("optimized-profile citizen soak requires 100 samples and 24 hours")
    return {
        "role": expected_observation_role,
        "p95_ms": p95_ms,
        "fallback_rate": fallback_rate,
        "provider_error_rate": provider_error_rate,
        "completed_samples": int(evidence.get("completed_samples", 0)),
        "soak_hours": float(evidence.get("soak_hours", 0)),
    }


def advance_state(
    state: dict[str, Any],
    *,
    now: datetime,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    stage = int(state.get("stage"))
    if stage >= len(ROLLOUT_ROLES):
        raise OptimizedProfileRolloutError("optimized-profile rollout is complete")
    evidence_summary = validate_transition_evidence(evidence, current_stage=stage)
    timestamp = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    result = copy.deepcopy(state)
    result.update({
        "schema_version": STATE_SCHEMA_VERSION,
        "stage": stage + 1,
        "enabled_roles": list(ROLLOUT_ROLES[: stage + 1]),
        "stage_started_at": timestamp.isoformat(),
        "updated_at": timestamp.isoformat(),
    })
    result.setdefault("history", []).append({
        "action": "advance",
        "from_stage": stage,
        "to_stage": stage + 1,
        "role": ROLLOUT_ROLES[stage],
        "profile_version": result["profile_version"],
        "at": timestamp.isoformat(),
        "evidence": evidence_summary,
    })
    return result


def rollback_state(state: dict[str, Any], *, to_stage: int, now: datetime) -> dict[str, Any]:
    stage = int(state.get("stage"))
    if not isinstance(to_stage, int) or isinstance(to_stage, bool) or not 0 <= to_stage < stage:
        raise OptimizedProfileRolloutError("rollback target must be lower than current stage")
    timestamp = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    result = copy.deepcopy(state)
    result.update({
        "stage": to_stage,
        "enabled_roles": list(ROLLOUT_ROLES[:to_stage]),
        "stage_started_at": timestamp.isoformat() if to_stage else None,
        "updated_at": timestamp.isoformat(),
    })
    result.setdefault("history", []).append({"action": "rollback", "from_stage": stage, "to_stage": to_stage, "at": timestamp.isoformat()})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--advance", action="store_true")
    parser.add_argument("--rollback", type=int, default=None)
    parser.add_argument("--evidence", type=Path, help="JSON quality/performance evidence required by --advance")
    args = parser.parse_args(argv)
    state = load_state(args.state) if args.state.exists() else default_state()
    if args.advance and args.rollback is not None:
        raise SystemExit("choose --advance or --rollback")
    if args.advance:
        if args.evidence is None:
            raise SystemExit("--advance requires --evidence JSON")
        try:
            evidence = json.loads(args.evidence.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"cannot read evidence: {exc}") from exc
        state = advance_state(state, now=datetime.now(timezone.utc), evidence=evidence)
        _write_atomic(args.state, state)
    elif args.rollback is not None:
        state = rollback_state(state, to_stage=args.rollback, now=datetime.now(timezone.utc))
        _write_atomic(args.state, state)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
