param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$logDir = Join-Path $projectRoot "logs"
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw "Repository Python runtime is missing: $projectPython"
}
$statePath = Join-Path $logDir "step4-benchmark-api.json"

if (Get-NetTCPConnection -State Listen -LocalPort 5055 -ErrorAction SilentlyContinue) {
    throw "Port 5055 is already in use. Stop the existing local API first."
}
foreach ($url in @(
    "http://127.0.0.1:8000/health",
    "http://127.0.0.1:8765/health"
)) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
        if ($response.StatusCode -ne 200) {
            throw "Dependency is not ready: $url"
        }
    } catch {
        throw "Dependency is not ready: $url"
    }
}

$configuredFlag = "false"
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $key, $value = $trimmed.Split("=", 2)
        $key = $key.Trim()
        $value = $value.Trim().Trim('"').Trim("'")
        if ($key -eq "LEGAL_SECTION_GROUNDING_ENABLED") {
            $configuredFlag = $value.ToLowerInvariant()
        }
        if ($key -in @(
            "OPEN_NOTEBOOK_PASSWORD",
            "OPEN_NOTEBOOK_CITIZEN_PASSWORD",
            "OPEN_NOTEBOOK_OFFICER_PASSWORD",
            "OPEN_NOTEBOOK_ADMIN_PASSWORD",
            "OPEN_NOTEBOOK_ENCRYPTION_KEY"
        )) {
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}
if ($configuredFlag -ne "false") {
    throw "Refusing isolated benchmark: .env must keep LEGAL_SECTION_GROUNDING_ENABLED=false."
}
if (
    -not $env:OPEN_NOTEBOOK_ENCRYPTION_KEY -or
    $env:OPEN_NOTEBOOK_ENCRYPTION_KEY -eq "change-me-to-a-secret-string"
) {
    throw "OPEN_NOTEBOOK_ENCRYPTION_KEY must be configured with a non-default value."
}

$env:SURREAL_URL = "ws://127.0.0.1:8000/rpc"
$env:SURREAL_USER = "root"
$env:SURREAL_PASSWORD = "root"
$env:SURREAL_NAMESPACE = "open_notebook"
$env:SURREAL_DATABASE = "open_notebook"
$env:OPEN_NOTEBOOK_DATA_DIR = Join-Path $projectRoot "notebook_data"
$env:LEGAL_SEARCH_URL = "http://127.0.0.1:8765"
$env:LEGAL_LLM_TIMEOUT_SECONDS = "60"
# The runtime classifies normal/hard questions deterministically and caps the
# actual model budget at 28/55 seconds, leaving room under the 30/60-second
# end-to-end release SLAs.
$env:LEGAL_STRUCTURED_GENERATION_TIMEOUT_SECONDS = "55"
$env:LEGAL_STRUCTURED_CONTEXT_MAX_CHARS = "4000"
$env:LEGAL_STRUCTURED_MAX_OUTPUT_TOKENS = "256"
$env:LEGAL_STRUCTURED_PROVIDER_JSON_MODE = "true"
# This flag is process-local. The persisted runtime configuration remains false.
$env:LEGAL_SECTION_GROUNDING_ENABLED = "true"
# Exercise the validated SSE contract in the same isolated acceptance runtime.
# The persisted rollout flag remains disabled in .env.
$env:LEGAL_ASK_PROGRESS_ENABLED = "true"
Remove-Item Env:LEGAL_DATABASE_URL -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$process = Start-Process `
    -FilePath $projectPython `
    -ArgumentList @("-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "5055") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir "step4-api.log") `
    -RedirectStandardError (Join-Path $logDir "step4-api.err.log") `
    -PassThru

$deadline = (Get-Date).AddSeconds(180)
do {
    try {
        $ready = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:5055/health" `
            -TimeoutSec 3
        if ($ready.StatusCode -eq 200) {
            @{
                pid = $process.Id
                started_at = (Get-Date).ToString("o")
                persisted_flag = $configuredFlag
                process_flag = "true"
                progress_process_flag = "true"
            } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
            Write-Host "Isolated Step 4 benchmark API ready on port 5055."
            return
        }
    } catch {
        Start-Sleep -Seconds 2
    }
} while ((Get-Date) -lt $deadline)

Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
throw "Isolated Step 4 benchmark API did not become ready."
