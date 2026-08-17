param(
    [int]$Port = 3001,
    [int]$ApiPort = 5056
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"
$envPath = Join-Path $projectRoot ".env"
$reportDir = Join-Path $projectRoot "reports\feature005\final-gate-20260727"

if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use."
}

$persistedFlag = "false"
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $key, $value = $trimmed.Split("=", 2)
        if ($key.Trim() -eq "LEGAL_SECTION_GROUNDING_ENABLED") {
            $persistedFlag = $value.Trim().Trim('"').Trim("'").ToLowerInvariant()
        }
    }
}
if ($persistedFlag -ne "false") {
    throw "Refusing UI gate: persisted LEGAL_SECTION_GROUNDING_ENABLED must remain false."
}

$api = Invoke-WebRequest `
    -UseBasicParsing `
    -Uri "http://127.0.0.1:$ApiPort/ready" `
    -TimeoutSec 30
if ($api.StatusCode -ne 200) {
    throw "Isolated API is not ready on port $ApiPort."
}

$env:INTERNAL_API_URL = "http://127.0.0.1:$ApiPort"
$env:NEXT_DIST_DIR = ".next-e2e-gate"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

$node = (Get-Command node).Source
$process = Start-Process `
    -FilePath $node `
    -ArgumentList @("node_modules/next/dist/bin/next", "dev", "--webpack", "-p", "$Port") `
    -WorkingDirectory $frontendRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $reportDir "frontend-e2e.out.log") `
    -RedirectStandardError (Join-Path $reportDir "frontend-e2e.err.log") `
    -PassThru

$deadline = (Get-Date).AddSeconds(120)
do {
    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Uri "http://127.0.0.1:$Port/login" `
            -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            Write-Host "Isolated UI gate ready on port $Port (PID $($process.Id))."
            return
        }
    } catch {
        Start-Sleep -Seconds 2
    }
} while ((Get-Date) -lt $deadline)

Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
throw "Isolated UI gate did not become ready."
