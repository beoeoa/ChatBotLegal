from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from api.form_release_gate import (
    launch_form_release_gates,
    mark_stale_form_release_gate,
)
from scripts.run_post_attestation_release_gates import (
    DEFAULT_API_BASE_URL,
    DEFAULT_UI_BASE_URL,
    _generation_command,
    _ensure_service,
    _load_private_environment,
    _project_python,
    _record_campaign_gate_status,
    _retrieval_dataset_command,
    _role_command,
    _runtime_environment,
    _ui_role_command,
    _wait_url_ready,
)


def test_post_attestation_gate_launcher_is_idempotent_and_flag_off(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict]] = []

    def launch(command: list[str], **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=321)

    status_path = tmp_path / "data/form_release_gate/status_v1.json"
    first = launch_form_release_gates(
        project_root=tmp_path,
        legal_as_of="2026-07-27",
        attestation_id="form-batch-safe",
        status_path=status_path,
        launcher=launch,
    )
    second = launch_form_release_gates(
        project_root=tmp_path,
        legal_as_of="2026-07-27",
        attestation_id="form-batch-safe",
        status_path=status_path,
        launcher=launch,
    )

    assert first["status"] == "queued"
    assert first["feature_flag_enabled"] is False
    assert second["launch_status"] == "already_running"
    assert len(calls) == 1


def test_release_gate_rejects_different_attestation_while_running(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "attestation_ref": "attestation-one",
                "feature_flag_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError, match="FORM_RELEASE_GATE_DIFFERENT_ATTESTATION_RUNNING"
    ):
        launch_form_release_gates(
            project_root=tmp_path,
            legal_as_of="2026-07-29",
            attestation_id="attestation-two",
            status_path=status_path,
            launcher=lambda *args, **kwargs: None,
        )


def test_stale_release_gate_recovery_is_fail_closed(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "status": "running",
                "stage": "retrieval_benchmark",
                "generated_at": "2026-07-29T00:00:00+00:00",
                "attestation_ref": "attestation-one",
                "failed": 0,
                "checks": [],
                "feature_flag_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    result = mark_stale_form_release_gate(
        status_path=status_path,
        expected_attestation_id="attestation-one",
        lock_path=tmp_path / "missing.lock",
        now=__import__("datetime").datetime(
            2026, 7, 29, 0, 10, tzinfo=__import__("datetime").timezone.utc
        ),
    )

    assert result["status"] == "BLOCKED_RELEASE"
    assert result["stage"] == "runner_terminated_unfinalized"
    assert result["failed"] == 1
    assert result["feature_flag_enabled"] is False
    assert result["checks"][-1]["reason_code"] == "RUNNER_TERMINATED_UNFINALIZED"


def test_release_gate_prefers_project_virtualenv_over_host_python(
    tmp_path: Path,
) -> None:
    expected = (
        tmp_path / ".venv" / ("Scripts" if __import__("os").name == "nt" else "bin")
        / ("python.exe" if __import__("os").name == "nt" else "python")
    )
    expected.parent.mkdir(parents=True)
    expected.touch()

    assert _project_python(tmp_path) == str(expected)


def test_post_attestation_commands_target_isolated_flag_on_runtime(
    tmp_path: Path,
) -> None:
    role_summary = tmp_path / "role.json"
    generation_artifact = tmp_path / "generation.json"

    role = _role_command("python", role_summary, DEFAULT_API_BASE_URL)
    generation = _generation_command(
        "python",
        concurrency=5,
        artifact=generation_artifact,
        api_base_url=DEFAULT_API_BASE_URL,
        case_ids=("case-one",),
    )
    ui = _ui_role_command("npm.cmd")

    assert role[role.index("--base-url") + 1] == "http://127.0.0.1:5056"
    assert generation[generation.index("--base-url") + 1] == "http://127.0.0.1:5056"
    assert "--grep" in ui
    assert ui[ui.index("--grep") + 1] == "Feature 005 (citizen|officer|admin)"


def test_retrieval_gate_uses_live_167_and_1000_dataset_pipeline(
    tmp_path: Path,
) -> None:
    command = _retrieval_dataset_command(
        "python",
        dataset=tmp_path / "dataset.json",
        expected_sources=tmp_path / "expected.jsonl",
        output=tmp_path / "report.json",
        legal_as_of="2026-07-29",
        dataset_version="release-dataset",
        minimum_case_count=1000,
    )

    assert "scripts/evaluate_retrieval_dataset.py" in command
    assert "scripts/benchmark_legal_retrieval.py" not in command
    assert command[command.index("--minimum-case-count") + 1] == "1000"
    assert command[command.index("--concurrency") + 1] == "5"
    assert "--model-url" not in command
    assert "--fixture-results" not in command


def test_unhandled_runner_exception_persists_blocked_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import run_post_attestation_release_gates as runner

    monkeypatch.setattr(runner, "LOCK_PATH", tmp_path / "gate.lock")
    monkeypatch.setattr(runner, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(runner, "REPORT_DIR", tmp_path / "reports")

    def _raise(*args, **kwargs):
        raise RuntimeError("sensitive diagnostic must not be persisted")

    monkeypatch.setattr(runner, "_ensure_service", _raise)

    result = runner.execute(
        legal_as_of="2026-07-29",
        attestation_id="attestation-one",
    )

    assert result["status"] == "BLOCKED_RELEASE"
    assert result["stage"] == "runner_internal_error"
    assert result["checks"][-1]["reason_code"] == "UNHANDLED_GATE_EXCEPTION"
    assert result["checks"][-1]["exception_type"] == "RuntimeError"
    persisted = (tmp_path / "status.json").read_text(encoding="utf-8")
    assert "sensitive diagnostic" not in persisted
    assert not (tmp_path / "gate.lock").exists()


def test_runtime_environment_forces_isolated_ui_and_benchmark_token() -> None:
    env = _runtime_environment(
        {
            "FEATURE005_CITIZEN_TOKEN": "citizen-token",
            "FEATURE005_OFFICER_TOKEN": "officer-token",
            "FEATURE005_ADMIN_TOKEN": "admin-token",
        },
        api_base_url=DEFAULT_API_BASE_URL,
        ui_base_url=DEFAULT_UI_BASE_URL,
    )

    assert env["E2E_BASE_URL"] == "http://127.0.0.1:3001"
    assert env["LEGAL_SECTION_GROUNDING_ENABLED"] == "true"
    assert env["E2E_PRIVACY_SAFE"] == "true"
    assert env["LEGAL_BENCHMARK_TOKEN"] == "admin-token"


def test_private_environment_parser_allows_only_feature005_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private = tmp_path / "roles.env"
    private.write_text(
        "\n".join(
            [
                "FEATURE005_CITIZEN_TOKEN=citizen-secret",
                "FEATURE005_OFFICER_TOKEN=officer-secret",
                "FEATURE005_ADMIN_TOKEN=admin-secret",
                "FEATURE005_ADMIN_IDENTIFIER=feature005_admin_gate",
                "FEATURE005_ADMIN_PASSWORD=password-secret",
            ]
        ),
        encoding="utf-8",
    )

    loaded = _load_private_environment(private)

    assert loaded["FEATURE005_ADMIN_TOKEN"] == "admin-secret"
    assert capsys.readouterr() == ("", "")
    assert "admin-secret" not in json.dumps(
        {"keys": sorted(loaded)},
        ensure_ascii=False,
    )

    private.write_text(
        "FEATURE005_ADMIN_TOKEN=admin-secret\nUNSAFE_RUNTIME_SECRET=reject-me\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported private environment key"):
        _load_private_environment(private)


def test_isolated_api_launcher_uses_project_virtualenv() -> None:
    source = Path("scripts/start_step5_benchmark_api.ps1").read_text(
        encoding="utf-8"
    )

    assert '.venv\\Scripts\\python.exe' in source
    assert '-FilePath $python' in source


def test_isolated_ui_launcher_uses_stable_webpack_dev_server() -> None:
    source = Path("scripts/start_step7_ui_gate.ps1").read_text(
        encoding="utf-8"
    )

    assert '"dev", "--webpack"' in source


def test_readiness_wait_retries_slow_model_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import run_post_attestation_release_gates as runner

    attempts = iter((False, False, True))
    monkeypatch.setattr(runner, "_url_ready", lambda *args, **kwargs: next(attempts))
    monkeypatch.setattr(runner.time, "sleep", lambda *_: None)

    assert _wait_url_ready("http://127.0.0.1:5056/ready") is True


def test_existing_service_uses_bounded_readiness_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import run_post_attestation_release_gates as runner

    monkeypatch.setattr(runner, "_wait_url_ready", lambda *args, **kwargs: True)
    result = _ensure_service(
        "isolated_api_ready",
        readiness_url="http://127.0.0.1:5056/ready",
        start_command=["must-not-run"],
        cwd=tmp_path,
        env={},
    )

    assert result["status"] == "PASS"
    assert result["reason_code"] == "ALREADY_READY"


def test_terminal_gate_status_is_bound_to_exact_campaign_attestation(
    tmp_path: Path,
) -> None:
    campaign = tmp_path / "status.json"
    campaign.write_text(
        json.dumps(
            {
                "schema_version": "form-resolution-campaign-v1",
                "run_id": "run-1",
                "status": "ATTESTED_PENDING_RELEASE_GATES",
                "stage": "post_attestation_release_gates",
                "attestation_id": "attestation-1",
                "human_attestation_required": False,
                "feature_flag_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    result = _record_campaign_gate_status(
        attestation_id="attestation-1",
        release_status="PASS",
        campaign_status_path=campaign,
    )

    assert result["status"] == "PASS"
    assert result["reason_code"] == "ATTESTED_RELEASE_GATES_PASS"
    persisted = json.loads(campaign.read_text(encoding="utf-8"))
    assert persisted["status"] == "ATTESTED_RELEASE_GATES_PASS"
    assert persisted["feature_flag_enabled"] is False


def test_terminal_gate_status_rejects_attestation_mismatch(tmp_path: Path) -> None:
    campaign = tmp_path / "status.json"
    campaign.write_text(
        json.dumps(
            {
                "schema_version": "form-resolution-campaign-v1",
                "run_id": "run-1",
                "status": "ATTESTED_PENDING_RELEASE_GATES",
                "attestation_id": "different-attestation",
                "feature_flag_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    result = _record_campaign_gate_status(
        attestation_id="attestation-1",
        release_status="PASS",
        campaign_status_path=campaign,
    )

    assert result["status"] == "FAIL"
    assert result["reason_code"] == "CAMPAIGN_RELEASE_GATE_HANDOFF_FAILED"
