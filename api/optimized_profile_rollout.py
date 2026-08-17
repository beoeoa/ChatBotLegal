"""Fail-closed state reader for the Feature 017 optimized-profile canary."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = ROOT / "data" / "pilot" / "ask_optimized_profile_rollout.json"
STATE_SCHEMA_VERSION = "feature017-optimized-profile-rollout-v1"
ROLLOUT_ROLES = ("citizen", "officer")
MAX_STATE_BYTES = 64 * 1024
_TRUE_VALUES = {"1", "true", "yes", "on"}


class OptimizedProfileRolloutError(RuntimeError):
    pass


def enforcement_enabled(environ: dict[str, str] | None = None) -> bool:
    values = os.environ if environ is None else environ
    raw = str(values.get("LEGAL_ANSWER_OPTIMIZED_PROFILE_ENFORCED", "false")).strip().casefold()
    if raw in _TRUE_VALUES:
        return True
    if raw in {"", "0", "false", "no", "off"}:
        return False
    raise OptimizedProfileRolloutError("invalid optimized-profile enforcement flag")


def state_path(environ: dict[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    configured = str(values.get("LEGAL_ANSWER_OPTIMIZED_PROFILE_STATE_PATH", "")).strip()
    if not configured:
        return DEFAULT_STATE_PATH
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def load_state(path: Path | None = None) -> dict[str, Any]:
    target = path or state_path()
    try:
        if target.stat().st_size > MAX_STATE_BYTES:
            raise OptimizedProfileRolloutError("optimized-profile state is too large")
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except OptimizedProfileRolloutError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise OptimizedProfileRolloutError("cannot read optimized-profile state") from exc
    if not isinstance(payload, dict):
        raise OptimizedProfileRolloutError("optimized-profile state must be an object")
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise OptimizedProfileRolloutError("optimized-profile state schema mismatch")
    stage = payload.get("stage")
    if isinstance(stage, bool) or not isinstance(stage, int) or not 0 <= stage <= len(ROLLOUT_ROLES):
        raise OptimizedProfileRolloutError("optimized-profile stage is invalid")
    if payload.get("enabled_roles") != list(ROLLOUT_ROLES[:stage]):
        raise OptimizedProfileRolloutError("optimized-profile roles do not match stage")
    if not isinstance(payload.get("profile_version"), str) or not payload["profile_version"].strip():
        raise OptimizedProfileRolloutError("optimized-profile version is missing")
    if not isinstance(payload.get("history"), list):
        raise OptimizedProfileRolloutError("optimized-profile history is invalid")
    started = payload.get("stage_started_at")
    updated = payload.get("updated_at")
    if stage == 0:
        if started is not None:
            raise OptimizedProfileRolloutError("stage zero cannot have a start time")
    elif not _timestamp(started):
        raise OptimizedProfileRolloutError("optimized-profile stage timestamp is invalid")
    if updated is not None and not _timestamp(updated):
        raise OptimizedProfileRolloutError("optimized-profile update timestamp is invalid")
    return payload


def enabled_roles() -> tuple[str, ...]:
    return tuple(load_state().get("enabled_roles") or ())


def enforce(request: Request) -> None:
    try:
        if not enforcement_enabled():
            return
        role = getattr(request.state, "user_role", None)
        if role not in ROLLOUT_ROLES:
            raise HTTPException(status_code=403, detail={"code": "OPTIMIZED_PROFILE_ROLE_DISABLED", "retryable": False})
        if role not in enabled_roles():
            raise HTTPException(status_code=403, detail={"code": "OPTIMIZED_PROFILE_ROLE_DISABLED", "retryable": False})
    except OptimizedProfileRolloutError as exc:
        raise HTTPException(status_code=503, detail={"code": "OPTIMIZED_PROFILE_STATE_INVALID", "retryable": False}) from exc
