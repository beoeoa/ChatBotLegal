[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RetrievalDataRoot,
    [Parameter(Mandatory = $true)][string]$SanitizedSurrealData,
    [string]$FormsRoot = "data\uploads\forms"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseRoot = Join-Path $projectRoot "release-data"

if (-not (Test-Path (Join-Path $RetrievalDataRoot "chroma_store"))) {
    throw "RetrievalDataRoot must contain chroma_store."
}
if (-not (Test-Path $SanitizedSurrealData)) {
    throw "SanitizedSurrealData does not exist. Export a clean database first."
}

$forbidden = @(".env", "private", "ask_sessions", "support_tickets", "browser_profiles", "logs")
foreach ($name in $forbidden) {
    if (Test-Path (Join-Path $SanitizedSurrealData $name)) {
        throw "Refusing to copy prohibited data path: $name"
    }
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
Remove-Item -LiteralPath (Join-Path $releaseRoot "legal\chroma_store") -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath (Join-Path $RetrievalDataRoot "chroma_store") -Destination (Join-Path $releaseRoot "legal\chroma_store") -Recurse

Remove-Item -LiteralPath (Join-Path $releaseRoot "surreal_data") -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath $SanitizedSurrealData -Destination (Join-Path $releaseRoot "surreal_data") -Recurse

if (Test-Path $FormsRoot) {
    Remove-Item -LiteralPath (Join-Path $releaseRoot "forms") -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $FormsRoot -Destination (Join-Path $releaseRoot "forms") -Recurse
}

New-Item -ItemType Directory -Force -Path (Join-Path $releaseRoot "notebook_data") | Out-Null
$manifest = Get-ChildItem $releaseRoot -Recurse -File | Where-Object { $_.Name -notmatch '^\.gitkeep$' } |
    ForEach-Object { $hash = Get-FileHash $_.FullName -Algorithm SHA256; [pscustomobject]@{ path = $_.FullName.Substring($projectRoot.Length + 1); sha256 = $hash.Hash.ToLowerInvariant(); bytes = $_.Length } }
$manifest | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $releaseRoot "manifest.sha256.json") -Encoding utf8
Write-Host "Prepared sanitized release data under $releaseRoot"
