"""Owner-scoped controls for opt-in long-term chat memory."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id
from api import chat_memory_service as memory
from api import conversation_service as conversations


router = APIRouter(prefix="/chat-memory", tags=["chat-memory"])


class MemorySettingsPatch(BaseModel):
    enabled: bool


class MemoryItemPatch(BaseModel):
    value: Any


class TrackProcedureRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1, max_length=160)


class MemorySettingsOut(BaseModel):
    enabled: bool
    role_context: str
    runtime_available: bool
    enabled_at: str | None = None
    updated_at: str | None = None
    retention_days: int = 365


class MemoryItemOut(BaseModel):
    id: str
    memory_key: str
    value: Any
    label: str | None = None
    source_kind: str
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    status: str
    consent_at: str | None = None
    last_used_at: str | None = None
    expires_at: str | None = None
    revision: int = 1
    created_at: str | None = None
    updated_at: str | None = None


def _owner(request: Request) -> tuple[str, str, str]:
    role = str(get_request_role(request) or "citizen").casefold()
    user_id = get_request_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="personal_account_required")
    if role not in {"citizen", "officer"}:
        raise HTTPException(status_code=403, detail="chat_memory_role_not_supported")
    return (
        conversations.resolve_owner_key(user_id=str(user_id), role=role),
        str(user_id),
        role,
    )


@router.get("/settings", response_model=MemorySettingsOut)
async def get_settings(request: Request):
    owner_key, user_id, role = _owner(request)
    return await memory.get_memory_settings(
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
    )


@router.patch("/settings", response_model=MemorySettingsOut)
async def patch_settings(body: MemorySettingsPatch, request: Request):
    owner_key, user_id, role = _owner(request)
    return await memory.set_memory_enabled(
        body.enabled,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
    )


@router.get("/items", response_model=list[MemoryItemOut])
async def list_items(request: Request):
    owner_key, user_id, role = _owner(request)
    return await memory.list_memory_items(
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
    )


@router.post("/items/track-procedure", response_model=MemoryItemOut)
async def track_procedure(body: TrackProcedureRequest, request: Request):
    owner_key, user_id, role = _owner(request)
    item = await memory.track_conversation_procedure(
        body.conversation_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
    )
    if not item:
        raise HTTPException(
            status_code=409,
            detail="long_term_memory_disabled_or_procedure_unavailable",
        )
    return item


@router.patch("/items/{item_id}", response_model=MemoryItemOut)
async def patch_item(item_id: str, body: MemoryItemPatch, request: Request):
    owner_key, user_id, role = _owner(request)
    item = await memory.update_memory_item(
        item_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
        value=body.value,
    )
    if not item:
        raise HTTPException(status_code=404, detail="memory_item_not_found")
    return item


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: str, request: Request):
    owner_key, user_id, role = _owner(request)
    if not await memory.delete_memory_item(
        item_id,
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
    ):
        raise HTTPException(status_code=404, detail="memory_item_not_found")
    return None


@router.delete("/items")
async def delete_all_items(request: Request):
    owner_key, user_id, role = _owner(request)
    deleted_count = await memory.delete_all_memory_items(
        owner_key=owner_key,
        real_user_id=user_id,
        role_context=role,
    )
    return {"deleted_count": deleted_count}
