"""Run privacy-safe technical gates after a human form attestation."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FRONTEND = ROOT / "frontend"
STATUS_PATH = ROOT / "data" / "form_release_gate" / "status_v1.json"
LOCK_PATH = ROOT / "data" / "form_release_gate" / "gate.lock"
CAMPAIGN_STATUS_PATH = ROOT / "data" / "form_resolution_campaign" / "status_v1.json"
REPORT_DIR = ROOT / "reports" / "feature005"
DEFAULT_API_BASE_URL = "http://127.0.0.1:5056"
DEFAULT_UI_BASE_URL = "http://127.0.0.1:3001"
ROLE_TOKEN_KEYS = tuple(
    f"FEATURE005_{role}_TOKEN" for role in ("CITIZEN", "OFFICER", "ADMIN")
)
PRIVATE_CREDENTIAL_KEYS = frozenset(
    f"FEATURE005_{role}_{suffix}"
    for role in ("CITIZEN", "OFFICER", "ADMIN")
    for suffix in ("TOKEN", "IDENTIFIER", "PASSWORD")
)


def _project_python(root: Path = ROOT) -> str:
    candidate = (
        root
        / ".venv"
        / ("Scripts" if os.name == "nt" else "bin")
        / ("python.exe" if os.name == "nt" else "python")
    )
    return str(candidate) if candidate.is_file() else sys.executable


def _load_private_environment(path: Path) -> dict[str, str]:
    """Read only disposable Feature 005 credentials without logging values."""

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError("invalid private environment line")
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in PRIVATE_CREDENTIAL_KEYS:
            raise ValueError(f"unsupported private environment key: {key}")
        if not value:
            raise ValueError(f"empty private environment value: {key}")
        values[key] = value
    return values


def _runtime_environment(
    base: dict[str, str],
    *,
    api_base_url: str,
    ui_base_url: str,
) -> dict[str, str]:
    """Build an in-memory-only environment for the isolated release gates."""

    env = dict(base)
    env.update(
        {
            "E2E_BASE_URL": ui_base_url,
            "LEGAL_SECTION_GROUNDING_ENABLED": "true",
            "E2E_PRIVACY_SAFE": "true",
        }
    )
    admin_token = env.get("FEATURE005_ADMIN_TOKEN", "").strip()
    if admin_token and not env.get("LEGAL_BENCHMARK_TOKEN", "").strip():
        env["LEGAL_BENCHMARK_TOKEN"] = admin_token
    # This is used by launchers/evaluators that support an explicit API target.
    env["FEATURE005_GATE_API_BASE_URL"] = api_base_url
    return env


def _role_command(
    python: str,
    summary: Path,
    api_base_url: str,
) -> list[str]:
    return [
        python,
        "scripts/evaluate_feature005_role_matrix.py",
        "--base-url",
        api_base_url,
        "--no-private-report",
        "--summary",
        str(summary),
    ]


def _ui_role_command(npm: str) -> list[str]:
    return [
        npm,
        "exec",
        "--",
        "playwright",
        "test",
        "e2e/section-grounding.spec.ts",
        "--grep",
        "Feature 005 (citizen|officer|admin)",
    ]


def _generation_command(
    python: str,
    *,
    concurrency: int,
    artifact: Path,
    api_base_url: str,
    case_ids: tuple[str, ...],
) -> list[str]:
    command = [
        python,
        "scripts/benchmark_section_grounding.py",
        "--base-url",
        api_base_url,
        "--mode",
        "warm",
        "--concurrency",
        str(concurrency),
        "--artifact",
        str(artifact),
    ]
    for case_id in case_ids:
        command.extend(["--case", case_id])
    return command


def _benchmark_case_ids() -> tuple[str, ...]:
    """Load benchmark cases in both module and direct-script execution modes."""

    if __package__:
        from scripts.benchmark_section_grounding import BENCHMARK_CASE_IDS
    else:
        from benchmark_section_grounding import BENCHMARK_CASE_IDS

    return tuple(BENCHMARK_CASE_IDS)


def _retrieval_dataset_command(
    python: str,
    *,
    dataset: Path,
    expected_sources: Path,
    output: Path,
    legal_as_of: str,
    dataset_version: str,
    minimum_case_count: int,
) -> list[str]:
    return [
        python,
        "scripts/evaluate_retrieval_dataset.py",
        "--dataset",
        str(dataset),
        "--expected-sources",
        str(expected_sources),
        "--retrieval-url",
        "http://127.0.0.1:8765",
        "--legal-as-of",
        legal_as_of,
        "--dataset-version",
        dataset_version,
        "--retrieval-version",
        "post-attestation-live",
        "--concurrency",
        "5",
        "--minimum-case-count",
        str(minimum_case_count),
        "--output",
        str(output),
    ]


def _url_ready(url: str, *, timeout_seconds: float = 5.0) -> bool:
    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            return 200 <= int(response.status) < 300
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def _wait_url_ready(
    url: str,
    *,
    deadline_seconds: float = 45.0,
    request_timeout_seconds: float = 10.0,
) -> bool:
    """Poll slow dependency readiness without treating startup latency as failure."""

    deadline = time.monotonic() + deadline_seconds
    while True:
        if _url_ready(url, timeout_seconds=request_timeout_seconds):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(1)


def _ensure_service(
    name: str,
    *,
    readiness_url: str,
    start_command: list[str],
    cwd: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    started = time.perf_counter()
    if _wait_url_ready(
        readiness_url,
        deadline_seconds=20.0,
        request_timeout_seconds=10.0,
    ):
        return {
            "name": name,
            "status": "PASS",
            "reason_code": "ALREADY_READY",
            "duration_seconds": round(time.perf_counter() - started, 2),
        }
    if os.name != "nt":
        return {
            "name": name,
            "status": "BLOCKED",
            "reason_code": "WINDOWS_ISOLATED_RUNTIME_LAUNCHER_REQUIRED",
            "duration_seconds": round(time.perf_counter() - started, 2),
        }
    try:
        completed = subprocess.run(
            start_command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return {
            "name": name,
            "status": "BLOCKED",
            "reason_code": "ISOLATED_RUNTIME_LAUNCHER_UNAVAILABLE",
            "duration_seconds": round(time.perf_counter() - started, 2),
        }
    ready = completed.returncode == 0 and _wait_url_ready(readiness_url)
    return {
        "name": name,
        "status": "PASS" if ready else "BLOCKED",
        "reason_code": "STARTED" if ready else "ISOLATED_RUNTIME_START_FAILED",
        "exit_code": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }


def _blocked(name: str, reason_code: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "BLOCKED",
        "reason_code": reason_code,
        "duration_seconds": 0,
    }


def _write(payload: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_suffix(f"{STATUS_PATH.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, STATUS_PATH)


def _run(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return {
            "name": name,
            "status": "BLOCKED",
            "reason_code": "COMMAND_UNAVAILABLE",
            "duration_seconds": round(time.perf_counter() - started, 2),
        }
    return {
        "name": name,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "exit_code": completed.returncode,
        "duration_seconds": round(time.perf_counter() - started, 2),
    }


def _record_campaign_gate_status(
    *,
    attestation_id: str,
    release_status: str,
    campaign_status_path: Path = CAMPAIGN_STATUS_PATH,
) -> dict[str, Any]:
    """Bind the terminal technical result to the exact human attestation."""

    started = time.perf_counter()
    try:
        from api.form_resolution_campaign import (
            load_campaign_status,
            mark_campaign_attested,
        )

        current = load_campaign_status(campaign_status_path)
        if str(current.get("attestation_id") or "") != str(attestation_id):
            raise ValueError("FORM_RESOLUTION_ATTESTATION_MISMATCH")
        run_id = str(current.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("FORM_RESOLUTION_RUN_ID_REQUIRED")
        updated = mark_campaign_attested(
            run_id=run_id,
            attestation_id=attestation_id,
            release_gate_status=release_status,
            path=campaign_status_path,
        )
        return {
            "name": "campaign_release_gate_handoff",
            "status": "PASS",
            "reason_code": updated["status"],
            "duration_seconds": round(time.perf_counter() - started, 2),
        }
    except (OSError, ValueError, KeyError):
        return {
            "name": "campaign_release_gate_handoff",
            "status": "FAIL",
            "reason_code": "CAMPAIGN_RELEASE_GATE_HANDOFF_FAILED",
            "duration_seconds": round(time.perf_counter() - started, 2),
        }


def execute(*, legal_as_of: str, attestation_id: str) -> dict[str, Any]:
    date.fromisoformat(legal_as_of)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("FORM_RELEASE_GATE_ALREADY_RUNNING") from exc
    os.close(lock_fd)
    report_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = {
        "schema_version": "form-post-attestation-gate-v1",
        "status": "running",
        "stage": "starting",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": legal_as_of,
        "attestation_ref": attestation_id,
        "passed": 0,
        "failed": 0,
        "blocked": 0,
        "checks": [],
        "feature_flag_enabled": False,
    }
    env = os.environ.copy()
    retrieval_site = ROOT / ".venv-retrieval-cu126" / "Lib" / "site-packages"
    if retrieval_site.is_dir():
        env["PYTHONPATH"] = os.pathsep.join(
            [str(retrieval_site), env.get("PYTHONPATH", "")]
        ).rstrip(os.pathsep)
    python = _project_python()
    npm = "npm.cmd" if os.name == "nt" else "npm"
    role_summary = REPORT_DIR / f"post-attestation-role-{report_id}.json"
    golden_retrieval_report = (
        REPORT_DIR / f"post-attestation-retrieval-167-{report_id}.json"
    )
    dataset_retrieval_report = (
        REPORT_DIR / f"post-attestation-retrieval-1000-{report_id}.json"
    )
    independent_checks: list[tuple[str, list[str], Path]] = [
        (
            "form_catalog_validation",
            [
                python,
                "-m",
                "pytest",
                "-q",
                "tests/test_step1_form_catalog_hard_gates.py",
                "tests/test_form_candidate_reconciliation.py",
                "tests/test_form_review_sync.py",
                "tests/test_official_form_runtime.py",
            ],
            ROOT,
        ),
        ("full_backend", [python, "-m", "pytest", "-q"], ROOT),
        ("frontend_tests", [npm, "test", "--", "--run"], FRONTEND),
        (
            "frontend_typecheck",
            [npm, "exec", "--", "tsc", "--noEmit", "--pretty", "false"],
            FRONTEND,
        ),
        ("frontend_lint", [npm, "run", "lint"], FRONTEND),
        ("frontend_build", [npm, "run", "build"], FRONTEND),
        (
            "authorization_legacy_rollback",
            [
                python,
                "-m",
                "pytest",
                "-q",
                "tests/test_section_authorization.py",
                "tests/test_step7_role_delivery.py",
                "tests/test_section_grounding_flag.py",
                "tests/test_section_response_contract.py",
            ],
            ROOT,
        ),
        (
            "golden_167_retrieval_only",
            _retrieval_dataset_command(
                python,
                dataset=ROOT / "notebook_data" / "legal-golden-set.json",
                expected_sources=(
                    ROOT
                    / "reports"
                    / "feature005"
                    / "step2-data-gap-20260724"
                    / "expected-sources.jsonl"
                ),
                output=golden_retrieval_report,
                legal_as_of=legal_as_of,
                dataset_version="post-attestation-golden-167",
                minimum_case_count=167,
            ),
            ROOT,
        ),
        (
            "dataset_1000_retrieval_only",
            _retrieval_dataset_command(
                python,
                dataset=(
                    ROOT
                    / "reports"
                    / "feature005"
                    / "goal-20260728"
                    / "dataset-1000-grounded-v4.json"
                ),
                expected_sources=(
                    ROOT
                    / "reports"
                    / "feature005"
                    / "goal-20260728"
                    / "dataset-1000-expected-v4.jsonl"
                ),
                output=dataset_retrieval_report,
                legal_as_of=legal_as_of,
                dataset_version="post-attestation-grounded-1000",
                minimum_case_count=1000,
            ),
            ROOT,
        ),
    ]
    private_dir = REPORT_DIR / ".private"
    credential_path = private_dir / f"feature005-gate-{report_id}.env"
    created_credentials = False
    cleanup_attempted = False
    results: list[dict[str, Any]] = []
    try:

        powershell = "powershell.exe"
        api_readiness = _ensure_service(
            "isolated_api_ready",
            readiness_url=f"{DEFAULT_API_BASE_URL}/ready",
            start_command=[
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "scripts" / "start_step5_benchmark_api.ps1"),
                "-Port",
                "5056",
            ],
            cwd=ROOT,
            env=env,
        )
        results.append(api_readiness)
        if api_readiness["status"] == "PASS":
            ui_readiness = _ensure_service(
                "isolated_ui_ready",
                readiness_url=f"{DEFAULT_UI_BASE_URL}/login",
                start_command=[
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "scripts" / "start_step7_ui_gate.ps1"),
                    "-Port",
                    "3001",
                    "-ApiPort",
                    "5056",
                ],
                cwd=ROOT,
                env=env,
            )
        else:
            ui_readiness = _blocked(
                "isolated_ui_ready",
                "ISOLATED_API_REQUIRED",
            )
        results.append(ui_readiness)

        if all(env.get(key, "").strip() for key in ROLE_TOKEN_KEYS):
            credential_result = {
                "name": "isolated_credentials",
                "status": "PASS",
                "reason_code": "CALLER_SUPPLIED",
                "duration_seconds": 0,
            }
        elif api_readiness["status"] != "PASS":
            credential_result = _blocked(
                "isolated_credentials",
                "ISOLATED_API_REQUIRED",
            )
        else:
            private_dir.mkdir(parents=True, exist_ok=True)
            create_result = _run(
                "isolated_credentials",
                [
                    python,
                    "scripts/create_feature005_test_accounts.py",
                    "--output",
                    str(credential_path),
                    "--suffix",
                    f"gate_{report_id.replace('-', '_')}",
                    "--include-login-credentials",
                ],
                cwd=ROOT,
                env=env,
            )
            if create_result["status"] == "PASS":
                try:
                    credentials = _load_private_environment(credential_path)
                    if not all(credentials.get(key, "").strip() for key in ROLE_TOKEN_KEYS):
                        raise ValueError("role token set is incomplete")
                    env.update(credentials)
                    created_credentials = True
                    credential_result = create_result
                except (OSError, ValueError):
                    credential_result = _blocked(
                        "isolated_credentials",
                        "PRIVATE_CREDENTIAL_MANIFEST_INVALID",
                    )
            else:
                credential_result = create_result
        results.append(credential_result)
        runtime_env = _runtime_environment(
            env,
            api_base_url=DEFAULT_API_BASE_URL,
            ui_base_url=DEFAULT_UI_BASE_URL,
        )

        for name, command, cwd in independent_checks:
            base["stage"] = name
            base["checks"] = results
            _write(base)
            results.append(_run(name, command, cwd=cwd, env=env))

        runtime_ready = (
            api_readiness["status"] == "PASS"
            and credential_result["status"] == "PASS"
        )
        if runtime_ready:
            base["stage"] = "api_role_cases_9"
            base["checks"] = results
            _write(base)
            results.append(
                _run(
                    "api_role_cases_9",
                    _role_command(python, role_summary, DEFAULT_API_BASE_URL),
                    cwd=ROOT,
                    env=runtime_env,
                )
            )

            if ui_readiness["status"] == "PASS":
                base["stage"] = "ui_role_cases_9"
                base["checks"] = results
                _write(base)
                results.append(
                    _run(
                        "ui_role_cases_9",
                        _ui_role_command(npm),
                        cwd=FRONTEND,
                        env=runtime_env,
                    )
                )
            else:
                results.append(
                    _blocked("ui_role_cases_9", "ISOLATED_UI_REQUIRED")
                )
        else:
            results.append(
                _blocked("api_role_cases_9", "ISOLATED_RUNTIME_OR_CREDENTIALS_REQUIRED")
            )
            results.append(
                _blocked("ui_role_cases_9", "ISOLATED_RUNTIME_OR_CREDENTIALS_REQUIRED")
            )

        if not runtime_ready:
            results.append(
                _blocked(
                    "generation_benchmark_concurrency_1_5",
                    "ISOLATED_RUNTIME_OR_CREDENTIALS_REQUIRED",
                )
            )
        elif not runtime_env.get("LEGAL_BENCHMARK_TOKEN", "").strip():
            results.append(
                _blocked(
                    "generation_benchmark_concurrency_1_5",
                    "LEGAL_BENCHMARK_TOKEN_REQUIRED",
                )
            )
        else:
            benchmark_case_ids = _benchmark_case_ids()

            for concurrency in (1, 5):
                artifact = (
                    REPORT_DIR
                    / f"post-attestation-generation-c{concurrency}-{report_id}.json"
                )
                command = _generation_command(
                    python,
                    concurrency=concurrency,
                    artifact=artifact,
                    api_base_url=DEFAULT_API_BASE_URL,
                    case_ids=benchmark_case_ids,
                )
                results.append(
                    _run(
                        f"generation_benchmark_warm_c{concurrency}",
                        command,
                        cwd=ROOT,
                        env=runtime_env,
                    )
                )

        if created_credentials:
            cleanup_attempted = True
            results.append(
                _run(
                    "isolated_credentials_cleanup",
                    [
                        python,
                        "scripts/cleanup_feature005_test_accounts.py",
                        "--input",
                        str(credential_path),
                    ],
                    cwd=ROOT,
                    env=env,
                )
            )

        pre_handoff_failed = sum(item["status"] == "FAIL" for item in results)
        pre_handoff_blocked = sum(item["status"] == "BLOCKED" for item in results)
        pre_handoff_status = (
            "PASS"
            if pre_handoff_failed == 0 and pre_handoff_blocked == 0
            else "BLOCKED_RELEASE"
        )
        results.append(
            _record_campaign_gate_status(
                attestation_id=attestation_id,
                release_status=pre_handoff_status,
            )
        )
        passed = sum(item["status"] == "PASS" for item in results)
        failed = sum(item["status"] == "FAIL" for item in results)
        blocked = sum(item["status"] == "BLOCKED" for item in results)
        status = "PASS" if failed == 0 and blocked == 0 else "BLOCKED_RELEASE"
        report = {
            **base,
            "status": status,
            "stage": "complete",
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "checks": results,
            "feature_flag_enabled": False,
        }
        _write(report)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"post-attestation-release-gate-{report_id}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report
    except Exception as exc:
        results.append(
            {
                "name": "runner_internal_error",
                "status": "FAIL",
                "reason_code": "UNHANDLED_GATE_EXCEPTION",
                "exception_type": type(exc).__name__,
                "duration_seconds": 0,
            }
        )
        report = {
            **base,
            "status": "BLOCKED_RELEASE",
            "stage": "runner_internal_error",
            "passed": sum(item["status"] == "PASS" for item in results),
            "failed": sum(item["status"] == "FAIL" for item in results),
            "blocked": sum(item["status"] == "BLOCKED" for item in results),
            "checks": results,
            "feature_flag_enabled": False,
        }
        _write(report)
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"post-attestation-release-gate-{report_id}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return report
    finally:
        if created_credentials and not cleanup_attempted and credential_path.exists():
            subprocess.run(
                [
                    python,
                    "scripts/cleanup_feature005_test_accounts.py",
                    "--input",
                    str(credential_path),
                ],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        credential_path.unlink(missing_ok=True)
        LOCK_PATH.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legal-as-of", required=True)
    parser.add_argument("--attestation-id", required=True)
    args = parser.parse_args()
    report = execute(
        legal_as_of=args.legal_as_of,
        attestation_id=args.attestation_id,
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
