[CmdletBinding()]
param(
    [string]$DatabaseUrl = $env:LEGAL_RELEASE_DATABASE_URL,
    [string]$OutputPath = "release-data\legal-corpus\legal-corpus.dump"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    throw "Set LEGAL_RELEASE_DATABASE_URL locally before exporting the legal corpus."
}

$normalizedUrl = $DatabaseUrl -replace '^postgresql\+psycopg2:', 'postgresql:'
$uri = [System.Uri]$normalizedUrl
if ($uri.Scheme -ne 'postgresql' -or [string]::IsNullOrWhiteSpace($uri.Host)) {
    throw "LEGAL_RELEASE_DATABASE_URL must be a PostgreSQL URL."
}

$credential = $uri.UserInfo.Split(':', 2)
if ($credential.Count -ne 2) {
    throw "The local PostgreSQL URL must include a username and password."
}

$tables = @(
    'legal_fields',
    'legal_documents',
    'legal_articles',
    'legal_article_chunks',
    'legal_document_relationships',
    'legal_search_scope',
    'legal_commune_field_groups'
)

$destination = Join-Path (Get-Location) $OutputPath
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null

$outputDirectory = Split-Path -Parent $destination
$arguments = @(
    'run', '--rm', '-e', 'PGPASSWORD',
    '--mount', "type=bind,source=$outputDirectory,target=/output",
    'postgres:18-alpine', 'pg_dump',
    '--host', $uri.Host,
    '--port', $(if ($uri.IsDefaultPort) { '5432' } else { [string]$uri.Port }),
    '--username', $credential[0],
    '--dbname', $uri.AbsolutePath.TrimStart('/'),
    '--format=custom', '--no-owner', '--no-privileges', '--file=/output/legal-corpus.dump'
)
foreach ($table in $tables) {
    $arguments += "--table=public.$table"
}

$env:PGPASSWORD = [System.Uri]::UnescapeDataString($credential[1])
& docker --context desktop-linux @arguments
if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed; no release corpus was created."
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $destination).Hash.ToLowerInvariant()
Write-Host "Legal corpus export completed: $destination"
Write-Host "SHA-256: $hash"
