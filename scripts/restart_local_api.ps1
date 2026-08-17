param(
    [switch]$ReadOnlyGate,
    [switch]$DisableAnswerFallback
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $projectRoot "logs"
$statePath = Join-Path $logDir "local-services.json"
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw "Repository Python runtime is missing: $projectPython"
}

function Import-LocalRuntimeEnv {
    param([string]$ProjectRoot)

    $envPath = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath)) {
        return
    }
    $allowedKeys = @(
        "OPEN_NOTEBOOK_PASSWORD",
        "OPEN_NOTEBOOK_ENCRYPTION_KEY",
        "OPEN_NOTEBOOK_CITIZEN_PASSWORD",
        "OPEN_NOTEBOOK_OFFICER_PASSWORD",
        "OPEN_NOTEBOOK_ADMIN_PASSWORD",
        "LEGAL_SECTION_GROUNDING_ENABLED",
        "LEGAL_ANSWER_PIPELINE_V2_ENABLED",
        "LEGAL_ANSWER_PIPELINE_V2_ROLES",
        "LEGAL_PROBLEM_MAP_LLM_ENABLED",
        "LEGAL_LOCAL_FALLBACK_ENABLED",
        "LEGAL_ASK_PROGRESS_ENABLED",
        "LEGAL_STRUCTURED_PROVIDER_JSON_MODE",
        "LEGAL_FORM_COMPLETION_ENABLED",
        "LEGAL_VALIDITY_SYNC_ENABLED",
        "LEGAL_CRAWLER_ENABLED",
        "LEGAL_CRAWL_DETAIL_FETCH_ENABLED",
        "LEGAL_SOURCE_GAP_ENABLED",
        "LEGAL_ANSWER_FALLBACK_ENABLED",
        "LEGAL_IMPORT_WORKER_ENABLED",
        "LEGAL_STRUCTURED_CONTEXT_MAX_CHARS",
        "LEGAL_STRUCTURED_HARD_CONTEXT_MAX_CHARS",
        "LEGAL_STRUCTURED_GENERATION_TIMEOUT_SECONDS",
        "LEGAL_STRUCTURED_HARD_GENERATION_TIMEOUT_SECONDS",
        "LEGAL_STRUCTURED_TOTAL_TIMEOUT_SECONDS",
        "LEGAL_STRUCTURED_HARD_TOTAL_TIMEOUT_SECONDS",
        "LEGAL_STRUCTURED_MAX_OUTPUT_TOKENS",
        "LEGAL_STRUCTURED_PROVIDER_MAX_CONCURRENCY",
        "LEGAL_ANSWER_OPTIMIZED_PROFILE_ENABLED",
        "LEGAL_ANSWER_OPTIMIZED_PROFILE_ROLES",
        "LEGAL_ANSWER_OPTIMIZED_PROFILE_CONTEXT_BENCHMARK",
        "LEGAL_ANSWER_OPTIMIZED_PROFILE_ENFORCED",
        "LEGAL_ANSWER_OPTIMIZED_PROFILE_STATE_PATH",
        "LEGAL_RELEASE_DATABASE_URL",
        "FORM_GOVERNANCE_SOURCE",
        "FORM_GOVERNANCE_ROUTER_MODE",
        "FORM_GOVERNANCE_SHADOW_RELEASE_ID",
        "FORM_GOVERNANCE_ROLLOUT_ROLES",
        "FORM_RELEASE_VERIFY_REMOTE_SOURCES",
        "FORM_RELEASE_ASSET_ROOT"
    )
    foreach ($line in Get-Content -LiteralPath $envPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $key, $value = $trimmed.Split("=", 2)
        $key = $key.Trim()
        if ($allowedKeys -notcontains $key) {
            continue
        }
        $value = $value.Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

function Test-Ready {
    try {
        $readyPath = if ($ReadOnlyGate) { "/api/config" } else { "/ready/import" }
        return (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5055$readyPath" -TimeoutSec 15).StatusCode -eq 200
    } catch {
        return $false
    }
}

function Normalize-ProcessPathKey {
    # Some Windows launchers inject both Path and PATH. Start-Process converts
    # the environment into a case-insensitive dictionary and otherwise fails
    # before the child process is created.
    $processEnvironment = [Environment]::GetEnvironmentVariables("Process")
    $keys = @($processEnvironment.Keys | ForEach-Object { [string]$_ })
    if ($keys -ccontains "Path" -and $keys -ccontains "PATH") {
        $canonicalPath = [string]$processEnvironment["Path"]
        [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
        [Environment]::SetEnvironmentVariable("Path", $canonicalPath, "Process")
    }
}

function Get-LocalApiListeners {
    # Docker Desktop/WSL may expose an IPv6 wildcard relay on the same numeric
    # port while the repository API owns only 127.0.0.1. Never stop or reject
    # those unrelated relays when restarting the IPv4 loopback service.
    return @(
        Get-NetTCPConnection `
            -State Listen `
            -LocalPort 5055 `
            -ErrorAction SilentlyContinue |
            Where-Object { $_.LocalAddress -eq "127.0.0.1" }
    )
}

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.backend_pid) {
        Stop-Process -Id $state.backend_pid -Force -ErrorAction SilentlyContinue
    }
}

$deadline = (Get-Date).AddSeconds(20)
while (@(Get-LocalApiListeners).Count -gt 0) {
    $stoppedListeningApi = $false
    $listenerCleanupPending = $false
    $listeners = @(Get-LocalApiListeners)
    foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            $listenerCleanupPending = $true
            continue
        }
        $commandLine = [string]$process.CommandLine
        if ($commandLine -match "uvicorn" -and $commandLine -match "api\.main:app") {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
            $stoppedListeningApi = $true
        }
    }
    if (-not $stoppedListeningApi -and -not $listenerCleanupPending) {
        throw "Port 5055 is occupied by a non-local-API process; the local API was not restarted."
    }
    if ((Get-Date) -ge $deadline) {
        throw "Port 5055 is still in use; the local API was not restarted."
    }
    Start-Sleep -Milliseconds 500
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Import-LocalRuntimeEnv -ProjectRoot $projectRoot
if ($ReadOnlyGate) {
    # UI verification for read-only lifecycle phases must not wake background
    # discovery, validity-sync, or import jobs against the live local stores.
    $env:LEGAL_VALIDITY_SYNC_ENABLED = "false"
    $env:LEGAL_CRAWLER_ENABLED = "false"
    $env:LEGAL_CRAWL_DETAIL_FETCH_ENABLED = "false"
    $env:LEGAL_SOURCE_GAP_ENABLED = "false"
    $env:LEGAL_IMPORT_WORKER_ENABLED = "false"
}
if ($DisableAnswerFallback) {
    # Local diagnostic only: provider/deterministic fallback is disabled, but
    # legal grounding and claim validation remain fail-closed.
    $env:LEGAL_ANSWER_FALLBACK_ENABLED = "false"
}
if (
    -not $env:OPEN_NOTEBOOK_ENCRYPTION_KEY -or
    $env:OPEN_NOTEBOOK_ENCRYPTION_KEY -eq "change-me-to-a-secret-string"
) {
    throw "OPEN_NOTEBOOK_ENCRYPTION_KEY must be configured with a non-default value."
}

# Keep the restart environment identical to scripts/start_local.ps1.
$env:SURREAL_URL = "ws://127.0.0.1:8000/rpc"
$env:SURREAL_USER = "root"
$env:SURREAL_PASSWORD = "root"
$env:SURREAL_NAMESPACE = "open_notebook"
$env:SURREAL_DATABASE = "open_notebook"
$env:OPEN_NOTEBOOK_DATA_DIR = Join-Path $projectRoot "notebook_data"
$env:LEGAL_SEARCH_URL = "http://127.0.0.1:8765"
if ($env:LEGAL_RELEASE_DATABASE_URL) {
    $env:LEGAL_DATABASE_URL = $env:LEGAL_RELEASE_DATABASE_URL.Replace(
        "@host.docker.internal:",
        "@127.0.0.1:"
    )
} else {
    Remove-Item Env:LEGAL_DATABASE_URL -ErrorAction SilentlyContinue
}
$env:LEGAL_LLM_TIMEOUT_SECONDS = "60"
$env:LEGAL_EMBED_DEVICE = if ($env:LEGAL_EMBED_DEVICE) { $env:LEGAL_EMBED_DEVICE } else { "auto" }

Normalize-ProcessPathKey
$backendProcess = Start-Process `
    -FilePath $projectPython `
    -ArgumentList @("-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "5055") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir "backend-local.log") `
    -RedirectStandardError (Join-Path $logDir "backend-local.err.log") `
    -PassThru

$deadline = (Get-Date).AddSeconds(180)
while (-not (Test-Ready)) {
    if ((Get-Date) -ge $deadline) {
        throw "Local API did not become ready. Check logs/backend-local.err.log"
    }
    Start-Sleep -Seconds 2
}

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $activeListeners = @(Get-LocalApiListeners)
    $activeBackendPid = if ($activeListeners.Count -gt 0) {
        $activeListeners[0].OwningProcess
    } else {
        $backendProcess.Id
    }
    $state.backend_pid = $activeBackendPid
    $state.started_at = (Get-Date).ToString("o")
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
}

Write-Host "Local API ready: http://127.0.0.1:5055"
