param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$logDir = Join-Path $projectRoot "logs"
$statePath = Join-Path $logDir "local-services.json"

if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $retrievalProcessId = [int]$state.retrieval_pid
    $process = Get-Process -Id $retrievalProcessId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $retrievalProcessId -Force
    }
}

$deadline = (Get-Date).AddSeconds(20)
while (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue) {
    if ((Get-Date) -ge $deadline) {
        throw "Port 8765 is still in use; the local retrieval service was not restarted."
    }
    Start-Sleep -Milliseconds 500
}

# Match the local-only launcher. In particular, never inherit a staging URL.
Remove-Item Env:LEGAL_DATABASE_URL -ErrorAction SilentlyContinue
$env:LEGAL_EMBED_DEVICE = if ($env:LEGAL_EMBED_DEVICE) { $env:LEGAL_EMBED_DEVICE } else { "auto" }

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start_legal_search.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Legal retrieval launcher failed. Check logs/legal_search_err.log"
}

$readyDeadline = (Get-Date).AddSeconds(30)
do {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 3
        if ($health.status -eq "healthy" -and $health.ready) {
            break
        }
    } catch {
        # The launcher may still be prewarming the embedding model.
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $readyDeadline)

if (-not $health -or $health.status -ne "healthy" -or -not $health.ready) {
    throw "Legal retrieval did not become ready."
}

$newRetrievalPid = Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction Stop |
    Select-Object -First 1 -ExpandProperty OwningProcess
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $state.retrieval_pid = [int]$newRetrievalPid
    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
}

Write-Host "Local retrieval ready: http://127.0.0.1:8765"
