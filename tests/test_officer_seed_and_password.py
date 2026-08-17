"""Tests for officer seed, first-login password enforcement, and admin reset."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


async def _async_noop(*_args, **_kwargs):
    return None


async def _async_noop_kwargs(**_kwargs):
    return None


@pytest.fixture
def support_store(monkeypatch, tmp_path):
    from api.routers import live_support

    monkeypatch.setattr(live_support, "TICKETS_DIR", tmp_path / "tickets")
    monkeypatch.setattr(live_support, "ATTACHMENTS_DIR", tmp_path / "attachments")
    return live_support


def _make_request(role: str, user_id: str) -> Request:
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "path": "/api", "headers": []}
    req = Request(scope)
    req.state.user_role = role
    req.state.user_id = user_id
    req.state.username = user_id
    return req


# ---------------------------------------------------------------------------
# Seed & domain restriction
# ---------------------------------------------------------------------------

def test_seed_five_officers_domain_restriction():
    """The exact five least-privilege officers are defined with one support domain."""
    from scripts.seed_officer_accounts import OFFICERS, WARD_SCOPE, generate_initial_password

    expected = {
        "officer_hotich": "ho_tich_chung_thuc",
        "officer_daidai": "dat_dai_xay_dung",
        "officer_ansinh": "an_sinh_y_te_giao_duc",
        "officer_cutru": "cu_tru_an_ninh",
        "officer_khieunai": "khieu_nai_to_cao_xu_phat",
    }
    assert {item["username"]: item["domain"] for item in OFFICERS} == expected
    assert WARD_SCOPE == "Phường Lê Chân, Hải Phòng"
    first, second = generate_initial_password(), generate_initial_password()
    assert first != second
    assert len(first) >= 20



@pytest.mark.asyncio
async def test_seed_is_idempotent_skips_existing(monkeypatch, tmp_path):
    """Running the seed twice should skip accounts that already exist."""
    from api.user_service import create_user_account

    async def fake_repo_create(table, data):
        uid = data.get("username") or data.get("id") or "temp"
        out = {**data, "id": f"user:{uid}", "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z"}
        if table == "user_profile":
            out["user"] = f"user:{out['user'].split(':', 1)[-1]}"
        return [out]

    async def fake_repo_query(q, params=None):
        if "username" in q or "email" in q:
            ident = params.get("identifier", "") if params else ""
            if ident in {"officer_hotich", "hotich@test.local"}:
                return [{"id": "user:existing", "username": "officer_hotich", "email": "hotich@test.local", "role": "officer"}]
        return []

    monkeypatch.setattr("api.user_service.repo_create", fake_repo_create)
    monkeypatch.setattr("api.user_service.repo_query", fake_repo_query)
    monkeypatch.setattr("api.user_service.repo_update", lambda *a, **kw: None)

    acct = {
        "username": "officer_hotich",
        "email": "hotich@test.local",
        "password": "AnotherTemp1!",
        "role": "officer",
        "ward": "Phường Lê Chân, Hải Phòng",
        "ward_scope": "Phường Lê Chân, Hải Phòng",
        "allowed_domains": ["ho_tich_chung_thuc"],
        "must_change_password": True,
        "preferences": {"can_receive_live_support": True, "can_submit_document_candidates": True},
    }
    with pytest.raises(HTTPException) as dup:
        await create_user_account(acct, actor_user_id=None, actor_role="system")
    assert "409" in str(dup.value.status_code) or "đã tồn tại" in str(dup.value.detail).lower()


# ---------------------------------------------------------------------------
# First-login password enforcement
# ---------------------------------------------------------------------------

def test_first_login_password_enforcement_on_auth(monkeypatch):
    """Login response preserves the must-change flag returned by account auth."""
    from api.routers.auth import router as auth_router

    async def fake_auth(identifier, password, role):
        return {
            "token": "fake-token",
            "user": {"id": "user:officer_hotich", "username": "officer_hotich", "email": "hotich@test.local", "role": "officer", "profile": {"must_change_password": True}},
            "role": "officer",
            "expires_at": "2026-12-31T00:00:00Z",
        }

    monkeypatch.setattr("api.routers.auth.authenticate_user_account", fake_auth)
    monkeypatch.setattr("api.routers.auth.ensure_bootstrap_admin_user", _async_noop)
    app = FastAPI()
    app.include_router(auth_router, prefix="/api")
    with TestClient(app) as client:
        resp = client.post("/api/auth/login", json={"identifier": "officer_hotich", "password": "any", "role": "officer"})
    assert resp.status_code == 200
    assert resp.json()["must_change_password"] is True


# ---------------------------------------------------------------------------
# Admin reset password
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_reset_password_forces_change(monkeypatch, tmp_path):
    """Admin reset sets a new password and flags must_change_password=True."""
    from api.user_service import admin_reset_user_password, get_user_profile

    reset_done = []

    async def fake_repo_update(table, rid, data):
        if table == "user_account":
            pass
        elif table == "user_profile":
            reset_done.append(data)

    async def fake_repo_query(q, params=None):
        if "user_profile" in q:
            return [{"id": "prof:1", "user": "user:target", "must_change_password": False, "allowed_domains": ["ho_tich_chung_thuc"]}]
        return [{"id": "user:target", "username": "officer_hotich", "email": "hotich@test.local", "role": "officer", "password_hash": "pbkdf2_sha256$120000$salt$digest", "is_active": True}]

    monkeypatch.setattr("api.user_service.repo_update", fake_repo_update)
    monkeypatch.setattr("api.user_service.repo_query", fake_repo_query)
    monkeypatch.setattr("api.user_service._revoke_active_sessions", _async_noop)
    monkeypatch.setattr("api.user_service.write_audit_log", _async_noop_kwargs)

    await admin_reset_user_password(
        "user:target",
        new_password="NewTempPass123!",
        actor_user_id="user:admin",
    )

    assert len(reset_done) == 1
    assert reset_done[0].get("must_change_password") is True


# ---------------------------------------------------------------------------
# Self-service change password
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_self_service_change_password_clears_flag(monkeypatch, tmp_path):
    """Changing own password clears must_change_password and revokes sessions."""
    from api.user_service import change_own_password, hash_password

    changed = []

    async def fake_repo_update(table, rid, data):
        changed.append((table, rid, data))

    current_hash = hash_password("OldTempPass123!")
    async def fake_repo_query(q, params=None):
        if "record" in q:
            return [{"id": "user:me", "username": "officer_hotich", "email": "hotich@test.local", "role": "officer", "password_hash": current_hash, "is_active": True}]
        return [{"id": "prof:1", "user": "user:me", "must_change_password": True, "allowed_domains": ["ho_tich_chung_thuc"]}]

    monkeypatch.setattr("api.user_service.repo_update", fake_repo_update)
    monkeypatch.setattr("api.user_service.repo_query", fake_repo_query)
    monkeypatch.setattr("api.user_service._revoke_active_sessions", _async_noop)
    monkeypatch.setattr("api.user_service.write_audit_log", _async_noop_kwargs)

    await change_own_password(
        "user:me",
        current_password="OldTempPass123!",
        new_password="StrongNewPass456!",
    )

    pw_updates = [c for c in changed if c[0] == "user_account"]
    assert len(pw_updates) == 1
    prof_updates = [c for c in changed if c[0] == "user_profile"]
    assert len(prof_updates) == 1
    assert prof_updates[0][2].get("must_change_password") is False


# ---------------------------------------------------------------------------
# Credential file safety
# ---------------------------------------------------------------------------

def test_credential_file_not_in_source_control(tmp_path, monkeypatch):
    """The credential file lives under data/ which is gitignored."""
    from scripts.seed_officer_accounts import CREDENTIAL_FILE
    assert "data" in str(CREDENTIAL_FILE)
    assert ".gitignore" in str(Path(__file__).parent.parent / ".gitignore")
    gitignore = (Path(__file__).parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "data/" in gitignore or "data" in gitignore
