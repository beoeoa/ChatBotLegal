"""Fail-closed runtime gate for staged Ask access by authenticated role."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = ROOT / "data" / "pilot" / "ask_role_rollout.json"
ROLLOUT_ROLES = ("admin", "officer", "citizen")
STATE_SCHEMA_VERSION = "1.0"
MAX_STATE_BYTES = 64 * 1024

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})


class RolloutStateError(RuntimeError):
    """Raised for an invalid enforcement flag or rollout state artifact."""


def rollout_enforcement_enabled() -> bool:
    """Return the explicit global gate state; reject ambiguous flag values."""

    raw = os.getenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED")
    if raw is None:
        return False
    normalized = raw.strip().casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise RolloutStateError("invalid rollout enforcement flag")


def _state_path() -> Path:
    configured = str(
        os.getenv("LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH") or ""
    ).strip()
    if not configured:
        return DEFAULT_STATE_PATH
    path = Path(configured)
    return path if path.is_absolute() else ROOT / path


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def load_enabled_roles() -> tuple[str, ...]:
    """Read and strictly validate the state on every request for fast rollback."""

    path = _state_path()
    try:
        if path.stat().st_size > MAX_STATE_BYTES:
            raise RolloutStateError("rollout state is too large")
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except RolloutStateError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RolloutStateError("cannot read rollout state") from exc

    if not isinstance(payload, dict):
        raise RolloutStateError("rollout state must be an object")
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise RolloutStateError("rollout state schema mismatch")
    stage = payload.get("stage")
    if isinstance(stage, bool) or not isinstance(stage, int) or not 0 <= stage <= 3:
        raise RolloutStateError("rollout stage is invalid")
    expected_roles = ROLLOUT_ROLES[:stage]
    if payload.get("enabled_roles") != list(expected_roles):
        raise RolloutStateError("rollout roles do not match stage")
    if not isinstance(payload.get("history"), list):
        raise RolloutStateError("rollout history is invalid")

    started_at = payload.get("stage_started_at")
    updated_at = payload.get("updated_at")
    if stage == 0:
        if started_at is not None:
            raise RolloutStateError("disabled rollout cannot have a stage start")
        if updated_at is not None and not _valid_timestamp(updated_at):
            raise RolloutStateError("rollout update time is invalid")
    elif not (_valid_timestamp(started_at) and _valid_timestamp(updated_at)):
        raise RolloutStateError("rollout timestamps are invalid")
    return expected_roles


def _detail(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "retryable": False}


def enforce_ask_role_rollout(request: Request) -> None:
    """Authorize Ask using middleware-resolved identity, never payload/header role."""

    try:
        enabled = rollout_enforcement_enabled()
    except RolloutStateError as exc:
        raise HTTPException(
            status_code=503,
            detail=_detail(
                "ASK_ROLE_ROLLOUT_CONFIG_INVALID",
                "Cấu hình rollout Ask không hợp lệ.",
            ),
        ) from exc
    if not enabled:
        return

    if (
        getattr(request.state, "authenticated", False) is not True
        or getattr(request.state, "auth_mode", None) != "user_session"
        or not getattr(request.state, "user_id", None)
    ):
        raise HTTPException(
            status_code=401,
            detail=_detail(
                "ASK_ROLE_ROLLOUT_AUTH_REQUIRED",
                "Yêu cầu phiên đăng nhập đã xác thực.",
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    role = getattr(request.state, "user_role", None)
    if role not in ROLLOUT_ROLES:
        raise HTTPException(
            status_code=401,
            detail=_detail(
                "ASK_ROLE_ROLLOUT_AUTH_REQUIRED",
                "Không xác định được vai trò đã xác thực.",
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        enabled_roles = load_enabled_roles()
    except RolloutStateError as exc:
        raise HTTPException(
            status_code=503,
            detail=_detail(
                "ASK_ROLE_ROLLOUT_STATE_INVALID",
                "Trạng thái rollout Ask chưa sẵn sàng.",
            ),
        ) from exc
    if role not in enabled_roles:
        raise HTTPException(
            status_code=403,
            detail=_detail(
                "ASK_ROLE_ROLLOUT_ROLE_DISABLED",
                "Tính năng hỏi đáp chưa được mở cho vai trò này.",
            ),
        )
