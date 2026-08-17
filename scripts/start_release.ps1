[CmdletBinding()]
param(
    [switch]$Build,
    [string]$EnvFile = ".env.release"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Missing dedicated release environment file: $EnvFile"
}

$releaseEnv = @{}
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    $trimmed = $line.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
        continue
    }
    $key, $value = $trimmed.Split("=", 2)
    $releaseEnv[$key.Trim()] = $value.Trim().Trim('"').Trim("'")
}

$requiredSecrets = @(
    "OPEN_NOTEBOOK_ADMIN_PASSWORD",
    "OPEN_NOTEBOOK_ENCRYPTION_KEY",
    "SURREAL_PASSWORD",
    "LEGAL_POSTGRES_PASSWORD"
    "GATEWAY_RATE_LIMIT_TOKEN"
)
foreach ($key in $requiredSecrets) {
    $value = [string]$releaseEnv[$key]
    if (
        -not $value -or
        $value.Length -lt 16 -or
        $value -match "(?i)^(change|replace|password|secret|root)" -or
        $value -match "(?i)(change-this|replace-with)"
    ) {
        throw "Release secret $key is missing, too short, or still uses a placeholder."
    }
}
if ([string]$releaseEnv["LEGAL_SECTION_GROUNDING_ENABLED"] -ne "false") {
    throw "Release preflight requires LEGAL_SECTION_GROUNDING_ENABLED=false."
}

foreach ($path in @(
    "release-data\legal",
    "release-data\legal-corpus",
    "release-data\postgres",
    "release-data\notebook_data",
    "release-data\forms",
    "release-data\manifest.sha256.json"
)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing release artifact: $path"
    }
}

docker compose --env-file $EnvFile -f docker-compose.release.yml config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Release compose validation failed."
}
if ($Build) {
    docker compose --env-file $EnvFile -f docker-compose.release.yml build
    if ($LASTEXITCODE -ne 0) {
        throw "Release image build failed."
    }
}
docker compose --env-file $EnvFile -f docker-compose.release.yml up -d
if ($LASTEXITCODE -ne 0) {
    throw "Release stack startup failed."
}
docker compose --env-file $EnvFile -f docker-compose.release.yml ps

$pilotHost = [string]$releaseEnv["PILOT_HOSTNAME"]
if (-not $pilotHost) {
    $pilotHost = "chatbotlegal.local"
}
Write-Host "Pilot TLS endpoint: https://$pilotHost"
Write-Host "API, retrieval and databases remain private inside the Docker network."
