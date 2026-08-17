from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "manage_legal_lifecycle_schema.py"
spec = importlib.util.spec_from_file_location("manage_legal_lifecycle_schema", SCRIPT)
assert spec and spec.loader
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


def test_phase2_migration_is_additive_and_has_exact_down_scope():
    up = manager.migration_sql("up")
    down = manager.migration_sql("down")
    for table in manager.TABLES:
        assert f"DEFINE TABLE IF NOT EXISTS {table}" in up
        assert f"REMOVE TABLE IF EXISTS {table}" in down
    forbidden = ("legal_documents", "legal_articles", "legal_article_chunks", "DELETE ", "UPDATE ")
    assert not any(token in up for token in forbidden)
    assert not any(token in down for token in forbidden)


def test_migration_defaults_to_plan_without_opening_a_database(capsys):
    assert manager.main(["plan"]) == 0
    output = capsys.readouterr().out
    assert "dry_run" in output
    assert "legal_document_draft" in output


@pytest.mark.parametrize("database", [None, "", "open_notebook", "legal_chatbot", "prod"])
def test_apply_refuses_default_or_live_database(monkeypatch, database):
    if database is None:
        monkeypatch.delenv("SURREAL_DATABASE", raising=False)
    else:
        monkeypatch.setenv("SURREAL_DATABASE", database)
    with pytest.raises(RuntimeError, match="isolated_database_required"):
        manager.assert_isolated_database(confirmed=True)


def test_apply_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setenv("SURREAL_DATABASE", "legal_lifecycle_phase2_test")
    with pytest.raises(RuntimeError, match="isolated_database_required"):
        manager.assert_isolated_database(confirmed=False)
    assert manager.assert_isolated_database(confirmed=True) == "legal_lifecycle_phase2_test"
