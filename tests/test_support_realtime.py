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
    monkeypatch.setattr(
        live_support,
        "get_user_profile",
        lambda user_id: asyncio.sleep(
            0,
            result={"allowed_domains": ["ho_tich_chung_thuc"]}
            if user_id == "officer-ht"
            else None,
        ),
    )
    live_support.hub.sessions.clear()
    live_support.hub.queue_watchers.clear()
    live_support.hub.user_sockets.clear()
    live_support.hub.officer_queue_sockets.clear()
    yield live_support
    live_support.configure_canonical_support_repository(None)


@pytest.mark.asyncio
async def test_waiting_ticket_cannot_open_realtime_but_assigned_ticket_can(canonical_support):
    live_support = canonical_support
    citizen = request("citizen", "citizen-1")
    officer = request("officer", "officer-ht")
    created = await live_support.create_ticket(
        live_support.CreateTicketRequest(
            question="Người chờ chỉ dùng polling",
            domain="ho_tich_chung_thuc",
        ),
        citizen,
    )
    with pytest.raises(live_support.HTTPException) as waiting:
        await live_support.issue_websocket_ticket(
            live_support.WebSocketTicketRequest(ticket_id=created["id"]), citizen
        )
    assert waiting.value.status_code == 409

    await live_support.update_officer_presence(
        live_support.OfficerPresenceRequest(
            domains=["ho_tich_chung_thuc"], max_capacity=3
        ),
        officer,
    )
    claimed = await live_support.claim_next_ticket(officer)
    token = await live_support.issue_websocket_ticket(
        live_support.WebSocketTicketRequest(ticket_id=created["id"]), citizen
    )
    assert token["realtime_scope"] == "assigned_ticket"
    assert claimed["assignment"]["ticket_id"] == created["id"]


@pytest.mark.asyncio
async def test_officer_receives_one_queue_stream_for_all_scoped_domains(canonical_support):
    live_support = canonical_support
    officer = request("officer", "officer-ht")
    token = await live_support.issue_websocket_ticket(
        live_support.WebSocketTicketRequest(officer_queue=True), officer
    )
    assert token["realtime_scope"] == "single_officer_queue"

    class Socket:
        def __init__(self):
            self.closed = False

        async def close(self, **kwargs):
            self.closed = True

    first = Socket()
    second = Socket()
    await live_support.hub.join_officer_queue(
        "officer-ht", ["ho_tich_chung_thuc"], first
    )
    await live_support.hub.join_officer_queue(
        "officer-ht", ["ho_tich_chung_thuc"], second
    )
    assert first.closed is True
    assert live_support.hub.officer_queue_sockets["officer-ht"] is second
    assert first not in live_support.hub.queue_watchers["ho_tich_chung_thuc"]
    assert second in live_support.hub.queue_watchers["ho_tich_chung_thuc"]
