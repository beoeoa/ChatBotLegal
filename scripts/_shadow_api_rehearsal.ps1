$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
foreach ($line in Get-Content (Join-Path $root ".env")) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
    $key, $value = $trimmed.Split("=", 2)
    [Environment]::SetEnvironmentVariable($key.Trim(), $value.Trim().Trim('"').Trim("'"), "Process")
}
$env:FORM_GOVERNANCE_ROUTER_MODE = "shadow"
$env:FORM_GOVERNANCE_SHADOW_RELEASE_ID = "forms-2026-08-11-feature017-attested"
$env:LEGAL_VALIDITY_SYNC_ENABLED = "false"
$env:LEGAL_CRAWLER_ENABLED = "false"
$env:LEGAL_CRAWL_DETAIL_FETCH_ENABLED = "false"
$env:LEGAL_SOURCE_GAP_ENABLED = "false"
$env:LEGAL_IMPORT_WORKER_ENABLED = "false"
$env:SURREAL_URL = "ws://127.0.0.1:8000/rpc"
$env:SURREAL_USER = "root"
$env:SURREAL_PASSWORD = "root"
$env:SURREAL_NAMESPACE = "open_notebook"
$env:SURREAL_DATABASE = "open_notebook"
$env:OPEN_NOTEBOOK_DATA_DIR = Join-Path $root "notebook_data"
$env:LEGAL_SEARCH_URL = "http://127.0.0.1:8765"
$logDir = Join-Path $root "logs"
$proc = Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList @("-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "5056", "--log-level", "warning") -WorkingDirectory $root -RedirectStandardOutput (Join-Path $logDir "shadow-api.log") -RedirectStandardError (Join-Path $logDir "shadow-api.err.log") -PassThru
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        if ((Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:5056/ready" -TimeoutSec 2).StatusCode -eq 200) { $ready = $true; break }
    } catch {}
    Start-Sleep -Seconds 2
}
Write-Output ("pid={0} ready={1}" -f $proc.Id, $ready)
if (-not $ready) { Get-Content (Join-Path $logDir "shadow-api.err.log") -Tail 40 }
