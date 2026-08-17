[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RetrievalDataRoot,
    [string]$CatalogRoot = "notebook_data\forms",
    [string]$LegalAsOf = (Get-Date -Format "yyyy-MM-dd")
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseRoot = Join-Path $projectRoot "release-data"
$projectPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw "Repository Python runtime is missing: $projectPython"
}

function Assert-ReleaseTarget {
    param([Parameter(Mandatory = $true)][string]$Path)
    $resolvedRoot = [System.IO.Path]::GetFullPath($releaseRoot).TrimEnd('\')
    $resolvedTarget = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (
        $resolvedTarget -eq $resolvedRoot -or
        -not $resolvedTarget.StartsWith($resolvedRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing destructive operation outside a scoped release-data child path: $resolvedTarget"
    }
}

if (-not (Test-Path (Join-Path $RetrievalDataRoot "chroma_store"))) {
    throw "RetrievalDataRoot must contain chroma_store."
}
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$releaseChroma = Join-Path $releaseRoot "legal\chroma_store"
Assert-ReleaseTarget -Path $releaseChroma
Remove-Item -LiteralPath $releaseChroma -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -LiteralPath (Join-Path $RetrievalDataRoot "chroma_store") -Destination (Join-Path $releaseRoot "legal\chroma_store") -Recurse

if (-not [System.IO.Path]::IsPathRooted($CatalogRoot)) {
    $CatalogRoot = Join-Path $projectRoot $CatalogRoot
}
if (-not (Test-Path (Join-Path $CatalogRoot "canonical_forms_catalog_v1.json"))) {
    throw "CatalogRoot does not contain canonical_forms_catalog_v1.json."
}
& $projectPython -X utf8 (Join-Path $PSScriptRoot "package_runtime_forms.py") `
    --catalog-dir $CatalogRoot `
    --output-root $releaseRoot `
    --legal-as-of $LegalAsOf
if ($LASTEXITCODE -ne 0) {
    throw "Fail-closed runtime form packaging failed."
}

New-Item -ItemType Directory -Force -Path (Join-Path $releaseRoot "notebook_data") | Out-Null
$manifestPath = Join-Path $releaseRoot "manifest.sha256.json"
$manifest = Get-ChildItem $releaseRoot -Recurse -File | Where-Object {
    $_.Name -notmatch '^\.gitkeep$' -and $_.FullName -ne $manifestPath
} |
    ForEach-Object { $hash = Get-FileHash $_.FullName -Algorithm SHA256; [pscustomobject]@{ path = $_.FullName.Substring($projectRoot.Length + 1); sha256 = $hash.Hash.ToLowerInvariant(); bytes = $_.Length } }
$manifest | ConvertTo-Json -Depth 4 | Set-Content $manifestPath -Encoding utf8
Write-Host "Prepared sanitized release data under $releaseRoot"
