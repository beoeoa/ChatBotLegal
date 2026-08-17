from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "manage_feature016_trust_schema.py"
SPEC = importlib.util.spec_from_file_location("manage_feature016_trust_schema", SCRIPT)
assert SPEC and SPEC.loader
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_additive_migration_defines_only_feature016_sidecars():
    up = MIGRATION.migration_sql("up")
    down = MIGRATION.migration_sql("down")
    for table in MIGRATION.TABLES:
        assert f"DEFINE TABLE IF NOT EXISTS {table}" in up
        assert f"REMOVE TABLE IF EXISTS {table}" in down
    assert "REMOVE TABLE IF EXISTS legal_document;" not in down
    assert "REMOVE TABLE IF EXISTS legal_chunk;" not in down


def test_runner_refuses_default_or_live_database(monkeypatch):
    for database in ("", "open_notebook", "production", "prod", "legal_chatbot"):
        monkeypatch.setenv("SURREAL_DATABASE", database)
        with pytest.raises(RuntimeError, match="isolated_database_required"):
            MIGRATION.assert_isolated_database(confirmed=True)


def test_runner_requires_confirmation_for_named_isolated_database(monkeypatch):
    monkeypatch.setenv("SURREAL_DATABASE", "feature016_trust_rehearsal_20260810")
    with pytest.raises(RuntimeError, match="isolated_database_required"):
        MIGRATION.assert_isolated_database(confirmed=False)
    assert (
        MIGRATION.assert_isolated_database(confirmed=True)
        == "feature016_trust_rehearsal_20260810"
    )


@pytest.mark.asyncio
async def test_rollback_verifier_requires_every_sidecar_to_be_absent(monkeypatch):
    monkeypatch.setenv("SURREAL_DATABASE", "feature016_trust_rehearsal_20260810")

    async def removed_query(_sql):
        return {"tables": {"unrelated_legacy_table": "DEFINE TABLE unrelated_legacy_table"}}

    monkeypatch.setattr(MIGRATION, "_query", removed_query)
    assert all((await MIGRATION.verify_removed()).values())

    async def leaked_query(_sql):
        return {"tables": {"legal_audit_chain": "DEFINE TABLE legal_audit_chain"}}

    monkeypatch.setattr(MIGRATION, "_query", leaked_query)
    with pytest.raises(RuntimeError, match="trust_sidecar_tables_still_present"):
        await MIGRATION.verify_removed()
