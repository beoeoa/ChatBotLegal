from __future__ import annotations

"""Conversations API for multi-turn Ask chat with owner-scoped persistence."""

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id, get_request_username
from api import conversation_service as svc
from api import chat_memory_service as memory_svc
from api.models import AskRequest, AskResponse
from api.observability import telemetry
from api.unified_chat_service import run_unified_chat
from time import perf_counter

router = APIRouter(prefix="/conversations", tags=["Conversations"])


class ConversationMessageOut(BaseModel):
    id: str
    turn_id: Optional[str] = None
    role: str
    content: str
    status: Optional[str] = None
    citations: Optional[List[dict]] = None
    recommended_forms: Optional[List[dict]] = None
    faq_refs: Optional[List[str]] = None
    faqs: Optional[List[dict]] = None
    procedure_detail: Optional[dict] = None
    suggested_questions: Optional[List[dict]] = None
    conversation_route: Optional[str] = None
    active_document: Optional[dict] = None
    related_documents: List[dict] = Field(default_factory=list)
    memory_usage: Optional[dict] = None
    timing_summary: Optional[dict] = None
    model_option_id: Optional[str] = None
    model_display_name: Optional[str] = None
    model_locked: bool = False
    generation_provenance: Optional[dict] = None
    answer_sections: Optional[List[dict]] = None
    request_items: List[dict] = Field(default_factory=list)
    item_results: List[dict] = Field(default_factory=list)
    forms_unavailable: Optional[bool] = None
    rag_trace: Optional[dict] = None
    grounding_status: Optional[str] = None
    answer_mode: Optional[str] = None
    answer_status: Optional[str] = None
    outcome: Optional[str] = None
    reason_code: Optional[str] = None
    retryable: Optional[bool] = None
    scope: Optional[str] = None
    quality: Optional[dict] = None
    persistence_degraded: bool = False
    fallback_tier: Optional[str] = None
    canonical_domain: Optional[str] = None
    evidence_count: Optional[int] = None
    coverage_warning: Optional[str] = None
    blocked_reason: Optional[str] = None
    presentation_version: Optional[str] = None
    answer_route: Optional[str] = None
    pipeline_version: Optional[str] = None
    data_release_id: Optional[str] = None
    release_id: Optional[str] = None
    index_fingerprint: Optional[str] = None
    manifest_hash: Optional[str] = None
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
    message_count: Optional[int] = None


class ConversationDetail(ConversationSummary):
    messages: List[ConversationMessageOut] = Field(default_factory=list)
    next_cursor: Optional[str] = None
    has_older_messages: bool = False
    model_option_id: Optional[str] = None
    model_display_name: Optional[str] = None
    model_locked: bool = False


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


from api.conversation_attachments import SavedDocumentContext, attachment_snapshot, KIND as DOCUMENT_CONTEXT_KIND


class SendMessageRequest(BaseModel):
    document_context: Optional[SavedDocumentContext] = None
    turn_id: Optional[str] = Field(None, min_length=8, max_length=128)
    content: str = Field(..., min_length=1)
    role: str = Field("user", pattern="^(user|assistant|system)$")
    status: Optional[str] = Field(None, pattern="^(pending|complete|error)$")
    citations: Optional[List[dict]] = None
    recommended_forms: Optional[List[dict]] = None
    faq_refs: Optional[List[str]] = None
    faqs: Optional[List[dict]] = None
    procedure_detail: Optional[dict] = None
    suggested_questions: Optional[List[dict]] = None
    conversation_route: Optional[str] = None
    active_document: Optional[dict] = None
    related_documents: Optional[List[dict]] = None
    memory_usage: Optional[dict] = None
    timing_summary: Optional[dict] = None
    model_option_id: Optional[str] = None
    model_display_name: Optional[str] = None
    model_locked: bool = False
    generation_provenance: Optional[dict] = None
    answer_sections: Optional[List[dict]] = None
    forms_unavailable: Optional[bool] = None
    rag_trace: Optional[dict] = None
    grounding_status: Optional[str] = None
    answer_mode: Optional[str] = None
    answer_status: Optional[str] = None
    outcome: Optional[str] = None
    reason_code: Optional[str] = None
    retryable: Optional[bool] = None
    scope: Optional[str] = None
    persistence_degraded: bool = False
    fallback_tier: Optional[str] = None
    canonical_domain: Optional[str] = None
    evidence_count: Optional[int] = None
    coverage_warning: Optional[str] = None
    blocked_reason: Optional[str] = None
    presentation_version: Optional[str] = None
    answer_route: Optional[str] = None
    pipeline_version: Optional[str] = None
    data_release_id: Optional[str] = None
    release_id: Optional[str] = None
    index_fingerprint: Optional[str] = None
    manifest_hash: Optional[str] = None
    validity_snapshot: Optional[str] = None
    verification_label: Optional[str] = None
    historical_label: Optional[str] = None
    sections: Optional[dict] = None
    attachments: Optional[List[dict]] = None


class ContextMessageOut(BaseModel):
    role: str
    content: str
    citations: Optional[List[dict]] = None
    grounding_status: Optional[str] = None


class ConversationStateOut(BaseModel):
    version: str
    conversation_id: str
    role_context: str
    canonical_domain: Optional[str] = None
    temporal_scope: Optional[str] = None
    legal_as_of: Optional[str] = None
    procedure: Optional[dict] = None
    actors: List[str] = Field(default_factory=list)
    legal_objects: List[str] = Field(default_factory=list)
    locations: List[str] = Field(default_factory=list)
    answered_facets: List[str] = Field(default_factory=list)
    unresolved_facets: List[str] = Field(default_factory=list)
    active_document: Optional[dict] = None
    recent_source_refs: List[dict] = Field(default_factory=list)
    conversation_digest: Optional[str] = None
    conversation_digest_v2: Optional[dict] = None
    digest_revision: int = 0
    digest_through_message_id: Optional[str] = None
    digest_checksum: Optional[str] = None
    source_turn_ids: List[str] = Field(default_factory=list)
    revision: int = 0
    updated_at: Optional[str] = None
    expires_at: Optional[str] = None
    model_option_id: Optional[str] = None
    model_display_name: Optional[str] = None
    model_locked: bool = False
    generation_provenance: Optional[dict] = None


class UpdateConversationStateRequest(BaseModel):
    forget_fields: List[str] = Field(default_factory=list, max_length=12)
    set_active_document_id: Optional[str] = Field(None, max_length=1000)
    clear_active_document: bool = False
    pin_active_document: bool = False
    unpin_active_document: bool = False


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
        message_count=(
            int(data.get("message_count"))
            if data.get("message_count") is not None
            else None
        ),
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
            suggested_questions=msg.get("suggested_questions"),
            conversation_route=msg.get("conversation_route"),
            active_document=msg.get("active_document"),
            related_documents=msg.get("related_documents") or [],
            memory_usage=msg.get("memory_usage"),
            timing_summary=msg.get("timing_summary"),
            model_option_id=msg.get("model_option_id"),
            model_display_name=msg.get("model_display_name"),
            model_locked=bool(msg.get("model_locked")),
            generation_provenance=msg.get("generation_provenance"),
            answer_sections=msg.get("answer_sections"),
            forms_unavailable=msg.get("forms_unavailable"),
            rag_trace=msg.get("rag_trace"),
            grounding_status=msg.get("grounding_status"),
            answer_mode=msg.get("answer_mode"),
            answer_status=msg.get("answer_status"),
            outcome=msg.get("outcome"),
            reason_code=msg.get("reason_code"),
            retryable=msg.get("retryable"),
            scope=msg.get("scope"),
            persistence_degraded=msg.get("persistence_degraded", False),
            fallback_tier=msg.get("fallback_tier"),
            canonical_domain=msg.get("canonical_domain"),
            evidence_count=msg.get("evidence_count"),
            coverage_warning=msg.get("coverage_warning"),
            blocked_reason=msg.get("blocked_reason"),
            presentation_version=msg.get("presentation_version"),
            answer_route=msg.get("answer_route"),
            pipeline_version=msg.get("pipeline_version"),
            data_release_id=msg.get("data_release_id"),
            manifest_hash=msg.get("manifest_hash"),
            release_id=msg.get("release_id"),
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
        model_option_id=next(
            (item.model_option_id for item in reversed(messages) if item.model_option_id),
            None,
        ),
        model_display_name=next(
            (item.model_display_name for item in reversed(messages) if item.model_display_name),
            None,
        ),
        model_locked=False,
    )


@router.get("", response_model=List[ConversationSummary])
@router.get("/", response_model=List[ConversationSummary])
async def list_conversations(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_deleted: bool = Query(False),
    include_message_count: bool = Query(True),
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
        include_message_count=include_message_count,
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


@router.get("/latest", response_model=Optional[ConversationDetail])
async def get_latest_conversation(request: Request):
    """Return the newest conversation and its first message page in one trip.

    The previous frontend startup path fetched a one-row summary and then made
    a second HTTP request for the detail. Keeping this owner-scoped endpoint in
    the existing router removes that serial round trip without changing stored
    conversation data or authorization rules.
    """
    started = perf_counter()
    owner_key, user_id, role, _ = _owner_state(request)
    data = await svc.get_latest_conversation_page(
        owner_key=owner_key,
        role_context=role,
        real_user_id=user_id,
        limit=svc.DEFAULT_MESSAGE_PAGE_SIZE,
    )
    if not data:
        return None

    telemetry.record_operation(
        category="conversation_load",
        route="/api/conversations/latest",
        duration_ms=(perf_counter() - started) * 1000,
        metadata={"cached": False},
    )
    return _detail(data)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: str, request: Request):
    started = perf_counter()
    owner_key, user_id, role, is_admin = _owner_state(request)
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
        verified_conversation=data,
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
    # Compose this attachment in the backend. It wins over a stale client
    # attachment and preserves legal-answer-v1 without a schema migration.
    attachments = [
        item for item in (body.attachments or [])
        if not (isinstance(item, dict) and item.get("kind") in {"legal_answer_presentation", DOCUMENT_CONTEXT_KIND})
    ]
    if body.role == "user":
        attachments.append(attachment_snapshot(body.document_context))
    presentation = {
        "presentation_version": body.presentation_version,
        "answer_status": body.answer_status,
        "answer_mode": body.answer_mode,
        "outcome": body.outcome,
        "reason_code": body.reason_code,
        "retryable": body.retryable,
        "scope": body.scope,
        "persistence_degraded": body.persistence_degraded,
        "fallback_tier": body.fallback_tier,
        "canonical_domain": body.canonical_domain,
        "evidence_count": body.evidence_count,
        "coverage_warning": body.coverage_warning,
        "blocked_reason": body.blocked_reason,
        "forms_unavailable": body.forms_unavailable,
        "answer_route": body.answer_route,
        "pipeline_version": body.pipeline_version,
        "data_release_id": body.data_release_id,
        "release_id": body.release_id,
        "index_fingerprint": body.index_fingerprint,
        "manifest_hash": body.manifest_hash,
        "validity_snapshot": body.validity_snapshot,
        "verification_label": body.verification_label,
        "historical_label": body.historical_label,
        "sections": body.sections,
    }
    if any(value is not None for value in presentation.values()):
        attachments.append({"kind": "legal_answer_presentation", "value": presentation})
    if body.answer_mode is not None:
        attachments = [
            item for item in attachments
            if not (isinstance(item, dict) and item.get("kind") == "answer_mode")
        ]
        attachments.append({"kind": "answer_mode", "value": body.answer_mode})
    if body.role == "assistant" and (
        body.model_option_id
        or body.model_display_name
        or body.generation_provenance
    ):
        attachments = [
            item for item in attachments
            if not (
                isinstance(item, dict)
                and item.get("kind") == "chat_model_snapshot_v1"
            )
        ]
        attachments.append(
            {
                "kind": "chat_model_snapshot_v1",
                "value": {
                    "model_option_id": body.model_option_id,
                    "model_display_name": body.model_display_name,
                    "model_locked": False,
                    "generation_provenance": body.generation_provenance,
                },
            }
        )
    # Only allow storing assistant/system snapshots for the same owner conversation.
    # Frontend/backend Ask pipeline may write assistant messages after generation.
    msg = await svc.add_message(
        conversation_id,
        owner_key=owner_key,
        role=body.role,
        content=body.content,
        turn_id=(f"user:{body.turn_id}" if body.role == "user" else body.turn_id) if body.turn_id else None,
        real_user_id=user_id,
        role_context=role,
        is_admin=is_admin,
        status=body.status,
        citations=body.citations,
        recommended_forms=body.recommended_forms,
        faq_refs=body.faq_refs,
        faqs=body.faqs,
        procedure_detail=body.procedure_detail,
        suggested_questions=body.suggested_questions,
        conversation_route=body.conversation_route,
        active_document=body.active_document,
        related_documents=body.related_documents,
        memory_usage=body.memory_usage,
        timing_summary=body.timing_summary,
        answer_sections=body.answer_sections,
        forms_unavailable=body.forms_unavailable,
        rag_trace=body.rag_trace if role == "admin" else None,
        grounding_status=body.grounding_status,
        answer_status=body.answer_status,
        outcome=body.outcome,
        reason_code=body.reason_code,
        retryable=body.retryable,
        scope=body.scope,
        persistence_degraded=body.persistence_degraded,
        fallback_tier=body.fallback_tier,
        canonical_domain=body.canonical_domain,
        evidence_count=body.evidence_count,
        coverage_warning=body.coverage_warning,
        blocked_reason=body.blocked_reason,
        attachments=attachments,
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
        suggested_questions=msg.get("suggested_questions"),
        conversation_route=msg.get("conversation_route"),
        active_document=msg.get("active_document"),
        related_documents=msg.get("related_documents") or [],
        memory_usage=msg.get("memory_usage"),
        timing_summary=msg.get("timing_summary") or body.timing_summary,
        model_option_id=msg.get("model_option_id") or body.model_option_id,
        model_display_name=msg.get("model_display_name") or body.model_display_name,
        model_locked=False,
        generation_provenance=(
            msg.get("generation_provenance") or body.generation_provenance
        ),
        answer_sections=msg.get("answer_sections"),
        forms_unavailable=msg.get("forms_unavailable"),
        rag_trace=msg.get("rag_trace"),
        grounding_status=msg.get("grounding_status"),
        answer_mode=msg.get("answer_mode"),
        answer_status=msg.get("answer_status"),
        outcome=msg.get("outcome"),
        reason_code=msg.get("reason_code"),
        retryable=msg.get("retryable"),
        scope=msg.get("scope"),
        persistence_degraded=msg.get("persistence_degraded", False),
        fallback_tier=msg.get("fallback_tier"),
        canonical_domain=msg.get("canonical_domain"),
        evidence_count=msg.get("evidence_count"),
        coverage_warning=msg.get("coverage_warning"),
        blocked_reason=msg.get("blocked_reason"),
        presentation_version=msg.get("presentation_version"),
        answer_route=msg.get("answer_route"),
        pipeline_version=msg.get("pipeline_version"),
        data_release_id=msg.get("data_release_id"),
        manifest_hash=msg.get("manifest_hash"),
        release_id=msg.get("release_id"),
        index_fingerprint=msg.get("index_fingerprint"),
        validity_snapshot=msg.get("validity_snapshot"),
        verification_label=msg.get("verification_label"),
        historical_label=msg.get("historical_label"),
        sections=msg.get("sections"),
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


@router.delete("/{conversation_id}/attachment", status_code=204)
async def detach_conversation_attachment(conversation_id: str, request: Request):
    owner_key, user_id, role, _ = _owner_state(request)
    if not await svc.clear_conversation_attachment(
        conversation_id, owner_key=owner_key, real_user_id=user_id, role_context=role,
    ):
        raise HTTPException(status_code=404, detail="Conversation not found")


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


@router.get("/{conversation_id}/state", response_model=ConversationStateOut)
async def get_conversation_state(conversation_id: str, request: Request):
    owner_key, user_id, role, is_admin = _owner_state(request)
    if is_admin:
        raise HTTPException(status_code=403, detail="chat_memory_role_not_supported")
    state = await memory_svc.get_conversation_state(
        conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
    )
    if state is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationStateOut(**state)


@router.patch("/{conversation_id}/state", response_model=ConversationStateOut)
async def forget_conversation_state(
    conversation_id: str,
    body: UpdateConversationStateRequest,
    request: Request,
):
    owner_key, user_id, role, is_admin = _owner_state(request)
    if is_admin:
        raise HTTPException(status_code=403, detail="chat_memory_role_not_supported")
    actions = sum(
        bool(value)
        for value in (
            body.forget_fields,
            body.set_active_document_id,
            body.clear_active_document,
            body.pin_active_document,
            body.unpin_active_document,
        )
    )
    if actions != 1:
        raise HTTPException(
            status_code=422,
            detail="Mỗi yêu cầu phải chứa đúng một thao tác cập nhật ngữ cảnh.",
        )
    try:
        if body.set_active_document_id or body.clear_active_document or body.pin_active_document or body.unpin_active_document:
            state = await memory_svc.update_active_document_state(
                conversation_id,
                owner_key=owner_key,
                real_user_id=user_id,
                role_context=role,
                document_id=body.set_active_document_id,
                clear=body.clear_active_document,
                pinned=(
                    True
                    if body.pin_active_document
                    else False
                    if body.unpin_active_document
                    else None
                ),
            )
        else:
            state = await memory_svc.forget_conversation_state_fields(
                conversation_id,
                owner_key=owner_key,
                real_user_id=user_id,
                role_context=role,
                forget_fields=body.forget_fields,
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationStateOut(**state)
