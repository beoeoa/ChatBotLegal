from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rehearse_feature017_postgres.py"


def _module():
    spec = importlib.util.spec_from_file_location("rehearse_feature017_postgres", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rehearsal_requires_explicit_confirmation():
    with pytest.raises(RuntimeError, match="isolated_database_confirmation_required"):
        _module().main([])


def test_rehearsal_metadata_is_bound_to_official_sources():
    metadata = _module()._metadata("a" * 64)
    assert metadata["procedure"]["official_source_url"].startswith(
        "https://dichvucong.gov.vn/"
    )
    assert metadata["asset"]["source_url"].startswith("https://vbpl.vn/")
    assert metadata["asset"]["source_checksum"] == "a" * 64
