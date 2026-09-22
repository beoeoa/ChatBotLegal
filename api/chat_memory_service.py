"""Owner-scoped session and opt-in long-term memory for legal chat.

The legal corpus remains the only source of legal truth.  This module stores
conversation anchors and user-approved preferences; it never stores model
prose as a fact and never writes into the retrieval/vector corpus.

Migration 48 defines the SurrealDB schema.  Until that migration is approved
and applied, the same contracts use an owner-scoped JSON fallback so the
feature can be exercised locally without mutating a live database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from loguru import logger

from api import conversation_service as conversations
from api.legal_simplified_pipeline import extract_actor_anchors
from open_notebook.database.repository import (
    ensure_record_id,
    repo_create,
    repo_query,
    repo_update,
)

STATE_VERSION = "conversation-state-v1"
STATE_V2_VERSION = "conversation-state-v2"
MEMORY_VERSION = "user-memory-v1"
MEMORY_TTL_DAYS = 365
_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_ROLES = {"citizen", "officer"}
_ALLOWED_MEMORY_KEYS = {
    "preferred_address",
    "ward_scope",
    "response_style",
    "tracked_procedure",
}
_ALLOWED_STATE_FIELDS = {
    "canonical_domain",
    "temporal_scope",
    "legal_as_of",
    "procedure",
    "actors",
    "legal_objects",
    "locations",
    "answered_facets",
    "unresolved_facets",
    "active_document",
    "recent_source_refs",
    "conversation_digest",
    "conversation_digest_v2",
}
_SUGGESTION_FACETS = {
    "authority",
    "deadline",
    "documents",
    "form",
    "procedure",
    "condition",
    "verification",
    "next_action",
    "fee",
    "rule",
}

_STATE_LOCKS: dict[str, asyncio.Lock] = {}
_STATE_LOCKS_GUARD = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    return text or None


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _fold(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", _compact(value).casefold())
    return "".join(
        char for char in normalized if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")


def _checksum(*values: Any) -> str:
    raw = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _role(value: Any) -> str:
    return _compact(value or "citizen").casefold()


def _usable_domain(value: Any) -> str | None:
    """Return only a concrete domain that is safe to persist as memory.

    Follow-up questions are intentionally short and their current-turn router
    can return ``unknown``. That sentinel must never erase a concrete domain
    already established by an earlier completed turn.
    """

    candidate = _compact(value)
    if not candidate or _fold(candidate) in {
        "unknown",
        "administrative",
        "khong xac dinh",
        "chua xac dinh",
    }:
        return None
    return candidate


def _domain_topic_label(value: Any) -> str | None:
    """Return a safe conversational topic label from backend-owned domain state."""

    candidate = _usable_domain(value)
    if not candidate:
        return None
    return {
        "ho_tich_chung_thuc": "Hộ tịch - Chứng thực",
        "ho_tich": "Hộ tịch - Chứng thực",
        "dat_dai_xay_dung": "Đất đai - Xây dựng",
        "dat_dai": "Đất đai - Xây dựng",
        "an_sinh_y_te_giao_duc": "An sinh - Y tế - Giáo dục",
        "an_sinh": "An sinh - Y tế - Giáo dục",
        "cu_tru_an_ninh": "Cư trú - An ninh",
        "cu_tru": "Cư trú - An ninh",
        "khieu_nai_to_cao": "Khiếu nại - Tố cáo",
        "khieu_nai": "Khiếu nại - Tố cáo",
    }.get(_fold(candidate).replace(" ", "_"), candidate[:120])


def _normalize_memory_value(memory_key: str, value: Any) -> Any | None:
    """Keep long-term items inside the approved, non-legal memory schema."""

    if memory_key in {"preferred_address", "response_style"}:
        text = _compact(value)[:120]
        return text or None
    if memory_key == "ward_scope":
        raw_values = value if isinstance(value, (list, tuple)) else [value]
        values = [_compact(item)[:120] for item in raw_values]
        values = [item for item in values if item]
        return list(dict.fromkeys(values))[:5] or None
    if memory_key == "tracked_procedure":
        if not isinstance(value, Mapping):
            return None
        procedure_id = _compact(value.get("id") or value.get("procedure_id"))[:80]
        procedure_name = _compact(value.get("name") or value.get("procedure_name"))[:200]
        if not (procedure_id or procedure_name):
            return None
        return {"id": procedure_id or None, "name": procedure_name or None}
    return None


def _procedure_memory_identity(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    return _fold(value.get("id") or value.get("name"))


def is_chat_memory_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    normalized_role = _role(role)
    if normalized_role not in _ALLOWED_ROLES:
        return False
    if _fold(values.get("CHAT_MEMORY_V1_ENABLED", "false")) not in _TRUE_VALUES:
        return False
    roles = {
        _role(item)
        for item in str(values.get("CHAT_MEMORY_V1_ROLES", "")).split(",")
        if _compact(item)
    }
    return normalized_role in roles


def is_long_term_memory_runtime_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    # Product default: a new conversation must not inherit prior-chat facts.
    if values.get("CHAT_MEMORY_SCOPE", "conversation").strip().lower() != "opt_in":
        return False
    return is_chat_memory_enabled(role, environ=values) and (
        _fold(values.get("CHAT_LONG_TERM_MEMORY_V1_ENABLED", "false"))
        in _TRUE_VALUES
    )


def extract_legal_object_anchors(value: Any) -> tuple[str, ...]:
    folded = _fold(value)
    values: list[str] = []
    for marker, label in (
        ("quyet dinh xu phat", "quyết định xử phạt"),
        ("quyet dinh hanh chinh", "quyết định hành chính"),
        ("dang ky lai khai sinh", "đăng ký lại khai sinh"),
        ("tro cap huu tri xa hoi", "trợ cấp hưu trí xã hội"),
        ("dang ky tam tru", "đăng ký tạm trú"),
        ("khieu nai lan dau", "khiếu nại lần đầu"),
        ("tach thua", "tách thửa"),
        ("to cao", "tố cáo"),
    ):
        if marker in folded:
            values.append(label)
    return tuple(dict.fromkeys(values))


def extract_location_anchors(value: Any) -> tuple[str, ...]:
    """Extract only explicit Vietnamese administrative-place anchors."""

    text = _compact(value)
    folded = _fold(text)
    locations: list[str] = []
    if "hai phong" in folded:
        locations.append("Hải Phòng")
    # Keep the complete short place phrase, not a guessed normalized ward.
    for match in re.finditer(
        r"\b(?:phường|xã|quận|huyện|thành phố)\s+[A-ZÀ-ỸĐ][\wÀ-ỹĐđ.-]*(?:\s+[A-ZÀ-ỸĐ][\wÀ-ỹĐđ.-]*){0,3}",
        text,
        flags=re.UNICODE,
    ):
        candidate = _compact(match.group(0)).rstrip(".,;:?!")
        if candidate and candidate not in locations:
            locations.append(candidate)
    return tuple(locations[:4])


def extract_explicit_safe_preferences(value: Any) -> dict[str, str]:
    """Extract only explicit address/style preferences, never case facts."""

    text = _compact(value)
    folded = _fold(text)
    preferences: dict[str, str] = {}
    address_match = re.search(
        r"(?:hãy|xin)?\s*gọi\s+tôi\s+là\s+(.{2,60}?)(?=\s+và\s+trả\s+lời|[.,;!?]|$)",
        text,
        flags=re.IGNORECASE,
    )
    if address_match:
        address = _compact(address_match.group(1))
        if len(address.split()) <= 8:
            preferences["preferred_address"] = address
    for marker, label in (
        ("tra loi ngan, de hieu", "ngắn, dễ hiểu"),
        ("tra loi ngan va de hieu", "ngắn, dễ hiểu"),
        ("tra loi ngan gon", "ngắn gọn"),
        ("tra loi ngan", "ngắn gọn"),
        ("tra loi chi tiet", "chi tiết"),
        ("tra loi de hieu", "dễ hiểu"),
        ("dung gach dau dong", "ưu tiên gạch đầu dòng"),
    ):
        if marker in folded:
            preferences["response_style"] = label
            break
    return preferences


def _empty_state(conversation_id: str, role: str) -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "conversation_id": conversation_id,
        "role_context": _role(role),
        "canonical_domain": None,
        "temporal_scope": None,
        "legal_as_of": None,
        "procedure": None,
        "actors": [],
        "legal_objects": [],
        "locations": [],
        "answered_facets": [],
        "unresolved_facets": [],
        "active_document": None,
        "recent_source_refs": [],
        "conversation_digest": None,
        "conversation_digest_v2": None,
        "digest_revision": 0,
        "digest_through_message_id": None,
        "digest_checksum": None,
        "compaction_summary_mode": None,
        "compaction_summary_model_id": None,
        "source_turn_ids": [],
        "model_option_id": None,
        "model_display_name": None,
        "model_locked": False,
        "generation_provenance": None,
        "revision": 0,
        "last_turn_checksum": None,
        "updated_at": None,
        "expires_at": None,
    }


def _state_public(value: Mapping[str, Any], *, include_internal: bool = False) -> dict[str, Any]:
    state = dict(value.get("state") or value)
    public = {
        "version": str(state.get("version") or STATE_VERSION),
        "conversation_id": str(
            state.get("conversation_id")
            or conversations.record_id_str(value.get("conversation"))
            or ""
        ),
        "role_context": _role(state.get("role_context") or value.get("role_context")),
        "canonical_domain": state.get("canonical_domain"),
        "temporal_scope": state.get("temporal_scope"),
        "legal_as_of": _iso(state.get("legal_as_of")),
        "procedure": state.get("procedure") if isinstance(state.get("procedure"), dict) else None,
        "actors": list(state.get("actors") or []),
        "legal_objects": list(state.get("legal_objects") or []),
        "locations": list(state.get("locations") or []),
        "answered_facets": list(state.get("answered_facets") or []),
        "unresolved_facets": list(state.get("unresolved_facets") or []),
        "active_document": (
            dict(state.get("active_document"))
            if isinstance(state.get("active_document"), Mapping)
            else None
        ),
        "recent_source_refs": [
            dict(item)
            for item in (state.get("recent_source_refs") or [])
            if isinstance(item, Mapping)
        ][:5],
        "model_option_id": _compact(state.get("model_option_id")) or None,
        "model_display_name": _compact(state.get("model_display_name")) or None,
        "model_locked": False,
        "generation_provenance": (
            dict(state.get("generation_provenance"))
            if isinstance(state.get("generation_provenance"), Mapping)
            else None
        ),
        "conversation_digest": _compact(state.get("conversation_digest")) or None,
        "conversation_digest_v2": (
            dict(state.get("conversation_digest_v2"))
            if isinstance(state.get("conversation_digest_v2"), Mapping)
            else None
        ),
        "digest_revision": int(state.get("digest_revision") or 0),
        "digest_through_message_id": _compact(
            state.get("digest_through_message_id")
        ) or None,
        "digest_checksum": _compact(state.get("digest_checksum")) or None,
        "compaction_summary_mode": _compact(
            state.get("compaction_summary_mode")
        ) or None,
        "compaction_summary_model_id": _compact(
            state.get("compaction_summary_model_id")
        ) or None,
        "source_turn_ids": list(state.get("source_turn_ids") or []),
        "revision": int(state.get("revision") or value.get("revision") or 0),
        "updated_at": _iso(state.get("updated_at") or value.get("updated_at")),
        "expires_at": _iso(state.get("expires_at") or value.get("expires_at")),
    }
    if include_internal:
        public["last_turn_checksum"] = state.get("last_turn_checksum")
        public["record_id"] = conversations.record_id_str(value.get("id"))
    return public


def _memory_root(owner_key: str) -> Path:
    path = Path(conversations._json_owner_dir(owner_key)) / "_memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _state_path(owner_key: str, conversation_id: str) -> Path:
    path = _memory_root(owner_key) / "conversation_states"
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{conversation_id}.json"


def _long_term_path(owner_key: str, role: str) -> Path:
    return _memory_root(owner_key) / f"long_term_{_role(role)}.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


async def _state_lock(owner_key: str, conversation_id: str) -> asyncio.Lock:
    key = f"{owner_key}:{conversation_id}"
    async with _STATE_LOCKS_GUARD:
        return _STATE_LOCKS.setdefault(key, asyncio.Lock())


async def _owned_conversation(
    conversation_id: str,
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
) -> dict[str, Any] | None:
    if _role(role_context) not in _ALLOWED_ROLES:
        return None
    return await conversations.get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=_role(role_context),
        is_admin=False,
        include_messages=False,
    )


async def get_conversation_state(
    conversation_id: str,
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
    include_internal: bool = False,
) -> dict[str, Any] | None:
    if not await _owned_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
    ):
        return None
    if real_user_id:
        try:
            rows = await repo_query(
                "SELECT * FROM conversation_state WHERE conversation = $conversation LIMIT 1;",
                {
                    "conversation": ensure_record_id(
                        conversations.full_record_id("conversation", conversation_id)
                        or conversation_id
                    )
                },
            )
            if rows:
                return _state_public(rows[0], include_internal=include_internal)
        except Exception as exc:
            logger.debug("Conversation-state table unavailable; using JSON fallback: {}", type(exc).__name__)
    value = _read_json(_state_path(owner_key, conversation_id), None)
    if isinstance(value, dict):
        return _state_public(value, include_internal=include_internal)
    return _state_public(
        _empty_state(conversation_id, role_context),
        include_internal=include_internal,
    )


async def forget_conversation_state_fields(
    conversation_id: str,
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
    forget_fields: Sequence[str],
) -> dict[str, Any] | None:
    fields = {str(item).strip() for item in forget_fields} & _ALLOWED_STATE_FIELDS
    lock = await _state_lock(owner_key, conversation_id)
    async with lock:
        state = await get_conversation_state(
            conversation_id,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            include_internal=True,
        )
        if state is None:
            return None
        for field in fields:
            if field in {
                "actors",
                "legal_objects",
                "locations",
                "answered_facets",
                "unresolved_facets",
                "recent_source_refs",
            }:
                state[field] = []
            else:
                state[field] = None
        expected_revision = int(state.get("revision") or 0)
        state["revision"] = expected_revision + 1
        state["updated_at"] = _now().isoformat()
        saved = await _save_state(
            state,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            expected_revision=expected_revision,
        )
        if not saved:
            return await get_conversation_state(
                conversation_id,
                owner_key=owner_key,
                real_user_id=real_user_id,
                role_context=role_context,
            )
        return _state_public(state)


async def update_active_document_state(
    conversation_id: str,
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
    document_id: str | None = None,
    clear: bool = False,
    pinned: bool | None = None,
) -> dict[str, Any] | None:
    """Select, clear, pin, or unpin one reviewed source already seen in chat.

    The endpoint never accepts client-authored document metadata. A document
    can become active only when it already exists in the backend-projected
    ``recent_source_refs`` list for this owner-scoped conversation.
    """

    lock = await _state_lock(owner_key, conversation_id)
    async with lock:
        state = await get_conversation_state(
            conversation_id,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            include_internal=True,
        )
        if state is None:
            return None
        expected_revision = int(state.get("revision") or 0)
        current = (
            dict(state.get("active_document"))
            if isinstance(state.get("active_document"), Mapping)
            else None
        )
        if clear:
            state["active_document"] = None
        elif document_id:
            target = _fold(document_id)
            selected = next(
                (
                    dict(item)
                    for item in state.get("recent_source_refs") or []
                    if isinstance(item, Mapping)
                    and _fold(item.get("document_id")) == target
                ),
                None,
            )
            if selected is None:
                raise ValueError("active_document_not_in_conversation_sources")
            selected["pinned"] = bool(pinned) if pinned is not None else False
            state["active_document"] = selected
        elif current is not None and pinned is not None:
            current["pinned"] = bool(pinned)
            state["active_document"] = current
        else:
            return _state_public(state)

        state["revision"] = expected_revision + 1
        state["updated_at"] = _now().isoformat()
        state["expires_at"] = (
            _now() + timedelta(days=MEMORY_TTL_DAYS)
        ).isoformat()
        saved = await _save_state(
            state,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            expected_revision=expected_revision,
        )
        if not saved:
            return await get_conversation_state(
                conversation_id,
                owner_key=owner_key,
                real_user_id=real_user_id,
                role_context=role_context,
            )
        return _state_public(state)


async def _save_state(
    state: Mapping[str, Any],
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
    expected_revision: int | None = None,
) -> bool:
    conversation_id = str(state.get("conversation_id") or "")
    stored = dict(state)
    if real_user_id:
        try:
            conversation_ref = ensure_record_id(
                conversations.full_record_id("conversation", conversation_id)
                or conversation_id
            )
            rows = await repo_query(
                "SELECT * FROM conversation_state WHERE conversation = $conversation LIMIT 1;",
                {"conversation": conversation_ref},
            )
            payload = {
                "conversation": conversation_ref,
                "owner_user": ensure_record_id(
                    conversations.full_record_id("user_account", real_user_id)
                    or real_user_id
                ),
                "role_context": _role(role_context),
                "state": stored,
                "revision": int(stored.get("revision") or 0),
                "expires_at": stored.get("expires_at"),
                "updated_at": _now(),
            }
            if rows:
                if expected_revision is not None:
                    updated = await repo_query(
                        "UPDATE $record MERGE $payload WHERE revision = $expected_revision RETURN AFTER;",
                        {
                            "record": ensure_record_id(str(rows[0].get("id"))),
                            "payload": payload,
                            "expected_revision": expected_revision,
                        },
                    )
                    if not updated:
                        return False
                else:
                    await repo_update(
                        "conversation_state",
                        str(rows[0].get("id")),
                        payload,
                    )
            else:
                if expected_revision not in {None, 0}:
                    return False
                payload["created_at"] = _now()
                await repo_create("conversation_state", payload)
            return True
        except Exception as exc:
            logger.debug("Conversation-state write fell back to JSON: {}", type(exc).__name__)
    if expected_revision is not None:
        current = _read_json(_state_path(owner_key, conversation_id), None)
        current_revision = int((current or {}).get("revision") or 0)
        if current_revision != expected_revision:
            return False
    _write_json(_state_path(owner_key, conversation_id), stored)
    return True


async def update_conversation_compaction_digest(
    *,
    conversation_id: str,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
    summary: Mapping[str, Any],
    through_message_id: str,
    summary_checksum: str,
    model_id: str | None,
) -> dict[str, Any] | None:
    """Persist a LangGraph-authored running summary in conversation state.

    The summary has already passed ``validate_conversation_patch_v2`` inside
    the owner/role-scoped LangGraph node. This update touches only compaction
    metadata; legal state, citations and the source transcript are unchanged.
    """

    if not conversation_id or not isinstance(summary, Mapping):
        return None
    lock = await _state_lock(owner_key, conversation_id)
    async with lock:
        state = await get_conversation_state(
            conversation_id,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            include_internal=True,
        ) or _empty_state(conversation_id, role_context)
        checksum = _compact(summary_checksum)
        if checksum and checksum == _compact(state.get("digest_checksum")):
            return _state_public(state)
        merged = _merge_conversation_digest_v2(
            state.get("conversation_digest_v2"),
            summary,
        )
        expected_revision = int(state.get("revision") or 0)
        now = _now()
        state.update(
            {
                "version": STATE_V2_VERSION,
                "conversation_digest_v2": merged,
                "digest_revision": int(state.get("digest_revision") or 0) + 1,
                "digest_through_message_id": _compact(through_message_id) or None,
                "digest_checksum": checksum or _checksum(
                    merged,
                    through_message_id,
                ),
                "compaction_summary_mode": "langgraph_llm",
                "compaction_summary_model_id": _compact(model_id) or None,
                "revision": expected_revision + 1,
                "updated_at": now.isoformat(),
                "expires_at": (now + timedelta(days=MEMORY_TTL_DAYS)).isoformat(),
            }
        )
        saved = await _save_state(
            state,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            expected_revision=expected_revision,
        )
        if not saved:
            return await get_conversation_state(
                conversation_id,
                owner_key=owner_key,
                real_user_id=real_user_id,
                role_context=role_context,
            )
        return _state_public(state)


def _latest_user_turn(conversation: Mapping[str, Any]) -> str | None:
    for message in reversed(list(conversation.get("messages") or [])):
        if str(message.get("role") or message.get("sender_role") or "") == "user":
            return str(message.get("id") or "") or None
    return None


async def update_conversation_state_after_answer(
    *,
    conversation_id: str,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
    question: str,
    answer: str,
    canonical_domain: str | None,
    legal_as_of: Any,
    procedure_detail: Mapping[str, Any] | None,
    required_facets: Sequence[str],
    unresolved_facets: Sequence[str],
    citations: Sequence[Mapping[str, Any]] = (),
    requested_active_document_id: str | None = None,
    conversation_patch: Mapping[str, Any] | None = None,
    digest_through_message_id: str | None = None,
    model_option_id: str | None = None,
    model_display_name: str | None = None,
    generation_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Deterministically update state after a completed assistant turn."""

    from api.conversational_orchestrator import (
        is_conversational_orchestrator_enabled,
        is_llm_router_v2_enabled,
    )

    if not (
        is_chat_memory_enabled(role_context)
        or is_conversational_orchestrator_enabled(role_context)
        or is_llm_router_v2_enabled(role_context)
    ):
        return None
    conversation = await conversations.get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=_role(role_context),
        is_admin=False,
        include_messages=True,
        message_limit=100,
    )
    if not conversation:
        return None
    lock = await _state_lock(owner_key, conversation_id)
    async with lock:
        state = await get_conversation_state(
            conversation_id,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            include_internal=True,
        ) or _empty_state(conversation_id, role_context)
        turn_checksum = _checksum(conversation_id, _compact(question), _compact(answer))
        if state.get("last_turn_checksum") == turn_checksum:
            return _state_public(state)

        previous_domain = _usable_domain(state.get("canonical_domain"))
        current_domain = _usable_domain(canonical_domain)
        effective_domain = current_domain or previous_domain
        domain_changed = bool(
            current_domain
            and previous_domain
            and current_domain != previous_domain
        )

        actors = list(extract_actor_anchors(question))
        legal_objects = list(extract_legal_object_anchors(question))
        locations = list(extract_location_anchors(question))
        procedure = None
        if isinstance(procedure_detail, Mapping):
            procedure_id = _compact(
                procedure_detail.get("procedure_id") or procedure_detail.get("id")
            )
            procedure_name = _compact(
                procedure_detail.get("procedure_name") or procedure_detail.get("name")
            )
            if procedure_id or procedure_name:
                procedure = {"id": procedure_id or None, "name": procedure_name or None}

        required = [str(item) for item in required_facets if _compact(item)]
        unresolved = [str(item) for item in unresolved_facets if _compact(item)]
        answered = [item for item in required if item not in set(unresolved)]
        source_turn = _latest_user_turn(conversation)
        from api.conversational_orchestrator import (
            document_reference_from_citation,
            resolve_explicit_document_reference,
        )

        new_references = [
            reference
            for reference in (
                document_reference_from_citation(citation)
                for citation in citations
                if isinstance(citation, Mapping)
            )
            if reference is not None
        ]
        recent_references: list[dict[str, Any]] = []
        seen_reference_ids: set[str] = set()
        for reference in [
            *new_references,
            *[
                dict(item)
                for item in state.get("recent_source_refs") or []
                if isinstance(item, Mapping)
            ],
        ]:
            identity = _fold(reference.get("document_id"))
            if not identity or identity in seen_reference_ids:
                continue
            seen_reference_ids.add(identity)
            recent_references.append(reference)
            if len(recent_references) >= 5:
                break

        active_document = (
            dict(state.get("active_document"))
            if isinstance(state.get("active_document"), Mapping)
            else None
        )
        if domain_changed and not (active_document or {}).get("pinned"):
            active_document = None
        explicitly_named_reference = resolve_explicit_document_reference(
            question,
            new_references,
        )
        requested_id = _fold(requested_active_document_id)
        if requested_id:
            requested_reference = next(
                (
                    dict(item)
                    for item in recent_references
                    if _fold(item.get("document_id")) == requested_id
                ),
                None,
            )
            if requested_reference:
                requested_reference["pinned"] = bool(
                    (active_document or {}).get("pinned")
                    and _fold((active_document or {}).get("document_id")) == requested_id
                )
                active_document = requested_reference
        elif explicitly_named_reference:
            explicitly_named_reference["pinned"] = False
            active_document = explicitly_named_reference
        elif not (active_document or {}).get("pinned") and new_references:
            active_document = dict(new_references[0])
            active_document["pinned"] = False

        completed_messages = [
            message
            for message in conversation.get("messages") or []
            if str(message.get("status") or "complete") not in {
                "pending",
                "error",
                "cancelled",
            }
        ]
        digest = None
        digest_through = None
        if len(completed_messages) > 12:
            older_messages = completed_messages[:-12]
            older_questions = [
                _compact(item.get("content"))[:240]
                for item in older_messages
                if str(item.get("role") or item.get("sender_role") or "") == "user"
                and _compact(item.get("content"))
            ][-8:]
            if older_questions:
                digest = " | ".join(older_questions)
                digest_through = str(older_messages[-1].get("id") or "") or None
        now = _now()
        # Keep a backend-owned domain trail in the compacted memory.  The model
        # patch remains useful for natural summaries, but topic recall must not
        # depend on whether a small/local model happened to repeat every older
        # domain in its latest JSON envelope.
        digest_patch = (
            dict(conversation_patch)
            if isinstance(conversation_patch, Mapping)
            else {}
        )
        domain_topic = _domain_topic_label(current_domain)
        if domain_topic:
            digest_patch["topics"] = [
                domain_topic,
                *list(digest_patch.get("topics") or []),
            ]
        digest_v2 = (
            _merge_conversation_digest_v2(
                state.get("conversation_digest_v2"),
                digest_patch,
            )
            if digest_patch
            else None
        )
        next_digest_revision = int(state.get("digest_revision") or 0)
        if digest_v2 is not None:
            next_digest_revision += 1
        effective_digest_through = (
            _compact(digest_through_message_id)
            if digest_v2 is not None
            else digest_through
        ) or state.get("digest_through_message_id")
        state.update(
            {
                "version": (
                    STATE_V2_VERSION
                    if digest_v2 is not None
                    or isinstance(state.get("conversation_digest_v2"), Mapping)
                    else STATE_VERSION
                ),
                "conversation_id": conversation_id,
                "role_context": _role(role_context),
                "canonical_domain": (
                    effective_domain
                ),
                "temporal_scope": state.get("temporal_scope") or "current",
                "legal_as_of": _iso(legal_as_of) or state.get("legal_as_of"),
                "procedure": (
                    procedure
                    if procedure is not None
                    else None if domain_changed else state.get("procedure")
                ),
                "actors": (
                    actors
                    if actors
                    else [] if domain_changed else list(state.get("actors") or [])
                ),
                "legal_objects": (
                    legal_objects
                    if legal_objects
                    else [] if domain_changed else list(state.get("legal_objects") or [])
                ),
                "locations": locations or list(state.get("locations") or []),
                "answered_facets": list(dict.fromkeys(answered)),
                "unresolved_facets": list(dict.fromkeys(unresolved)),
                "active_document": active_document,
                "recent_source_refs": recent_references,
                "conversation_digest": digest or state.get("conversation_digest"),
                "conversation_digest_v2": (
                    digest_v2
                    if digest_v2 is not None
                    else state.get("conversation_digest_v2")
                ),
                "digest_revision": next_digest_revision,
                "digest_through_message_id": effective_digest_through,
                "digest_checksum": (
                    _checksum(
                        digest_v2,
                        effective_digest_through,
                        next_digest_revision,
                    )
                    if digest_v2 is not None
                    else _checksum(digest, effective_digest_through)
                    if digest
                    else state.get("digest_checksum")
                ),
                "source_turn_ids": list(
                    dict.fromkeys(
                        [
                            *list(state.get("source_turn_ids") or []),
                            *([source_turn] if source_turn else []),
                        ]
                    )
                )[-12:],
                "model_option_id": _compact(model_option_id) or None,
                "model_display_name": _compact(model_display_name) or None,
                "model_locked": False,
                "generation_provenance": dict(generation_provenance or {}) or None,
                "revision": int(state.get("revision") or 0) + 1,
                "last_turn_checksum": turn_checksum,
                "updated_at": now.isoformat(),
                "expires_at": (now + timedelta(days=MEMORY_TTL_DAYS)).isoformat(),
            }
        )
        expected_revision = int(state.get("revision") or 0) - 1
        saved = await _save_state(
            state,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            expected_revision=expected_revision,
        )
        if not saved:
            logger.info(
                "ChatMemory state update skipped after optimistic revision conflict role={}",
                _role(role_context),
            )
            return await get_conversation_state(
                conversation_id,
                owner_key=owner_key,
                real_user_id=real_user_id,
                role_context=role_context,
            )
        await _sync_safe_long_term_items(
            state,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            source_message_id=source_turn,
            current_question=question,
        )
        return _state_public(state)


def _default_long_term(role: str) -> dict[str, Any]:
    return {
        "version": MEMORY_VERSION,
        "role_context": _role(role),
        "enabled": False,
        "enabled_at": None,
        "updated_at": None,
        "items": [],
    }


async def _load_long_term(owner_key: str, real_user_id: str | None, role: str) -> dict[str, Any]:
    normalized_role = _role(role)
    if real_user_id:
        try:
            owner_ref = ensure_record_id(
                conversations.full_record_id("user_account", real_user_id)
                or real_user_id
            )
            preferences = await repo_query(
                "SELECT * FROM chat_memory_preference WHERE owner_user = $owner AND role_context = $role LIMIT 1;",
                {"owner": owner_ref, "role": normalized_role},
            )
            items = await repo_query(
                "SELECT * FROM user_memory_item WHERE owner_user = $owner AND role_context = $role AND status = 'active' ORDER BY updated_at DESC;",
                {"owner": owner_ref, "role": normalized_role},
            )
            preference = preferences[0] if preferences else {}
            return {
                "version": MEMORY_VERSION,
                "role_context": normalized_role,
                "enabled": bool(preference.get("enabled")),
                "enabled_at": _iso(preference.get("enabled_at")),
                "updated_at": _iso(preference.get("updated_at")),
                "items": [_memory_item_public(item) for item in (items or [])],
            }
        except Exception as exc:
            logger.debug("Long-term-memory tables unavailable; using JSON fallback: {}", type(exc).__name__)
    value = _read_json(_long_term_path(owner_key, normalized_role), None)
    return value if isinstance(value, dict) else _default_long_term(normalized_role)


def _memory_item_public(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(conversations.record_id_str(item.get("id")) or item.get("id") or ""),
        "memory_key": str(item.get("memory_key") or ""),
        "value": item.get("value"),
        "label": item.get("label"),
        "source_kind": str(item.get("source_kind") or "user_explicit"),
        "source_conversation_id": conversations.record_id_str(item.get("source_conversation")) or item.get("source_conversation_id"),
        "source_message_id": conversations.record_id_str(item.get("source_message")) or item.get("source_message_id"),
        "status": str(item.get("status") or "active"),
        "consent_at": _iso(item.get("consent_at")),
        "last_used_at": _iso(item.get("last_used_at")),
        "expires_at": _iso(item.get("expires_at")),
        "revision": int(item.get("revision") or 1),
        "created_at": _iso(item.get("created_at")),
        "updated_at": _iso(item.get("updated_at")),
    }


async def get_memory_settings(
    *, owner_key: str, real_user_id: str | None, role_context: str
) -> dict[str, Any]:
    data = await _load_long_term(owner_key, real_user_id, role_context)
    return {
        "enabled": bool(data.get("enabled")),
        "role_context": _role(role_context),
        "runtime_available": is_long_term_memory_runtime_enabled(role_context),
        "enabled_at": data.get("enabled_at"),
        "updated_at": data.get("updated_at"),
        "retention_days": MEMORY_TTL_DAYS,
    }


async def set_memory_enabled(
    enabled: bool,
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
) -> dict[str, Any]:
    normalized_role = _role(role_context)
    if normalized_role not in _ALLOWED_ROLES:
        raise ValueError("memory_role_not_supported")
    now = _now()
    if real_user_id:
        try:
            owner_ref = ensure_record_id(
                conversations.full_record_id("user_account", real_user_id)
                or real_user_id
            )
            rows = await repo_query(
                "SELECT * FROM chat_memory_preference WHERE owner_user = $owner AND role_context = $role LIMIT 1;",
                {"owner": owner_ref, "role": normalized_role},
            )
            payload = {
                "owner_user": owner_ref,
                "role_context": normalized_role,
                "enabled": bool(enabled),
                "enabled_at": now if enabled else None,
                "updated_at": now,
            }
            if rows:
                await repo_update("chat_memory_preference", str(rows[0].get("id")), payload)
            else:
                payload["created_at"] = now
                await repo_create("chat_memory_preference", payload)
            return await get_memory_settings(
                owner_key=owner_key,
                real_user_id=real_user_id,
                role_context=normalized_role,
            )
        except Exception as exc:
            logger.debug("Memory preference write fell back to JSON: {}", type(exc).__name__)
    data = await _load_long_term(owner_key, None, normalized_role)
    data.update(
        {
            "enabled": bool(enabled),
            "enabled_at": now.isoformat() if enabled else None,
            "updated_at": now.isoformat(),
        }
    )
    _write_json(_long_term_path(owner_key, normalized_role), data)
    return await get_memory_settings(
        owner_key=owner_key,
        real_user_id=None,
        role_context=normalized_role,
    )


async def list_memory_items(
    *, owner_key: str, real_user_id: str | None, role_context: str
) -> list[dict[str, Any]]:
    data = await _load_long_term(owner_key, real_user_id, role_context)
    now = _now()
    active: list[dict[str, Any]] = []
    for item in data.get("items") or []:
        expires_at = conversations._as_utc_datetime(item.get("expires_at"))
        if str(item.get("status") or "active") != "active":
            continue
        if expires_at and expires_at <= now:
            continue
        if str(item.get("memory_key") or "") not in _ALLOWED_MEMORY_KEYS:
            continue
        active.append(_memory_item_public(item))
    return active


async def _upsert_memory_item(
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
    memory_key: str,
    value: Any,
    label: str | None,
    source_kind: str,
    source_conversation_id: str | None,
    source_message_id: str | None,
) -> dict[str, Any] | None:
    if memory_key not in _ALLOWED_MEMORY_KEYS:
        return None
    value = _normalize_memory_value(memory_key, value)
    if value is None:
        return None
    settings = await get_memory_settings(
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
    )
    if not settings.get("enabled"):
        return None
    now = _now()
    expires = now + timedelta(days=MEMORY_TTL_DAYS)
    normalized_role = _role(role_context)
    if real_user_id:
        try:
            owner_ref = ensure_record_id(
                conversations.full_record_id("user_account", real_user_id)
                or real_user_id
            )
            candidates = await repo_query(
                "SELECT * FROM user_memory_item WHERE owner_user = $owner AND role_context = $role AND memory_key = $key AND status = 'active';",
                {"owner": owner_ref, "role": normalized_role, "key": memory_key},
            )
            rows = list(candidates or [])
            if memory_key == "tracked_procedure":
                identity = _procedure_memory_identity(value)
                rows = [
                    item
                    for item in rows
                    if _procedure_memory_identity(item.get("value")) == identity
                ]
            rows = rows[:1]
            payload = {
                "owner_user": owner_ref,
                "role_context": normalized_role,
                "memory_key": memory_key,
                "value": value,
                "label": label,
                "source_kind": source_kind,
                "source_conversation": ensure_record_id(
                    conversations.full_record_id("conversation", source_conversation_id)
                    or source_conversation_id
                ) if source_conversation_id else None,
                "source_message_id": source_message_id,
                "status": "active",
                "consent_at": settings.get("enabled_at") or now,
                "expires_at": expires,
                "updated_at": now,
                "revision": int((rows[0] if rows else {}).get("revision") or 0) + 1,
            }
            if rows:
                await repo_update("user_memory_item", str(rows[0].get("id")), payload)
                saved = {**rows[0], **payload}
            else:
                payload["created_at"] = now
                saved_raw = await repo_create("user_memory_item", payload)
                saved = saved_raw[0] if isinstance(saved_raw, list) else saved_raw
            return _memory_item_public(saved)
        except Exception as exc:
            logger.debug("Memory item write fell back to JSON: {}", type(exc).__name__)
    data = await _load_long_term(owner_key, None, normalized_role)
    items = list(data.get("items") or [])
    existing = next(
        (
            item
            for item in items
            if item.get("memory_key") == memory_key
            and item.get("status", "active") == "active"
            and (
                memory_key != "tracked_procedure"
                or _procedure_memory_identity(item.get("value"))
                == _procedure_memory_identity(value)
            )
        ),
        None,
    )
    payload = {
        "id": str((existing or {}).get("id") or uuid.uuid4().hex[:12]),
        "memory_key": memory_key,
        "value": value,
        "label": label,
        "source_kind": source_kind,
        "source_conversation_id": source_conversation_id,
        "source_message_id": source_message_id,
        "status": "active",
        "consent_at": data.get("enabled_at") or now.isoformat(),
        "last_used_at": (existing or {}).get("last_used_at"),
        "expires_at": expires.isoformat(),
        "revision": int((existing or {}).get("revision") or 0) + 1,
        "created_at": (existing or {}).get("created_at") or now.isoformat(),
        "updated_at": now.isoformat(),
    }
    if existing:
        items[items.index(existing)] = payload
    else:
        items.append(payload)
    data["items"] = items
    data["updated_at"] = now.isoformat()
    _write_json(_long_term_path(owner_key, normalized_role), data)
    return _memory_item_public(payload)


async def _sync_safe_long_term_items(
    state: Mapping[str, Any],
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
    source_message_id: str | None,
    current_question: str,
) -> None:
    if not is_long_term_memory_runtime_enabled(role_context):
        return
    settings = await get_memory_settings(
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
    )
    if not settings.get("enabled"):
        return
    locations = list(state.get("locations") or [])
    if locations:
        await _upsert_memory_item(
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            memory_key="ward_scope",
            value=locations,
            label="Địa bàn thường dùng",
            source_kind="user_explicit",
            source_conversation_id=str(state.get("conversation_id") or "") or None,
            source_message_id=source_message_id,
        )
    for memory_key, value in extract_explicit_safe_preferences(current_question).items():
        await _upsert_memory_item(
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
            memory_key=memory_key,
            value=value,
            label=(
                "Cách xưng hô"
                if memory_key == "preferred_address"
                else "Phong cách trả lời"
            ),
            source_kind="user_explicit",
            source_conversation_id=str(state.get("conversation_id") or "") or None,
            source_message_id=source_message_id,
        )
    # A procedure is never copied automatically. It becomes long-term memory
    # only after the user explicitly chooses "Theo dõi thủ tục này".


async def track_conversation_procedure(
    conversation_id: str,
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
) -> dict[str, Any] | None:
    if not is_long_term_memory_runtime_enabled(role_context):
        return None
    state = await get_conversation_state(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        include_internal=False,
    )
    procedure = (state or {}).get("procedure")
    if not isinstance(procedure, Mapping) or not (
        procedure.get("id") or procedure.get("name")
    ):
        return None
    source_turn_ids = list((state or {}).get("source_turn_ids") or [])
    return await _upsert_memory_item(
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        memory_key="tracked_procedure",
        value={"id": procedure.get("id"), "name": procedure.get("name")},
        label=str(
            procedure.get("name")
            or procedure.get("id")
            or "Thủ tục đang theo dõi"
        ),
        source_kind="user_explicit",
        source_conversation_id=conversation_id,
        source_message_id=(str(source_turn_ids[-1]) if source_turn_ids else None),
    )


async def update_memory_item(
    item_id: str,
    *,
    owner_key: str,
    real_user_id: str | None,
    role_context: str,
    value: Any,
) -> dict[str, Any] | None:
    items = await list_memory_items(
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
    )
    item = next((candidate for candidate in items if candidate.get("id") == item_id), None)
    if not item:
        return None
    normalized_value = value
    if item.get("memory_key") == "tracked_procedure" and isinstance(value, str):
        previous = item.get("value") if isinstance(item.get("value"), Mapping) else {}
        normalized_value = {"id": previous.get("id"), "name": value}
    return await _upsert_memory_item(
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        memory_key=str(item["memory_key"]),
        value=normalized_value,
        label=item.get("label"),
        source_kind="user_explicit",
        source_conversation_id=item.get("source_conversation_id"),
        source_message_id=item.get("source_message_id"),
    )


async def delete_memory_item(
    item_id: str,
    *, owner_key: str, real_user_id: str | None, role_context: str
) -> bool:
    normalized_role = _role(role_context)
    if real_user_id:
        try:
            rows = await repo_query(
                "SELECT * FROM user_memory_item WHERE id = $id AND owner_user = $owner AND role_context = $role LIMIT 1;",
                {
                    "id": ensure_record_id(
                        conversations.full_record_id("user_memory_item", item_id) or item_id
                    ),
                    "owner": ensure_record_id(
                        conversations.full_record_id("user_account", real_user_id) or real_user_id
                    ),
                    "role": normalized_role,
                },
            )
            if not rows:
                return False
            await repo_update(
                "user_memory_item",
                str(rows[0].get("id")),
                {"status": "revoked", "updated_at": _now()},
            )
            return True
        except Exception as exc:
            logger.debug("Memory delete fell back to JSON: {}", type(exc).__name__)
    data = await _load_long_term(owner_key, None, normalized_role)
    changed = False
    for item in data.get("items") or []:
        if str(item.get("id") or "") == item_id and item.get("status", "active") == "active":
            item["status"] = "revoked"
            item["updated_at"] = _now().isoformat()
            changed = True
    if changed:
        _write_json(_long_term_path(owner_key, normalized_role), data)
    return changed


async def delete_all_memory_items(
    *, owner_key: str, real_user_id: str | None, role_context: str
) -> int:
    items = await list_memory_items(
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
    )
    count = 0
    for item in items:
        if await delete_memory_item(
            str(item.get("id")),
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
        ):
            count += 1
    return count


async def revoke_memory_from_conversation(
    conversation_id: str,
    *, owner_key: str, real_user_id: str | None, role_context: str
) -> None:
    items = await list_memory_items(
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
    )
    for item in items:
        if str(item.get("source_conversation_id") or "") == conversation_id:
            await delete_memory_item(
                str(item.get("id")),
                owner_key=owner_key,
                real_user_id=real_user_id,
                role_context=role_context,
            )
    path = _state_path(owner_key, conversation_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            logger.warning("Could not remove conversation memory state {}", conversation_id)
    if real_user_id:
        try:
            await repo_query(
                "DELETE conversation_state WHERE conversation = $conversation;",
                {
                    "conversation": ensure_record_id(
                        conversations.full_record_id("conversation", conversation_id)
                        or conversation_id
                    )
                },
            )
        except Exception:
            pass


async def _touch_memory_items(
    items: Sequence[Mapping[str, Any]],
    *, owner_key: str, real_user_id: str | None, role_context: str
) -> None:
    now = _now()
    expires = now + timedelta(days=MEMORY_TTL_DAYS)
    for item in items:
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        if real_user_id:
            try:
                await repo_update(
                    "user_memory_item",
                    conversations.full_record_id("user_memory_item", item_id) or item_id,
                    {"last_used_at": now, "expires_at": expires, "updated_at": now},
                )
                continue
            except Exception:
                pass
        data = await _load_long_term(owner_key, None, role_context)
        for raw in data.get("items") or []:
            if str(raw.get("id") or "") == item_id:
                raw["last_used_at"] = now.isoformat()
                raw["expires_at"] = expires.isoformat()
        _write_json(_long_term_path(owner_key, role_context), data)


async def build_memory_context(
    *,
    conversation_id: str | None,
    owner_key: str | None,
    real_user_id: str | None,
    role_context: str,
    current_question: str,
    selected_memory_item_ids: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return verified metadata messages and a privacy-safe usage summary."""

    from api.conversational_orchestrator import (
        is_conversational_orchestrator_enabled,
    )

    empty_usage = {
        "used": False,
        "inherited_fields": [],
        "source": None,
        "state_revision": 0,
    }
    if (
        not conversation_id
        or not owner_key
        or not (
            is_chat_memory_enabled(role_context)
            or is_conversational_orchestrator_enabled(role_context)
        )
    ):
        return [], empty_usage
    state = await get_conversation_state(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        include_internal=False,
    )
    if state is None:
        return [], empty_usage

    inherited_fields: list[str] = []
    source = "conversation"
    selected_items: list[dict[str, Any]] = []
    if is_long_term_memory_runtime_enabled(role_context):
        settings = await get_memory_settings(
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
        )
        if settings.get("enabled"):
            all_items = await list_memory_items(
                owner_key=owner_key,
                real_user_id=real_user_id,
                role_context=role_context,
            )
            selected_ids = {str(item) for item in selected_memory_item_ids}
            folded_question = _fold(current_question)
            for item in all_items:
                explicit = str(item.get("id")) in selected_ids
                if item.get("memory_key") == "tracked_procedure":
                    value = item.get("value") if isinstance(item.get("value"), Mapping) else {}
                    explicit = explicit or bool(
                        _fold(value.get("name"))
                        and _fold(value.get("name")) in folded_question
                    )
                elif item.get("memory_key") == "ward_scope":
                    # A saved location is a safe default only when the current
                    # question does not explicitly name another location.
                    explicit = explicit or not extract_location_anchors(current_question)
                else:
                    explicit = explicit or item.get("memory_key") in {
                        "preferred_address",
                        "response_style",
                    }
                if explicit:
                    selected_items.append(item)

    memory_state = dict(state)
    for field in (
        "canonical_domain",
        "temporal_scope",
        "legal_as_of",
        "procedure",
        "actors",
        "legal_objects",
        "locations",
        "active_document",
        "recent_source_refs",
        "conversation_digest",
        "conversation_digest_v2",
    ):
        value = memory_state.get(field)
        if value not in (None, [], {}, ""):
            inherited_fields.append(field)
    for item in selected_items:
        key = str(item.get("memory_key") or "")
        value = item.get("value")
        if key == "tracked_procedure" and not memory_state.get("procedure"):
            memory_state["procedure"] = value
            inherited_fields.append("procedure")
            source = "conversation+long_term"
        elif key == "ward_scope" and not memory_state.get("locations"):
            memory_state["locations"] = value if isinstance(value, list) else [value]
            inherited_fields.append("locations")
            source = "conversation+long_term"
        elif key in {"preferred_address", "response_style"}:
            memory_state[key] = value
            inherited_fields.append(key)
            source = "conversation+long_term"
    if selected_items:
        await _touch_memory_items(
            selected_items,
            owner_key=owner_key,
            real_user_id=real_user_id,
            role_context=role_context,
        )

    procedure = memory_state.get("procedure")
    procedure_detail = None
    if isinstance(procedure, Mapping) and (procedure.get("id") or procedure.get("name")):
        procedure_detail = {
            "procedure_id": procedure.get("id"),
            "procedure_name": procedure.get("name"),
        }
    metadata_message = {
        "id": f"memory-state-r{int(memory_state.get('revision') or 0)}",
        "role": "assistant",
        "sender_role": "assistant",
        "content": "",
        "status": "complete",
        "canonical_domain": memory_state.get("canonical_domain"),
        "procedure_detail": procedure_detail,
        "memory_state": {
            "version": STATE_VERSION,
            "actors": list(memory_state.get("actors") or []),
            "legal_objects": list(memory_state.get("legal_objects") or []),
            "locations": list(memory_state.get("locations") or []),
            "preferred_address": memory_state.get("preferred_address"),
            "response_style": memory_state.get("response_style"),
            "source_turn_ids": list(memory_state.get("source_turn_ids") or []),
            "active_document": (
                dict(memory_state.get("active_document"))
                if isinstance(memory_state.get("active_document"), Mapping)
                else None
            ),
            "recent_source_refs": [
                dict(item)
                for item in memory_state.get("recent_source_refs") or []
                if isinstance(item, Mapping)
            ][:5],
            "conversation_digest": memory_state.get("conversation_digest"),
            "conversation_digest_v2": memory_state.get(
                "conversation_digest_v2"
            ),
            "digest_revision": int(memory_state.get("digest_revision") or 0),
            "digest_through_message_id": memory_state.get(
                "digest_through_message_id"
            ),
            "revision": int(memory_state.get("revision") or 0),
        },
    }
    used = bool(inherited_fields)
    return (
        [metadata_message] if used else [],
        {
            "used": used,
            "inherited_fields": list(dict.fromkeys(inherited_fields)),
            "source": source if used else None,
            "state_revision": int(memory_state.get("revision") or 0),
        },
    )


_PRESENTATION_UNSAFE_BLOCK_RE = re.compile(
    r"<(?:think|script|style)\b[^>]*>.*?</(?:think|script|style)\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_PRESENTATION_UNCLOSED_UNSAFE_BLOCK_RE = re.compile(
    r"<(?:think|script|style)\b[^>]*>.*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)
_PRESENTATION_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)
_PRESENTATION_BREAK_RE = re.compile(r"<br\s*/?\s*>", flags=re.IGNORECASE)
_PRESENTATION_LIST_ITEM_OPEN_RE = re.compile(r"<li\b[^>]*>", flags=re.IGNORECASE)
_PRESENTATION_LIST_ITEM_CLOSE_RE = re.compile(r"</li\s*>", flags=re.IGNORECASE)
_PRESENTATION_BLOCK_OPEN_RE = re.compile(
    r"<(?:p|div|section|article|header|footer|h[1-6]|ul|ol|blockquote)\b[^>]*>",
    flags=re.IGNORECASE,
)
_PRESENTATION_BLOCK_CLOSE_RE = re.compile(
    r"</(?:p|div|section|article|header|footer|h[1-6]|ul|ol|blockquote)\s*>",
    flags=re.IGNORECASE,
)
_PRESENTATION_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]{0,1000}>")
_MARKDOWN_TABLE_DELIMITER_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)


def sanitize_model_answer_markdown(raw: Any) -> str:
    """Repair provider presentation artifacts without rewriting legal prose.

    The sanitizer is deliberately provider-neutral and conservative. It
    removes hidden/unsafe HTML blocks, converts layout HTML to Markdown text,
    repairs stray pipes outside real Markdown tables, and drops one unmatched
    bold marker. It does not add, delete, or infer legal claims or citations.
    """

    text = str(raw or "")
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _PRESENTATION_UNSAFE_BLOCK_RE.sub("", text)
    text = _PRESENTATION_UNCLOSED_UNSAFE_BLOCK_RE.sub("", text)
    text = _PRESENTATION_HTML_COMMENT_RE.sub("", text)
    text = _PRESENTATION_BREAK_RE.sub("\n", text)
    text = _PRESENTATION_LIST_ITEM_OPEN_RE.sub("\n- ", text)
    text = _PRESENTATION_LIST_ITEM_CLOSE_RE.sub("\n", text)
    text = _PRESENTATION_BLOCK_OPEN_RE.sub("\n", text)
    text = _PRESENTATION_BLOCK_CLOSE_RE.sub("\n", text)
    # Inline tags such as <u> keep their readable inner text.
    text = _PRESENTATION_HTML_TAG_RE.sub("", text)

    lines = text.split("\n")
    table_lines: set[int] = set()
    for index, line in enumerate(lines):
        if not _MARKDOWN_TABLE_DELIMITER_RE.match(line):
            continue
        table_lines.add(index)
        cursor = index - 1
        while cursor >= 0 and lines[cursor].lstrip().startswith("|"):
            table_lines.add(cursor)
            cursor -= 1
        cursor = index + 1
        while cursor < len(lines) and lines[cursor].lstrip().startswith("|"):
            table_lines.add(cursor)
            cursor += 1

    repaired_lines: list[str] = []
    for index, line in enumerate(lines):
        if index not in table_lines:
            line = re.sub(r"^(\s*)\|\s?", r"\1", line)
            line = re.sub(r"\s+\|\s*$", "", line)
        repaired_lines.append(line.rstrip())
    text = "\n".join(repaired_lines)

    # Qwen occasionally repeats the salutation after a repaired paragraph.
    # It is presentation noise, not legal content. Keep the first standalone
    # salutation and remove later copies.
    salutation_re = re.compile(
        r"^\s*thưa\s+anh/chị\s*[,.!?]?\s*$", re.IGNORECASE
    )
    salutation_prefix_re = re.compile(
        r"^\s*thưa\s+anh/chị\s*[,.:!?-]\s*", re.IGNORECASE
    )
    salutation_seen = False
    deduplicated_lines: list[str] = []
    for line in text.split("\n"):
        if salutation_re.match(line):
            if salutation_seen:
                continue
            salutation_seen = True
        elif salutation_prefix_re.match(line):
            if salutation_seen:
                line = salutation_prefix_re.sub("", line, count=1)
                if not line.strip():
                    continue
            else:
                salutation_seen = True
        deduplicated_lines.append(line)
    text = "\n".join(deduplicated_lines)

    bold_markers = list(re.finditer(r"(?<!\\)\*\*", text))
    if len(bold_markers) % 2:
        marker = bold_markers[-1]
        text = text[: marker.start()] + text[marker.end() :]

    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_answer_suggestion_envelope(
    raw: Any,
    *,
    allowed_issue_ids: Sequence[str],
    allowed_facets: Sequence[str],
) -> tuple[str, list[dict[str, str]], bool]:
    """Parse one DeepSeek envelope; malformed output remains readable Markdown."""

    text = str(raw or "").strip()
    if not text:
        return "", [], False
    candidate = text
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        return sanitize_model_answer_markdown(text), [], False
    if not isinstance(payload, Mapping):
        return sanitize_model_answer_markdown(text), [], False
    answer = str(payload.get("answer_markdown") or "").strip()
    if not answer:
        return sanitize_model_answer_markdown(text), [], False
    issue_ids = {str(item) for item in allowed_issue_ids}
    facets = {str(item) for item in allowed_facets} | _SUGGESTION_FACETS
    suggestions: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_item in payload.get("suggested_questions") or []:
        if not isinstance(raw_item, Mapping):
            continue
        question = _compact(raw_item.get("text"))
        issue_id = _compact(raw_item.get("issue_id"))
        facet = _compact(raw_item.get("facet")).casefold()
        folded = _fold(question)
        if not (10 <= len(question) <= 180):
            continue
        if "http://" in question.casefold() or "https://" in question.casefold():
            continue
        if issue_id not in issue_ids or facet not in facets:
            continue
        if folded in seen:
            continue
        seen.add(folded)
        suggestions.append(
            {
                "id": _checksum(issue_id, facet, question)[:12],
                "text": question,
                "issue_id": issue_id,
                "facet": facet,
            }
        )
        if len(suggestions) >= 4:
            break
    return sanitize_model_answer_markdown(answer), suggestions, True


_DIGEST_FORBIDDEN_PATTERNS = (
    r"https?://",
    r"\b(?:điều|khoản|điểm)\s+\d+",
    r"\b\d{1,4}/\d{4}/[A-ZÀ-ỸĐa-zà-ỹđ-]+",
    r"\b\d[\d.,]*\s*(?:đồng|vnd|triệu|nghìn)\b",
    r"\b(?:thời hạn|giải quyết trong)\s+(?:là\s+)?\d+\s*(?:ngày|tháng|năm)\b",
    r"\b(?:theo quy định|được phép|không bắt buộc|có thẩm quyền|phải nộp)\b",
)


def _digest_text_is_safe(value: Any) -> bool:
    text = _compact(value)
    if not text:
        return True
    return not any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in _DIGEST_FORBIDDEN_PATTERNS
    )


def validate_conversation_patch_v2(
    raw_patch: Any,
    *,
    allowed_message_ids: Sequence[str],
) -> dict[str, Any] | None:
    """Validate model-authored conversational memory as non-legal metadata.

    Invalid patches are discarded atomically.  The legal answer remains
    readable; the previous digest is kept by the caller.
    """

    if not isinstance(raw_patch, Mapping):
        return None
    valid_message_ids = {
        _compact(message_id) for message_id in allowed_message_ids if _compact(message_id)
    }
    topic_summary = _compact(raw_patch.get("topic_summary"))[:1200]
    current_goal = _compact(raw_patch.get("current_goal"))[:300]
    topics = list(
        dict.fromkeys(
            _compact(item)[:160]
            for item in raw_patch.get("topics") or []
            if _compact(item)
        )
    )[:8]
    open_questions = list(
        dict.fromkeys(
            _compact(item)[:240]
            for item in raw_patch.get("open_questions") or []
            if _compact(item)
        )
    )[:8]
    user_facts: list[dict[str, str]] = []
    for raw_fact in list(raw_patch.get("user_facts") or [])[:10]:
        if not isinstance(raw_fact, Mapping):
            return None
        text = _compact(raw_fact.get("text"))[:300]
        source_id = _compact(raw_fact.get("source_message_id"))
        status = _compact(raw_fact.get("status")).casefold()
        if not text:
            continue
        if status != "user_stated" or source_id not in valid_message_ids:
            return None
        user_facts.append(
            {
                "text": text,
                "source_message_id": source_id,
                "status": "user_stated",
            }
        )
    referenced_turn_ids = list(
        dict.fromkeys(
            _compact(item)
            for item in raw_patch.get("referenced_turn_ids") or []
            if _compact(item) in valid_message_ids
        )
    )[:12]
    all_text = [
        topic_summary,
        current_goal,
        *topics,
        *open_questions,
        *(item["text"] for item in user_facts),
    ]
    if any(not _digest_text_is_safe(item) for item in all_text):
        return None
    if not any(all_text) and not referenced_turn_ids:
        return None
    return {
        "version": "conversation-digest-v2",
        "topic_summary": topic_summary,
        "current_goal": current_goal,
        "topics": topics,
        "user_facts": user_facts,
        "open_questions": open_questions,
        "referenced_turn_ids": referenced_turn_ids,
    }


def _merge_conversation_digest_v2(
    previous: Any,
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge a validated patch into a loss-aware running conversation digest.

    The model supplies a compact description of the current goal, while the
    backend preserves older topic labels and user-stated facts with provenance.
    Legal metadata is deliberately excluded by ``validate_conversation_patch_v2``.
    """

    old = dict(previous) if isinstance(previous, Mapping) else {}
    new = dict(current)

    def distinct_text(values: Sequence[Any], *, limit: int) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = _compact(value)
            folded = _fold(text)
            if not text or folded in seen:
                continue
            seen.add(folded)
            output.append(text)
            if len(output) >= limit:
                break
        return output

    topics = distinct_text(
        [*(new.get("topics") or []), *(old.get("topics") or [])],
        limit=8,
    )
    facts: list[dict[str, str]] = []
    seen_facts: set[tuple[str, str]] = set()
    for raw in [*(old.get("user_facts") or []), *(new.get("user_facts") or [])]:
        if not isinstance(raw, Mapping):
            continue
        fact = {
            "text": _compact(raw.get("text")),
            "source_message_id": _compact(raw.get("source_message_id")),
            "status": "user_stated",
        }
        identity = (_fold(fact["text"]), fact["source_message_id"])
        if not fact["text"] or identity in seen_facts:
            continue
        seen_facts.add(identity)
        facts.append(fact)
    referenced = distinct_text(
        [
            *(old.get("referenced_turn_ids") or []),
            *(new.get("referenced_turn_ids") or []),
        ],
        limit=12,
    )
    return {
        "version": "conversation-digest-v2",
        "topic_summary": _compact(new.get("topic_summary"))[:1200]
        or _compact(old.get("topic_summary"))[:1200],
        "current_goal": _compact(new.get("current_goal"))[:300]
        or _compact(old.get("current_goal"))[:300],
        "topics": topics,
        "user_facts": facts[-10:],
        # Open questions are current state, not an append-only event log.
        "open_questions": distinct_text(new.get("open_questions") or [], limit=8),
        "referenced_turn_ids": referenced,
    }


def parse_answer_envelope_v2(
    raw: Any,
    *,
    allowed_issue_ids: Sequence[str],
    allowed_facets: Sequence[str],
    allowed_message_ids: Sequence[str],
) -> tuple[str, list[dict[str, str]], dict[str, Any] | None, bool]:
    """Parse answer, suggestions and a separately fail-closed digest patch."""

    answer, suggestions, parsed = parse_answer_suggestion_envelope(
        raw,
        allowed_issue_ids=allowed_issue_ids,
        allowed_facets=allowed_facets,
    )
    if not parsed:
        return answer, suggestions, None, False
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return answer, suggestions, None, parsed
    patch = validate_conversation_patch_v2(
        payload.get("conversation_patch") if isinstance(payload, Mapping) else None,
        allowed_message_ids=allowed_message_ids,
    )
    return answer, suggestions, patch, parsed


def parse_qwen_answer_envelope_v1(
    raw: Any,
    *,
    allowed_issue_ids: Sequence[str],
    allowed_facets: Sequence[str],
    allowed_message_ids: Sequence[str],
) -> tuple[str, list[dict[str, str]], dict[str, Any] | None]:
    """Accept plain Markdown first and retain JSON-envelope compatibility.

    The common answer prompt now requests ordinary Markdown from every model.
    Older Qwen responses may still be valid envelopes, so they are parsed when
    possible.  A normal Markdown response is an answer, not a transport
    failure; suggestions and the optional memory patch are simply absent.
    """

    text = str(raw or "").strip()
    if not text:
        raise ValueError("QWEN_OUTPUT_INVALID")
    candidate = text
    if candidate.startswith("```"):
        candidate = re.sub(
            r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE
        )
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        payload = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        # Do not render a truncated stale JSON envelope as chat prose. Plain
        # Markdown commonly starts with text or a Markdown heading/list, not
        # an opening object brace.
        if candidate.lstrip().startswith("{"):
            return "", [], None
        return sanitize_model_answer_markdown(text), [], None
    if not isinstance(payload, Mapping):
        # A JSON array/value is not ordinary Markdown and must not leak as a
        # transport artifact into the chat.
        return "", [], None
    answer = str(payload.get("answer_markdown") or "").strip()
    if not answer:
        # A malformed JSON-like response should not appear as raw braces in
        # the chat. The active prompt never asks for JSON, so this branch only
        # handles stale provider output safely.
        return "", [], None
    answer, suggestions, patch, parsed = parse_answer_envelope_v2(
        candidate,
        allowed_issue_ids=allowed_issue_ids,
        allowed_facets=allowed_facets,
        allowed_message_ids=allowed_message_ids,
    )
    if not parsed or not answer:
        return sanitize_model_answer_markdown(answer), [], None
    return answer, suggestions, patch
