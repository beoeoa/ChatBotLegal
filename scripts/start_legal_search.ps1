[CmdletBinding()]
param(
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$requirementsPath = Join-Path $projectRoot "requirements\legal-retrieval-cu126.txt"
$reasonCode = "launcher_failed"

function Write-StructuredStatus {
    param([hashtable]$Payload)

    Write-Output ($Payload | ConvertTo-Json -Compress -Depth 6)
}

function ConvertTo-PythonRunner {
    param([string]$Source)

    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Source))
    return "import base64;exec(base64.b64decode('$encoded'))"
}

function Resolve-PythonExecutable {
    param([string]$Candidate)

    if ([string]::IsNullOrWhiteSpace($Candidate)) {
        return $null
    }
    if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
        return [System.IO.Path]::GetFullPath($Candidate)
    }
    $command = Get-Command $Candidate -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return $command.Source
    }
    return $null
}

function Invoke-PythonPreflight {
    param(
        [string]$Executable,
        [string]$RequestedDevice
    )

    if (-not $Executable) {
        return $null
    }
    $probeCode = @'
import json
import platform
import sys

try:
    import chromadb
    import fastapi
    import numpy
    import pydantic
    import sqlalchemy
    import torch
    import transformers
    import uvicorn
    from dotenv import dotenv_values

    requested = __import__("os").environ["LEGAL_RETRIEVAL_PROBE_DEVICE"]
    cuda_available = bool(torch.cuda.is_available())
    eligible = (
        sys.version_info[:2] == (3, 12)
        and platform.architecture()[0] == "64bit"
        and (requested != "cuda" or cuda_available)
    )
    print(json.dumps({
        "eligible": eligible,
        "cuda_available": cuda_available,
    }))
    raise SystemExit(0 if eligible else 2)
except Exception:
    print(json.dumps({
        "eligible": False,
        "cuda_available": False,
    }))
    raise SystemExit(2)
'@
    $probeRunner = ConvertTo-PythonRunner $probeCode
    $previousProbeDevice = $env:LEGAL_RETRIEVAL_PROBE_DEVICE
    $env:LEGAL_RETRIEVAL_PROBE_DEVICE = $RequestedDevice
    try {
        $output = & $Executable -c $probeRunner 2>$null
        $probeExitCode = $LASTEXITCODE
    } catch {
        return $null
    } finally {
        $env:LEGAL_RETRIEVAL_PROBE_DEVICE = $previousProbeDevice
    }
    if ($probeExitCode -ne 0) {
        return $null
    }
    try {
        return $output |
            Where-Object { $_ -match '^\s*\{' } |
            Select-Object -Last 1 |
            ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-VerifiedCudaMarker {
    param(
        [string]$MarkerPath,
        [string]$ManifestPath
    )

    if (
        -not (Test-Path -LiteralPath $MarkerPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)
    ) {
        return $false
    }
    try {
        $marker = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
        $manifestHash = (Get-FileHash -LiteralPath $ManifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
        return (
            $marker.status -eq "eligible" -and
            $marker.python_arch -eq "64bit" -and
            $marker.torch_version -eq "2.8.0+cu126" -and
            $marker.embedding_device -eq "cuda" -and
            $marker.embedding_dtype -eq "float16" -and
            [bool]$marker.cuda_prewarm_passed -and
            $marker.cpu_rollback_device -eq "cpu" -and
            $marker.cpu_rollback_dtype -eq "float32" -and
            [bool]$marker.cpu_rollback_prewarm_passed -and
            $marker.dependency_hash -eq $manifestHash
        )
    } catch {
        return $false
    }
}

try {
    $requestedDevice = if ($env:LEGAL_EMBED_DEVICE) {
        $env:LEGAL_EMBED_DEVICE.Trim().ToLowerInvariant()
    } else {
        "auto"
    }
    if ($requestedDevice -notin @("auto", "cuda", "cpu")) {
        $reasonCode = "invalid_device_request"
        throw "LEGAL_EMBED_DEVICE must be auto, cuda, or cpu"
    }

    $selectedPython = $null
    $selectedSource = $null
    $selectedProbe = $null

    # Runtime selection order: explicit environment, verified CUDA venv,
    # existing project venv, then the existing global CPU runtime.
    if ($env:LEGAL_SEARCH_PYTHON) {
        $explicitPython = Resolve-PythonExecutable $env:LEGAL_SEARCH_PYTHON
        $explicitProbe = Invoke-PythonPreflight $explicitPython $requestedDevice
        if (-not $explicitPython -or -not $explicitProbe -or -not $explicitProbe.eligible) {
            $reasonCode = "explicit_python_invalid"
            throw "explicit Python runtime failed preflight"
        }
        $selectedPython = $explicitPython
        $selectedSource = "environment"
        $selectedProbe = $explicitProbe
    } else {
        $cudaVenvPath = Join-Path $projectRoot ".venv-retrieval-cu126"
        $cudaVenvPython = Join-Path $cudaVenvPath "Scripts\python.exe"
        $cudaMarker = Join-Path $cudaVenvPath ".legal-retrieval-cu126-verified.json"
        if (
            (Test-VerifiedCudaMarker $cudaMarker $requirementsPath) -and
            (Test-Path -LiteralPath $cudaVenvPython -PathType Leaf)
        ) {
            $cudaProbe = Invoke-PythonPreflight $cudaVenvPython $requestedDevice
            if ($cudaProbe -and $cudaProbe.eligible) {
                $selectedPython = $cudaVenvPython
                $selectedSource = "verified_cuda_venv"
                $selectedProbe = $cudaProbe
            }
        }

        if (-not $selectedPython) {
            $projectVenvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
            if (Test-Path -LiteralPath $projectVenvPython -PathType Leaf) {
                $projectProbe = Invoke-PythonPreflight $projectVenvPython $requestedDevice
                if ($projectProbe -and $projectProbe.eligible) {
                    $selectedPython = $projectVenvPython
                    $selectedSource = "project_venv"
                    $selectedProbe = $projectProbe
                }
            }
        }

        if (-not $selectedPython) {
            $globalPython = Resolve-PythonExecutable "python"
            $globalProbe = Invoke-PythonPreflight $globalPython $requestedDevice
            if ($globalPython -and $globalProbe -and $globalProbe.eligible) {
                $selectedPython = $globalPython
                $selectedSource = "global"
                $selectedProbe = $globalProbe
            }
        }
    }

    if (-not $selectedPython) {
        $reasonCode = if ($requestedDevice -eq "cuda") {
            "LEGAL_EMBED_DEVICE=cuda_no_eligible_runtime"
        } else {
            "no_eligible_runtime"
        }
        throw "no eligible legal retrieval runtime"
    }

    if ($PreflightOnly) {
        Write-StructuredStatus @{
            status = "eligible"
            selected_source = $selectedSource
            requested_device = $requestedDevice
            cuda_available = [bool]$selectedProbe.cuda_available
        }
        # This script is also invoked by start_local.ps1. `exit` would end
        # that parent launcher before it can start the API and frontend.
        return
    }

    $dataRoot = if ($env:LEGAL_DATA_ROOT) {
        $env:LEGAL_DATA_ROOT
    } else {
        "J:\legal-chatbot-data"
    }
    $chromaPath = if ($env:LEGAL_CHROMA_PATH) {
        $env:LEGAL_CHROMA_PATH
    } else {
        Join-Path $dataRoot "chroma_store"
    }
    $modelPath = if ($env:VNLEGAL_LAL_MODEL_PATH) {
        $env:VNLEGAL_LAL_MODEL_PATH
    } else {
        $modelRoot = Join-Path $dataRoot "sentence_transformers\models--darklethelong--vnlegal-lal\snapshots"
        $snapshot = if (Test-Path -LiteralPath $modelRoot -PathType Container) {
            Get-ChildItem -LiteralPath $modelRoot -Directory |
                Sort-Object -Property Name |
                Select-Object -First 1
        } else {
            $null
        }
        if ($snapshot) { $snapshot.FullName } else { $null }
    }
    $oldEnvPath = if ($env:LEGAL_OLD_ENV_PATH) {
        $env:LEGAL_OLD_ENV_PATH
    } else {
        "J:\ChatBot\legal-chatbot\backend\.env"
    }
    $logDir = Join-Path $projectRoot "logs"

    if (-not (Test-Path -LiteralPath $chromaPath -PathType Container)) {
        $reasonCode = "chroma_missing"
        throw "Chroma store is missing"
    }
    if (-not $modelPath -or -not (Test-Path -LiteralPath $modelPath -PathType Container)) {
        $reasonCode = "model_missing"
        throw "VNLegal-LAL model is missing"
    }

    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 3
        if ($health.status -eq "healthy") {
            if (
                ($requestedDevice -eq "cuda" -and $health.embedding_device -ne "cuda") -or
                ($requestedDevice -eq "cpu" -and $health.embedding_device -ne "cpu")
            ) {
                $reasonCode = "existing_service_device_mismatch"
                throw "stop retrieval before changing the requested device"
            }
            Write-Host "Legal search da chay: $($health.indexed_records) vectors"
            return
        }
    } catch {
        if ($reasonCode -eq "existing_service_device_mismatch") {
            throw
        }
        # Start the service below when the health endpoint is absent.
    }

    New-Item -ItemType Directory -Force -Path $logDir | Out-Null

    $env:LEGAL_DATA_ROOT = $dataRoot
    $env:LEGAL_CHROMA_PATH = $chromaPath
    $env:VNLEGAL_LAL_MODEL_PATH = $modelPath
    $env:LEGAL_OLD_ENV_PATH = $oldEnvPath
    $env:LEGAL_EMBED_DEVICE = $requestedDevice

    $process = Start-Process `
        -FilePath $selectedPython `
        -ArgumentList "scripts/legal_search_server.py" `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "legal_search.log") `
        -RedirectStandardError (Join-Path $logDir "legal_search_err.log") `
        -PassThru

    $deadline = (Get-Date).AddMinutes(3)
    do {
        Start-Sleep -Seconds 5
        if ($process.HasExited) {
            $reasonCode = "retrieval_process_exited"
            throw "legal retrieval process exited before readiness"
        }
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 3
            if ($health.status -eq "healthy" -and $health.ready) {
                if (
                    ($requestedDevice -eq "cuda" -and $health.embedding_device -ne "cuda") -or
                    ($requestedDevice -eq "cpu" -and $health.embedding_device -ne "cpu")
                ) {
                    $reasonCode = "started_service_device_mismatch"
                    throw "retrieval started on an unexpected device"
                }
                Write-Host "Legal search san sang: $($health.indexed_records) vectors"
                return
            }
        } catch {
            if ($reasonCode -eq "started_service_device_mismatch") {
                throw
            }
            # Model prewarm can take more than one minute on CPU.
        }
    } while ((Get-Date) -lt $deadline)

    $reasonCode = "retrieval_start_timeout"
    throw "legal retrieval startup timed out"
} catch {
    Write-StructuredStatus @{
        status = "rejected"
        reason_code = $reasonCode
    }
    throw "Legal retrieval startup rejected: $reasonCode"
}
