from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

from api.support_repository import InMemorySupportRepository


def request(role: str, user_id: str) -> Request:
    scope = {"type": "http", "method": "GET", "path": "/api/support", "headers": []}
    value = Request(scope)
    value.state.user_role = role
    value.state.user_id = user_id
    value.state.username = user_id
    return value


@pytest.fixture
def canonical_support(monkeypatch):
    from api.routers import live_support

    repository = InMemorySupportRepository()
    live_support.configure_canonical_support_repository(repository)
    profiles = {
        "officer-ht": {"allowed_domains": ["ho_tich_chung_thuc"]},
        "officer-land": {"allowed_domains": ["dat_dai_xay_dung"]},
    }
    monkeypatch.setattr(
        live_support,
        "get_user_profile",
        lambda user_id: asyncio.sleep(0, result=profiles.get(user_id)),
    )
    audits: list[dict] = []

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(live_support, "write_audit_log", audit)
    yield live_support, audits
    live_support.configure_canonical_support_repository(None)


@pytest.mark.asyncio
async def test_api_enforces_owner_domain_assignment_and_privacy_safe_queue(canonical_support):
    live_support, _ = canonical_support
    created = await live_support.create_ticket(
        live_support.CreateTicketRequest(
            question="Nội dung riêng tư không được lộ ra hàng chờ",
            domain="ho_tich_chung_thuc",
        ),
        request("citizen", "owner"),
    )
    ticket_id = created["id"]

    with pytest.raises(live_support.HTTPException) as other_owner:
        await live_support.get_ticket(ticket_id, request("citizen", "other"))
    assert other_owner.value.status_code == 403

    queue = await live_support.canonical_officer_queue(
        request("officer", "officer-ht")
    )
    assert len(queue) == 1
    assert "question" not in queue[0]
    assert "owner_user_id" not in queue[0]
    assert await live_support.canonical_officer_queue(
        request("officer", "officer-land")
    ) == []

    with pytest.raises(live_support.HTTPException) as unassigned:
        await live_support.get_ticket(ticket_id, request("officer", "officer-ht"))
    assert unassigned.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_content_requires_reason_and_records_audit_before_returning_content(canonical_support):
    live_support, audits = canonical_support
    created = await live_support.create_ticket(
        live_support.CreateTicketRequest(
            question="Nội dung nhạy cảm của người dân",
            domain="ho_tich_chung_thuc",
        ),
        request("citizen", "owner"),
    )
    admin = request("admin", "admin-1")

    with pytest.raises(live_support.HTTPException) as missing_reason:
        await live_support.get_ticket(created["id"], admin)
    assert missing_reason.value.status_code == 403

    detail = await live_support.admin_view_support_content(
        created["id"],
        live_support.AdminContentAccessRequest(
            reason="Kiểm tra khiếu nại SLA số 123"
        ),
        admin,
    )
    assert detail["messages"][0]["content"] == "Nội dung nhạy cảm của người dân"
    assert audits and audits[0]["action"] == "support.chat.view"
    assert audits[0]["details"]["reason"] == "Kiểm tra khiếu nại SLA số 123"


@pytest.mark.asyncio
async def test_admin_metadata_never_contains_owner_or_question(canonical_support):
    live_support, _ = canonical_support
    await live_support.create_ticket(
        live_support.CreateTicketRequest(
            question="Không được xuất hiện trong metadata",
            domain="ho_tich_chung_thuc",
        ),
        request("citizen", "owner"),
    )
    rows = await live_support.admin_support_ticket_metadata(
        request("admin", "admin-1")
    )
    assert len(rows) == 1
    assert "owner_user_id" not in rows[0]
    assert "question" not in rows[0]
