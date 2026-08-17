from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api import readiness


def test_validity_readiness_is_privacy_safe_and_optional_in_protect_mode(monkeypatch):
    monkeypatch.setenv("LEGAL_VALIDITY_SYNC_MODE", "protect")

    component = readiness.check_legal_validity_sync(
        snapshot_loader=lambda: None,
        now=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )

    assert component == {
        "healthy": False,
        "code": "validity_snapshot_unavailable",
        "required": False,
        "status": "missing",
        "age_seconds": None,
    }
    assert "url" not in component
    assert "exception" not in component


def test_validity_readiness_is_required_and_healthy_in_strict_mode(monkeypatch):
    monkeypatch.setenv("LEGAL_VALIDITY_SYNC_MODE", "strict")
    snapshot = {
        "schema_version": "legal-validity-serving-v1",
        "last_success_at": "2026-08-08T00:00:00+00:00",
        "documents": {},
    }

    component = readiness.check_legal_validity_sync(
        snapshot_loader=lambda: snapshot,
        now=datetime(2026, 8, 8, 0, 1, tzinfo=timezone.utc),
    )

    assert component["healthy"] is True
    assert component["required"] is True
    assert component["code"] == "ok"


@pytest.mark.asyncio
async def test_strict_validity_failure_blocks_answer_readiness(monkeypatch):
    healthy = {"healthy": True, "code": "ok"}

    async def healthy_check(*args, **kwargs):
        return dict(healthy)

    monkeypatch.setattr(readiness, "check_database", healthy_check)
    monkeypatch.setattr(readiness, "check_legal_retrieval", healthy_check)
    monkeypatch.setattr(readiness, "check_model_provider", healthy_check)
    monkeypatch.setattr(readiness, "check_ollama", healthy_check)
    monkeypatch.setattr(
        readiness,
        "check_legal_validity_sync",
        lambda: {
            "healthy": False,
            "code": "validity_snapshot_stale",
            "required": True,
            "status": "stale",
            "age_seconds": 99999,
        },
    )

    result = await readiness.collect_readiness()

    assert result["status"] == "not_ready"
    assert result["components"]["legal_validity_sync"]["code"] == "validity_snapshot_stale"
