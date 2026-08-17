"""Live support: domain-routed human assistance with realtime delivery.

The file-backed store is deliberate for local pilot deployment.  Every access
check is server-side; queue previews omit citizen identifiers until an officer
claims a session.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from api.auth import (
    get_request_role,
    get_request_user_id,
    get_request_username,
    production_mode_enabled,
)
from api.user_service import get_user_profile, has_real_users, write_audit_log
from api.observability import telemetry
from api.upload_security import (
    SUPPORT_ATTACHMENT_POLICY,
    UploadSecurityError,
    validate_upload,
    write_validated_upload,
)
from api.support_allocator import SupportAllocator
from api.support_repository import (
    PostgresSupportRepository,
    SupportAccessError,
    SupportActor,
    SupportConflictError,
    SupportNotFoundError,
    SupportRepositoryError,
    SupportStateError,
    SupportTicketRecord,
    TicketStatus,
)
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
WS_AUTH_TICKET_TTL_SECONDS = 60
_ws_auth_tickets: dict[str, dict[str, Any]] = {}
_ws_auth_ticket_lock = threading.Lock()
_canonical_repository_override: Any | None = None
_canonical_repository_singleton: PostgresSupportRepository | None = None
_canonical_allocator_singleton: SupportAllocator | None = None


def configure_canonical_support_repository(repository: Any | None) -> None:
    """Inject the canonical repository for isolated tests; production uses env."""

    global _canonical_repository_override, _canonical_allocator_singleton
    _canonical_repository_override = repository
    _canonical_allocator_singleton = None


def _canonical_support_repository() -> Any | None:
    global _canonical_repository_singleton
    if _canonical_repository_override is not None:
        return _canonical_repository_override
    if str(os.getenv("FEATURE018_SUPPORT_MODE") or "legacy_json").strip() != "postgres_active":
        return None
    database_url = str(os.getenv("FEATURE018_DATABASE_URL") or "").strip()
    if not database_url:
        raise RuntimeError("FEATURE018_DATABASE_URL_required_for_postgres_active")
    if _canonical_repository_singleton is None:
        _canonical_repository_singleton = PostgresSupportRepository(database_url)
    return _canonical_repository_singleton


def _canonical_support_allocator() -> SupportAllocator | None:
    global _canonical_allocator_singleton
    repository = _canonical_support_repository()
    if repository is None:
        return None
    if _canonical_allocator_singleton is None or _canonical_allocator_singleton.repository is not repository:
        _canonical_allocator_singleton = SupportAllocator(repository)
    return _canonical_allocator_singleton


def _canonical_ticket_payload(ticket: SupportTicketRecord, *, include_content: bool = True) -> dict[str, Any]:
    payload = {
        "id": ticket.id,
        "domain": ticket.canonical_domain,
        "assigned_department": ticket.canonical_domain,
        "status": ticket.status.value,
        "priority": ticket.priority,
        "assigned_officer_id": ticket.assigned_officer_id,
        "assignment_generation": ticket.assignment_generation,
        "version": ticket.version,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }
    if include_content:
        payload["question"] = ticket.question_summary
    return payload


def _canonical_http_error(exc: SupportRepositoryError) -> HTTPException:
    if isinstance(exc, SupportNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, SupportAccessError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, SupportConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, SupportStateError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="support_repository_error")


def _ws_ticket_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_ws_auth_ticket(
    *,
    user_id: str,
    role: str,
    ticket_id: str | None,
    domain: str | None,
    now: float | None = None,
) -> str:
    issued_at = perf_counter() if now is None else now
    token = secrets.token_urlsafe(32)
    key = _ws_ticket_key(token)
    with _ws_auth_ticket_lock:
        expired = [
            item_key
            for item_key, item in _ws_auth_tickets.items()
            if float(item["expires_at"]) <= issued_at
        ]
        for item_key in expired:
            _ws_auth_tickets.pop(item_key, None)
        _ws_auth_tickets[key] = {
            "user_id": user_id,
            "role": role,
            "ticket_id": ticket_id,
            "domain": domain,
            "expires_at": issued_at + WS_AUTH_TICKET_TTL_SECONDS,
        }
    return token


def _consume_ws_auth_ticket(
    token: str,
    *,
    ticket_id: str | None,
    domain: str | None,
    now: float | None = None,
) -> tuple[str, str] | None:
    checked_at = perf_counter() if now is None else now
    with _ws_auth_ticket_lock:
        item = _ws_auth_tickets.pop(_ws_ticket_key(token), None)
    if not item or float(item["expires_at"]) <= checked_at:
        return None
    if item.get("ticket_id") != ticket_id or item.get("domain") != domain:
        return None
    return str(item["user_id"]), str(item["role"])


def _legacy_ws_query_token_allowed() -> bool:
    return not production_mode_enabled()


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
    sha256: str | None = None


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


class WebSocketTicketRequest(BaseModel):
    ticket_id: str | None = Field(default=None, max_length=200)
    domain: str | None = Field(default=None, max_length=120)
    officer_queue: bool = False


class OfficerPresenceRequest(BaseModel):
    domains: list[str] = Field(default_factory=list, max_length=5)
    max_capacity: int = Field(default=3, ge=1, le=3)


class ResolveTicketRequest(BaseModel):
    resolution_note: str = Field(default="", max_length=2000)


class AdminContentAccessRequest(BaseModel):
    reason: str = Field(min_length=8, max_length=1000)


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


async def _canonical_actor(request: Request, *, reason: str | None = None) -> SupportActor:
    user_id = _resolve_user_key(request)
    role = str(get_request_role(request) or "")
    domains: tuple[str, ...] = ()
    if role == "officer":
        domains = tuple(await _officer_domains(user_id))
    return SupportActor(
        user_id=user_id,
        role=role,
        domains=domains,
        access_reason=reason,
    )


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
        self.officer_queue_sockets: dict[str, WebSocket] = {}

    async def join_user(self, user_id: str, socket: WebSocket) -> None:
        self.user_sockets.setdefault(user_id, set()).add(socket)

    async def join_session(self, ticket_id: str, socket: WebSocket) -> None:
        self.sessions.setdefault(ticket_id, set()).add(socket)

    async def join_officer_queue(
        self,
        officer_user_id: str,
        domains: list[str] | tuple[str, ...],
        socket: WebSocket,
    ) -> None:
        previous = self.officer_queue_sockets.get(officer_user_id)
        if previous is not None and previous is not socket:
            try:
                await previous.close(code=4001, reason="Replaced by a newer officer queue stream")
            except Exception:
                pass
            for watchers in self.queue_watchers.values():
                watchers.discard(previous)
        self.officer_queue_sockets[officer_user_id] = socket
        for domain in domains:
            self.queue_watchers.setdefault(domain, set()).add(socket)

    def is_user_online(self, user_id: str) -> bool:
        return bool(self.user_sockets.get(user_id, set()))

    async def leave(self, ticket_id: str | None, domain: str | None, user_id: str, socket: WebSocket) -> None:
        if ticket_id:
            self.sessions.get(ticket_id, set()).discard(socket)
        if domain:
            self.queue_watchers.get(domain, set()).discard(socket)
        if self.officer_queue_sockets.get(user_id) is socket:
            self.officer_queue_sockets.pop(user_id, None)
            for watchers in self.queue_watchers.values():
                watchers.discard(socket)
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


async def _canonical_queue_event(ticket: SupportTicketRecord, kind: str) -> None:
    """Broadcast queue changes without citizen identity or question content."""

    await hub.broadcast_queue(
        ticket.canonical_domain,
        {
            "type": kind,
            "ticket_id": ticket.id,
            "ticket": {
                "id": ticket.id,
                "canonical_domain": ticket.canonical_domain,
                "status": ticket.status.value,
                "priority": ticket.priority,
                "created_at": ticket.created_at.isoformat(),
            },
        },
    )


@router.post("/tickets", status_code=status.HTTP_201_CREATED)
async def create_ticket(body: CreateTicketRequest, request: Request) -> dict[str, Any]:
    role = get_request_role(request)
    if role != "citizen":
        raise HTTPException(status_code=403, detail="Chỉ người dân có thể tạo yêu cầu hỗ trợ.")
    if body.domain not in SUPPORT_DOMAINS:
        raise HTTPException(status_code=422, detail="Lĩnh vực hỗ trợ không hợp lệ.")
    repository = _canonical_support_repository()
    if repository is not None:
        actor = await _canonical_actor(request)
        try:
            ticket = repository.create_ticket(
                actor,
                domain=body.domain,
                question_summary=(body.ai_summary or body.question[:500]).strip(),
                question_content=body.question.strip(),
                priority=body.priority,
            )
        except SupportRepositoryError as exc:
            raise _canonical_http_error(exc) from exc
        await _canonical_queue_event(ticket, "queue.created")
        return {
            **_canonical_ticket_payload(ticket),
            "notice": "Yêu cầu sẽ được cán bộ phụ trách tiếp nhận; người đang chờ dùng trạng thái hàng đợi, không mở realtime.",
        }
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
    repository = _canonical_support_repository()
    if repository is not None:
        actor = await _canonical_actor(request)
        try:
            if actor.role == "citizen":
                tickets = repository.list_mine(actor)
                rows = [_canonical_ticket_payload(ticket) for ticket in tickets]
            elif actor.role == "officer":
                queue = repository.queue_for_officer(actor)
                rows = [item.model_dump(mode="json") for item in queue]
            else:
                raise SupportAccessError("support_list_role_not_allowed")
        except SupportRepositoryError as exc:
            raise _canonical_http_error(exc) from exc
        if status_filter:
            rows = [row for row in rows if row.get("status") == status_filter]
        if domain:
            rows = [row for row in rows if row.get("domain", row.get("canonical_domain")) == domain]
        return rows[:limit]
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


@router.get("/tickets/mine")
async def list_my_tickets(request: Request) -> list[dict[str, Any]]:
    repository = _canonical_support_repository()
    if repository is None:
        user_id = _resolve_user_key(request)
        if get_request_role(request) != "citizen":
            raise HTTPException(status_code=403, detail="Citizen role required.")
        return [
            _ticket_summary(item, current_user_id=user_id)
            for item in _list_all_tickets()
            if str(item.get("citizen_id") or "") == user_id
        ]
    actor = await _canonical_actor(request)
    try:
        return [
            _canonical_ticket_payload(ticket)
            for ticket in repository.list_mine(actor)
        ]
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc


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
    repository = _canonical_support_repository()
    if repository is not None:
        actor = await _canonical_actor(request)
        try:
            rows = [item.model_dump(mode="json") for item in repository.queue_for_officer(actor)]
        except SupportRepositoryError as exc:
            raise _canonical_http_error(exc) from exc
        return [row for row in rows if not domain or row["canonical_domain"] == domain]
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


@router.post("/officer/presence")
async def update_officer_presence(
    body: OfficerPresenceRequest,
    request: Request,
) -> dict[str, Any]:
    allocator = _canonical_support_allocator()
    if allocator is None:
        raise HTTPException(status_code=409, detail="Canonical support repository is not active.")
    actor = await _canonical_actor(request)
    requested_domains = body.domains or list(actor.domains)
    try:
        presence = allocator.heartbeat(
            actor,
            domains=requested_domains,
            max_capacity=body.max_capacity,
        )
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc
    return presence.model_dump(mode="json")


@router.get("/officer/queue")
async def canonical_officer_queue(request: Request) -> list[dict[str, Any]]:
    repository = _canonical_support_repository()
    if repository is None:
        raise HTTPException(status_code=409, detail="Canonical support repository is not active.")
    actor = await _canonical_actor(request)
    try:
        return [item.model_dump(mode="json") for item in repository.queue_for_officer(actor)]
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc


@router.post("/officer/claim-next")
async def claim_next_ticket(request: Request) -> dict[str, Any]:
    allocator = _canonical_support_allocator()
    if allocator is None:
        raise HTTPException(status_code=409, detail="Canonical support repository is not active.")
    actor = await _canonical_actor(request)
    try:
        allocation = allocator.claim_next(actor)
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc
    if allocation is None:
        return {"assignment": None, "reason": "no_eligible_ticket_or_capacity"}
    return allocation.model_dump(mode="json")


@router.post("/officer/assignments/{assignment_id}/activate")
async def activate_support_assignment(
    assignment_id: str,
    body: dict[str, str],
    request: Request,
) -> dict[str, Any]:
    allocator = _canonical_support_allocator()
    if allocator is None:
        raise HTTPException(status_code=409, detail="Canonical support repository is not active.")
    actor = await _canonical_actor(request)
    if actor.role != "officer":
        raise HTTPException(status_code=403, detail="Officer role required.")
    lease_token = str(body.get("lease_token") or "")
    if not lease_token:
        raise HTTPException(status_code=422, detail="lease_token_required")
    try:
        assignment = allocator.activate_by_id(
            assignment_id,
            lease_token,
            actor,
        )
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc
    return assignment.model_dump(mode="json")


@router.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, request: Request, reason: str | None = None) -> dict[str, Any]:
    repository = _canonical_support_repository()
    if repository is not None:
        actor = await _canonical_actor(request, reason=reason)
        try:
            if actor.role == "admin":
                repository.record_admin_content_access(
                    ticket_id,
                    actor,
                    reason=(reason or ""),
                )
                await _require_audit_log(
                    action="support.chat.view",
                    ticket_id=ticket_id,
                    actor_user_id=actor.user_id,
                    actor_role=actor.role,
                    reason=(reason or "").strip(),
                    request=request,
                )
            ticket = repository.get_ticket(ticket_id, actor, include_content=True)
            messages = repository.list_messages(ticket_id, actor)
        except SupportRepositoryError as exc:
            raise _canonical_http_error(exc) from exc
        return {
            **_canonical_ticket_payload(ticket),
            "messages": [message.model_dump(mode="json") for message in messages],
        }
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
    repository = _canonical_support_repository()
    if repository is not None:
        actor = await _canonical_actor(request, reason=reason)
        try:
            if actor.role == "admin":
                repository.record_admin_content_access(
                    ticket_id,
                    actor,
                    reason=(reason or ""),
                )
                await _require_audit_log(
                    action="support.chat.messages.view",
                    ticket_id=ticket_id,
                    actor_user_id=actor.user_id,
                    actor_role=actor.role,
                    reason=(reason or "").strip(),
                    request=request,
                )
            messages = repository.list_messages(ticket_id, actor)
        except SupportRepositoryError as exc:
            raise _canonical_http_error(exc) from exc
        return [message.model_dump(mode="json") for message in messages]
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


@router.get("/tickets/{ticket_id}/queue-status")
async def get_ticket_queue_status(ticket_id: str, request: Request) -> dict[str, Any]:
    repository = _canonical_support_repository()
    if repository is None:
        data = _load_ticket(ticket_id)
        if not data:
            raise HTTPException(status_code=404, detail="Support ticket not found.")
        user_id = _resolve_user_key(request)
        if str(data.get("citizen_id") or "") != user_id:
            raise HTTPException(status_code=403, detail="support_ticket_owner_required")
        waiting = [
            item for item in _list_all_tickets()
            if _normalize_status(item.get("status")) == "waiting"
            and item.get("domain") == data.get("domain")
        ]
        waiting.sort(key=lambda item: item.get("created_at") or "")
        ids = [str(item.get("id")) for item in waiting]
        position = ids.index(ticket_id) + 1 if ticket_id in ids else None
        return {
            "ticket_id": ticket_id,
            "status": _normalize_status(data.get("status")),
            "position": position,
            "estimated_wait_seconds": position * 120 if position else None,
        }
    actor = await _canonical_actor(request)
    try:
        return repository.queue_position(ticket_id, actor)
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc


@router.post("/tickets/{ticket_id}/cancel")
async def cancel_support_ticket(ticket_id: str, request: Request) -> dict[str, Any]:
    repository = _canonical_support_repository()
    if repository is None:
        data = _load_ticket(ticket_id)
        if not data:
            raise HTTPException(status_code=404, detail="Support ticket not found.")
        user_id = _resolve_user_key(request)
        if get_request_role(request) != "citizen" or str(data.get("citizen_id") or "") != user_id:
            raise HTTPException(status_code=403, detail="support_ticket_owner_required")
        if _normalize_status(data.get("status")) not in {"waiting", "assigned"}:
            raise HTTPException(status_code=409, detail="support_ticket_cannot_be_cancelled")
        data.update({"status": "cancelled", "cancelled_at": _now(), "updated_at": _now()})
        data.setdefault("events", []).append({"type": "cancelled", "at": _now(), "actor": user_id})
        _save_ticket(ticket_id, data)
        await _event(data, "ticket.cancelled")
        return _ticket_summary(data, current_user_id=user_id)
    actor = await _canonical_actor(request)
    try:
        current = repository.get_ticket(ticket_id, actor, include_content=False)
        cancelled = repository.transition_ticket(
            ticket_id,
            TicketStatus.CANCELLED,
            actor,
            expected_version=current.version,
            reason_code="citizen_cancelled",
        )
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc
    await _canonical_queue_event(cancelled, "ticket.cancelled")
    return _canonical_ticket_payload(cancelled, include_content=False)


@router.post("/tickets/{ticket_id}/resolve")
async def resolve_support_ticket(
    ticket_id: str,
    body: ResolveTicketRequest,
    request: Request,
) -> dict[str, Any]:
    repository = _canonical_support_repository()
    if repository is None:
        raise HTTPException(status_code=409, detail="Canonical support repository is not active.")
    actor = await _canonical_actor(request)
    try:
        current = repository.get_ticket(ticket_id, actor, include_content=False)
        if body.resolution_note.strip():
            repository.append_message(
                ticket_id,
                actor,
                f"Kết quả xử lý: {body.resolution_note.strip()}",
            )
        resolved = repository.transition_ticket(
            ticket_id,
            TicketStatus.RESOLVED,
            actor,
            expected_version=current.version,
            reason_code="officer_resolved",
        )
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc
    event = {
        "type": "ticket.resolved",
        "ticket_id": resolved.id,
        "ticket": _canonical_ticket_payload(resolved, include_content=False),
    }
    await hub.broadcast_session(resolved.id, event)
    await _canonical_queue_event(resolved, "ticket.resolved")
    return {
        **_canonical_ticket_payload(resolved, include_content=False),
        "resolution_note_recorded": bool(body.resolution_note.strip()),
    }


@router.post("/admin/tickets/{ticket_id}/view-content")
async def admin_view_support_content(
    ticket_id: str,
    body: AdminContentAccessRequest,
    request: Request,
) -> dict[str, Any]:
    repository = _canonical_support_repository()
    if repository is None:
        raise HTTPException(status_code=409, detail="Canonical support repository is not active.")
    actor = await _canonical_actor(request, reason=body.reason)
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")
    try:
        grant = repository.record_admin_content_access(
            ticket_id,
            actor,
            reason=body.reason,
        )
        ticket = repository.get_ticket(ticket_id, actor, include_content=True)
        messages = repository.list_messages(ticket_id, actor)
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc
    await _require_audit_log(
        action="support.chat.view",
        ticket_id=ticket_id,
        actor_user_id=actor.user_id,
        actor_role=actor.role,
        reason=body.reason,
        request=request,
    )
    return {
        "grant": grant,
        "ticket": _canonical_ticket_payload(ticket),
        "messages": [message.model_dump(mode="json") for message in messages],
    }


@router.get("/admin/tickets")
async def admin_support_ticket_metadata(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    domain: str | None = None,
) -> list[dict[str, Any]]:
    # Keep the callable usable in isolated service tests as well as through
    # FastAPI, which resolves ``Query`` defaults before invoking the route.
    if not isinstance(status_filter, str):
        status_filter = None
    repository = _canonical_support_repository()
    if repository is None:
        if get_request_role(request) != "admin":
            raise HTTPException(status_code=403, detail="Admin role required.")
        rows = []
        now = datetime.now(timezone.utc)
        for item in _list_all_tickets():
            normalized_status = _normalize_status(item.get("status"))
            if status_filter and normalized_status != status_filter:
                continue
            if domain and item.get("domain") != domain:
                continue
            created_at = item.get("created_at") or ""
            rows.append({
                "id": str(item.get("id")),
                "canonical_domain": item.get("domain"),
                "status": normalized_status,
                "priority": item.get("priority") or "normal",
                "assigned_officer_id": item.get("assigned_officer_id"),
                "assignment_generation": int(item.get("assignment_generation") or 0),
                "created_at": created_at,
                "updated_at": item.get("updated_at") or created_at,
                "first_response_due_at": None,
                "resolution_due_at": None,
                "overdue": False,
                "observed_at": now.isoformat(),
            })
        return rows
    actor = await _canonical_actor(request)
    try:
        return repository.admin_ticket_metadata(
            actor,
            status=status_filter,
            domain=domain,
        )
    except SupportRepositoryError as exc:
        raise _canonical_http_error(exc) from exc


@router.post("/tickets/{ticket_id}/claim")
async def claim_ticket(ticket_id: str, body: ClaimRequest, request: Request) -> dict[str, Any]:
    if _canonical_support_repository() is not None:
        raise HTTPException(
            status_code=410,
            detail="Arbitrary ticket claims are disabled; use /officer/claim-next.",
        )
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
    repository = _canonical_support_repository()
    if repository is not None:
        if body.attachment_ids:
            raise HTTPException(status_code=422, detail="Canonical attachment upload is not active in this slice.")
        actor = await _canonical_actor(request)
        try:
            message = repository.append_message(ticket_id, actor, body.content.strip())
        except SupportRepositoryError as exc:
            raise _canonical_http_error(exc) from exc
        await hub.broadcast_session(
            ticket_id,
            {
                "type": "message.created",
                "ticket_id": ticket_id,
                "message": message.model_dump(mode="json"),
            },
        )
        return message.model_dump(mode="json")
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
    payload = await file.read(MAX_ATTACHMENT_BYTES + 1)
    try:
        validated = validate_upload(
            filename=file.filename or "",
            content=payload,
            policy=SUPPORT_ATTACHMENT_POLICY,
        )
    except UploadSecurityError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    attachment_id = uuid.uuid4().hex[:12]
    dest_dir = ATTACHMENTS_DIR / ticket_id
    name = _safe_filename(validated.filename)
    stored = dest_dir / f"{attachment_id}-{name}"
    write_validated_upload(root=dest_dir, storage_name=stored.name, content=payload)
    item = SupportAttachment(
        id=attachment_id,
        name=name,
        content_type=validated.detected_type,
        size=len(payload),
        download_url=f"/api/support/tickets/{ticket_id}/attachments/{attachment_id}/download",
        sha256=validated.sha256,
    )
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


@router.post("/ws-ticket")
async def issue_websocket_ticket(
    body: WebSocketTicketRequest,
    request: Request,
) -> dict[str, Any]:
    if sum((bool(body.ticket_id), bool(body.domain), bool(body.officer_queue))) != 1:
        raise HTTPException(
            status_code=400,
            detail="Exactly one live-support target is required.",
        )
    user_id = _resolve_user_key(request)
    role = get_request_role(request)
    if role not in {"citizen", "officer"}:
        raise HTTPException(status_code=403, detail="Live support role required.")
    repository = _canonical_support_repository()
    if repository is not None:
        actor = SupportActor(
            user_id=user_id,
            role=str(role),
            domains=tuple(await _officer_domains(user_id)) if role == "officer" else (),
        )
        if body.ticket_id:
            try:
                ticket = repository.get_ticket(
                    body.ticket_id,
                    actor,
                    include_content=False,
                )
            except SupportRepositoryError as exc:
                raise _canonical_http_error(exc) from exc
            if ticket.status not in {
                TicketStatus.ASSIGNED,
                TicketStatus.ACTIVE,
                TicketStatus.WAITING_CITIZEN,
                TicketStatus.WAITING_OFFICER,
            }:
                raise HTTPException(
                    status_code=409,
                    detail="Waiting tickets use queue-status polling; realtime starts after assignment.",
                )
        elif body.officer_queue:
            if role != "officer" or not actor.domains:
                raise HTTPException(status_code=403, detail="Officer queue scope required.")
        elif body.domain:
            if role != "officer" or body.domain not in actor.domains:
                raise HTTPException(status_code=403, detail="Officer domain required.")
        token = _issue_ws_auth_ticket(
            user_id=user_id,
            role=str(role),
            ticket_id=body.ticket_id,
            domain="__officer_queue__" if body.officer_queue else body.domain,
        )
        return {
            "ticket": token,
            "expires_in_seconds": WS_AUTH_TICKET_TTL_SECONDS,
            "realtime_scope": "assigned_ticket" if body.ticket_id else "single_officer_queue",
        }
    if body.officer_queue:
        raise HTTPException(
            status_code=409,
            detail="Single officer queue stream requires canonical support mode.",
        )
    if body.ticket_id:
        data = _load_ticket(body.ticket_id)
        if not data:
            raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
        await _assert_chat_access(data, user_id, role)
    elif body.domain:
        if body.domain not in SUPPORT_DOMAINS:
            raise HTTPException(status_code=400, detail="Lĩnh vực hỗ trợ không hợp lệ.")
        if role != "officer" or not _domain_allowed(
            body.domain,
            await _officer_domains(user_id),
        ):
            raise HTTPException(status_code=403, detail="Không có quyền theo dõi lĩnh vực.")

    token = _issue_ws_auth_ticket(
        user_id=user_id,
        role=role,
        ticket_id=body.ticket_id,
        domain=body.domain,
    )
    return {
        "ticket": token,
        "expires_in_seconds": WS_AUTH_TICKET_TTL_SECONDS,
    }


async def _ws_identity(
    websocket: WebSocket,
    *,
    ticket_id: str | None = None,
    domain: str | None = None,
) -> tuple[str, str] | None:
    """Authenticate live support with an individual account session only.

    Query-string roles/IDs in the legacy shared-password mode cannot prove a
    person owns a ticket.  Realtime support therefore fails closed until the
    system has real account users and the client presents a valid session token.
    """
    if not await has_real_users():
        return None
    auth_ticket = websocket.query_params.get("auth_ticket") or ""
    if auth_ticket:
        return _consume_ws_auth_ticket(
            auth_ticket,
            ticket_id=ticket_id,
            domain=domain,
        )
    if not _legacy_ws_query_token_allowed():
        return None
    token = websocket.query_params.get("token") or ""
    from api.user_service import get_user_from_session_token
    session = await get_user_from_session_token(token)
    if not session:
        return None
    return str(session["user"]["id"]), str(session["role"])


@router.websocket("/ws")
async def support_websocket(websocket: WebSocket) -> None:
    ticket_id = websocket.query_params.get("ticket_id")
    officer_queue = websocket.query_params.get("queue") == "officer"
    domain = "__officer_queue__" if officer_queue else websocket.query_params.get("domain")
    identity = await _ws_identity(
        websocket,
        ticket_id=ticket_id,
        domain=domain,
    )
    if not identity:
        await websocket.close(code=4401)
        return
    user_id, role = identity
    # Realtime chat is strictly citizen <-> assigned officer. Admin manages
    # legal candidates and accounts but is never a chat participant.
    if role not in {"citizen", "officer"}:
        await websocket.close(code=4403)
        return
    if ticket_id:
        repository = _canonical_support_repository()
        if repository is not None:
            actor = SupportActor(
                user_id=user_id,
                role=str(role),
                domains=tuple(await _officer_domains(user_id)) if role == "officer" else (),
            )
            try:
                ticket = repository.get_ticket(ticket_id, actor, include_content=False)
            except SupportNotFoundError:
                await websocket.close(code=4404)
                return
            except SupportRepositoryError:
                await websocket.close(code=4403)
                return
            if ticket.status not in {
                TicketStatus.ASSIGNED,
                TicketStatus.ACTIVE,
                TicketStatus.WAITING_CITIZEN,
                TicketStatus.WAITING_OFFICER,
            }:
                await websocket.close(code=4409)
                return
        else:
            data = _load_ticket(ticket_id)
            if not data:
                await websocket.close(code=4404)
                return
            try:
                await _assert_chat_access(data, user_id, role)
            except HTTPException:
                await websocket.close(code=4403)
                return
    elif officer_queue:
        if role != "officer" or not await _officer_domains(user_id):
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
        if _canonical_support_repository() is not None:
            await hub.join_officer_queue(
                user_id,
                await _officer_domains(user_id),
                websocket,
            )
        else:
            hub.queue_watchers.setdefault(domain, set()).add(websocket)
    try:
        await websocket.send_json({"type": "connected", "ticket_id": ticket_id, "domain": None if officer_queue else domain, "scope": "officer_queue" if officer_queue else "ticket"})
        while True:
            payload = await websocket.receive_json()
            if payload.get("type") == "typing" and ticket_id:
                await hub.broadcast_session(ticket_id, {"type": "typing", "ticket_id": ticket_id, "sender_id": user_id, "is_typing": bool(payload.get("is_typing"))})
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(ticket_id, domain, user_id, websocket)
