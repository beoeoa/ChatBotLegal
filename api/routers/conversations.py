from __future__ import annotations

"""Conversations API for multi-turn Ask chat with owner-scoped persistence."""

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id, get_request_username
from api import conversation_service as svc
from api.models import AskRequest, AskResponse
from api.observability import telemetry
from api.unified_chat_service import run_unified_chat
from time import perf_counter

router = APIRouter(prefix="/conversations", tags=["Conversations"])


class ConversationMessageOut(BaseModel):
    id: str
    role: str
    content: str
    status: Optional[str] = None
    citations: Optional[List[dict]] = None
    recommended_forms: Optional[List[dict]] = None
    faq_refs: Optional[List[str]] = None
    faqs: Optional[List[dict]] = None
    procedure_detail: Optional[dict] = None
    answer_sections: Optional[List[dict]] = None
    forms_unavailable: Optional[bool] = None
    rag_trace: Optional[dict] = None
    grounding_status: Optional[str] = None
    answer_mode: Optional[str] = None
    answer_status: Optional[str] = None
    fallback_tier: Optional[str] = None
    canonical_domain: Optional[str] = None
    evidence_count: Optional[int] = None
    coverage_warning: Optional[str] = None
    blocked_reason: Optional[str] = None
    presentation_version: Optional[str] = None
    answer_route: Optional[str] = None
    pipeline_version: Optional[str] = None
    data_release_id: Optional[str] = None
    index_fingerprint: Optional[str] = None
    validity_snapshot: Optional[str] = None
    verification_label: Optional[str] = None
    historical_label: Optional[str] = None
    sections: Optional[dict] = None
    attachments: Optional[List[dict]] = None
    created_at: str


class ConversationSummary(BaseModel):
    id: str
    title: str
    domain: Optional[str] = None
    role_context: str
    status: str
    owner_user_id: Optional[str] = None
    created_at: str
    last_message_at: str
    expires_at: str = ""
    message_count: int = 0


class ConversationDetail(ConversationSummary):
    messages: List[ConversationMessageOut] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    has_older_messages: bool = False


class ConversationMessagePage(BaseModel):
    messages: List[ConversationMessageOut] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    has_more: bool = False
    limit: int = 30


class CreateConversationRequest(BaseModel):
    title: Optional[str] = None
    domain: Optional[str] = None


class RenameConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)
    role: str = Field("user", pattern="^(user|assistant|system)$")
    status: Optional[str] = Field(None, pattern="^(pending|complete|error)$")
    citations: Optional[List[dict]] = None
    recommended_forms: Optional[List[dict]] = None
    faq_refs: Optional[List[str]] = None
    faqs: Optional[List[dict]] = None
    procedure_detail: Optional[dict] = None
    answer_sections: Optional[List[dict]] = None
    forms_unavailable: Optional[bool] = None
    rag_trace: Optional[dict] = None
    grounding_status: Optional[str] = None
    attachments: Optional[List[dict]] = None


class ContextMessageOut(BaseModel):
    role: str
    content: str
    citations: Optional[List[dict]] = None
    grounding_status: Optional[str] = None


class UnifiedChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=12000)
    engine: str = Field("legal", pattern="^(legal|general)$")
    domain: Optional[str] = None
    strategy_model: str = ""
    answer_model: str = ""
    final_answer_model: str = ""
    offline_mode: bool = False
    offline_model: str = "qwen2.5:3b"
    event_date: Optional[str] = None
    legal_as_of: Optional[str] = None
    idempotency_key: Optional[str] = Field(None, min_length=8, max_length=128)
    general_context: Optional[dict[str, Any]] = None
    model_override: Optional[str] = None


def _owner_state(request: Request) -> tuple[str, str, str, bool]:
    """Require an account identity for persisted personal conversations.

    A shared legacy role password cannot identify a person and therefore must
    never become a conversation owner key.
    """
    role = get_request_role(request) or "citizen"
    user_id = get_request_user_id(request)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Hãy đăng nhập bằng tài khoản cá nhân để sử dụng lịch sử hội thoại.",
        )
    owner_key = svc.resolve_owner_key(user_id=user_id, role=role)
    return owner_key, str(user_id), role, role == "admin"


def _summary(data: dict[str, Any]) -> ConversationSummary:
    return ConversationSummary(
        id=str(data.get("id")),
        title=data.get("title") or svc.DEFAULT_TITLE,
        domain=data.get("domain"),
        role_context=data.get("role_context") or "citizen",
        status=data.get("status") or "active",
        owner_user_id=data.get("owner_user_id"),
        created_at=str(data.get("created_at") or ""),
        last_message_at=str(data.get("last_message_at") or ""),
        expires_at=str(data.get("expires_at") or ""),
        message_count=int(data.get("message_count") or 0),
    )


def _detail(data: dict[str, Any]) -> ConversationDetail:
    messages = [
        ConversationMessageOut(
            id=str(msg.get("id")),
            role=str(msg.get("role") or "assistant"),
            content=str(msg.get("content") or ""),
            status=msg.get("status"),
            citations=msg.get("citations"),
            recommended_forms=msg.get("recommended_forms"),
            faq_refs=msg.get("faq_refs"),
            faqs=msg.get("faqs"),
            procedure_detail=msg.get("procedure_detail"),
            answer_sections=msg.get("answer_sections"),
            forms_unavailable=msg.get("forms_unavailable"),
            rag_trace=msg.get("rag_trace"),
            grounding_status=msg.get("grounding_status"),
            answer_mode=msg.get("answer_mode"),
            answer_status=msg.get("answer_status"),
            fallback_tier=msg.get("fallback_tier"),
            canonical_domain=msg.get("canonical_domain"),
            evidence_count=msg.get("evidence_count"),
            coverage_warning=msg.get("coverage_warning"),
            blocked_reason=msg.get("blocked_reason"),
            presentation_version=msg.get("presentation_version"),
            answer_route=msg.get("answer_route"),
            pipeline_version=msg.get("pipeline_version"),
            data_release_id=msg.get("data_release_id"),
            index_fingerprint=msg.get("index_fingerprint"),
            validity_snapshot=msg.get("validity_snapshot"),
            verification_label=msg.get("verification_label"),
            historical_label=msg.get("historical_label"),
            sections=msg.get("sections"),
            attachments=msg.get("attachments"),
            created_at=str(msg.get("created_at") or ""),
        )
        for msg in (data.get("messages") or [])
    ]
    base = _summary(data)
    return ConversationDetail(
        **base.model_dump(),
        messages=messages,
        next_cursor=data.get("next_cursor"),
        has_older_messages=bool(data.get("has_older_messages")),
    )


@router.get("", response_model=List[ConversationSummary])
@router.get("/", response_model=List[ConversationSummary])
async def list_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_deleted: bool = Query(False),
):
    started = perf_counter()
    owner_key, user_id, role, is_admin = _owner_state(request)
    rows = await svc.list_conversations(
        owner_key=owner_key,
        role_context=role,
        real_user_id=user_id,
        include_deleted=include_deleted and is_admin,
        limit=limit,
        offset=offset,
    )
    telemetry.record_operation(
        category="conversation_load", route="/api/conversations/",
        duration_ms=(perf_counter() - started) * 1000,
        metadata={"cached": False},
    )
    return [_summary(row) for row in rows]


@router.post("", response_model=ConversationDetail, status_code=201)
@router.post("/", response_model=ConversationDetail, status_code=201)
async def create_conversation(body: CreateConversationRequest, request: Request):
    owner_key, user_id, role, _ = _owner_state(request)
    created = await svc.create_conversation(
        owner_key=owner_key,
        role_context=role,
        title=body.title,
        domain=body.domain,
        real_user_id=user_id,
    )
    return _detail(created)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str, request: Request):
    started = perf_counter()
    owner_key, user_id, role, is_admin = _owner_state(request)
    await svc.recover_missing_assistant_message(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
    )
    data = await svc.get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        is_admin=is_admin,
        include_messages=False,
    )
    if not data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    page = await svc.get_conversation_message_page(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        is_admin=is_admin,
        limit=svc.DEFAULT_MESSAGE_PAGE_SIZE,
    )
    data["messages"] = (page or {}).get("messages") or []
    data["next_cursor"] = (page or {}).get("next_cursor")
    data["has_older_messages"] = bool((page or {}).get("has_more"))
    telemetry.record_operation(
        category="conversation_load", route="/api/conversations/{conversation_id}",
        duration_ms=(perf_counter() - started) * 1000,
        metadata={"cached": False},
    )
    return _detail(data)


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def rename_conversation(
    conversation_id: str,
    body: RenameConversationRequest,
    request: Request,
):
    owner_key, user_id, role, is_admin = _owner_state(request)
    data = await svc.rename_conversation(
        conversation_id,
        title=body.title,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        is_admin=is_admin,
    )
    if not data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return _summary(data)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request):
    owner_key, user_id, role, is_admin = _owner_state(request)
    ok = await svc.soft_delete_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        is_admin=is_admin,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return None


@router.post(
    "/{conversation_id}/messages",
    response_model=ConversationMessageOut,
    status_code=201,
)
async def send_message(
    conversation_id: str,
    body: SendMessageRequest,
    request: Request,
):
    owner_key, user_id, role, is_admin = _owner_state(request)
    # Only allow storing assistant/system snapshots for the same owner conversation.
    # Frontend/backend Ask pipeline may write assistant messages after generation.
    msg = await svc.add_message(
        conversation_id,
        owner_key=owner_key,
        role=body.role,
        content=body.content,
        real_user_id=user_id,
        role_context=role,
        is_admin=is_admin,
        status=body.status,
        citations=body.citations,
        recommended_forms=body.recommended_forms,
        faq_refs=body.faq_refs,
        faqs=body.faqs,
        procedure_detail=body.procedure_detail,
        answer_sections=body.answer_sections,
        forms_unavailable=body.forms_unavailable,
        rag_trace=body.rag_trace if role == "admin" else None,
        grounding_status=body.grounding_status,
        attachments=body.attachments,
    )
    if not msg:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationMessageOut(
        id=str(msg.get("id")),
        role=str(msg.get("role") or body.role),
        content=str(msg.get("content") or ""),
        status=msg.get("status"),
        citations=msg.get("citations"),
        recommended_forms=msg.get("recommended_forms"),
        faq_refs=msg.get("faq_refs"),
        faqs=msg.get("faqs"),
        procedure_detail=msg.get("procedure_detail"),
        answer_sections=msg.get("answer_sections"),
        forms_unavailable=msg.get("forms_unavailable"),
        rag_trace=msg.get("rag_trace"),
        grounding_status=msg.get("grounding_status"),
        answer_mode=msg.get("answer_mode"),
        attachments=msg.get("attachments"),
        created_at=str(msg.get("created_at") or ""),
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=ConversationMessagePage,
)
async def list_messages(
    conversation_id: str,
    request: Request,
    before: Optional[str] = Query(None),
    limit: int = Query(30, ge=1, le=100),
):
    owner_key, user_id, role, is_admin = _owner_state(request)
    try:
        page = await svc.get_conversation_message_page(
            conversation_id,
            owner_key=owner_key,
            real_user_id=user_id,
            role_context=role,
            is_admin=is_admin,
            limit=limit,
            before=before,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if page is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationMessagePage(
        messages=[ConversationMessageOut(**message) for message in page["messages"]],
        next_cursor=page["next_cursor"],
        has_more=page["has_more"],
        limit=page["limit"],
    )


@router.get("/{conversation_id}/context", response_model=List[ContextMessageOut])
async def get_context(
    conversation_id: str,
    request: Request,
    max_messages: int = Query(12, ge=1, le=30),
    max_chars: int = Query(3500, ge=500, le=12000),
):
    owner_key, user_id, role, is_admin = _owner_state(request)
    context = await svc.get_followup_context(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        is_admin=is_admin,
        max_messages=max_messages,
        max_chars=max_chars,
    )
    if context is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Distinguish missing conversation vs empty context.
    existing = await svc.get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        is_admin=is_admin,
        include_messages=False,
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return [
        ContextMessageOut(
            role=str(item.get("role") or "assistant"),
            content=str(item.get("content") or ""),
            citations=item.get("citations"),
            grounding_status=item.get("grounding_status"),
        )
        for item in context
    ]
