param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $projectRoot "logs"
$statePath = Join-Path $logDir "local-services.json"

function Test-Ready {
    try {
        return (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5055/ready" -TimeoutSec 3).StatusCode -eq 200
    } catch {
        return $false
    }
}

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.backend_pid) {
        Stop-Process -Id $state.backend_pid -Force -ErrorAction SilentlyContinue
    }
}

$deadline = (Get-Date).AddSeconds(20)
while (Get-NetTCPConnection -State Listen -LocalPort 5055 -ErrorAction SilentlyContinue) {
    if ((Get-Date) -ge $deadline) {
        throw "Port 5055 is still in use; the local API was not restarted."
    }
    Start-Sleep -Milliseconds 500
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Keep the restart environment identical to scripts/start_local.ps1.
$env:SURREAL_URL = "ws://127.0.0.1:8000/rpc"
$env:SURREAL_USER = "root"
$env:SURREAL_PASSWORD = "root"
$env:SURREAL_NAMESPACE = "open_notebook"
$env:SURREAL_DATABASE = "open_notebook"
$env:OPEN_NOTEBOOK_ENCRYPTION_KEY = "change-me-to-a-secret-string"
$env:OPEN_NOTEBOOK_DATA_DIR = Join-Path $projectRoot "notebook_data"
$env:LEGAL_SEARCH_URL = "http://127.0.0.1:8765"
Remove-Item Env:LEGAL_DATABASE_URL -ErrorAction SilentlyContinue
$env:LEGAL_LLM_TIMEOUT_SECONDS = "90"
$env:LEGAL_EMBED_DEVICE = if ($env:LEGAL_EMBED_DEVICE) { $env:LEGAL_EMBED_DEVICE } else { "auto" }

$backendProcess = Start-Process `
    -FilePath "python" `
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
    $state.backend_pid = $backendProcess.Id
    $state.started_at = (Get-Date).ToString("o")
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
}

Write-Host "Local API ready: http://127.0.0.1:5055"
