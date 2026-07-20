from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = ROOT / "scripts" / "setup_legal_retrieval_cuda.ps1"
START_SCRIPT = ROOT / "scripts" / "start_legal_search.ps1"
REQUIREMENTS = ROOT / "requirements" / "legal-retrieval-cu126.txt"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _run_powershell(script: Path, *arguments: str, env: dict[str, str] | None = None):
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *arguments,
    ]
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=45,
        check=False,
    )


def _last_json(stdout: str) -> dict:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    assert lines, "script did not emit structured status"
    return json.loads(lines[-1])


def test_cuda_manifest_pins_verified_direct_dependencies_and_official_index():
    manifest = _read(REQUIREMENTS)

    assert "--extra-index-url https://download.pytorch.org/whl/cu126" in manifest
    assert "torch==2.8.0+cu126" in manifest
    for dependency in (
        "chromadb==1.5.9",
        "fastapi==0.136.3",
        "numpy==2.4.6",
        "pydantic==2.13.4",
        "python-dotenv==1.2.2",
        "rank-bm25==0.2.2",
        "SQLAlchemy==2.0.50",
        "psycopg2-binary==2.9.12",
        "transformers==5.9.0",
        "uvicorn==0.48.0",
    ):
        assert dependency in manifest


def test_setup_is_isolated_idempotent_and_marks_only_after_both_prewarms():
    script = _read(SETUP_SCRIPT)

    assert '.venv-retrieval-cu126' in script
    assert "[switch]$VerifyOnly" in script
    assert "Python 3.12" in script
    assert "64bit" in script
    assert "& $venvPython -m pip install" in script
    assert "torch.cuda.is_available()" in script
    assert "torch.float16" in script
    assert "torch.float32" in script
    assert "local_files_only=True" in script
    assert '"corpus_fingerprint"' in script
    assert '"LEGAL_RETRIEVAL_SETUP_CHROMA_PATH"' in script
    assert script.index('"cuda_prewarm_passed"') < script.index('"cpu_rollback_prewarm_passed"')
    assert script.index('"cpu_rollback_prewarm_passed"') < script.index("Set-Content")
    assert "LEGAL_SEARCH_PYTHON" not in script
    assert "Select-Object -First 1" in script
    assert not any(
        line.strip().startswith(("pip install", "python -m pip install"))
        for line in script.splitlines()
    )


def test_setup_verify_only_fails_closed_for_missing_venv(tmp_path: Path):
    missing_venv = tmp_path / "missing-retrieval-venv"

    result = _run_powershell(
        SETUP_SCRIPT,
        "-VerifyOnly",
        "-VenvPath",
        str(missing_venv),
    )

    assert result.returncode != 0
    payload = _last_json(result.stdout)
    assert payload["status"] == "rejected"
    assert payload["reason_code"] == "venv_missing"
    assert not (missing_venv / ".legal-retrieval-cu126-verified.json").exists()


def test_launcher_preflight_prefers_explicit_valid_cpu_runtime_without_path_leak():
    env = os.environ.copy()
    env["LEGAL_SEARCH_PYTHON"] = sys.executable
    env["LEGAL_EMBED_DEVICE"] = "cpu"

    result = _run_powershell(START_SCRIPT, "-PreflightOnly", env=env)

    assert result.returncode == 0, result.stderr
    payload = _last_json(result.stdout)
    assert payload == {
        "status": "eligible",
        "selected_source": "environment",
        "requested_device": "cpu",
        "cuda_available": False,
    }
    assert sys.executable.lower() not in result.stdout.lower()


def test_launcher_fails_closed_for_invalid_explicit_interpreter(tmp_path: Path):
    env = os.environ.copy()
    env["LEGAL_SEARCH_PYTHON"] = str(tmp_path / "missing-python.exe")
    env["LEGAL_EMBED_DEVICE"] = "cuda"

    result = _run_powershell(START_SCRIPT, "-PreflightOnly", env=env)

    assert result.returncode != 0
    payload = _last_json(result.stdout)
    assert payload["status"] == "rejected"
    assert payload["reason_code"] == "explicit_python_invalid"
    assert "Start-Process" not in result.stdout


def test_launcher_requires_verified_cuda_marker_before_automatic_selection():
    script = _read(START_SCRIPT)

    environment_branch = script.index("$env:LEGAL_SEARCH_PYTHON")
    cuda_venv_branch = script.index(".venv-retrieval-cu126")
    project_venv_branch = script.index('.venv\\Scripts\\python.exe')
    global_branch = script.index('"python"')

    assert environment_branch < cuda_venv_branch < project_venv_branch < global_branch
    assert ".legal-retrieval-cu126-verified.json" in script
    assert "Select-Object -First 1" in script
    assert "torch.cuda.is_available()" in script
    assert "LEGAL_EMBED_DEVICE=cuda" in script


def test_cuda_venv_is_excluded_from_docker_context_and_rollback_is_documented():
    dockerignore = _read(ROOT / ".dockerignore")
    runtime_doc = _read(ROOT / "docs" / "7-DEVELOPMENT" / "legal-retrieval-cuda-runtime.md")

    assert ".venv-retrieval-cu126" in dockerignore
    assert "LEGAL_EMBED_DEVICE=cpu" in runtime_doc
    assert "LEGAL_SEARCH_PYTHON" in runtime_doc
    assert "không xóa" in runtime_doc.lower()
    assert "corpus" in runtime_doc.lower()
    assert "corpus_fingerprint" in runtime_doc
