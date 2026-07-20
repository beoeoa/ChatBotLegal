$ErrorActionPreference = "Continue"

$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot "logs\local-services.json"

$pids = @()
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $pids += $state.surreal_pid
    $pids += $state.backend_pid
    $pids += $state.retrieval_pid
    $pids += $state.frontend_pid
}

$pids += Get-NetTCPConnection -State Listen -LocalPort 3000,5055,8000,8765 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess

$pids | Where-Object { $_ -and $_ -gt 0 } | Sort-Object -Unique | ForEach-Object {
    Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Seconds 2
Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
Write-Host "Local services stopped. Data files were not removed."
