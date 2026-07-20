from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_lightweight_utils_submodule_does_not_import_transformers() -> None:
    """Auth/readiness imports must not initialize the NLP stack."""

    script = """
import sys
from open_notebook.utils.encryption import get_secret_from_env
from open_notebook.utils.chunking import detect_content_type
assert callable(get_secret_from_env)
assert callable(detect_content_type)
assert 'transformers' not in sys.modules
assert 'langchain_text_splitters' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr


def test_legacy_public_utility_exports_remain_compatible() -> None:
    """PEP 562 resolution keeps existing `from ...utils import ...` callers."""

    script = """
from open_notebook.utils import compare_versions, token_count
assert callable(compare_versions)
assert callable(token_count)
assert token_count('abc') > 0
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr


def test_model_registry_import_does_not_initialize_inference_stack() -> None:
    """Readiness may inspect model records without importing Torch/Transformers."""

    script = """
import sys
from open_notebook.ai.models import Model, model_manager
from open_notebook.ai.provision import provision_langchain_model
assert Model is not None
assert model_manager is not None
assert callable(provision_langchain_model)
assert 'torch' not in sys.modules
assert 'transformers' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
