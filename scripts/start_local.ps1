param(
    [switch]$NoReload,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"
$logDir = Join-Path $projectRoot "logs"
$statePath = Join-Path $logDir "local-services.json"
$surrealExe = Join-Path $projectRoot "tools\surrealdb\surreal-v2.6.5.exe"
$surrealData = (Join-Path $projectRoot "surreal_data\mydatabase.db").Replace("\", "/")

function Test-HttpService {
    param([string]$Url, [int]$TimeoutSeconds = 3)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSeconds
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Wait-HttpService {
    param(
        [string]$Name,
        [string]$Url,
        [int]$TimeoutSeconds = 120
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (Test-HttpService -Url $Url) {
            Write-Host "$Name ready: $Url"
            return
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not become ready. Check logs in $logDir"
}

function Get-ContainerPreflightIssues {
    param(
        [string]$ExpectedProjectRoot,
        [string]$ExpectedApplicationImage
    )

    $issues = [System.Collections.Generic.List[string]]::new()
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Warning "Docker is unavailable; port checks still protect local startup."
        return $issues
    }

    & docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Docker daemon is unavailable; port checks still protect local startup."
        return $issues
    }

    $containerIds = @(& docker ps -a --format "{{.ID}}" 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not inspect Docker containers; no container was changed."
        return $issues
    }

    $expectedRoot = [System.IO.Path]::GetFullPath($ExpectedProjectRoot).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    foreach ($containerId in $containerIds) {
        if ([string]::IsNullOrWhiteSpace($containerId)) {
            continue
        }

        $rawInspect = @(& docker inspect $containerId 2>$null)
        if ($LASTEXITCODE -ne 0 -or $rawInspect.Count -eq 0) {
            continue
        }

        try {
            $inspectItems = (($rawInspect -join [Environment]::NewLine) | ConvertFrom-Json)
            $container = @($inspectItems)[0]
        } catch {
            continue
        }

        $labels = $container.Config.Labels
        $workingDir = ""
        $service = ""
        $composeProject = ""
        if ($null -ne $labels) {
            $workingDirProperty = $labels.PSObject.Properties[
                'com.docker.compose.project.working_dir'
            ]
            $serviceProperty = $labels.PSObject.Properties[
                'com.docker.compose.service'
            ]
            $projectProperty = $labels.PSObject.Properties[
                'com.docker.compose.project'
            ]
            if ($null -ne $workingDirProperty) {
                $workingDir = [string]$workingDirProperty.Value
            }
            if ($null -ne $serviceProperty) {
                $service = [string]$serviceProperty.Value
            }
            if ($null -ne $projectProperty) {
                $composeProject = [string]$projectProperty.Value
            }
        }

        $sameProject = $false
        if (-not [string]::IsNullOrWhiteSpace($workingDir)) {
            try {
                $candidateRoot = [System.IO.Path]::GetFullPath($workingDir).TrimEnd(
                    [System.IO.Path]::DirectorySeparatorChar,
                    [System.IO.Path]::AltDirectorySeparatorChar
                )
                $sameProject = $candidateRoot -ieq $expectedRoot
            } catch {
                $sameProject = $false
            }
        }

        $image = [string]$container.Config.Image
        $state = ([string]$container.State.Status).ToLowerInvariant()
        $health = ""
        $healthProperty = $container.State.PSObject.Properties['Health']
        if ($null -ne $healthProperty -and $null -ne $healthProperty.Value) {
            $healthStatusProperty = $healthProperty.Value.PSObject.Properties['Status']
            if ($null -ne $healthStatusProperty) {
                $health = ([string]$healthStatusProperty.Value).ToLowerInvariant()
            }
        }
        $expectedProjectName = (
            (Split-Path -Leaf $ExpectedProjectRoot) -replace '[^A-Za-z0-9]', ''
        ).ToLowerInvariant()
        $actualProjectName = ($composeProject -replace '[^A-Za-z0-9]', '').ToLowerInvariant()
        $sameProjectName = $actualProjectName -eq $expectedProjectName
        $expectedService = ($sameProject -or $sameProjectName) -and $service -in @("open_notebook", "surrealdb")
        $applicationContainer = $image -ieq $ExpectedApplicationImage
        $managedApplication = $expectedService -or $applicationContainer
        if (-not $managedApplication) {
            continue
        }

        $isOrphan = $managedApplication -and $state -in @("running", "restarting") -and -not (
            $sameProject -and $service -eq "open_notebook"
        )
        $isRestartLoop = $expectedService -and (
            $state -eq "restarting" -or $health -eq "unhealthy"
        )
        if (-not $isOrphan -and -not $isRestartLoop) {
            continue
        }

        $kind = if ($isOrphan) { "orphan" } else { "restart-loop" }
        $name = ([string]$container.Name).TrimStart("/")
        $status = if ($health) { "$state/$health" } else { $state }
        [void]$issues.Add(
            "Docker $kind container detected: name=$name, id=$containerId, status=$status"
        )
    }

    return $issues
}

function Get-LocalPreflightIssues {
    $issues = [System.Collections.Generic.List[string]]::new()

    if (-not (Test-Path -LiteralPath $surrealExe)) {
        [void]$issues.Add("Missing SurrealDB 2.6.5: $surrealExe")
    }
    if (-not (Test-Path -LiteralPath $frontendRoot)) {
        [void]$issues.Add("Missing frontend directory: $frontendRoot")
    }
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        [void]$issues.Add("Python is not available in PATH")
    }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        [void]$issues.Add("npm.cmd is not available in PATH")
    }

    $blockingListeners = Get-NetTCPConnection `
        -State Listen `
        -LocalPort 5055,8000 `
        -ErrorAction SilentlyContinue
    if ($blockingListeners) {
        $ports = ($blockingListeners.LocalPort | Sort-Object -Unique) -join ", "
        [void]$issues.Add("Required local ports are already in use: $ports")
    }

    $retrievalListener = Get-NetTCPConnection `
        -State Listen `
        -LocalPort 8765 `
        -ErrorAction SilentlyContinue
    if ($retrievalListener -and -not (
        Test-HttpService -Url "http://127.0.0.1:8765/health"
    )) {
        [void]$issues.Add("Port 8765 is in use by a service that is not healthy retrieval")
    }

    $frontendListener = Get-NetTCPConnection `
        -State Listen `
        -LocalPort 3000 `
        -ErrorAction SilentlyContinue
    if ($frontendListener -and -not (
        Test-HttpService -Url "http://127.0.0.1:3000/login" -TimeoutSeconds 5
    )) {
        [void]$issues.Add("Port 3000 is in use by a service that is not the local frontend")
    }

    return $issues
}

$preflightIssues = @(Get-LocalPreflightIssues)
if ($preflightIssues.Count -gt 0) {
    foreach ($issue in $preflightIssues) {
        Write-Host "Preflight issue: $issue" -ForegroundColor Red
    }
    throw "Local preflight failed. No container or process was stopped or removed."
}

if ($PreflightOnly) {
    Write-Host "Local preflight passed. No process was changed."
    return
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Write-Host "Starting the local-only test runtime (Docker and ngrok are not used)."

$env:SURREAL_URL = "ws://127.0.0.1:8000/rpc"
$env:SURREAL_USER = "root"
$env:SURREAL_PASSWORD = "root"
$env:SURREAL_NAMESPACE = "open_notebook"
$env:SURREAL_DATABASE = "open_notebook"
$env:OPEN_NOTEBOOK_ENCRYPTION_KEY = "change-me-to-a-secret-string"
$env:OPEN_NOTEBOOK_DATA_DIR = Join-Path $projectRoot "notebook_data"
$env:LEGAL_SEARCH_URL = "http://127.0.0.1:8765"
# The local test runtime must use the existing reviewed corpus configured by
# LEGAL_OLD_ENV_PATH. Do not inherit a staging URL from the desktop session:
# that database may intentionally have no imported legal tables.
Remove-Item Env:LEGAL_DATABASE_URL -ErrorAction SilentlyContinue
$env:LEGAL_LLM_TIMEOUT_SECONDS = "90"
$env:LEGAL_EMBED_DEVICE = if ($env:LEGAL_EMBED_DEVICE) {
    $env:LEGAL_EMBED_DEVICE
} else {
    "auto"
}

$surrealProcess = Start-Process `
    -FilePath $surrealExe `
    -ArgumentList @("start", "--log", "info", "--user", "root", "--pass", "root", "rocksdb:$surrealData") `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir "surreal-local.log") `
    -RedirectStandardError (Join-Path $logDir "surreal-local.err.log") `
    -PassThru

Wait-HttpService -Name "SurrealDB" -Url "http://127.0.0.1:8000/health" -TimeoutSeconds 120

# The retrieval launcher returns immediately when a healthy local service is
# already listening.  Do not invoke it in that case: PowerShell's script-level
# `return` can otherwise unwind this parent launcher before API/frontend start.
if (-not (Test-HttpService -Url "http://127.0.0.1:8765/health")) {
    # Run the retrieval launcher in its own PowerShell process. Its successful
    # early-exit path uses script-level `return`; isolating that control flow
    # prevents it from unwinding this all-services launcher.
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start_legal_search.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Legal retrieval launcher failed. No API or frontend was started."
    }
}
Wait-HttpService -Name "Legal retrieval" -Url "http://127.0.0.1:8765/health" -TimeoutSeconds 240

# Test startup deliberately runs one API worker without the repository-wide file
# watcher.  Watching the large external/ tree can delay or duplicate startup on
# Windows, leaving the frontend available while the API is not actually ready.
$backendArgs = @("-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "5055")
$backendProcess = Start-Process `
    -FilePath "python" `
    -ArgumentList $backendArgs `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir "backend-local.log") `
    -RedirectStandardError (Join-Path $logDir "backend-local.err.log") `
    -PassThru

Wait-HttpService `
    -Name "Backend readiness" `
    -Url "http://127.0.0.1:5055/ready" `
    -TimeoutSeconds 240

$frontendProcess = $null
if (-not (Test-HttpService -Url "http://127.0.0.1:3000/login" -TimeoutSeconds 5)) {
    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $frontendProcess = Start-Process `
        -FilePath $npm `
        -ArgumentList @("run", "dev", "--", "-p", "3000") `
        -WorkingDirectory $frontendRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "frontend-local.log") `
        -RedirectStandardError (Join-Path $logDir "frontend-local.err.log") `
        -PassThru
}

Wait-HttpService -Name "Frontend" -Url "http://127.0.0.1:3000/login" -TimeoutSeconds 180

$retrievalPid = (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty OwningProcess)
$frontendPid = (Get-NetTCPConnection -State Listen -LocalPort 3000 -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty OwningProcess)

@{
    started_at = (Get-Date).ToString("o")
    surreal_pid = $surrealProcess.Id
    backend_pid = $backendProcess.Id
    retrieval_pid = $retrievalPid
    frontend_pid = $frontendPid
    mode = "local"
} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

Write-Host "Local system ready: http://127.0.0.1:3000"
Write-Host "Backend: http://127.0.0.1:5055"
Write-Host "Legal retrieval: http://127.0.0.1:8765"
Write-Host "Database: http://127.0.0.1:8000"
