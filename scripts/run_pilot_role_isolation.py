"""Plan or run cross-account identifier-tampering checks for Feature 4.

This security evidence is intentionally separate from the 240 Ask quality
attempts. Live mode requires two ordinary-account tokens from environment
variables and writes only identifiers, HTTP status, timing, and normalized
outcome codes. Response bodies and exception messages are never persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

try:
    from scripts.pilot_quality_gate import artifact_fingerprint
except ModuleNotFoundError:  # Direct execution from the scripts directory.
    from pilot_quality_gate import artifact_fingerprint


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT / "specs" / "004-gate-role-rollout" / "role-isolation-input.template.json"
)
TOKEN_ENV = {
    "a": "PILOT_ISOLATION_ACCOUNT_A_TOKEN",
    "b": "PILOT_ISOLATION_ACCOUNT_B_TOKEN",
}
RESOURCE_KEYS = (
    "conversation_id",
    "profile_id",
    "notebook_id",
    "legal_dossier_id",
)
RESOURCE_TYPES = {
    "conversation_id": "conversation",
    "profile_id": "profile",
    "notebook_id": "notebook",
    "legal_dossier_id": "legal_dossier",
}
EXPECTED_ATTEMPTS = 8
ACCEPTED_DENIAL_STATUSES = (403, 404)
_PLACEHOLDER_MARKERS = ("replace", "placeholder", "todo", "example", "<", ">")
_FORBIDDEN_INPUT_KEYS = {
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
}
_FORBIDDEN_REPORT_KEYS = {
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

Requester = Callable[[dict[str, Any]], Awaitable[tuple[int, float]]]


class IsolationConfigurationError(RuntimeError):
    """Raised when a live isolation run cannot be performed safely."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IsolationConfigurationError(f"cannot_read_input:{path}") from exc
    if not isinstance(value, dict):
        raise IsolationConfigurationError("input_root_must_be_object")
    return value


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().casefold()
    return not normalized or any(marker in normalized for marker in _PLACEHOLDER_MARKERS)


def validate_isolation_input(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def scan_credentials(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if str(key).casefold() in _FORBIDDEN_INPUT_KEYS:
                    errors.append(f"credential_field_forbidden_in_input:{key}")
                scan_credentials(item)
        elif isinstance(value, list):
            for item in value:
                scan_credentials(item)

    scan_credentials(payload)
    if payload.get("schema_version") != "1.0":
        errors.append("input_schema_version_mismatch")
    accounts = [
        item for item in payload.get("accounts") or [] if isinstance(item, dict)
    ]
    if len(accounts) != 2:
        errors.append(f"account_count:{len(accounts)}")
        return errors
    by_slot = {str(item.get("slot") or ""): item for item in accounts}
    if set(by_slot) != {"a", "b"}:
        errors.append("account_slots_must_be_a_and_b")
        return errors
    account_ids = [str(by_slot[slot].get("account_id") or "") for slot in ("a", "b")]
    if len(set(account_ids)) != 2:
        errors.append("account_ids_not_distinct")
    for slot, account in by_slot.items():
        account_id = str(account.get("account_id") or "")
        if _is_placeholder(account_id):
            errors.append(f"placeholder_account_id:{slot}")
        role = str(account.get("role") or "").casefold()
        if role not in {"citizen", "officer"}:
            errors.append(f"account_role_not_ordinary:{slot}")
        resources = account.get("resources")
        if not isinstance(resources, dict):
            errors.append(f"resources_missing:{slot}")
            continue
        for key in RESOURCE_KEYS:
            resource_id = str(resources.get(key) or "")
            if _is_placeholder(resource_id):
                errors.append(f"placeholder_resource_id:{slot}:{key}")
        if str(resources.get("profile_id") or "") != account_id:
            errors.append(f"profile_id_must_match_account_id:{slot}")
    for key in RESOURCE_KEYS:
        values = {
            str((by_slot[slot].get("resources") or {}).get(key) or "")
            for slot in ("a", "b")
        }
        if len(values) != 2:
            errors.append(f"resource_ids_not_distinct:{key}")
    return errors


def resolve_account_tokens(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    tokens: dict[str, str] = {}
    for slot, env_name in TOKEN_ENV.items():
        value = str(source.get(env_name) or "").strip()
        if not value:
            raise IsolationConfigurationError(
                f"Set {env_name} through secret environment injection."
            )
        tokens[slot] = value
    return tokens


def validate_authenticated_identity(
    account: Mapping[str, Any], identity_payload: Mapping[str, Any]
) -> list[str]:
    """Ensure an injected token belongs to the declared ordinary account."""

    errors: list[str] = []
    slot = str(account.get("slot") or "")
    if str(identity_payload.get("id") or "") != str(account.get("account_id") or ""):
        errors.append(f"identity_account_mismatch:{slot}")
    expected_role = str(account.get("role") or "").casefold()
    actual_role = str(identity_payload.get("role") or "").casefold()
    if actual_role != expected_role:
        errors.append(f"identity_role_mismatch:{slot}")
    if actual_role not in {"citizen", "officer"}:
        errors.append(f"identity_role_not_ordinary:{slot}")
    return errors


def _attempt_id(
    run_id: str, direction: str, resource_type: str, resource_id: str
) -> str:
    digest = hashlib.sha256(
        f"{run_id}|{direction}|{resource_type}|{resource_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"isolation-f4-{digest}"


def _request_contract(resource_type: str, resource_id: str) -> tuple[str, str, Any]:
    encoded = quote(resource_id, safe="")
    if resource_type == "conversation":
        return "GET", f"/api/conversations/{encoded}", None
    if resource_type == "profile":
        return "GET", f"/api/users/{encoded}", None
    if resource_type == "notebook":
        return "GET", f"/api/notebooks/{encoded}", None
    if resource_type == "legal_dossier":
        return (
            "GET",
            f"/api/admin/control/legal-cases/{encoded}?reason=pilot-isolation",
            None,
        )
    raise IsolationConfigurationError(f"unsupported_resource_type:{resource_type}")


def build_isolation_plan(
    payload: dict[str, Any], *, run_id: str
) -> list[dict[str, Any]]:
    errors = validate_isolation_input(payload)
    if errors:
        raise IsolationConfigurationError(";".join(errors))
    by_slot = {str(item["slot"]): item for item in payload["accounts"]}
    plan: list[dict[str, Any]] = []
    for actor_slot, target_slot in (("a", "b"), ("b", "a")):
        actor = by_slot[actor_slot]
        target = by_slot[target_slot]
        for key in RESOURCE_KEYS:
            resource_type = RESOURCE_TYPES[key]
            resource_id = str(target["resources"][key])
            method, path, request_body = _request_contract(
                resource_type, resource_id
            )
            direction = f"{actor_slot}_to_{target_slot}"
            plan.append(
                {
                    "attempt_id": _attempt_id(
                        run_id, direction, resource_type, resource_id
                    ),
                    "direction": direction,
                    "actor_slot": actor_slot,
                    "actor_account_id": str(actor["account_id"]),
                    "target_account_id": str(target["account_id"]),
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "method": method,
                    "path": path,
                    "request_body": request_body,
                    "accepted_status_codes": list(ACCEPTED_DENIAL_STATUSES),
                }
            )
    return plan


def _safe_result(
    attempt: dict[str, Any],
    *,
    status_code: int | None,
    elapsed_ms: float,
    outcome_code: str,
) -> dict[str, Any]:
    passed = status_code in ACCEPTED_DENIAL_STATUSES
    return {
        "attempt_id": attempt["attempt_id"],
        "direction": attempt["direction"],
        "actor_account_id": attempt["actor_account_id"],
        "target_account_id": attempt["target_account_id"],
        "resource_type": attempt["resource_type"],
        "resource_id": attempt["resource_id"],
        "status_code": status_code,
        "elapsed_ms": round(max(float(elapsed_ms), 0.0), 3),
        "outcome_code": "access_denied" if passed else outcome_code,
        "passed": passed,
    }


def _assert_report_privacy(value: Any, *, path: str = "report") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_REPORT_KEYS:
                raise IsolationConfigurationError(
                    f"forbidden_report_field:{path}.{key}"
                )
            _assert_report_privacy(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_report_privacy(item, path=f"{path}[{index}]")


async def execute_isolation_plan(
    plan: list[dict[str, Any]],
    *,
    requester: Requester,
    run_id: str,
    input_fingerprint: str | None = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for attempt in plan:
        started = time.perf_counter()
        try:
            status_code, elapsed_ms = await requester(attempt)
            results.append(
                _safe_result(
                    attempt,
                    status_code=int(status_code),
                    elapsed_ms=elapsed_ms,
                    outcome_code="unexpected_status",
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never persist raw exception content
            results.append(
                _safe_result(
                    attempt,
                    status_code=None,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    outcome_code="request_error",
                )
            )
    report = {
        "schema_version": "1.0",
        "evidence_type": "cross_account_role_isolation",
        "quality_matrix_attempts": 0,
        "run_id": run_id,
        "input_sha256": input_fingerprint,
        "expected_attempts": EXPECTED_ATTEMPTS,
        "executed_attempts": len(results),
        "pass": (
            len(results) == EXPECTED_ATTEMPTS
            and len({item["attempt_id"] for item in results}) == EXPECTED_ATTEMPTS
            and all(item["passed"] for item in results)
        ),
        "results": results,
    }
    _assert_report_privacy(report)
    return report


def assert_isolation_report(report: dict[str, Any]) -> None:
    _assert_report_privacy(report)
    results = [
        item for item in report.get("results") or [] if isinstance(item, dict)
    ]
    if report.get("evidence_type") != "cross_account_role_isolation":
        raise IsolationConfigurationError("evidence_type_mismatch")
    if report.get("quality_matrix_attempts") != 0:
        raise IsolationConfigurationError("isolation_attempts_must_not_count_as_quality")
    if len(results) != EXPECTED_ATTEMPTS:
        raise IsolationConfigurationError("isolation_attempt_count_mismatch")
    if len({str(item.get("attempt_id") or "") for item in results}) != EXPECTED_ATTEMPTS:
        raise IsolationConfigurationError("duplicate_isolation_attempt")
    if any(item.get("status_code") not in ACCEPTED_DENIAL_STATUSES for item in results):
        raise IsolationConfigurationError("cross_account_access_not_denied")
    if report.get("pass") is not True:
        raise IsolationConfigurationError("isolation_report_not_passing")


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    _assert_report_privacy(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise IsolationConfigurationError(
            f"refusing_to_overwrite_evidence:{path}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--base-url", default=os.getenv("CHATBOT_API_URL", "http://127.0.0.1:5055"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run eight live checks. Without this flag only the plan is validated.",
    )
    return parser


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    payload = _read_json(args.input)
    run_id = uuid.uuid4().hex
    plan = build_isolation_plan(payload, run_id=run_id)
    if not args.execute:
        return {
            "schema_version": "1.0",
            "mode": "dry-run",
            "evidence_type": "cross_account_role_isolation",
            "quality_matrix_attempts": 0,
            "run_id": run_id,
            "input_sha256": artifact_fingerprint(payload),
            "planned_attempts": len(plan),
            "executed_attempts": 0,
            "ready_for_execute": True,
        }
    if args.output is None:
        raise IsolationConfigurationError("--output is required with --execute")
    tokens = resolve_account_tokens()
    timeout = httpx.Timeout(args.timeout_seconds, connect=10.0)
    async with httpx.AsyncClient(
        base_url=str(args.base_url).rstrip("/"),
        timeout=timeout,
        follow_redirects=False,
    ) as client:
        by_slot = {str(item["slot"]): item for item in payload["accounts"]}
        for slot in ("a", "b"):
            response = await client.get(
                "/api/users/me",
                headers={"Authorization": f"Bearer {tokens[slot]}"},
            )
            if response.status_code != 200:
                raise IsolationConfigurationError(
                    f"identity_preflight_status:{slot}:{response.status_code}"
                )
            try:
                identity = response.json()
            except ValueError as exc:
                raise IsolationConfigurationError(
                    f"identity_preflight_invalid_json:{slot}"
                ) from exc
            if not isinstance(identity, dict):
                raise IsolationConfigurationError(
                    f"identity_preflight_invalid_payload:{slot}"
                )
            identity_errors = validate_authenticated_identity(
                by_slot[slot], identity
            )
            if identity_errors:
                raise IsolationConfigurationError(";".join(identity_errors))

        async def request_one(attempt: dict[str, Any]) -> tuple[int, float]:
            started = time.perf_counter()
            response = await client.request(
                attempt["method"],
                attempt["path"],
                headers={
                    "Authorization": f"Bearer {tokens[attempt['actor_slot']]}",
                },
                json=attempt["request_body"],
            )
            return response.status_code, (time.perf_counter() - started) * 1000

        report = await execute_isolation_plan(
            plan,
            requester=request_one,
            run_id=run_id,
            input_fingerprint=artifact_fingerprint(payload),
        )
    _write_new(args.output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = asyncio.run(_run_cli(args))
    except IsolationConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    summary = {key: value for key, value in report.items() if key != "results"}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
