"""Launch and read the post-attestation release gate without exposing logs."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def load_form_release_gate_status(status_path: Path) -> dict[str, Any]:
    if not status_path.is_file():
        return {
            "schema_version": "form-post-attestation-gate-v1",
            "status": "not_started",
            "stage": "not_started",
            "passed": 0,
            "failed": 0,
            "blocked": 0,
            "checks": [],
            "feature_flag_enabled": False,
        }
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("FORM_RELEASE_GATE_STATUS_INVALID")
    return payload


def mark_stale_form_release_gate(
    *,
    status_path: Path,
    expected_attestation_id: str,
    lock_path: Path | None = None,
    minimum_age_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fail-close a terminated runner only after deterministic stale checks."""

    current = load_form_release_gate_status(status_path)
    if current.get("status") not in {"queued", "running"}:
        raise ValueError("FORM_RELEASE_GATE_NOT_RUNNING")
    if str(current.get("attestation_ref") or "") != str(expected_attestation_id):
        raise ValueError("FORM_RELEASE_GATE_ATTESTATION_MISMATCH")
    lock_path = lock_path or status_path.parent / "gate.lock"
    if lock_path.exists():
        raise ValueError("FORM_RELEASE_GATE_LOCK_STILL_PRESENT")
    raw_generated = str(current.get("generated_at") or "").strip()
    if not raw_generated:
        raise ValueError("FORM_RELEASE_GATE_GENERATED_AT_REQUIRED")
    try:
        generated = datetime.fromisoformat(raw_generated.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("FORM_RELEASE_GATE_GENERATED_AT_INVALID") from exc
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if (now - generated).total_seconds() < minimum_age_seconds:
        raise ValueError("FORM_RELEASE_GATE_NOT_STALE")
    checks = list(current.get("checks") or [])
    checks.append(
        {
            "name": "runner_terminal_status",
            "status": "FAIL",
            "reason_code": "RUNNER_TERMINATED_UNFINALIZED",
            "duration_seconds": 0,
        }
    )
    updated = {
        **current,
        "status": "BLOCKED_RELEASE",
        "stage": "runner_terminated_unfinalized",
        "failed": int(current.get("failed") or 0) + 1,
        "checks": checks,
        "feature_flag_enabled": False,
        "updated_at": now.astimezone(timezone.utc).isoformat(),
    }
    temporary = status_path.with_suffix(f"{status_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, status_path)
    return updated


def launch_form_release_gates(
    *,
    project_root: Path,
    legal_as_of: str,
    attestation_id: str,
    status_path: Path,
    launcher: Any = subprocess.Popen,
) -> dict[str, Any]:
    date.fromisoformat(legal_as_of)
    current = load_form_release_gate_status(status_path)
    if current.get("status") in {"queued", "running"}:
        if str(current.get("attestation_ref") or "") != str(attestation_id):
            raise ValueError("FORM_RELEASE_GATE_DIFFERENT_ATTESTATION_RUNNING")
        return {**current, "launch_status": "already_running"}
    queued = {
        "schema_version": "form-post-attestation-gate-v1",
        "status": "queued",
        "stage": "queued",
        "legal_as_of": legal_as_of,
        "attestation_ref": attestation_id,
        "passed": 0,
        "failed": 0,
        "blocked": 0,
        "checks": [],
        "feature_flag_enabled": False,
        "launch_status": "started",
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = status_path.with_suffix(f"{status_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(queued, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, status_path)
    command = [
        sys.executable,
        str(project_root / "scripts" / "run_post_attestation_release_gates.py"),
        "--legal-as-of",
        legal_as_of,
        "--attestation-id",
        attestation_id,
    ]
    kwargs: dict[str, Any] = {
        "cwd": project_root,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        launcher(command, **kwargs)
    except Exception:
        failed = {**queued, "status": "BLOCKED_RELEASE", "stage": "launch_failed"}
        temporary.write_text(
            json.dumps(failed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, status_path)
        raise
    return queued
