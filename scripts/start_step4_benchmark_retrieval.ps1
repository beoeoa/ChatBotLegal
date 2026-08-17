param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $projectRoot "reports\feature005\db4-serving-20260723\manifest.json"
$statePath = Join-Path $projectRoot "logs\step4-benchmark-retrieval.json"

if (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue) {
    throw "Port 8765 is already in use. Stop the existing retrieval process first."
}
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Missing approved DB-4 serving manifest."
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if (
    $manifest.status -ne "verified" -or
    -not $manifest.collections.primary.valid -or
    -not $manifest.collections.support.valid -or
    -not $manifest.source_unchanged -or
    -not $manifest.active_pointer_unchanged
) {
    throw "DB-4 serving manifest is not verified."
}

$env:LEGAL_CHROMA_COLLECTION = [string]$manifest.primary_collection
$env:LEGAL_CHROMA_SOURCE_COLLECTION = [string]$manifest.support_collection
$env:LEGAL_SEARCH_CACHE_TTL_SECONDS = "600"
$env:LEGAL_EMBED_DEVICE = "auto"

& powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File (Join-Path $PSScriptRoot "start_legal_search.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Isolated Step 4 retrieval launcher failed."
}

$health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 10
if (
    $health.status -ne "healthy" -or
    $health.collection -ne $manifest.primary_collection
) {
    throw "Isolated retrieval did not load the approved primary collection."
}
$pidValue = (
    Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction Stop |
        Select-Object -First 1 -ExpandProperty OwningProcess
)
@{
    pid = $pidValue
    started_at = (Get-Date).ToString("o")
    primary_collection = $manifest.primary_collection
    support_collection = $manifest.support_collection
    active_pointer_changed = $false
    source_collection_changed = $false
} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host "Isolated Step 4 primary/support retrieval ready."
