from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "manage_feature017_form_schema.py"


def _module():
    spec = importlib.util.spec_from_file_location("manage_feature017_form_schema", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_feature017_migration_is_additive_and_defines_canonical_tables():
    migration = _module()
    up = migration.migration_sql("up")
    down = migration.migration_sql("down")
    for table in migration.TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in up
        assert f"DROP TABLE IF EXISTS {table}" in down
    assert "DROP TABLE IF EXISTS legal_documents" not in down
    assert "DROP TABLE IF EXISTS legal_chunks" not in down
    assert "ON DELETE CASCADE" not in up
    assert "release_ref TEXT NOT NULL REFERENCES form_release(id) ON DELETE RESTRICT" in up
    assert "UNIQUE (release_ref, procedure_id)" in up
    assert "procedure_id TEXT NOT NULL UNIQUE" not in up


def test_runner_refuses_live_or_default_postgres_database_names(monkeypatch):
    migration = _module()
    for database in ("legal_chatbot", "postgres", "production", "prod", "release"):
        monkeypatch.setenv(
            "LEGAL_DATABASE_URL",
            f"postgresql+psycopg2://user:secret@127.0.0.1:5432/{database}",
        )
        with pytest.raises(RuntimeError, match="isolated_database_required"):
            migration.assert_isolated_database(confirmed=True)


def test_runner_requires_explicit_confirmation(monkeypatch):
    migration = _module()
    monkeypatch.setenv(
        "LEGAL_DATABASE_URL",
        "postgresql+psycopg2://user:secret@127.0.0.1:5432/feature017_isolated_20260811",
    )
    with pytest.raises(RuntimeError, match="isolated_database_required"):
        migration.assert_isolated_database(confirmed=False)
    assert migration.assert_isolated_database(confirmed=True).endswith(
        "/feature017_isolated_20260811"
    )


def test_plan_output_never_contains_database_password(monkeypatch):
    migration = _module()
    monkeypatch.setenv(
        "LEGAL_DATABASE_URL",
        "postgresql+psycopg2://user:super-secret@127.0.0.1:5432/feature017_isolated",
    )
    payload = migration.plan_payload()
    assert "super-secret" not in str(payload)
    assert payload["database"] == "feature017_isolated"
