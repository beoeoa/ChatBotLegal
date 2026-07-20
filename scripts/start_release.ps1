[CmdletBinding()]
param([switch]$Build)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path -LiteralPath ".env")) {
    throw "Missing .env. Copy .env.release.example to .env and fill the deployment secrets first."
}
foreach ($path in @("release-data\legal", "release-data\surreal_data", "release-data\notebook_data", "release-data\forms")) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing release data directory: $path"
    }
}

if ($Build) {
    docker compose --env-file .env -f docker-compose.release.yml build
}
docker compose --env-file .env -f docker-compose.release.yml up -d
docker compose --env-file .env -f docker-compose.release.yml ps

Write-Host "Release stack: http://localhost:3000"
Write-Host "API readiness: http://localhost:5055/ready"
Write-Host "Retrieval health: http://localhost:8765/health"
