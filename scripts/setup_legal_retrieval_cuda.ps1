[CmdletBinding()]
param(
    [string]$PythonExecutable = "",
    [string]$VenvPath = "",
    [switch]$VerifyOnly,
    [string]$ModelPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$requirementsPath = Join-Path $projectRoot "requirements\legal-retrieval-cu126.txt"
$defaultVenvPath = Join-Path $projectRoot ".venv-retrieval-cu126"
$reasonCode = "setup_failed"

function Write-StructuredStatus {
    param([hashtable]$Payload)

    Write-Output ($Payload | ConvertTo-Json -Compress -Depth 8)
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

function Read-PythonRuntime {
    param([string]$Executable)

    $probe = @'
import json
import platform
import sys
print(json.dumps({
    "version": ".".join(str(part) for part in sys.version_info[:3]),
    "major_minor": list(sys.version_info[:2]),
    "architecture": platform.architecture()[0],
}))
'@
    $runner = ConvertTo-PythonRunner $probe
    $output = & $Executable -c $runner 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }
    try {
        return ($output | Select-Object -Last 1 | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Resolve-BasePython {
    if (-not [string]::IsNullOrWhiteSpace($PythonExecutable)) {
        return Resolve-PythonExecutable $PythonExecutable
    }

    $pyLauncher = Resolve-PythonExecutable "py.exe"
    if ($pyLauncher) {
        $resolved = & $pyLauncher -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $resolved) {
            return Resolve-PythonExecutable ($resolved | Select-Object -Last 1)
        }
    }
    return Resolve-PythonExecutable "python.exe"
}

function Resolve-LocalModelPath {
    if (-not [string]::IsNullOrWhiteSpace($ModelPath)) {
        return $ModelPath
    }
    if (-not [string]::IsNullOrWhiteSpace($env:VNLEGAL_LAL_MODEL_PATH)) {
        return $env:VNLEGAL_LAL_MODEL_PATH
    }

    $snapshotRoot = "J:\legal-chatbot-data\sentence_transformers\models--darklethelong--vnlegal-lal\snapshots"
    if (-not (Test-Path -LiteralPath $snapshotRoot -PathType Container)) {
        return $null
    }
    $snapshot = Get-ChildItem -LiteralPath $snapshotRoot -Directory |
        Sort-Object -Property Name |
        Select-Object -First 1
    if ($snapshot) {
        return $snapshot.FullName
    }
    return $null
}

try {
    if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
        $reasonCode = "requirements_missing"
        throw "requirements missing"
    }

    $resolvedVenvPath = if ([string]::IsNullOrWhiteSpace($VenvPath)) {
        $defaultVenvPath
    } elseif ([System.IO.Path]::IsPathRooted($VenvPath)) {
        [System.IO.Path]::GetFullPath($VenvPath)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $VenvPath))
    }
    $venvPython = Join-Path $resolvedVenvPath "Scripts\python.exe"
    $verifiedMarker = Join-Path $resolvedVenvPath ".legal-retrieval-cu126-verified.json"

    if ($VerifyOnly -and -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $reasonCode = "venv_missing"
        throw "venv missing"
    }

    if (-not $VerifyOnly) {
        $reasonCode = "base_python_missing"
        $basePython = Resolve-BasePython
        if (-not $basePython) {
            throw "base Python missing"
        }
        $baseRuntime = Read-PythonRuntime $basePython
        if (
            -not $baseRuntime -or
            $baseRuntime.major_minor[0] -ne 3 -or
            $baseRuntime.major_minor[1] -ne 12
        ) {
            $reasonCode = "python_version_mismatch"
            throw "Python 3.12 is required"
        }
        if ($baseRuntime.architecture -ne "64bit") {
            $reasonCode = "python_architecture_mismatch"
            throw "Python 3.12 64bit is required"
        }

        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            & $basePython -m venv $resolvedVenvPath
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
                $reasonCode = "venv_create_failed"
                throw "venv creation failed"
            }
        }
    }

    $venvRuntime = Read-PythonRuntime $venvPython
    if (
        -not $venvRuntime -or
        $venvRuntime.major_minor[0] -ne 3 -or
        $venvRuntime.major_minor[1] -ne 12
    ) {
        $reasonCode = "venv_python_version_mismatch"
        throw "venv must use Python 3.12"
    }
    if ($venvRuntime.architecture -ne "64bit") {
        $reasonCode = "venv_python_architecture_mismatch"
        throw "venv must use Python 3.12 64bit"
    }

    # A stale marker must never make a partially upgraded or failed runtime
    # eligible for automatic launcher selection.
    if (Test-Path -LiteralPath $verifiedMarker -PathType Leaf) {
        Remove-Item -LiteralPath $verifiedMarker -Force
    }

    if (-not $VerifyOnly) {
        & $venvPython -m pip install --disable-pip-version-check -r $requirementsPath
        if ($LASTEXITCODE -ne 0) {
            $reasonCode = "dependency_install_failed"
            throw "dependency installation failed"
        }
    }

    $resolvedModelPath = Resolve-LocalModelPath
    if (-not $resolvedModelPath -or -not (Test-Path -LiteralPath $resolvedModelPath -PathType Container)) {
        $reasonCode = "model_missing"
        throw "local model missing"
    }
    $dataRoot = if ($env:LEGAL_DATA_ROOT) {
        $env:LEGAL_DATA_ROOT
    } else {
        "J:\legal-chatbot-data"
    }
    $resolvedChromaPath = if ($env:LEGAL_CHROMA_PATH) {
        $env:LEGAL_CHROMA_PATH
    } else {
        Join-Path $dataRoot "chroma_store"
    }
    if (-not (Test-Path -LiteralPath $resolvedChromaPath -PathType Container)) {
        $reasonCode = "chroma_missing"
        throw "Chroma store missing"
    }

    $previousSetupModelPath = $env:LEGAL_RETRIEVAL_SETUP_MODEL_PATH
    $env:LEGAL_RETRIEVAL_SETUP_MODEL_PATH = [System.IO.Path]::GetFullPath($resolvedModelPath)
    $env:LEGAL_RETRIEVAL_SETUP_CHROMA_PATH = [System.IO.Path]::GetFullPath($resolvedChromaPath)
    $env:LEGAL_RETRIEVAL_SETUP_CHROMA_COLLECTION = if ($env:LEGAL_CHROMA_COLLECTION) {
        $env:LEGAL_CHROMA_COLLECTION
    } else {
        "legal_chunks_vnlegal_lal_haiphong"
    }
    $env:LEGAL_RETRIEVAL_SETUP_SOURCE_COLLECTION = if ($env:LEGAL_CHROMA_SOURCE_COLLECTION) {
        $env:LEGAL_CHROMA_SOURCE_COLLECTION
    } else {
        "legal_chunks_vnlegal_lal"
    }
    $verifyCode = @'
import gc
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

result = {"status": "rejected", "reason_code": "verification_failed"}

try:
    import torch
    import torch.nn.functional as F
    from transformers import AutoModel, AutoTokenizer

    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("python_version_mismatch")
    if platform.architecture()[0] != "64bit":
        raise RuntimeError("python_architecture_mismatch")
    if torch.__version__ != "2.8.0+cu126":
        raise RuntimeError("torch_version_mismatch")
    if not torch.cuda.is_available():
        raise RuntimeError("cuda_unavailable")

    model_path = os.environ["LEGAL_RETRIEVAL_SETUP_MODEL_PATH"]
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    tokenized = tokenizer(
        ["retrieval runtime prewarm"],
        padding=True,
        truncation=True,
        max_length=64,
        return_tensors="pt",
    )

    cuda_model = AutoModel.from_pretrained(
        model_path,
        local_files_only=True,
        dtype=torch.float16,
    )
    cuda_model.to(torch.device("cuda"))
    cuda_model.eval()
    cuda_inputs = {name: value.to("cuda") for name, value in tokenized.items()}
    with torch.inference_mode():
        cuda_output = cuda_model(**cuda_inputs).last_hidden_state
        cuda_embedding = F.normalize(cuda_output[:, -1, :], p=2, dim=1)
    if cuda_embedding.device.type != "cuda" or cuda_embedding.dtype != torch.float16:
        raise RuntimeError("cuda_dtype_mismatch")

    del cuda_embedding, cuda_output, cuda_inputs, cuda_model
    gc.collect()
    torch.cuda.empty_cache()

    cpu_model = AutoModel.from_pretrained(
        model_path,
        local_files_only=True,
        dtype=torch.float32,
    )
    cpu_model.to(torch.device("cpu"))
    cpu_model.eval()
    with torch.inference_mode():
        cpu_output = cpu_model(**tokenized).last_hidden_state
        cpu_embedding = F.normalize(cpu_output[:, -1, :], p=2, dim=1)
    if cpu_embedding.device.type != "cpu" or cpu_embedding.dtype != torch.float32:
        raise RuntimeError("cpu_dtype_mismatch")

    fingerprint_parts = [str(os.path.abspath(model_path))]
    for filename in ("config.json", "model.safetensors", "tokenizer.json"):
        item = os.path.join(model_path, filename)
        try:
            stat = os.stat(item)
            fingerprint_parts.append(
                f"{filename}:{stat.st_size}:{stat.st_mtime_ns}"
            )
        except OSError:
            fingerprint_parts.append(f"{filename}:missing")

    import chromadb
    chroma_path = Path(os.environ["LEGAL_RETRIEVAL_SETUP_CHROMA_PATH"])
    collection_names = (
        os.environ["LEGAL_RETRIEVAL_SETUP_CHROMA_COLLECTION"],
        os.environ["LEGAL_RETRIEVAL_SETUP_SOURCE_COLLECTION"],
    )
    client = chromadb.PersistentClient(path=str(chroma_path))
    corpus_parts = []
    for collection_name in collection_names:
        collection = client.get_collection(collection_name)
        corpus_parts.append(f"{collection_name}:{collection.count()}")
    sqlite_path = chroma_path / "chroma.sqlite3"
    sqlite_stat = sqlite_path.stat()
    corpus_parts.append(f"sqlite:{sqlite_stat.st_size}:{sqlite_stat.st_mtime_ns}")
    corpus_fingerprint = hashlib.sha256(
        "\0".join(corpus_parts).encode("utf-8")
    ).hexdigest()

    result = {
        "status": "eligible",
        "reason_code": None,
        "python_version": platform.python_version(),
        "python_arch": "64bit",
        "torch_version": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "gpu_model": torch.cuda.get_device_name(0),
        "embedding_device": "cuda",
        "embedding_dtype": "float16",
        "cuda_prewarm_passed": True,
        "cpu_rollback_device": "cpu",
        "cpu_rollback_dtype": "float32",
        "cpu_rollback_prewarm_passed": True,
        "corpus_fingerprint": corpus_fingerprint,
        "corpus_collections": list(collection_names),
        "model_fingerprint": hashlib.sha256(
            "\0".join(fingerprint_parts).encode("utf-8")
        ).hexdigest(),
    }
except torch.cuda.OutOfMemoryError:
    result["reason_code"] = "cuda_oom_during_prewarm"
except RuntimeError as exc:
    code = str(exc)
    allowed_codes = {
        "python_version_mismatch",
        "python_architecture_mismatch",
        "torch_version_mismatch",
        "cuda_unavailable",
        "cuda_dtype_mismatch",
        "cpu_dtype_mismatch",
    }
    result["reason_code"] = code if code in allowed_codes else "runtime_verification_failed"
except Exception:
    result["reason_code"] = "dependency_or_model_verification_failed"

print(json.dumps(result, ensure_ascii=True, sort_keys=True))
raise SystemExit(0 if result["status"] == "eligible" else 2)
'@

    # The script itself runs in an isolated PowerShell process, so the temporary
    # model-path value expires with this process. Avoid a try/finally cleanup
    # around a native command: some Windows PowerShell hosts turn that cleanup
    # boundary into a generic terminating error and discard the verifier JSON.
    $verifyRunner = ConvertTo-PythonRunner $verifyCode
    # Windows PowerShell 5.1 promotes harmless native stderr (the Transformers
    # progress bar) to a terminating NativeCommandError when the global
    # preference is Stop, even when stderr is redirected. Keep the strict
    # policy everywhere else and judge this child process from its explicit
    # exit code plus structured JSON.
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $verifyOutput = & $venvPython -c $verifyRunner 2>$null
    $verifyExitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference

    $verification = $null
    try {
        $verification = $verifyOutput |
            Where-Object { $_ -match '^\s*\{' } |
            Select-Object -Last 1 |
            ConvertFrom-Json
    } catch {
        $verification = $null
    }
    if (
        $verifyExitCode -ne 0 -or
        -not $verification -or
        $verification.status -ne "eligible"
    ) {
        $reasonCode = if ($verification -and $verification.reason_code) {
            [string]$verification.reason_code
        } else {
            "verification_failed"
        }
        throw "runtime verification failed"
    }

    $manifestHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $markerPayload = [ordered]@{
        schema_version = "1"
        status = "eligible"
        python_version = [string]$verification.python_version
        python_arch = [string]$verification.python_arch
        torch_version = [string]$verification.torch_version
        cuda_runtime = [string]$verification.cuda_runtime
        gpu_model = [string]$verification.gpu_model
        embedding_device = [string]$verification.embedding_device
        embedding_dtype = [string]$verification.embedding_dtype
        cuda_prewarm_passed = [bool]$verification.cuda_prewarm_passed
        cpu_rollback_device = [string]$verification.cpu_rollback_device
        cpu_rollback_dtype = [string]$verification.cpu_rollback_dtype
        cpu_rollback_prewarm_passed = [bool]$verification.cpu_rollback_prewarm_passed
        corpus_fingerprint = [string]$verification.corpus_fingerprint
        corpus_collections = @($verification.corpus_collections)
        model_fingerprint = [string]$verification.model_fingerprint
        dependency_hash = $manifestHash
        verified_at_utc = [DateTime]::UtcNow.ToString("o")
    }
    $markerPayload | ConvertTo-Json -Depth 8 |
        Set-Content -LiteralPath $verifiedMarker -Encoding UTF8
    Write-StructuredStatus $markerPayload
    exit 0
} catch {
    Write-StructuredStatus @{
        status = "rejected"
        reason_code = $reasonCode
    }
    exit 1
}
