from __future__ import annotations

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

from api import user_service
from api.routers import users as users_router
from api.routers.users import (
    UserCreateRequest,
    UserSelfUpdateRequest,
    UserUpdateRequest,
    _require_business_reason,
)


def test_self_update_rejects_privileged_account_fields() -> None:
    for field, value in (
        ("role", "admin"),
        ("is_active", False),
        ("allowed_domains", ["dat_dai_xay_dung"]),
        ("must_change_password", False),
        ("preferences", {"mfa": {"totp_enabled": False}}),
    ):
        with pytest.raises(ValidationError):
            UserSelfUpdateRequest.model_validate({field: value})


def test_business_reason_decodes_utf8_header_value() -> None:
    assert _require_business_reason(
        "Ki%E1%BB%83m%20th%E1%BB%AD%20tr%E1%BB%B1c%20ti%E1%BA%BFp"
    ) == "Kiểm thử trực tiếp"


def test_managed_account_password_policy_requires_twelve_characters() -> None:
    with pytest.raises(ValidationError):
        UserCreateRequest(
            username="new_user",
            email="new_user@example.test",
            password="short123",
        )

    with pytest.raises(ValidationError):
        UserUpdateRequest(password="short123")


def _admin_request() -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/api/users/ask-history", "headers": []})
    request.state.user_role = "admin"
    request.state.user_id = "user_account:admin"
    return request


@pytest.mark.asyncio
async def test_account_ask_history_returns_full_details_and_audits_access(monkeypatch) -> None:
    captured: dict = {}
    audit_entries: list[dict] = []

    async def has_users():
        return True

    async def get_user(user_id: str):
        return {"id": user_id, "username": "citizen"}

    async def list_history(**kwargs):
        captured.update(kwargs)
        return [{"id": "user_ask_history:1", "question": "Câu hỏi", "answer": "Câu trả lời"}]

    async def audit(**kwargs):
        audit_entries.append(kwargs)

    monkeypatch.setattr(users_router, "has_real_users", has_users)
    monkeypatch.setattr(users_router, "get_user_with_profile", get_user)
    monkeypatch.setattr(users_router, "list_ask_history", list_history)
    monkeypatch.setattr(users_router, "write_audit_log", audit)

    rows = await users_router.get_ask_history(
        _admin_request(),
        limit=25,
        user_id="user_account:citizen",
        role=None,
        domain=None,
        department=None,
        grounding_status=None,
        date_from=None,
        date_to=None,
    )

    assert rows[0]["answer"] == "Câu trả lời"
    assert captured["user_id"] == "user_account:citizen"
    assert captured["summary_only"] is False
    assert audit_entries[0]["action"] == "user.ask_history.view"
    assert audit_entries[0]["target_user_id"] == "user_account:citizen"


@pytest.mark.asyncio
async def test_global_ask_history_keeps_answers_out_of_summary(monkeypatch) -> None:
    captured: dict = {}

    async def has_users():
        return True

    async def list_history(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(users_router, "has_real_users", has_users)
    monkeypatch.setattr(users_router, "list_ask_history", list_history)

    await users_router.get_ask_history(
        _admin_request(),
        limit=50,
        user_id=None,
        role=None,
        domain=None,
        department=None,
        grounding_status=None,
        date_from=None,
        date_to=None,
    )

    assert captured["user_id"] is None
    assert captured["summary_only"] is True


def test_contact_validation_rejects_malformed_email_and_phone() -> None:
    with pytest.raises(HTTPException, match="Email"):
        user_service.validate_account_contacts(email="nguoidung@", phone=None)

    with pytest.raises(HTTPException, match="Số điện thoại"):
        user_service.validate_account_contacts(
            email="nguoidung@example.test",
            phone="09AB-123",
        )


def test_contact_validation_accepts_common_vietnamese_phone_formats() -> None:
    for phone in ("0912 345 678", "+84 912 345 678", "(0225) 123 4567", ""):
        user_service.validate_account_contacts(
            email="nguoidung@example.test",
            phone=phone,
        )


@pytest.mark.asyncio
async def test_account_service_rejects_short_passwords() -> None:
    with pytest.raises(HTTPException, match="12 ký tự"):
        await user_service.create_user_account(
            {
                "username": "new_user",
                "email": "new_user@example.test",
                "password": "short123",
                "role": "citizen",
            },
            actor_user_id="user_account:admin",
            actor_role="admin",
        )


@pytest.mark.asyncio
async def test_deactivate_account_revokes_every_active_session(monkeypatch) -> None:
    updates: list[tuple[str, str, dict]] = []

    async def get_user(_user_id: str):
        return {"id": "user_account:target", "role": "citizen", "is_active": True}

    async def query(_sql: str, _params=None):
        return [{"id": "user_session:first"}, {"id": "user_session:second"}]

    async def update(table: str, record_id: str, payload: dict):
        updates.append((table, record_id, payload))

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(user_service, "get_user_with_profile", get_user)
    monkeypatch.setattr(user_service, "repo_query", query)
    monkeypatch.setattr(user_service, "repo_update", update)
    monkeypatch.setattr(user_service, "write_audit_log", no_op)

    await user_service.deactivate_user_account(
        "user_account:target",
        actor_user_id="user_account:admin",
        actor_role="admin",
        reason="Không còn công tác",
    )

    assert updates[0] == (
        "user_account",
        "user_account:target",
        {"is_active": False},
    )
    assert [item[:2] for item in updates[1:]] == [
        ("user_session", "user_session:first"),
        ("user_session", "user_session:second"),
    ]
    assert all(item[2].get("revoked_at") is not None for item in updates[1:])


def test_merge_user_profile_exposes_soft_delete_state() -> None:
    merged = user_service.merge_user_profile(
        {
            "id": "user_account:deleted",
            "username": "deleted",
            "email": "deleted@example.test",
            "role": "citizen",
            "is_active": False,
        },
        {
            "preferences": {
                "account_lifecycle": {
                    "deleted_at": "2026-08-09T08:00:00+00:00",
                    "deleted_by": "user_account:admin",
                }
            }
        },
    )

    assert merged["is_deleted"] is True
    assert merged["deleted_at"] == "2026-08-09T08:00:00+00:00"


@pytest.mark.asyncio
async def test_soft_delete_preserves_profile_and_revokes_sessions(monkeypatch) -> None:
    updates: list[tuple[str, str, dict]] = []
    audit_entries: list[dict] = []

    async def get_user(_user_id: str):
        return {
            "id": "user_account:target",
            "role": "citizen",
            "is_active": True,
            "profile": {"preferences": {"theme": "system"}},
        }

    async def get_profile(_user_id: str):
        return {
            "id": "user_profile:target",
            "preferences": {"theme": "system"},
        }

    async def query(sql: str, _params=None):
        if "user_session" in sql:
            return [{"id": "user_session:first"}]
        return [{"count": 2}]

    async def update(table: str, record_id: str, payload: dict):
        updates.append((table, record_id, payload))

    async def audit(**kwargs):
        audit_entries.append(kwargs)

    monkeypatch.setattr(user_service, "get_user_with_profile", get_user)
    monkeypatch.setattr(user_service, "get_user_profile", get_profile)
    monkeypatch.setattr(user_service, "repo_query", query)
    monkeypatch.setattr(user_service, "repo_update", update)
    monkeypatch.setattr(user_service, "write_audit_log", audit)

    await user_service.soft_delete_user_account(
        "user_account:target",
        actor_user_id="user_account:admin",
        actor_role="admin",
        reason="Tài khoản thử nghiệm không còn sử dụng",
    )

    assert updates[0] == (
        "user_account",
        "user_account:target",
        {"is_active": False},
    )
    profile_update = next(item for item in updates if item[0] == "user_profile")
    lifecycle = profile_update[2]["preferences"]["account_lifecycle"]
    assert lifecycle["deleted_at"]
    assert lifecycle["deleted_by"] == "user_account:admin"
    assert lifecycle["reason"] == "Tài khoản thử nghiệm không còn sử dụng"
    assert profile_update[2]["preferences"]["theme"] == "system"
    assert any(item[0] == "user_session" and item[2].get("revoked_at") for item in updates)
    assert audit_entries[0]["action"] == "user.soft_delete"
