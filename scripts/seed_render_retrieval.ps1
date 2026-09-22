[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^srv-[a-z0-9]+$')]
    [string]$RetrievalServiceId,

    [ValidateSet('singapore', 'oregon', 'ohio', 'virginia', 'frankfurt')]
    [string]$Region = 'singapore',

    [string]$SeedRoot = 'scratch/render-core-seed'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedSeedRoot = (Resolve-Path -LiteralPath (Join-Path $projectRoot $SeedRoot)).Path
$legalRoot = Join-Path $resolvedSeedRoot 'legal'
$requiredMembers = @(
    'core-288-release.json',
    'chroma_core_288_release_20260919',
    'serving_manifests',
    'vnlegal-lal-model'
)
foreach ($member in $requiredMembers) {
    if (-not (Test-Path -LiteralPath (Join-Path $legalRoot $member))) {
        throw "Reviewed retrieval seed member is missing: $member"
    }
}

$pythonExecutable = (Get-Command python -ErrorAction Stop).Source
& $pythonExecutable (Join-Path $projectRoot 'scripts/verify_core_288_release.py') `
    --release-root $resolvedSeedRoot --skip-postgres-dump
if ($LASTEXITCODE -ne 0) {
    throw 'Local reviewed retrieval seed verification failed.'
}

$sshExecutable = (Get-Command ssh -ErrorAction Stop).Source
$scpExecutable = (Get-Command scp -ErrorAction Stop).Source
$sshTarget = "$RetrievalServiceId@ssh.$Region.render.com"
$sshOptions = @('-o', 'BatchMode=yes')

# This script is intentionally limited to a fresh disk. It never overwrites an
# activated corpus or the writable Chroma copy created by the service.
& $sshExecutable @sshOptions $sshTarget `
    'mkdir -p /data/legal && test ! -e /data/legal/.render-seed-complete && test ! -e /data/legal/chroma_store'
if ($LASTEXITCODE -ne 0) {
    throw 'The Render retrieval disk is already activated or has a runtime Chroma copy.'
}

$payload = $requiredMembers | ForEach-Object { Join-Path $legalRoot $_ }
& $scpExecutable -s @sshOptions -r @payload "$($sshTarget):/data/legal/"
if ($LASTEXITCODE -ne 0) {
    throw 'Retrieval seed transfer failed. The activation marker was not created.'
}

& $sshExecutable @sshOptions $sshTarget `
    'python /app/scripts/verify_core_288_release.py --release-root /data --skip-postgres-dump && touch /data/legal/.render-seed-complete'
if ($LASTEXITCODE -ne 0) {
    throw 'Remote retrieval seed verification failed. The service remains unactivated.'
}

Write-Host 'Reviewed retrieval seed verified and activated on Render.'
