import asyncio
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient


@pytest.fixture
def support_store(monkeypatch, tmp_path):
    from api.routers import live_support

    monkeypatch.setattr(live_support, "TICKETS_DIR", tmp_path / "tickets")
    monkeypatch.setattr(live_support, "ATTACHMENTS_DIR", tmp_path / "attachments")
    return live_support


def request(role: str, user_id: str):
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "path": "/api/support", "headers": []}
    req = Request(scope)
    req.state.user_role = role
    req.state.user_id = user_id
    req.state.username = user_id
    return req


@pytest.mark.asyncio
async def test_queue_domain_routing_and_claim_ownership(support_store, monkeypatch):
    profiles = {"officer-ht": {"allowed_domains": ["ho_tich_chung_thuc"]}, "officer-land": {"allowed_domains": ["dat_dai_xay_dung"]}}
    monkeypatch.setattr(support_store, "get_user_profile", lambda user_id: asyncio.sleep(0, result=profiles.get(user_id)))
    citizen = request("citizen", "citizen-1")
    created = await support_store.create_ticket(support_store.CreateTicketRequest(question="T?i c?n h?i v? khai sinh", domain="ho_tich_chung_thuc"), citizen)
    ticket_id = created["id"]

    waiting_for_ht = await support_store.list_queue(request("officer", "officer-ht"))
    assert [item["id"] for item in waiting_for_ht] == [ticket_id]
    assert waiting_for_ht[0]["citizen_id"] == ""  # pre-claim queue is privacy-safe
    assert await support_store.list_queue(request("officer", "officer-land")) == []

    claimed = await support_store.claim_ticket(ticket_id, support_store.ClaimRequest(note="Ti?p nh?n"), request("officer", "officer-ht"))
    assert claimed["status"] == "assigned"
    with pytest.raises(HTTPException) as denied:
        await support_store.get_ticket(ticket_id, request("officer", "officer-land"))
    assert denied.value.status_code == 403
    detail = await support_store.get_ticket(ticket_id, citizen)
    assert detail["assigned_officer_id"] == "officer-ht"


@pytest.mark.asyncio
async def test_transfer_owner_and_realtime_event_delivery(support_store, monkeypatch):
    monkeypatch.setattr(support_store, "get_user_profile", lambda _id: asyncio.sleep(0, result={"allowed_domains": ["ho_tich_chung_thuc", "dat_dai_xay_dung"]}))
    created = await support_store.create_ticket(support_store.CreateTicketRequest(question="C?n h? tr? ??t ?ai", domain="ho_tich_chung_thuc"), request("citizen", "citizen-1"))
    ticket_id = created["id"]
    await support_store.claim_ticket(ticket_id, support_store.ClaimRequest(), request("officer", "officer-a"))

    class Socket:
        def __init__(self): self.events = []
        async def send_json(self, event): self.events.append(event)
    socket = Socket()
    await support_store.hub.join_session(ticket_id, socket)
    await support_store.send_message(ticket_id, support_store.SendMessageRequest(content="T?i ?? ti?p nh?n"), request("officer", "officer-a"))
    assert any(event["type"] == "message.created" for event in socket.events)

    moved = await support_store.decline_ticket(ticket_id, support_store.DeclineRequest(reason="Chuy?n ??ng l?nh v?c", transfer_domain="dat_dai_xay_dung"), request("officer", "officer-a"))
    assert moved["domain"] == "dat_dai_xay_dung"
    assert moved["status"] == "waiting"
    assert moved["assigned_officer_id"] is None
    with pytest.raises(HTTPException):
        await support_store.send_message(ticket_id, support_store.SendMessageRequest(content="Kh?ng c?n quy?n"), request("officer", "officer-a"))


@pytest.mark.asyncio
async def test_attachment_close_rating_and_admin_audit(support_store, monkeypatch):
    audit = []
    async def audit_log(**kwargs): audit.append(kwargs)
    monkeypatch.setattr(support_store, "write_audit_log", audit_log)
    monkeypatch.setattr(support_store, "get_user_profile", lambda _id: asyncio.sleep(0, result={"allowed_domains": ["ho_tich_chung_thuc"]}))
    created = await support_store.create_ticket(support_store.CreateTicketRequest(question="C?n h? tr? h? t?ch", domain="ho_tich_chung_thuc"), request("citizen", "citizen-1"))
    ticket_id = created["id"]
    await support_store.claim_ticket(ticket_id, support_store.ClaimRequest(), request("officer", "officer-a"))
    data = support_store._load_ticket(ticket_id)
    attachment_dir = support_store.ATTACHMENTS_DIR / ticket_id
    attachment_dir.mkdir(parents=True)
    (attachment_dir / "file-a-note.txt").write_text("n?i dung", encoding="utf-8")
    data["attachments"] = [{"id": "file-a", "name": "note.txt", "content_type": "text/plain", "size": 9, "download_url": f"/api/support/tickets/{ticket_id}/attachments/file-a/download"}]
    support_store._save_ticket(ticket_id, data)
    msg = await support_store.send_message(ticket_id, support_store.SendMessageRequest(content="C? t?p", attachment_ids=["file-a"]), request("citizen", "citizen-1"))
    assert msg["attachments"][0]["id"] == "file-a"

    with pytest.raises(HTTPException) as missing_reason:
        await support_store.get_ticket(ticket_id, request("admin", "admin-1"))
    assert missing_reason.value.status_code == 400
    detail = await support_store.get_ticket(ticket_id, request("admin", "admin-1"), reason="Ki?m tra nghi?p v?")
    assert detail["id"] == ticket_id
    assert audit[-1]["details"]["reason"] == "Ki?m tra nghi?p v?"

    closed = await support_store.close_ticket(ticket_id, support_store.CloseRequest(resolution_note="?? h??ng d?n"), request("officer", "officer-a"))
    assert closed["status"] == "closed"
    rating = await support_store.rate_ticket(ticket_id, support_store.RatingRequest(rating=5, feedback="R? r?ng"), request("citizen", "citizen-1"))
    assert rating["rating"] == 5


@pytest.mark.asyncio
async def test_queue_realtime_event_is_anonymised_until_claim(support_store, monkeypatch):
    monkeypatch.setattr(
        support_store,
        "get_user_profile",
        lambda _user_id: asyncio.sleep(0, result={"allowed_domains": ["ho_tich_chung_thuc"]}),
    )

    class Socket:
        def __init__(self):
            self.events = []
        async def send_json(self, event):
            self.events.append(event)

    queue_socket = Socket()
    support_store.hub.queue_watchers.setdefault("ho_tich_chung_thuc", set()).add(queue_socket)
    created = await support_store.create_ticket(
        support_store.CreateTicketRequest(question="Noi dung rieng tu cua cong dan", domain="ho_tich_chung_thuc"),
        request("citizen", "citizen-private"),
    )
    event = next(item for item in queue_socket.events if item["type"] == "queue.created")
    assert event["ticket"]["citizen_id"] == ""
    assert event["ticket"]["question"] != "Noi dung rieng tu cua cong dan"
    await support_store.claim_ticket(created["id"], support_store.ClaimRequest(), request("officer", "officer-ht"))
    officer_view = await support_store.get_ticket(created["id"], request("officer", "officer-ht"))
    assert officer_view["question"] == "Noi dung rieng tu cua cong dan"


@pytest.mark.asyncio
async def test_admin_chat_access_fails_closed_when_audit_write_fails(support_store, monkeypatch):
    async def unavailable_audit(**_kwargs):
        raise RuntimeError("audit backend unavailable")

    monkeypatch.setattr(support_store, "write_audit_log", unavailable_audit)
    created = await support_store.create_ticket(
        support_store.CreateTicketRequest(question="Can ho tro ho tich", domain="ho_tich_chung_thuc"),
        request("citizen", "citizen-1"),
    )
    with pytest.raises(HTTPException) as denied:
        await support_store.get_ticket(created["id"], request("admin", "admin-1"), reason="Kiem tra nghiep vu")
    assert denied.value.status_code == 503


@pytest.mark.asyncio
async def test_admin_cannot_join_or_send_messages_in_citizen_officer_chat(support_store, monkeypatch):
    monkeypatch.setattr(
        support_store,
        "get_user_profile",
        lambda _id: asyncio.sleep(0, result={"allowed_domains": ["ho_tich_chung_thuc"]}),
    )
    created = await support_store.create_ticket(
        support_store.CreateTicketRequest(question="Can ho tro dang ky khai sinh", domain="ho_tich_chung_thuc"),
        request("citizen", "citizen-1"),
    )
    await support_store.claim_ticket(
        created["id"], support_store.ClaimRequest(), request("officer", "officer-ht")
    )

    with pytest.raises(HTTPException) as denied:
        await support_store.send_message(
            created["id"],
            support_store.SendMessageRequest(content="Admin must not join this chat"),
            request("admin", "admin-1"),
        )
    assert denied.value.status_code == 403


@pytest.fixture
def support_http_client(support_store, monkeypatch):
    async def profile(user_id):
        domains = {
            "officer-ht": ["ho_tich_chung_thuc"],
            "officer-land": ["dat_dai_xay_dung"],
        }
        return {"allowed_domains": domains.get(user_id, [])}

    async def real_users_exist():
        return True

    async def session_for_token(token: str):
        identities = {
            "officer-token": {"user": {"id": "officer-ht", "username": "officer-ht"}, "role": "officer"},
            "citizen-token": {"user": {"id": "citizen-ws", "username": "citizen-ws"}, "role": "citizen"},
        }
        return identities.get(token)

    monkeypatch.setattr(support_store, "get_user_profile", profile)
    monkeypatch.setattr(support_store, "has_real_users", real_users_exist)
    import api.user_service as user_service
    monkeypatch.setattr(user_service, "get_user_from_session_token", session_for_token)
    app = FastAPI()

    @app.middleware("http")
    async def identity_headers(request: Request, call_next):
        request.state.user_role = request.headers.get("X-User-Role", "citizen")
        request.state.user_id = request.headers.get("X-User-Id")
        request.state.username = request.headers.get("X-User-Id")
        return await call_next(request)

    app.include_router(support_store.router, prefix="/api")
    with TestClient(app) as client:
        yield client


def _headers(role, user_id):
    return {"X-User-Role": role, "X-User-Id": user_id}


def test_http_and_websocket_realtime_delivery_and_domain_privacy(support_http_client):
    client = support_http_client
    with client.websocket_connect(
        "/api/support/ws?token=officer-token&domain=ho_tich_chung_thuc"
    ) as queue_socket:
        assert queue_socket.receive_json()["type"] == "connected"
        created = client.post(
            "/api/support/tickets",
            headers=_headers("citizen", "citizen-ws"),
            json={"question": "Private support request", "domain": "ho_tich_chung_thuc"},
        )
        assert created.status_code == 201
        ticket_id = created.json()["id"]
        queue_event = queue_socket.receive_json()
        assert queue_event["type"] == "queue.created"
        assert queue_event["ticket"]["citizen_id"] == ""
        assert queue_event["ticket"]["question"] != "Private support request"

    denied = client.get(f"/api/support/tickets/{ticket_id}", headers=_headers("officer", "officer-land"))
    assert denied.status_code == 403
    assert client.post(
        f"/api/support/tickets/{ticket_id}/claim",
        headers=_headers("officer", "officer-ht"),
        json={},
    ).status_code == 200

    with client.websocket_connect(
        f"/api/support/ws?token=citizen-token&ticket_id={ticket_id}"
    ) as citizen_socket, client.websocket_connect(
        f"/api/support/ws?token=officer-token&ticket_id={ticket_id}"
    ) as officer_socket:
        assert citizen_socket.receive_json()["type"] == "connected"
        assert officer_socket.receive_json()["type"] == "connected"
        response = client.post(
            f"/api/support/tickets/{ticket_id}/messages",
            headers=_headers("officer", "officer-ht"),
            json={"content": "Officer reply", "attachment_ids": []},
        )
        assert response.status_code == 201
        citizen_event = citizen_socket.receive_json()
        assert citizen_event["type"] == "message.created"
        assert citizen_event["message"]["content"] == "Officer reply"

    citizen_list = client.get("/api/support/tickets", headers=_headers("citizen", "citizen-ws"))
    assert citizen_list.status_code == 200
    assert citizen_list.json()[0]["unread_count"] == 1



def test_live_support_uses_exactly_the_five_staffed_domains():
    from api.routers.live_support import SUPPORT_DOMAINS

    assert SUPPORT_DOMAINS == {
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "an_sinh_y_te_giao_duc",
        "hanh_chinh_cong",
        "trat_tu_do_thi",
    }
