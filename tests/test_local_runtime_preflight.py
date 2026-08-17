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
    ready_wait = script.index("http://127.0.0.1:5055/ready/import")

    assert retrieval_start < backend_start < ready_wait
    assert "http://127.0.0.1:5055/health" not in script
    assert '"--reload"' not in script
    assert '$env:LEGAL_SEARCH_HOST = "127.0.0.1"' in script


def test_local_readiness_probe_allows_model_provider_latency():
    script = _read("scripts/start_local.ps1")

    assert "[int]$ProbeTimeoutSeconds = 3" in script
    assert (
        "Test-HttpService -Url $Url -TimeoutSeconds $ProbeTimeoutSeconds"
        in script
    )
    assert re.search(
        r'Name "Backend readiness".*?'
        r'Url "http://127\.0\.0\.1:5055/ready/import".*?'
        r"ProbeTimeoutSeconds 15",
        script,
        re.S,
    )


def test_retrieval_launcher_returns_to_its_parent_after_ready():
    script = _read("scripts/start_legal_search.ps1")

    assert "exit 0" not in script
    assert "return" in script
    assert 'throw "Legal retrieval startup rejected: $reasonCode"' in script


def test_retrieval_launcher_is_reachable_from_docker_host_gateway():
    launcher = _read("scripts/start_legal_search.ps1")
    server = _read("scripts/legal_search_server.py")

    assert '$env:LEGAL_SEARCH_HOST' in launcher
    assert '"0.0.0.0"' in launcher
    assert 'os.getenv("LEGAL_SEARCH_HOST", "127.0.0.1")' in server


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


def test_local_api_launchers_import_auth_and_feature_flags_from_dotenv():
    for relative_path in (
        "scripts/start_local.ps1",
        "scripts/restart_local_api.ps1",
        "scripts/start_step4_benchmark_api.ps1",
        "scripts/start_step5_benchmark_api.ps1",
    ):
        script = _read(relative_path)
        assert '"OPEN_NOTEBOOK_PASSWORD"' in script
        assert '"OPEN_NOTEBOOK_ENCRYPTION_KEY"' in script
        assert '"LEGAL_SECTION_GROUNDING_ENABLED"' in script
        assert "Get-Content -LiteralPath $envPath" in script
        assert '$env:OPEN_NOTEBOOK_ENCRYPTION_KEY = "change-me-to-a-secret-string"' not in script


def test_local_api_launchers_use_repository_virtualenv():
    for relative_path in (
        "scripts/start_local.ps1",
        "scripts/restart_local_api.ps1",
        "scripts/start_step4_benchmark_api.ps1",
        "scripts/start_step5_benchmark_api.ps1",
    ):
        script = _read(relative_path)
        assert '.venv\\Scripts\\python.exe' in script
        assert "-FilePath $projectPython" in script or "-FilePath $python" in script
        assert '-FilePath "python"' not in script


def test_local_api_restart_recovers_when_state_pid_is_stale():
    script = _read("scripts/restart_local_api.ps1")

    assert "Get-NetTCPConnection" in script
    assert "-State Listen" in script
    assert "-LocalPort 5055" in script
    assert "Get-CimInstance Win32_Process" in script
    assert 'if ($commandLine -match "uvicorn" -and $commandLine -match "api\\.main:app")' in script
    assert "Port 5055 is occupied by a non-local-API process" in script
    assert '$readyPath = if ($ReadOnlyGate) { "/api/config" } else { "/ready/import" }' in script
    assert 'Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5055$readyPath" -TimeoutSec 15' in script


def test_local_api_restart_tolerates_listener_cleanup_after_process_exit():
    script = _read("scripts/restart_local_api.ps1")

    assert "if ($null -eq $process)" in script
    assert "$listenerCleanupPending = $true" in script
    assert "if (-not $stoppedListeningApi -and -not $listenerCleanupPending)" in script


def test_local_api_restart_owns_only_its_ipv4_loopback_listener():
    script = _read("scripts/restart_local_api.ps1")

    assert "function Get-LocalApiListeners" in script
    assert '$_.LocalAddress -eq "127.0.0.1"' in script
    assert "while (@(Get-LocalApiListeners).Count -gt 0)" in script
    assert "$activeBackendPid = if ($activeListeners.Count -gt 0)" in script
    assert "$activeListeners[0].OwningProcess" in script
    assert "$state.backend_pid = $activeBackendPid" in script


def test_local_api_restart_normalizes_duplicate_windows_path_key():
    script = _read("scripts/restart_local_api.ps1")

    assert "Normalize-ProcessPathKey" in script
    assert "[Environment]::GetEnvironmentVariables(\"Process\")" in script
    assert '[Environment]::SetEnvironmentVariable("PATH", $null, "Process")' in script
