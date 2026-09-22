[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^dpg-[a-z0-9]+$')]
    [string]$DatabaseId,

    [string]$SeedRoot = 'scratch/render-pg-legal-only-v2'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedSeedRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot $SeedRoot)).Path
$manifestPath = Join-Path $resolvedSeedRoot 'manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.source_dump_uploaded -ne $false) {
    throw 'Refusing a PostgreSQL seed that includes the mixed local source dump.'
}

$files = @(
    @{ Name = 'extensions.sql'; Hash = [string]$manifest.extensions_sha256 },
    @{ Name = 'schema.sql'; Hash = [string]$manifest.schema_sha256 },
    @{ Name = 'legal-data.sql'; Hash = [string]$manifest.legal_data_sha256 }
)
foreach ($entry in $files) {
    $path = Join-Path $resolvedSeedRoot $entry.Name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Legal-only PostgreSQL seed file is missing: $($entry.Name)"
    }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $entry.Hash.ToLowerInvariant()) {
        throw "Checksum mismatch for $($entry.Name)"
    }
}

$renderExecutable = (Get-Command render -ErrorAction Stop).Source
foreach ($entry in $files) {
    $path = Join-Path $resolvedSeedRoot $entry.Name
    & $renderExecutable psql $DatabaseId -- -v ON_ERROR_STOP=1 -f $path
    if ($LASTEXITCODE -ne 0) {
        throw "Render PostgreSQL restore failed for $($entry.Name)"
    }
}

$verificationSql = @'
SELECT
  (SELECT COUNT(*) FROM legal_documents) AS legal_documents,
  (SELECT COUNT(*) FROM legal_article_chunks) AS legal_article_chunks,
  (SELECT COUNT(*) FROM users) AS users,
  (SELECT COUNT(*) FROM conversations) AS conversations,
  (SELECT COUNT(*) FROM chat_histories) AS chat_histories;
'@
& $renderExecutable psql $DatabaseId --command $verificationSql -o text
if ($LASTEXITCODE -ne 0) {
    throw 'Render PostgreSQL post-restore verification failed.'
}

Write-Host 'Legal-only PostgreSQL seed restored. Confirm counts are 290 documents, 11204 chunks, and zero user/chat rows.'
