from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rehearse_feature018_postgres.py"


def _module():
    spec = importlib.util.spec_from_file_location("rehearse_feature018_postgres", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_feature018_support_faq_schema_is_additive_and_rehearsal_down_is_scoped():
    migration = _module()
    up = migration.migration_sql("up")
    down = migration.migration_sql("down")
    for table in migration.TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in up
        assert f"DROP TABLE IF EXISTS {table}" in down
    assert "ON DELETE CASCADE" not in up
    assert "DROP TABLE IF EXISTS legal_documents" not in down
    assert "DROP TABLE IF EXISTS legal_procedure" not in down
    assert "DROP TABLE IF EXISTS form_release" not in down
    assert "WHERE status IN ('leased','active')" in up
    assert "active_count <= max_capacity" in up
    assert "confirmed_procedure_id TEXT NOT NULL" in up
    assert "form_ids" not in up
    assert "FEATURE018_APPEND_ONLY_RECORD" in up


def test_runner_refuses_live_default_and_ambiguous_database_names(monkeypatch):
    migration = _module()
    for database in ("legal_chatbot", "postgres", "production", "prod", "release", "staging", "customer_data"):
        monkeypatch.setenv(
            "FEATURE018_DATABASE_URL",
            f"postgresql+psycopg2://user:secret@127.0.0.1:5432/{database}",
        )
        with pytest.raises(RuntimeError, match="isolated_database_required"):
            migration.assert_isolated_database(confirmed=True)


def test_runner_requires_confirmation_and_down_requires_fixture_drop_flag(monkeypatch):
    migration = _module()
    monkeypatch.setenv(
        "FEATURE018_DATABASE_URL",
        "postgresql+psycopg2://user:secret@127.0.0.1:5432/feature018_isolated_20260813",
    )
    with pytest.raises(RuntimeError, match="isolated_database_required"):
        migration.assert_isolated_database(confirmed=False)
    with pytest.raises(RuntimeError, match="allow-drop-fixtures"):
        migration.apply_schema("down", confirmed=True, allow_drop_fixtures=False)


def test_plan_is_safe_without_database_and_redacts_password(monkeypatch):
    migration = _module()
    monkeypatch.delenv("FEATURE018_DATABASE_URL", raising=False)
    assert migration.plan_payload()["database"] == "not_configured"
    monkeypatch.setenv(
        "FEATURE018_DATABASE_URL",
        "postgresql+psycopg2://user:super-secret@127.0.0.1:5432/feature018_isolated",
    )
    payload = migration.plan_payload()
    assert "super-secret" not in str(payload)
    assert payload["active_pointer_change"] is False
    assert payload["json_mode"] == "read_only"


def test_source_snapshot_is_stable_and_does_not_modify_json(tmp_path):
    migration = _module()
    support_dir = tmp_path / "support"
    support_dir.mkdir()
    ticket = support_dir / "ticket-1.json"
    ticket.write_text(json.dumps({"id": "ticket-1", "citizen_id": "citizen-1", "domain": "ho_tich_chung_thuc"}), encoding="utf-8")
    faq_file = tmp_path / "faq.json"
    faq_file.write_text(json.dumps({"faqs": [{"id": "faq-1", "question": "Q", "answer": "A"}]}), encoding="utf-8")
    before = {ticket: ticket.read_bytes(), faq_file: faq_file.read_bytes()}

    first = migration.read_source_snapshot(support_dir, faq_file)
    second = migration.read_source_snapshot(support_dir, faq_file)

    assert first["snapshot_sha256"] == second["snapshot_sha256"]
    assert first["support_count"] == 1
    assert first["faq_count"] == 1
    assert {path: path.read_bytes() for path in before} == before


def test_shadow_normalization_refuses_unowned_support_and_never_releases_faq():
    migration = _module()
    with pytest.raises(ValueError, match="support_owner_missing"):
        migration.normalize_support_ticket({"id": "ticket-without-owner", "domain": "hanh_chinh_cong"})
    faq = migration.normalize_faq({
        "id": "faq-approved-json",
        "question": "Câu hỏi",
        "answer": "Câu trả lời",
        "domain": "hanh_chinh_cong",
        "review_status": "approved",
    })
    assert faq["public_state"] == "needs_review"
    assert faq["confirmed_procedure_id"] == "unresolved"
