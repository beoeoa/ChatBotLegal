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
        "officer-ht": {
            "allowed_domains": ["ho_tich_chung_thuc"],
            "preferences": {"can_receive_live_support": True},
        }
    }
    monkeypatch.setattr(
        live_support,
        "get_user_profile",
        lambda user_id: asyncio.sleep(0, result=profiles.get(user_id)),
    )
    yield live_support, repository
    live_support.configure_canonical_support_repository(None)


@pytest.mark.asyncio
async def test_canonical_ticket_queue_claim_activate_message_and_resolve(canonical_support):
    live_support, repository = canonical_support
    citizen = request("citizen", "citizen-1")
    officer = request("officer", "officer-ht")
    created = await live_support.create_ticket(
        live_support.CreateTicketRequest(
            question="Tôi cần cán bộ hướng dẫn đăng ký khai sinh",
            domain="ho_tich_chung_thuc",
            ai_summary="Hướng dẫn khai sinh",
        ),
        citizen,
    )
    ticket_id = created["id"]
    assert created["status"] == "queued"

    mine = await live_support.list_my_tickets(citizen)
    assert [item["id"] for item in mine] == [ticket_id]
    queue_status = await live_support.get_ticket_queue_status(ticket_id, citizen)
    assert queue_status["position"] == 1

    presence = await live_support.update_officer_presence(
        live_support.OfficerPresenceRequest(
            domains=["ho_tich_chung_thuc"], max_capacity=3
        ),
        officer,
    )
    assert presence["active_count"] == 0
    claimed = await live_support.claim_next_ticket(officer)
    assignment = claimed["assignment"]
    assert assignment["ticket_id"] == ticket_id
    assert "lease_token" in claimed

    activated = await live_support.activate_support_assignment(
        assignment["id"],
        {"lease_token": claimed["lease_token"]},
        officer,
    )
    assert activated["status"] == "active"
    sent = await live_support.send_message(
        ticket_id,
        live_support.SendMessageRequest(content="Cán bộ đã tiếp nhận yêu cầu."),
        officer,
    )
    assert sent["sequence_number"] == 2
    resolved = await live_support.resolve_support_ticket(
        ticket_id,
        live_support.ResolveTicketRequest(resolution_note="Đã hướng dẫn đầy đủ"),
        officer,
    )
    assert resolved["status"] == "resolved"
    assert len(repository.list_messages(
        ticket_id,
        live_support.SupportActor(
            user_id="officer-ht",
            role="officer",
            domains=("ho_tich_chung_thuc",),
        ),
    )) == 3


@pytest.mark.asyncio
async def test_canonical_mode_disables_arbitrary_ticket_claim(canonical_support):
    live_support, _ = canonical_support
    citizen = request("citizen", "citizen-1")
    created = await live_support.create_ticket(
        live_support.CreateTicketRequest(
            question="Yêu cầu đủ dài để tạo ticket",
            domain="ho_tich_chung_thuc",
        ),
        citizen,
    )
    with pytest.raises(live_support.HTTPException) as denied:
        await live_support.claim_ticket(
            created["id"],
            live_support.ClaimRequest(),
            request("officer", "officer-ht"),
        )
    assert denied.value.status_code == 410
