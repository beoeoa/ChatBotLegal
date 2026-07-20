import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_surreal_wait_wrapper_is_bounded_and_executes_child_process():
    script = _read("scripts/wait-for-surreal.sh")

    assert "SURREAL_WAIT_TIMEOUT_SECONDS" in script
    assert "SURREAL_WAIT_INTERVAL_SECONDS" in script
    assert 'exec "$@"' in script
    assert "sleep" in script
    assert "exit 1" in script


def test_docker_runtime_waits_for_healthy_surreal_before_api_and_worker():
    supervisor = _read("supervisord.conf")
    dockerfile = _read("Dockerfile")
    compose = _read("docker-compose.yml")

    assert supervisor.count("/app/scripts/wait-for-surreal.sh") >= 2
    assert "COPY scripts/wait-for-surreal.sh" in dockerfile
    assert "chmod +x /app/scripts/wait-for-surreal.sh" in dockerfile
    assert "healthcheck:" in compose
    assert "is-ready" in compose
    assert re.search(
        r"depends_on:\s+surrealdb:\s+condition:\s+service_healthy",
        compose,
    )


def test_local_preflight_is_non_mutating_and_does_not_depend_on_docker():
    script = _read("scripts/start_local.ps1")

    assert "[switch]$PreflightOnly" in script
    assert not re.search(r"docker\s+(?:compose\s+.*\s+)?(?:stop|rm|remove|kill)\b", script, re.I)
    assert "Get-ContainerPreflightIssues" not in script[script.index("function Get-LocalPreflightIssues"):]

    preflight_exit = script.index("if ($PreflightOnly)")
    first_mutation = script.index("New-Item -ItemType Directory")
    assert preflight_exit < first_mutation


def test_local_startup_starts_retrieval_before_waiting_for_api_readiness():
    script = _read("scripts/start_local.ps1")

    retrieval_start = script.index("start_legal_search.ps1")
    backend_start = script.index('"api.main:app"')
    ready_wait = script.index("http://127.0.0.1:5055/ready")

    assert retrieval_start < backend_start < ready_wait
    assert "http://127.0.0.1:5055/health" not in script
    assert '"--reload"' not in script


def test_retrieval_launcher_returns_to_its_parent_after_ready():
    script = _read("scripts/start_legal_search.ps1")

    assert "exit 0" not in script
    assert "return" in script
    assert 'throw "Legal retrieval startup rejected: $reasonCode"' in script


def test_legacy_start_commands_delegate_to_the_single_local_runtime():
    start_all = _read("scripts/start_all.ps1")
    historical_start = _read("start-services.ps1")

    assert 'Join-Path $PSScriptRoot "start_local.ps1"' in start_all
    assert "docker compose" not in start_all.lower()
    assert not re.search(r"start-process.*(?:docker|ngrok)", start_all, re.I)
    assert 'Join-Path $PSScriptRoot "scripts\\start_local.ps1"' in historical_start
    assert "docker compose" not in historical_start.lower()
    assert not re.search(r"start-process.*(?:docker|ngrok)", historical_start, re.I)


def test_local_stop_clears_only_the_runtime_state_file():
    script = _read("scripts/stop_local.ps1")

    assert "Remove-Item -LiteralPath $statePath" in script
    assert "Data files were not removed" in script
