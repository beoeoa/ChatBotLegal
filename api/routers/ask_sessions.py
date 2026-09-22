"""
Ask Chat Sessions Router — Bước 14/15/16

Standalone chat session management for the Ask pipeline (citizen, officer, admin).
Separate from notebook-based chat sessions.

Storage: JSON file at data/ask_sessions/
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id, get_request_username

router = APIRouter(prefix="/ask-sessions", tags=["Ask Sessions"])

SESSIONS_DIR = os.getenv(
    "ASK_SESSIONS_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "ask_sessions"),
)

def _resolve_user_key(request: Request) -> str:
    """Return the authenticated account ID used as the personal session owner."""
    user_id = get_request_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Hãy đăng nhập bằng tài khoản cá nhân để sử dụng lịch sử hội thoại.",
        )
    return str(user_id)




# --- Models ---

class AskMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    role: str = Field(..., description="user|assistant|system")
    content: str = Field(...)
    status: Optional[str] = Field(None, pattern="^(pending|complete|error)$")
    citations: Optional[List[dict]] = None
    recommended_forms: Optional[List[dict]] = None
    faq_refs: Optional[List[str]] = None
    faqs: Optional[List[dict]] = None
    procedure_detail: Optional[dict] = None
    rag_trace: Optional[dict] = None
    grounding_status: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class AskSession(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    user_id: str
    title: str = "Cuộc trò chuyện mới"
    role: str = "citizen"
    domain: Optional[str] = None
    ward_scope: Optional[str] = None
    messages: List[AskMessage] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CreateSessionRequest(BaseModel):
    title: Optional[str] = None
    domain: Optional[str] = None
    ward_scope: Optional[str] = None


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = None


class AddMessageRequest(BaseModel):
    role: str = Field("user", pattern="^(user|assistant|system)$")
    content: str = Field(..., min_length=1)
    status: Optional[str] = Field(None, pattern="^(pending|complete|error)$")
    citations: Optional[List[dict]] = None
    recommended_forms: Optional[List[dict]] = None
    faq_refs: Optional[List[str]] = None
    faqs: Optional[List[dict]] = None
    procedure_detail: Optional[dict] = None
    rag_trace: Optional[dict] = None
    grounding_status: Optional[str] = None


class SessionSummary(BaseModel):
    id: str
    title: str
    role: str
    domain: Optional[str] = None
    message_count: int = 0
    created_at: str
    updated_at: str


class SessionDetail(BaseModel):
    id: str
    user_id: str
    title: str
    role: str
    domain: Optional[str] = None
    ward_scope: Optional[str] = None
    messages: List[AskMessage]
    created_at: str
    updated_at: str


# --- Storage helpers ---

def _user_dir(user_id: str) -> str:
    """Get directory for a user's sessions."""
    safe_id = user_id.replace(":", "_").replace("/", "_")
    path = os.path.join(SESSIONS_DIR, safe_id)
    os.makedirs(path, exist_ok=True)
    return path


def _session_path(user_id: str, session_id: str) -> str:
    return os.path.join(_user_dir(user_id), f"{session_id}.json")


def _load_session(user_id: str, session_id: str) -> dict | None:
    path = _session_path(user_id, session_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_session(user_id: str, session_id: str, data: dict) -> None:
    path = _session_path(user_id, session_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _delete_session_file(user_id: str, session_id: str) -> bool:
    path = _session_path(user_id, session_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def _list_sessions(user_id: str) -> list[dict]:
    """List all sessions for a user, sorted by updated_at desc."""
    user_path = _user_dir(user_id)
    sessions = []
    for fname in os.listdir(user_path):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(user_path, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
                sessions.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return sessions


def _auto_title(content: str) -> str:
    """Generate a safe auto-title from first user message, no PII."""
    text = content.strip()[:60].replace("\n", " ")
    if len(text) > 50:
        text = text[:50] + "..."
    return text or "Cuộc trò chuyện mới"


# --- Routes ---

@router.get("", response_model=List[SessionSummary])
@router.get("/", response_model=List[SessionSummary])
async def list_sessions(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all sessions for the current user."""
    user_id = _resolve_user_key(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    sessions = _list_sessions(user_id)
    page = sessions[offset:offset + limit]
    return [
        SessionSummary(
            id=s["id"],
            title=s.get("title", ""),
            role=s.get("role", "citizen"),
            domain=s.get("domain"),
            message_count=len(s.get("messages", [])),
            created_at=s.get("created_at", ""),
            updated_at=s.get("updated_at", ""),
        )
        for s in page
    ]


@router.post("", response_model=SessionDetail, status_code=201)
@router.post("/", response_model=SessionDetail, status_code=201)
async def create_session(body: CreateSessionRequest, request: Request):
    """Create a new Ask chat session."""
    user_id = _resolve_user_key(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    role = get_request_role(request) or "citizen"
    session = AskSession(
        user_id=user_id,
        title=body.title or "Cuộc trò chuyện mới",
        role=role,
        domain=body.domain,
        ward_scope=body.ward_scope,
    )
    _save_session(user_id, session.id, session.model_dump())
    return SessionDetail(**session.model_dump())


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, request: Request):
    """Get a session with all messages."""
    user_id = _resolve_user_key(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    data = _load_session(user_id, session_id)
    if not data:
        # Legacy JSON sessions remain strictly owner-scoped. The auditable
        # conversation/legal-profile APIs are the only supported path for an
        # administrator to inspect cross-account content.
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDetail(**data)


@router.post("/{session_id}/messages", response_model=AskMessage, status_code=201)
async def add_message(session_id: str, body: AddMessageRequest, request: Request):
    """Add a message to an existing session."""
    user_id = _resolve_user_key(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    data = _load_session(user_id, session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    msg = AskMessage(
        role=body.role,
        content=body.content,
        status=body.status or ("complete" if body.role == "assistant" else None),
        citations=body.citations,
        recommended_forms=body.recommended_forms,
        faq_refs=body.faq_refs,
        faqs=body.faqs,
        procedure_detail=body.procedure_detail,
        rag_trace=body.rag_trace,
        grounding_status=body.grounding_status,
    )

    data.setdefault("messages", []).append(msg.model_dump())
    data["updated_at"] = datetime.now().isoformat()

    # Auto-title from first user message if still default
    if data.get("title") in ("Cuộc trò chuyện mới", None, "") and body.role == "user":
        data["title"] = _auto_title(body.content)

    _save_session(user_id, session_id, data)
    return msg


@router.patch("/{session_id}", response_model=SessionSummary)
async def update_session(session_id: str, body: UpdateSessionRequest, request: Request):
    """Update session title."""
    user_id = _resolve_user_key(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    data = _load_session(user_id, session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    if body.title is not None:
        data["title"] = body.title
    data["updated_at"] = datetime.now().isoformat()
    _save_session(user_id, session_id, data)

    return SessionSummary(
        id=data["id"],
        title=data.get("title", ""),
        role=data.get("role", "citizen"),
        domain=data.get("domain"),
        message_count=len(data.get("messages", [])),
        created_at=data.get("created_at", ""),
        updated_at=data.get("updated_at", ""),
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request):
    """Delete a session and all its messages."""
    user_id = _resolve_user_key(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not _delete_session_file(user_id, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return None


@router.get("/{session_id}/context", response_model=List[AskMessage])
async def get_session_context(
    session_id: str,
    request: Request,
    last_n: int = Query(5, ge=1, le=20),
):
    """Get the last N messages from a session for context building."""
    user_id = _resolve_user_key(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    data = _load_session(user_id, session_id)
    if not data:
        raise HTTPException(status_code=404, detail="Session not found")

    messages = data.get("messages", [])
    recent = messages[-last_n:] if len(messages) > last_n else messages
    return [AskMessage(**m) for m in recent]
