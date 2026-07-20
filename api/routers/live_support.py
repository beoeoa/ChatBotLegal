"""Live support: domain-routed human assistance with realtime delivery.

The file-backed store is deliberate for local pilot deployment.  Every access
check is server-side; queue previews omit citizen identifiers until an officer
claims a session.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id, get_request_username
from api.user_service import get_user_profile, has_real_users, write_audit_log
from api.observability import telemetry
from time import perf_counter

router = APIRouter(prefix="/support", tags=["Live Support"])
ROOT = Path(__file__).resolve().parents[2]
TICKETS_DIR = ROOT / "data" / "support_tickets"
ATTACHMENTS_DIR = ROOT / "data" / "support_attachments"
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_ATTACHMENT_SUFFIXES = {".txt", ".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}
# Live support follows the five staffed ward desks. Residence/security and
# complaints/denunciations are routed to the responsible canonical desk rather
# than creating unstaffed queues.
SUPPORT_DOMAINS = {
    "ho_tich_chung_thuc",
    "dat_dai_xay_dung",
    "an_sinh_y_te_giao_duc",
    "hanh_chinh_cong",
    "trat_tu_do_thi",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_user_key(request: Request) -> str:
    """Return a real account ID; shared role passwords cannot own support data."""
    user_id = get_request_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Hãy đăng nhập bằng tài khoản cá nhân để tạo hoặc xem hỗ trợ trực tuyến.",
        )
    return str(user_id)


def _ensure_dirs() -> None:
    TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def _ticket_path(ticket_id: str) -> Path:
    _ensure_dirs()
    return TICKETS_DIR / f"{ticket_id}.json"


def _load_ticket(ticket_id: str) -> dict[str, Any] | None:
    path = _ticket_path(ticket_id)
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_ticket(ticket_id: str, value: dict[str, Any]) -> None:
    _ticket_path(ticket_id).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_all_tickets() -> list[dict[str, Any]]:
    _ensure_dirs()
    result: list[dict[str, Any]] = []
    for path in TICKETS_DIR.glob("*.json"):
        try:
            result.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(result, key=lambda item: item.get("updated_at", ""), reverse=True)


def _safe_filename(value: str) -> str:
    name = Path(value or "attachment").name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name[:120] or "attachment"


def _normalize_status(value: str | None) -> str:
    return {"open": "waiting", "answered": "active"}.get(str(value or "waiting"), str(value or "waiting"))


def _ticket_summary(
    data: dict[str, Any],
    *,
    reveal_citizen: bool = True,
    reveal_content: bool = True,
    current_user_id: str | None = None,
) -> dict[str, Any]:
    # Waiting-queue previews deliberately withhold citizen identity and text.
    # An officer can see the actual conversation only after a valid claim.
    return {
        "id": str(data["id"]), "citizen_id": str(data.get("citizen_id") or "") if reveal_citizen else "",
        "question": str(data.get("question") or "") if reveal_content else "Yêu cầu hỗ trợ đang chờ tiếp nhận.", "domain": data.get("domain") or data.get("assigned_department"),
        "assigned_department": data.get("domain") or data.get("assigned_department"),
        "status": _normalize_status(data.get("status")), "priority": data.get("priority") or "normal",
        "assigned_officer_id": data.get("assigned_officer_id"), "message_count": len(data.get("messages") or []),
        "unread_count": int((data.get("unread_counts") or {}).get(str(current_user_id or ""), 0)),
        "created_at": data.get("created_at") or "", "updated_at": data.get("updated_at") or "",
        "transfer_reason": data.get("transfer_reason"), "escalated": bool(data.get("escalated")),
    }


class SupportAttachment(BaseModel):
    id: str
    name: str
    content_type: str = "application/octet-stream"
    size: int
    download_url: str


class SupportMessage(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    ticket_id: str = ""
    sender_id: str = ""
    sender_role: str = "citizen"
    content: str = ""
    attachments: list[SupportAttachment] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


class CreateTicketRequest(BaseModel):
    question: str = Field(min_length=5, max_length=6000)
    domain: str = Field(min_length=2, max_length=100)
    ai_summary: str | None = Field(default=None, max_length=2000)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=6000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)


class ClaimRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class DeclineRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)
    transfer_domain: str | None = Field(default=None, max_length=100)


class ReassignRequest(BaseModel):
    officer_id: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)
    escalate: bool = False


class CloseRequest(BaseModel):
    resolution_note: str = Field(default="", max_length=2000)


class RatingRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    feedback: str = Field(default="", max_length=1000)


async def _can_receive_live_support(user_id: str) -> bool:
    profile = await get_user_profile(user_id)
    if not profile:
        return False
    return (profile.get("preferences") or {}).get("can_receive_live_support") is not False


async def _officer_domains(user_id: str) -> list[str]:
    profile = await get_user_profile(user_id)
    if not profile:
        return []
    return list(profile.get("allowed_domains") or profile.get("profile", {}).get("allowed_domains") or [])


def _domain_allowed(domain: str | None, allowed: list[str]) -> bool:
    if not domain:
        return False
    if domain in allowed:
        return True
    domain_parts = set(str(domain).split("_"))
    return any(domain_parts.intersection(str(item).split("_")) for item in allowed)


async def _can_view_queue_item(data: dict[str, Any], user_id: str, role: str) -> bool:
    if role == "admin":
        return True
    if role != "officer":
        return False
    if not await _can_receive_live_support(user_id):
        return False
    if str(data.get("assigned_officer_id") or "") == user_id:
        return True
    return _normalize_status(data.get("status")) == "waiting" and _domain_allowed(data.get("domain"), await _officer_domains(user_id))


async def _require_audit_log(*, action: str, ticket_id: str, actor_user_id: str | None, actor_role: str, reason: str, request: Request | None = None) -> None:
    """Fail closed when an admin action cannot be recorded for audit."""
    try:
        await write_audit_log(
            action=action,
            entity_type="support_session",
            entity_id=ticket_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            details={"reason": reason},
            request=request,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Không thể ghi audit nên chưa mở hoặc thay đổi phiên hỗ trợ.",
        ) from exc


async def _assert_chat_access(data: dict[str, Any], user_id: str, role: str) -> None:
    if role == "citizen" and str(data.get("citizen_id")) == user_id:
        return
    if role == "officer" and str(data.get("assigned_officer_id") or "") == user_id:
        return
    raise HTTPException(status_code=403, detail="Bạn không có quyền truy cập phiên hỗ trợ này.")


class _Hub:
    def __init__(self) -> None:
        self.sessions: dict[str, set[WebSocket]] = {}
        self.queue_watchers: dict[str, set[WebSocket]] = {}
        self.user_sockets: dict[str, set[WebSocket]] = {}

    async def join_user(self, user_id: str, socket: WebSocket) -> None:
        self.user_sockets.setdefault(user_id, set()).add(socket)

    async def join_session(self, ticket_id: str, socket: WebSocket) -> None:
        self.sessions.setdefault(ticket_id, set()).add(socket)

    def is_user_online(self, user_id: str) -> bool:
        return bool(self.user_sockets.get(user_id, set()))

    async def leave(self, ticket_id: str | None, domain: str | None, user_id: str, socket: WebSocket) -> None:
        if ticket_id:
            self.sessions.get(ticket_id, set()).discard(socket)
        if domain:
            self.queue_watchers.get(domain, set()).discard(socket)
        self.user_sockets.get(user_id, set()).discard(socket)

    async def broadcast_session(self, ticket_id: str, event: dict[str, Any]) -> None:
        for socket in list(self.sessions.get(ticket_id, set())):
            try:
                await socket.send_json(event)
            except Exception:
                self.sessions.get(ticket_id, set()).discard(socket)

    async def broadcast_queue(self, domain: str | None, event: dict[str, Any]) -> None:
        if not domain:
            return
        for socket in list(self.queue_watchers.get(domain, set())):
            try:
                await socket.send_json(event)
            except Exception:
                self.queue_watchers.get(domain, set()).discard(socket)


hub = _Hub()


async def _event(data: dict[str, Any], kind: str, *, payload: dict[str, Any] | None = None) -> None:
    """Notify an active session and its matching domain queue without leaking citizen data.

    Queue subscribers have not claimed the ticket yet, so they receive only an
    anonymised summary. The full summary/message remains limited to the guarded
    ticket WebSocket channel.
    """
    started = perf_counter()
    event = {"type": kind, "ticket_id": data["id"], "ticket": _ticket_summary(data), **(payload or {})}
    await hub.broadcast_session(str(data["id"]), event)
    queue_event = {"type": kind, "ticket_id": data["id"], "ticket": _ticket_summary(data, reveal_citizen=False, reveal_content=False)}
    await hub.broadcast_queue(data.get("domain"), queue_event)
    telemetry.record_operation(
        category="support_delivery", route="websocket:support_delivery",
        duration_ms=(perf_counter() - started) * 1000,
        metadata={"outcome": "success"},
    )


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def create_ticket(body: CreateTicketRequest, request: Request) -> dict[str, Any]:
    role = get_request_role(request)
    if role != "citizen":
        raise HTTPException(status_code=403, detail="Chỉ người dân có thể tạo yêu cầu hỗ trợ.")
    if body.domain not in SUPPORT_DOMAINS:
        raise HTTPException(status_code=422, detail="Lĩnh vực hỗ trợ không hợp lệ.")
    citizen_id = _resolve_user_key(request)
    ticket_id = uuid.uuid4().hex[:12]
    message = SupportMessage(ticket_id=ticket_id, sender_id=citizen_id, sender_role="citizen", content=body.question)
    data = {
        "id": ticket_id, "citizen_id": citizen_id, "domain": body.domain,
        "question": body.question, "ai_summary": body.ai_summary, "priority": body.priority,
        "status": "waiting", "assigned_officer_id": None, "messages": [message.model_dump()],
        "events": [{"type": "created", "at": _now(), "actor": citizen_id}],
        "attachments": [], "unread_counts": {}, "created_at": _now(), "updated_at": _now(),
        "rating": None, "feedback": "", "resolution_note": "", "escalated": False,
    }
    _save_ticket(ticket_id, data)
    await _event(data, "queue.created")
    return {**_ticket_summary(data), "messages": data["messages"], "notice": "Yêu cầu này sẽ được cán bộ phụ trách tiếp nhận; đây không phải chatbot."}


@router.get("/tickets")
async def list_tickets(request: Request, status_filter: str | None = Query(default=None, alias="status"), domain: str | None = None, limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    user_id, role = _resolve_user_key(request), get_request_role(request)
    rows: list[dict[str, Any]] = []
    for data in _list_all_tickets():
        state = _normalize_status(data.get("status"))
        if status_filter and state != status_filter:
            continue
        if domain and data.get("domain") != domain:
            continue
        if role == "citizen" and str(data.get("citizen_id")) != user_id:
            continue
        if role == "officer" and not await _can_view_queue_item(data, user_id, role):
            continue
        assigned_to_me = str(data.get("assigned_officer_id") or "") == user_id
        reveal = role != "officer" or assigned_to_me
        rows.append(_ticket_summary(
            data,
            reveal_citizen=reveal,
            reveal_content=reveal,
            current_user_id=user_id,
        ))
    return rows[:limit]


@router.get("/my-domains")
async def my_support_domains(request: Request) -> list[str]:
    """Domains eligible for the authenticated officer's realtime queue."""
    role = get_request_role(request)
    if role == "admin":
        return sorted(SUPPORT_DOMAINS)
    if role != "officer":
        raise HTTPException(status_code=403, detail="Chỉ cán bộ hoặc admin xem được lĩnh vực hỗ trợ.")
    profile = await get_user_profile(_resolve_user_key(request))
    preferences = (profile or {}).get("preferences") or {}
    if preferences.get("can_receive_live_support") is False:
        return []
    return [item for item in await _officer_domains(_resolve_user_key(request)) if item in SUPPORT_DOMAINS]


@router.get("/queue")
async def list_queue(request: Request, domain: str | None = None) -> list[dict[str, Any]]:
    user_id, role = _resolve_user_key(request), get_request_role(request)
    if role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="Chỉ cán bộ hoặc admin xem được hàng chờ.")
    rows = []
    for data in _list_all_tickets():
        if _normalize_status(data.get("status")) != "waiting":
            continue
        if domain and data.get("domain") != domain:
            continue
        if not await _can_view_queue_item(data, user_id, role):
            continue
        rows.append(_ticket_summary(
            data,
            reveal_citizen=False,
            reveal_content=False,
            current_user_id=user_id,
        ))
    return rows


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, request: Request, reason: str | None = None) -> dict[str, Any]:
    data = _load_ticket(ticket_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    user_id, role = _resolve_user_key(request), get_request_role(request)
    if role == "admin":
        if not (reason or "").strip():
            raise HTTPException(status_code=400, detail="Admin phải nhập lý do nghiệp vụ trước khi xem nội dung chat.")
        await _require_audit_log(action="support.chat.view", ticket_id=ticket_id, actor_user_id=get_request_user_id(request), actor_role=role, reason=reason.strip(), request=request)
    else:
        await _assert_chat_access(data, user_id, role)
    if role != "admin":
        unread = dict(data.get("unread_counts") or {})
        if unread.get(user_id):
            unread[user_id] = 0
            data["unread_counts"] = unread
            _save_ticket(ticket_id, data)
    return {**_ticket_summary(data, current_user_id=user_id), "ai_summary": data.get("ai_summary"), "messages": data.get("messages") or [], "attachments": data.get("attachments") or [], "rating": data.get("rating"), "feedback": data.get("feedback"), "resolution_note": data.get("resolution_note")}


@router.get("/tickets/{ticket_id}/messages")
async def get_messages(ticket_id: str, request: Request, reason: str | None = None) -> list[dict[str, Any]]:
    data = _load_ticket(ticket_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    user_id, role = _resolve_user_key(request), get_request_role(request)
    if role == "admin":
        if not (reason or "").strip():
            raise HTTPException(status_code=400, detail="Admin phải nhập lý do nghiệp vụ trước khi xem nội dung chat.")
        await _require_audit_log(action="support.chat.messages.view", ticket_id=ticket_id, actor_user_id=get_request_user_id(request), actor_role=role, reason=reason.strip(), request=request)
    else:
        await _assert_chat_access(data, user_id, role)
    unread = dict(data.get("unread_counts") or {})
    unread[user_id] = 0
    data["unread_counts"] = unread
    _save_ticket(ticket_id, data)
    return data.get("messages") or []


@router.post("/tickets/{ticket_id}/claim")
async def claim_ticket(ticket_id: str, body: ClaimRequest, request: Request) -> dict[str, Any]:
    user_id, role = _resolve_user_key(request), get_request_role(request)
    if role != "officer":
        raise HTTPException(status_code=403, detail="Chỉ cán bộ có thể tiếp nhận phiên.")
    data = _load_ticket(ticket_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    if _normalize_status(data.get("status")) != "waiting" or data.get("assigned_officer_id"):
        raise HTTPException(status_code=409, detail="Phiên đã được cán bộ khác tiếp nhận hoặc đã đóng.")
    if not await _can_receive_live_support(user_id) or not _domain_allowed(data.get("domain"), await _officer_domains(user_id)):
        raise HTTPException(status_code=403, detail="Bạn không được phân quyền lĩnh vực của yêu cầu này.")
    data["assigned_officer_id"], data["status"], data["updated_at"] = user_id, "assigned", _now()
    data.setdefault("events", []).append({"type": "claimed", "at": _now(), "actor": user_id, "note": body.note})
    _save_ticket(ticket_id, data)
    await _event(data, "ticket.claimed")
    return _ticket_summary(data)


@router.post("/tickets/{ticket_id}/decline")
async def decline_ticket(ticket_id: str, body: DeclineRequest, request: Request) -> dict[str, Any]:
    user_id, role = _resolve_user_key(request), get_request_role(request)
    if role != "officer":
        raise HTTPException(status_code=403, detail="Chỉ cán bộ có thể từ chối hoặc chuyển phiên.")
    data = _load_ticket(ticket_id)
    if not data or not await _can_view_queue_item(data, user_id, role):
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu phù hợp trong hàng chờ.")
    target = body.transfer_domain or data.get("domain")
    if target not in SUPPORT_DOMAINS:
        raise HTTPException(status_code=422, detail="Lĩnh vực chuyển tiếp không hợp lệ.")
    data.update({"domain": target, "assigned_officer_id": None, "status": "waiting", "transfer_reason": body.reason, "updated_at": _now()})
    data.setdefault("events", []).append({"type": "declined_or_transferred", "at": _now(), "actor": user_id, "reason": body.reason, "domain": target})
    _save_ticket(ticket_id, data)
    await _event(data, "ticket.transferred")
    return _ticket_summary(data, reveal_citizen=False)


@router.patch("/tickets/{ticket_id}/assign")
async def reassign_ticket(ticket_id: str, body: ReassignRequest, request: Request) -> dict[str, Any]:
    if get_request_role(request) != "admin":
        raise HTTPException(status_code=403, detail="Chỉ admin có thể phân công hoặc nâng cấp xử lý.")
    data = _load_ticket(ticket_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    target_domain = body.domain or data.get("domain")
    if target_domain not in SUPPORT_DOMAINS:
        raise HTTPException(status_code=422, detail="Lĩnh vực chuyển tiếp không hợp lệ.")
    data.update({"domain": target_domain, "assigned_officer_id": body.officer_id, "status": "assigned" if body.officer_id else "waiting", "escalated": body.escalate, "transfer_reason": body.reason, "updated_at": _now()})
    _save_ticket(ticket_id, data)
    await _require_audit_log(action="support.ticket.reassign", ticket_id=ticket_id, actor_user_id=get_request_user_id(request), actor_role="admin", reason=body.reason, request=request)
    await _event(data, "ticket.reassigned")
    return _ticket_summary(data)


@router.post("/tickets/{ticket_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(ticket_id: str, body: SendMessageRequest, request: Request) -> dict[str, Any]:
    data = _load_ticket(ticket_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    user_id, role = _resolve_user_key(request), get_request_role(request)
    await _assert_chat_access(data, user_id, role)
    if _normalize_status(data.get("status")) == "closed":
        raise HTTPException(status_code=409, detail="Phiên hỗ trợ đã đóng.")
    attachment_ids = set(body.attachment_ids)
    attachment_map = {item.get("id"): item for item in data.get("attachments") or []}
    if not attachment_ids.issubset(attachment_map):
        raise HTTPException(status_code=422, detail="Có tệp đính kèm không thuộc phiên hỗ trợ này.")
    msg = SupportMessage(ticket_id=ticket_id, sender_id=user_id, sender_role=role, content=body.content.strip(), attachments=[SupportAttachment(**attachment_map[key]) for key in attachment_ids])
    data.setdefault("messages", []).append(msg.model_dump())
    data["status"] = "active" if data.get("assigned_officer_id") else "waiting"
    data["updated_at"] = _now()
    recipient = data.get("assigned_officer_id") if role == "citizen" else data.get("citizen_id")
    unread = dict(data.get("unread_counts") or {})
    if recipient:
        unread[str(recipient)] = int(unread.get(str(recipient), 0)) + 1
    data["unread_counts"] = unread
    _save_ticket(ticket_id, data)
    await _event(data, "message.created", payload={"message": msg.model_dump()})
    return msg.model_dump()


@router.post("/tickets/{ticket_id}/attachments", status_code=status.HTTP_201_CREATED)
async def upload_attachment(ticket_id: str, request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    data = _load_ticket(ticket_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    await _assert_chat_access(data, _resolve_user_key(request), get_request_role(request))
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
        raise HTTPException(status_code=415, detail="Định dạng tệp không được hỗ trợ cho hỗ trợ trực tuyến.")
    payload = await file.read(MAX_ATTACHMENT_BYTES + 1)
    if not payload or len(payload) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="Tệp trống hoặc vượt quá dung lượng 10 MB.")
    attachment_id = uuid.uuid4().hex[:12]
    dest_dir = ATTACHMENTS_DIR / ticket_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_filename(file.filename or f"attachment{suffix}")
    stored = dest_dir / f"{attachment_id}-{name}"
    stored.write_bytes(payload)
    item = SupportAttachment(id=attachment_id, name=name, content_type=file.content_type or "application/octet-stream", size=len(payload), download_url=f"/api/support/tickets/{ticket_id}/attachments/{attachment_id}/download")
    data.setdefault("attachments", []).append(item.model_dump())
    data["updated_at"] = _now()
    _save_ticket(ticket_id, data)
    return item.model_dump()


@router.get("/tickets/{ticket_id}/attachments/{attachment_id}/download")
async def download_attachment(
    ticket_id: str,
    attachment_id: str,
    request: Request,
    reason: str | None = Query(default=None),
):
    from fastapi.responses import FileResponse
    data = _load_ticket(ticket_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    user_id, role = _resolve_user_key(request), get_request_role(request)
    if role == "admin":
        if not (reason or "").strip():
            raise HTTPException(status_code=400, detail="Admin phải nhập lý do nghiệp vụ trước khi tải tệp đính kèm.")
        await _require_audit_log(
            action="support.attachment.download",
            ticket_id=ticket_id,
            actor_user_id=get_request_user_id(request),
            actor_role=role,
            reason=reason.strip(),
            request=request,
        )
    else:
        await _assert_chat_access(data, user_id, role)
    item = next((x for x in data.get("attachments") or [] if x.get("id") == attachment_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="Không tìm thấy tệp.")
    matches = list((ATTACHMENTS_DIR / ticket_id).glob(f"{attachment_id}-*"))
    if not matches or not matches[0].is_file():
        raise HTTPException(status_code=404, detail="Tệp không còn khả dụng.")
    return FileResponse(matches[0], media_type=item.get("content_type") or "application/octet-stream", filename=item.get("name") or "attachment")


@router.patch("/tickets/{ticket_id}/close")
async def close_ticket(ticket_id: str, body: CloseRequest | None = None, request: Request = None) -> dict[str, Any]:
    data = _load_ticket(ticket_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    user_id, role = _resolve_user_key(request), get_request_role(request)
    await _assert_chat_access(data, user_id, role)
    data.update({"status": "closed", "closed_at": _now(), "resolution_note": (body.resolution_note if body else ""), "updated_at": _now()})
    _save_ticket(ticket_id, data)
    await _event(data, "ticket.closed")
    return _ticket_summary(data)


@router.post("/tickets/{ticket_id}/rating")
async def rate_ticket(ticket_id: str, body: RatingRequest, request: Request) -> dict[str, Any]:
    data = _load_ticket(ticket_id)
    if not data:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    if get_request_role(request) != "citizen" or str(data.get("citizen_id")) != _resolve_user_key(request):
        raise HTTPException(status_code=403, detail="Chỉ người dân tạo phiên mới có thể đánh giá.")
    if _normalize_status(data.get("status")) != "closed":
        raise HTTPException(status_code=409, detail="Chỉ có thể đánh giá sau khi phiên đã đóng.")
    data.update({"rating": body.rating, "feedback": body.feedback, "rated_at": _now(), "updated_at": _now()})
    _save_ticket(ticket_id, data)
    await _event(data, "ticket.rated")
    return {"rating": body.rating, "feedback": body.feedback}


async def _ws_identity(websocket: WebSocket) -> tuple[str, str] | None:
    """Authenticate live support with an individual account session only.

    Query-string roles/IDs in the legacy shared-password mode cannot prove a
    person owns a ticket.  Realtime support therefore fails closed until the
    system has real account users and the client presents a valid session token.
    """
    if not await has_real_users():
        return None
    token = websocket.query_params.get("token") or ""
    from api.user_service import get_user_from_session_token
    session = await get_user_from_session_token(token)
    if not session:
        return None
    return str(session["user"]["id"]), str(session["role"])


@router.websocket("/ws")
async def support_websocket(websocket: WebSocket) -> None:
    identity = await _ws_identity(websocket)
    if not identity:
        await websocket.close(code=4401)
        return
    user_id, role = identity
    # Realtime chat is strictly citizen <-> assigned officer. Admin manages
    # legal candidates and accounts but is never a chat participant.
    if role not in {"citizen", "officer"}:
        await websocket.close(code=4403)
        return
    ticket_id = websocket.query_params.get("ticket_id")
    domain = websocket.query_params.get("domain")
    if ticket_id:
        data = _load_ticket(ticket_id)
        if not data:
            await websocket.close(code=4404)
            return
        try:
            await _assert_chat_access(data, user_id, role)
        except HTTPException:
            await websocket.close(code=4403)
            return
    elif domain:
        if role == "officer" and not _domain_allowed(domain, await _officer_domains(user_id)):
            await websocket.close(code=4403)
            return
        if role != "officer":
            await websocket.close(code=4403)
            return
    else:
        await websocket.close(code=4400)
        return
    await websocket.accept()
    await hub.join_user(user_id, websocket)
    if ticket_id:
        await hub.join_session(ticket_id, websocket)
    else:
        hub.queue_watchers.setdefault(domain, set()).add(websocket)
    try:
        await websocket.send_json({"type": "connected", "ticket_id": ticket_id, "domain": domain})
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") == "typing" and ticket_id:
                await hub.broadcast_session(ticket_id, {"type": "typing", "ticket_id": ticket_id, "sender_id": user_id, "is_typing": bool(payload.get("is_typing"))})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(ticket_id, domain, user_id, websocket)
