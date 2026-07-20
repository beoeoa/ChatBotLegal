[CmdletBinding()]
param(
    [string]$ApiUrl = "http://127.0.0.1:5055",
    [switch]$Full,
    [switch]$BuildFrontend
)

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
$reportDir = Join-Path $repo "reports"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$reportPath = Join-Path $reportDir "pilot-test-$stamp.json"
$results = [System.Collections.Generic.List[object]]::new()

function Invoke-Check {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    $started = Get-Date
    Push-Location $repo
    try {
        & $Action
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
        $status = if ($exitCode -eq 0) { "PASS" } else { "FAIL" }
    } catch {
        $exitCode = 1
        $status = "FAIL"
        Write-Host $_ -ForegroundColor Red
    } finally {
        Pop-Location
    }
    $duration = ((Get-Date) - $started).TotalSeconds
    $results.Add([pscustomobject]@{
        name = $Name
        status = $status
        exit_code = $exitCode
        duration_seconds = [math]::Round($duration, 2)
    })
    $color = if ($status -eq "PASS") { "Green" } else { "Red" }
    Write-Host ("[{0}] {1} ({2}s)" -f $status, $Name, ([math]::Round($duration, 2))) -ForegroundColor $color
}

Invoke-Check "Backend health" {
    $health = Invoke-RestMethod -Uri "$ApiUrl/health" -TimeoutSec 15
    if ($health.status -ne "healthy") { throw "Backend health is not healthy" }
}

$coreSuites = @(
    "tests/test_public_auth_flow.py",
    "tests/test_domain_authorization.py",
    "tests/test_conversation_service.py",
    "tests/test_ask_history.py",
    "tests/test_legal_grounding.py",
    "tests/test_grounding_verification.py",
    "tests/test_forms_metadata.py",
    "tests/test_form_recommendation_and_citations.py",
    "tests/test_legal_forms_pipeline.py",
    "tests/test_legal_document_viewer.py",
    "tests/test_review_workflow.py",
    "tests/test_ai_assessment.py"
)

Invoke-Check "Backend pilot logic" {
    python -m pytest @coreSuites -q
}

if ($Full) {
    Invoke-Check "Full backend test suite" {
        python -m pytest tests -q
    }
}

Invoke-Check "Frontend unit tests" {
    Push-Location (Join-Path $repo "frontend")
    try { npm test -- --run } finally { Pop-Location }
}

Invoke-Check "Frontend type check" {
    Push-Location (Join-Path $repo "frontend")
    try { npm exec -- tsc --noEmit --pretty false } finally { Pop-Location }
}

if ($BuildFrontend) {
    Invoke-Check "Frontend production build" {
        Push-Location (Join-Path $repo "frontend")
        try { npm run build } finally { Pop-Location }
    }
}

$passed = @($results | Where-Object status -eq "PASS").Count
$failed = @($results | Where-Object status -eq "FAIL").Count
$report = [pscustomobject]@{
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    api_url = $ApiUrl
    mode = if ($Full) { "full" } else { "pilot" }
    passed = $passed
    failed = $failed
    status = if ($failed -eq 0) { "PASS" } else { "FAIL" }
    checks = @($results)
}
$report | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $reportPath
Write-Host "Report: $reportPath"
if ($failed -gt 0) { exit 1 }
exit 0
