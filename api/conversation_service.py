from __future__ import annotations

"""Conversation service for multi-turn Ask chat.

Uses Surreal tables from migration 27:
- conversation
- conversation_message

Falls back to JSON files under data/conversations/ only when Surreal is unavailable,
so local pilot environments remain usable.
"""

import json
import base64
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from open_notebook.database.repository import (
    ensure_record_id,
    repo_create,
    repo_query,
    repo_update,
)

CHAT_RETENTION_DAYS = 365
DEFAULT_CONTEXT_MAX_CHARS = 3500
DEFAULT_CONTEXT_MAX_MESSAGES = 12
DEFAULT_TITLE = "Cuộc trò chuyện mới"
DEFAULT_MESSAGE_PAGE_SIZE = 30
JSON_FALLBACK_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "data", "conversations"
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utcnow_iso() -> str:
    return utcnow().isoformat()


def _as_utc_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def record_id_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if ":" in text:
        return text.split(":", 1)[1]
    return text


def full_record_id(table: str, value: str | None) -> str | None:
    if not value:
        return None
    text = str(value)
    if text.startswith(f"{table}:"):
        return text
    if ":" in text:
        return text
    return f"{table}:{text}"


def auto_title(content: str) -> str:
    text = re.sub(r"\s+", " ", (content or "").strip())
    if not text:
        return DEFAULT_TITLE
    if len(text) > 60:
        text = text[:57].rstrip() + "..."
    return text


def retention_expires_at(from_dt: datetime | None = None) -> datetime:
    base = from_dt or utcnow()
    return base + timedelta(days=CHAT_RETENTION_DAYS)


def estimate_tokens(text: str) -> int:
    # Rough Vietnamese/English hybrid estimate: ~3.5 chars/token.
    return max(1, int(len(text or "") / 3.5))


def _encode_message_cursor(conversation_id: str, offset: int) -> str:
    payload = json.dumps(
        {"v": 1, "conversation_id": conversation_id, "offset": offset},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_message_cursor(cursor: str | None, conversation_id: str) -> int:
    if not cursor:
        return 0
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
        if payload.get("v") != 1 or payload.get("conversation_id") != conversation_id:
            raise ValueError
        offset = int(payload.get("offset"))
        if offset < 0:
            raise ValueError
        return offset
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid conversation message cursor") from exc


def _json_owner_dir(owner_key: str) -> str:
    safe = owner_key.replace(":", "_").replace("/", "_")
    path = os.path.join(JSON_FALLBACK_DIR, safe)
    os.makedirs(path, exist_ok=True)
    return path


def _json_path(owner_key: str, conversation_id: str) -> str:
    return os.path.join(_json_owner_dir(owner_key), f"{conversation_id}.json")


def _load_json_conversation(owner_key: str, conversation_id: str) -> dict | None:
    path = _json_path(owner_key, conversation_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json_conversation(owner_key: str, conversation_id: str, data: dict) -> None:
    path = _json_path(owner_key, conversation_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _list_json_conversations(owner_key: str, role_context: str | None = None) -> list[dict]:
    owner_dir = _json_owner_dir(owner_key)
    rows: list[dict] = []
    for name in os.listdir(owner_dir):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(owner_dir, name), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("status") == "deleted":
            continue
        if role_context and data.get("role_context") != role_context:
            continue
        rows.append(data)
    rows.sort(key=lambda item: item.get("last_message_at") or item.get("updated_at") or "", reverse=True)
    return rows


def _message_to_public(msg: dict) -> dict[str, Any]:
    attachments = list(msg.get("attachments") or [])
    # Records written during the initial migration-27 phase may contain the
    # snapshot in an attachment-like metadata item. Continue reading those
    # safely while new writes use first-class migration-28 fields.
    legacy_snapshot = next(
        (
            item for item in attachments
            if isinstance(item, dict) and item.get("kind") == "message_snapshot"
        ),
        {},
    )
    real_attachments = [
        item for item in attachments
        if not (
            isinstance(item, dict)
            and item.get("kind") in {
                "message_snapshot",
                "legal_answer_presentation",
                "chat_suggestions_v1",
                "conversation_context_v1",
                "chat_model_snapshot_v1",
            }
        )
    ]
    answer_mode_item = next(
        (
            item for item in attachments
            if isinstance(item, dict) and item.get("kind") == "answer_mode"
        ),
        {},
    )
    presentation_item = next(
        (
            item for item in attachments
            if isinstance(item, dict) and item.get("kind") == "legal_answer_presentation"
        ),
        {},
    )
    presentation = (
        presentation_item.get("value")
        if isinstance(presentation_item.get("value"), dict)
        else {}
    )
    suggestions_item = next(
        (
            item for item in attachments
            if isinstance(item, dict) and item.get("kind") == "chat_suggestions_v1"
        ),
        {},
    )
    attached_suggestions = (
        suggestions_item.get("value")
        if isinstance(suggestions_item.get("value"), list)
        else []
    )
    conversation_context_item = next(
        (
            item for item in attachments
            if isinstance(item, dict) and item.get("kind") == "conversation_context_v1"
        ),
        {},
    )
    conversation_context = (
        conversation_context_item.get("value")
        if isinstance(conversation_context_item.get("value"), dict)
        else {}
    )
    model_snapshot_item = next(
        (
            item for item in attachments
            if isinstance(item, dict) and item.get("kind") == "chat_model_snapshot_v1"
        ),
        {},
    )
    model_snapshot = (
        model_snapshot_item.get("value")
        if isinstance(model_snapshot_item.get("value"), dict)
        else {}
    )
    return {
        "id": record_id_str(msg.get("id")) or msg.get("id") or str(uuid.uuid4())[:12],
        "role": msg.get("sender_role") or msg.get("role") or "assistant",
        "turn_id": msg.get("turn_id") or legacy_snapshot.get("turn_id"),
        "content": msg.get("content") or "",
        "status": msg.get("status") or legacy_snapshot.get("status") or ("complete" if (msg.get("sender_role") or msg.get("role")) == "assistant" else None),
        "citations": msg.get("citations_snapshot") or msg.get("citations") or None,
        "recommended_forms": msg.get("recommended_forms") or legacy_snapshot.get("recommended_forms"),
        "faq_refs": msg.get("faq_refs") or legacy_snapshot.get("faq_refs"),
        "faqs": msg.get("faqs") or legacy_snapshot.get("faqs"),
        "procedure_detail": msg.get("procedure_detail") or legacy_snapshot.get("procedure_detail"),
        "suggested_questions": msg.get("suggested_questions") or attached_suggestions or legacy_snapshot.get("suggested_questions") or [],
        "conversation_route": conversation_context.get("conversation_route"),
        "active_document": conversation_context.get("active_document"),
        "related_documents": conversation_context.get("related_documents") or [],
        "memory_usage": conversation_context.get("memory_usage"),
        "request_items": presentation.get("request_items") or [],
        "item_results": presentation.get("item_results") or [],
        "conversation_patch": next((item.get("value") for item in attachments if isinstance(item, dict) and item.get("kind") == "conversation_digest_snapshot_v1" and isinstance(item.get("value"), dict)), None),

        "timing_summary": msg.get("timing_summary") or conversation_context.get("timing_summary"),
        # Direct RAG exposes the canonical `timing` name while history keeps
        # the pre-existing timing_summary storage shape for compatibility.
        "timing": msg.get("timing") or msg.get("timing_summary") or conversation_context.get("timing_summary"),
        "model_option_id": model_snapshot.get("model_option_id"),
        "model_display_name": model_snapshot.get("model_display_name"),
        "model_locked": bool(model_snapshot.get("model_locked")),
        "generation_provenance": model_snapshot.get("generation_provenance"),
        "answer_sections": msg.get("answer_sections") or legacy_snapshot.get("answer_sections"),
        "forms_unavailable": (
            presentation.get("forms_unavailable")
            if presentation.get("forms_unavailable") is not None
            else msg.get("forms_unavailable")
            if msg.get("forms_unavailable") is not None
            else legacy_snapshot.get("forms_unavailable")
        ),
        "rag_trace": msg.get("rag_trace") or legacy_snapshot.get("rag_trace"),
        "grounding_status": msg.get("grounding_status") or legacy_snapshot.get("grounding_status"),
        "answer_mode": presentation.get("answer_mode") if presentation.get("answer_mode") is not None else msg.get("answer_mode") or answer_mode_item.get("value"),
        "outcome": presentation.get("outcome") if presentation.get("outcome") is not None else msg.get("outcome"),
        "reason_code": presentation.get("reason_code") if presentation.get("reason_code") is not None else msg.get("reason_code"),
        "retryable": presentation.get("retryable") if presentation.get("retryable") is not None else msg.get("retryable"),
        "scope": presentation.get("scope") if presentation.get("scope") is not None else msg.get("scope"),
        "quality": presentation.get("quality") if presentation.get("quality") is not None else msg.get("quality"),
        "persistence_degraded": bool(
            presentation.get("persistence_degraded")
            if presentation.get("persistence_degraded") is not None
            else msg.get("persistence_degraded")
        ),
        "release_id": presentation.get("release_id") if presentation.get("release_id") is not None else msg.get("release_id"),
        "manifest_hash": presentation.get("manifest_hash") if presentation.get("manifest_hash") is not None else msg.get("manifest_hash"),
        "answer_status": presentation.get("answer_status") if presentation.get("answer_status") is not None else msg.get("answer_status") or legacy_snapshot.get("answer_status"),
        "fallback_tier": presentation.get("fallback_tier") if presentation.get("fallback_tier") is not None else msg.get("fallback_tier") or legacy_snapshot.get("fallback_tier"),
        "canonical_domain": presentation.get("canonical_domain") if presentation.get("canonical_domain") is not None else msg.get("canonical_domain") or legacy_snapshot.get("canonical_domain"),
        "evidence_count": presentation.get("evidence_count") if presentation.get("evidence_count") is not None else msg.get("evidence_count") if msg.get("evidence_count") is not None else legacy_snapshot.get("evidence_count"),
        "coverage_warning": presentation.get("coverage_warning") if presentation.get("coverage_warning") is not None else msg.get("coverage_warning") or legacy_snapshot.get("coverage_warning"),
        "blocked_reason": presentation.get("blocked_reason") if presentation.get("blocked_reason") is not None else msg.get("blocked_reason") or legacy_snapshot.get("blocked_reason"),
        "presentation_version": presentation.get("presentation_version"),
        "answer_route": presentation.get("answer_route"),
        "pipeline_version": presentation.get("pipeline_version"),
        "data_release_id": presentation.get("data_release_id"),
        "index_fingerprint": presentation.get("index_fingerprint"),
        "validity_snapshot": presentation.get("validity_snapshot"),
        "verification_label": presentation.get("verification_label"),
        "historical_label": presentation.get("historical_label"),
        "sections": presentation.get("sections"),
        "attachments": real_attachments or None,
        "created_at": str(msg.get("created_at") or msg.get("created") or utcnow_iso()),
    }


def _conversation_to_public(row: dict, message_count: int | None = None) -> dict[str, Any]:
    return {
        "id": record_id_str(row.get("id")) or row.get("id"),
        "title": row.get("title") or DEFAULT_TITLE,
        "domain": row.get("domain"),
        "role_context": row.get("role_context") or "citizen",
        "status": row.get("status") or "active",
        "owner_user_id": record_id_str(row.get("owner_user")) or row.get("owner_key"),
        "created_at": str(row.get("created_at") or row.get("created") or ""),
        "last_message_at": str(row.get("last_message_at") or row.get("updated") or row.get("updated_at") or ""),
        "expires_at": str(row.get("expires_at") or ""),
        "message_count": message_count if message_count is not None else int(row.get("message_count") or 0),
    }


async def _use_surreal() -> bool:
    try:
        rows = await repo_query("INFO FOR DB;")
        return bool(rows is not None)
    except Exception as exc:
        logger.warning(f"Conversation service Surreal unavailable, using JSON fallback: {exc}")
        return False


def resolve_owner_key(
    *,
    user_id: str | None,
    username: str | None = None,
    role: str | None = None,
) -> str:
    if user_id:
        return str(user_id)
    if username:
        return f"user:{username}"
    # Legacy password auth shares a single key per role - mark as unowned
    # so the access layer blocks cross-user leakage until an admin reviews.
    return f"legacy:{role or 'citizen'}"


def is_legacy_owner_key(owner_key: str) -> bool:
    """Return True when the owner key comes from shared-role password auth."""
    return owner_key.startswith("legacy:")


async def create_conversation(
    *,
    owner_key: str,
    role_context: str,
    title: str | None = None,
    domain: str | None = None,
    real_user_id: str | None = None,
) -> dict[str, Any]:
    now = utcnow()
    expires = retention_expires_at(now)
    title_value = (title or DEFAULT_TITLE).strip() or DEFAULT_TITLE

    if await _use_surreal() and real_user_id:
        data = {
            "owner_user": ensure_record_id(full_record_id("user_account", real_user_id) or real_user_id),
            "role_context": role_context,
            "domain": domain,
            "title": title_value,
            "status": "active",
            "ownership_status": "resolved",
            "created_at": now,
            "last_message_at": now,
            "expires_at": expires,
        }
        created = await repo_create("conversation", data)
        row = created[0] if isinstance(created, list) else created
        public = _conversation_to_public(row, message_count=0)
        public["messages"] = []
        return public

    conversation_id = str(uuid.uuid4())[:12]
    ownership_status = "resolved" if real_user_id else "needs_admin_review"
    if is_legacy_owner_key(owner_key):
        ownership_status = "needs_admin_review"
    payload = {
        "id": conversation_id,
        "owner_key": owner_key,
        "owner_user": real_user_id,
        "role_context": role_context,
        "domain": domain,
        "title": title_value,
        "status": "active",
        "ownership_status": ownership_status,
        "created_at": now.isoformat(),
        "last_message_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "messages": [],
        "message_count": 0,
    }
    _save_json_conversation(owner_key, conversation_id, payload)
    public = _conversation_to_public(payload, message_count=0)
    public["messages"] = []
    return public


async def list_conversations(
    *,
    owner_key: str,
    role_context: str | None = None,
    real_user_id: str | None = None,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
    include_message_count: bool = True,
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    if await _use_surreal() and real_user_id:
        filters = ["owner_user = $owner", "status != 'deleted'"]
        if include_deleted:
            filters = ["owner_user = $owner"]
        if role_context:
            filters.append("role_context = $role_context")
        count_projection = (
            ", count(SELECT * FROM conversation_message WHERE conversation = $parent.id) AS message_count"
            if include_message_count
            else ""
        )
        query = f"""
            SELECT *{count_projection}
            FROM conversation
            WHERE {' AND '.join(filters)}
            ORDER BY last_message_at DESC
            LIMIT $limit START $offset;
        """
        rows = await repo_query(
            query,
            {
                "owner": ensure_record_id(full_record_id("user_account", real_user_id) or real_user_id),
                "role_context": role_context,
                "limit": limit,
                "offset": offset,
            },
        )
        public_rows = []
        for row in rows:
            public = _conversation_to_public(
                row,
                message_count=int(row.get("message_count") or 0) if include_message_count else None,
            )
            if not include_message_count:
                public.pop("message_count", None)
            public_rows.append(public)
        return public_rows

    rows = _list_json_conversations(owner_key, role_context=role_context)
    page = rows[offset : offset + limit]
    return [_conversation_to_public(row, message_count=len(row.get("messages") or [])) for row in page]


async def get_latest_conversation(
    *,
    owner_key: str,
    role_context: str | None = None,
    real_user_id: str | None = None,
) -> dict[str, Any] | None:
    """Fetch the newest owner-scoped conversation without counting every row."""
    if await _use_surreal() and real_user_id:
        filters = ["owner_user = $owner", "status != 'deleted'"]
        if role_context:
            filters.append("role_context = $role_context")
        rows = await repo_query(
            f"""
            SELECT * FROM conversation
            WHERE {' AND '.join(filters)}
            ORDER BY last_message_at DESC
            LIMIT 1;
            """,
            {
                "owner": ensure_record_id(
                    full_record_id("user_account", real_user_id) or real_user_id
                ),
                "role_context": role_context,
            },
        )
        return _conversation_to_public(rows[0]) if rows else None

    rows = _list_json_conversations(owner_key, role_context=role_context)
    if not rows:
        return None
    row = rows[0]
    return _conversation_to_public(row, message_count=len(row.get("messages") or []))


async def get_latest_conversation_page(
    *,
    owner_key: str,
    role_context: str | None = None,
    real_user_id: str | None = None,
    limit: int = DEFAULT_MESSAGE_PAGE_SIZE,
) -> dict[str, Any] | None:
    """Fetch the newest conversation and newest message window in one query."""
    page_size = max(1, min(int(limit), 100))
    fetch_limit = page_size + 1
    if await _use_surreal() and real_user_id:
        filters = ["owner_user = $owner", "status != 'deleted'"]
        if role_context:
            filters.append("role_context = $role_context")
        rows = await repo_query(
            f"""
            SELECT *, (
                SELECT * FROM conversation_message
                WHERE conversation = $parent.id
                ORDER BY created_at DESC
                LIMIT $fetch_limit
            ) AS recent_messages
            FROM conversation
            WHERE {' AND '.join(filters)}
            ORDER BY last_message_at DESC
            LIMIT 1;
            """,
            {
                "owner": ensure_record_id(
                    full_record_id("user_account", real_user_id) or real_user_id
                ),
                "role_context": role_context,
                "fetch_limit": fetch_limit,
            },
        )
        if not rows:
            return None
        row = rows[0]
        descending = [
            _message_to_public(message)
            for message in (row.pop("recent_messages", None) or [])
        ]
        public = _conversation_to_public(row)
    else:
        rows = _list_json_conversations(owner_key, role_context=role_context)
        if not rows:
            return None
        row = rows[0]
        # JSON storage is append-only and therefore already has an exact
        # message order. Timestamps can collide during fast/parallel turns;
        # sorting ties by random UUID makes cursor pages skip or reorder turns.
        descending = [
            _message_to_public(message)
            for message in reversed(row.get("messages") or [])
        ]
        descending = descending[:fetch_limit]
        public = _conversation_to_public(row, message_count=len(row.get("messages") or []))

    has_more = len(descending) > page_size
    public["messages"] = list(reversed(descending[:page_size]))
    public["next_cursor"] = (
        _encode_message_cursor(str(public.get("id") or ""), page_size)
        if has_more
        else None
    )
    public["has_older_messages"] = has_more
    return public


async def get_conversation(
    conversation_id: str,
    *,
    owner_key: str,
    role_context: str | None = None,
    real_user_id: str | None = None,
    is_admin: bool = False,
    include_messages: bool = True,
    message_limit: int | None = None,
) -> dict[str, Any] | None:
    if await _use_surreal() and (real_user_id or is_admin):
        full_id = full_record_id("conversation", conversation_id)
        rows = await repo_query(
            "SELECT * FROM $id;",
            {"id": ensure_record_id(full_id or conversation_id)},
        )
        if not rows:
            return None
        row = rows[0]
        if row.get("status") == "deleted" and not is_admin:
            return None
        owner = record_id_str(row.get("owner_user"))
        if not is_admin and real_user_id and owner and owner != record_id_str(real_user_id):
            return None
        if role_context and row.get("role_context") != role_context and not is_admin:
            return None
        public = _conversation_to_public(row)
        if include_messages:
            bounded_limit = (
                max(1, min(int(message_limit), 200))
                if message_limit is not None
                else None
            )
            if bounded_limit is None:
                messages = await repo_query(
                    """
                    SELECT * FROM conversation_message
                    WHERE conversation = $conversation
                    ORDER BY created_at ASC;
                    """,
                    {"conversation": ensure_record_id(full_id or conversation_id)},
                )
            else:
                messages = await repo_query(
                    """
                    SELECT * FROM conversation_message
                    WHERE conversation = $conversation
                    ORDER BY created_at DESC
                    LIMIT $limit;
                    """,
                    {
                        "conversation": ensure_record_id(full_id or conversation_id),
                        "limit": bounded_limit,
                    },
                )
                messages = list(reversed(messages))
            public["messages"] = [_message_to_public(msg) for msg in messages]
            public["loaded_message_count"] = len(public["messages"])
            if bounded_limit is None:
                public["message_count"] = len(public["messages"])
        return public

    data = _load_json_conversation(owner_key, conversation_id)
    if not data:
        if is_admin:
            # Admin can scan fallback dirs.
            root = JSON_FALLBACK_DIR
            if os.path.isdir(root):
                for owner_name in os.listdir(root):
                    candidate = _load_json_conversation(owner_name.replace("_", ":", 1), conversation_id)
                    if candidate:
                        data = candidate
                        break
        if not data:
            return None
    if data.get("status") == "deleted" and not is_admin:
        return None
    if role_context and data.get("role_context") != role_context and not is_admin:
        return None
    public = _conversation_to_public(data, message_count=len(data.get("messages") or []))
    if include_messages:
        source_messages = list(data.get("messages") or [])
        if message_limit is not None:
            bounded_limit = max(1, min(int(message_limit), 200))
            source_messages = source_messages[-bounded_limit:]
        public["messages"] = [_message_to_public(msg) for msg in source_messages]
        public["loaded_message_count"] = len(public["messages"])
    return public


async def clear_conversation_attachment(
    conversation_id: str, *, owner_key: str,
    real_user_id: str | None = None, role_context: str | None = None,
) -> bool:
    """Persist detachment without adding a fake chat turn or deleting the original."""
    from api.conversation_attachments import KIND, attachment_snapshot

    existing = await get_conversation(
        conversation_id, owner_key=owner_key, real_user_id=real_user_id,
        role_context=role_context, is_admin=False, include_messages=False,
    )
    if not existing:
        return False

    def detached(items):
        return [item for item in (items or [])
                if not isinstance(item, dict) or item.get('kind') != KIND] + [attachment_snapshot(None)]

    if await _use_surreal() and real_user_id:
        rows = await repo_query(
            "SELECT * FROM conversation_message WHERE conversation = $conversation "
            "AND role = 'user' ORDER BY created_at DESC LIMIT 1;",
            {'conversation': ensure_record_id(full_record_id('conversation', conversation_id))},
        )
        if rows:
            await repo_update('conversation_message', str(rows[0]['id']),
                              {'attachments': detached(rows[0].get('attachments'))})
        return True
    data = _load_json_conversation(owner_key, conversation_id)
    if not data:
        return False
    for message in reversed(data.get('messages') or []):
        if message.get('role') == 'user':
            message['attachments'] = detached(message.get('attachments'))
            break
    _save_json_conversation(owner_key, conversation_id, data)
    return True


async def get_conversation_message_page(
    conversation_id: str,
    *,
    owner_key: str,
    role_context: str | None = None,
    real_user_id: str | None = None,
    is_admin: bool = False,
    limit: int = DEFAULT_MESSAGE_PAGE_SIZE,
    before: str | None = None,
    verified_conversation: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return one newest-first cursor window, rendered chronologically.

    The cursor is deliberately opaque to clients and scoped to one
    conversation. The API returns at most 30 messages by default; older pages
    are requested explicitly by passing ``before=next_cursor``.
    """
    page_size = max(1, min(int(limit), 100))
    offset = _decode_message_cursor(before, conversation_id)
    existing = verified_conversation
    if existing is None:
        existing = await get_conversation(
            conversation_id,
            owner_key=owner_key,
            role_context=role_context,
            real_user_id=real_user_id,
            is_admin=is_admin,
            include_messages=False,
        )
    if not existing:
        return None

    fetch_limit = page_size + 1
    if await _use_surreal() and (real_user_id or is_admin):
        full_id = full_record_id("conversation", conversation_id)
        rows = await repo_query(
            """
            SELECT * FROM conversation_message
            WHERE conversation = $conversation
            ORDER BY created_at DESC
            LIMIT $fetch_limit START $offset;
            """,
            {
                "conversation": ensure_record_id(full_id or conversation_id),
                "fetch_limit": fetch_limit,
                "offset": offset,
            },
        )
        descending = [_message_to_public(msg) for msg in rows]
    else:
        detail = await get_conversation(
            conversation_id,
            owner_key=owner_key,
            role_context=role_context,
            real_user_id=real_user_id,
            is_admin=is_admin,
            include_messages=True,
        )
        all_messages = list((detail or {}).get("messages") or [])
        descending = list(reversed(all_messages))[offset : offset + fetch_limit]

    has_more = len(descending) > page_size
    window = descending[:page_size]
    return {
        "messages": list(reversed(window)),
        "next_cursor": (
            _encode_message_cursor(conversation_id, offset + page_size)
            if has_more
            else None
        ),
        "has_more": has_more,
        "limit": page_size,
    }


async def rename_conversation(
    conversation_id: str,
    *,
    title: str,
    owner_key: str,
    real_user_id: str | None = None,
    role_context: str | None = None,
    is_admin: bool = False,
) -> dict[str, Any] | None:
    clean_title = (title or "").strip() or DEFAULT_TITLE
    existing = await get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        is_admin=is_admin,
        include_messages=False,
    )
    if not existing:
        return None

    if await _use_surreal() and (real_user_id or is_admin):
        full_id = full_record_id("conversation", conversation_id)
        await repo_update("conversation", full_id or conversation_id, {"title": clean_title})
        existing["title"] = clean_title
        return existing

    data = _load_json_conversation(owner_key, conversation_id)
    if not data:
        return None
    data["title"] = clean_title
    data["updated_at"] = utcnow_iso()
    _save_json_conversation(owner_key, conversation_id, data)
    existing["title"] = clean_title
    return existing


async def soft_delete_conversation(
    conversation_id: str,
    *,
    owner_key: str,
    real_user_id: str | None = None,
    role_context: str | None = None,
    is_admin: bool = False,
) -> bool:
    existing = await get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        is_admin=is_admin,
        include_messages=False,
    )


async def rename_conversation(
    conversation_id: str,
    *,
    title: str,
    owner_key: str,
    real_user_id: str | None = None,
    role_context: str | None = None,
    is_admin: bool = False,
) -> dict[str, Any] | None:
    clean_title = (title or "").strip() or DEFAULT_TITLE
    existing = await get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        is_admin=is_admin,
        include_messages=False,
    )
    if not existing:
        return None

    if await _use_surreal() and (real_user_id or is_admin):
        full_id = full_record_id("conversation", conversation_id)
        await repo_update("conversation", full_id or conversation_id, {"title": clean_title})
        existing["title"] = clean_title
        return existing

    data = _load_json_conversation(owner_key, conversation_id)
    if not data:
        return None
    data["title"] = clean_title
    data["updated_at"] = utcnow_iso()
    _save_json_conversation(owner_key, conversation_id, data)
    existing["title"] = clean_title
    return existing


async def soft_delete_conversation(
    conversation_id: str,
    *,
    owner_key: str,
    real_user_id: str | None = None,
    role_context: str | None = None,
    is_admin: bool = False,
) -> bool:
    existing = await get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        is_admin=is_admin,
        include_messages=False,
    )
    if not existing:
        return False

    surreal_success = False
    if await _use_surreal() and (real_user_id or is_admin):
        full_id = full_record_id("conversation", conversation_id)
        await repo_update(
            "conversation",
            full_id or conversation_id,
            {
                "status": "deleted",
                "last_message_at": utcnow(),
            },
        )
        surreal_success = True

    if not is_admin and role_context in {"citizen", "officer"}:
        try:
            from api.chat_memory_service import revoke_memory_from_conversation

            await revoke_memory_from_conversation(
                conversation_id,
                owner_key=owner_key,
                real_user_id=real_user_id,
                role_context=role_context,
            )
        except Exception as exc:
            logger.warning(
                "Conversation {} was deleted but its optional memory cleanup failed: {}",
                conversation_id,
                type(exc).__name__,
            )

    data = _load_json_conversation(owner_key, conversation_id)
    if not data:
        return surreal_success
    data["status"] = "deleted"
    data["updated_at"] = utcnow_iso()
    data["deleted_at"] = utcnow_iso()
    _save_json_conversation(owner_key, conversation_id, data)
    return True


async def add_message(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    content: str,
    real_user_id: str | None = None,
    role_context: str | None = None,
    is_admin: bool = False,
    status: str | None = None,
    citations: list[dict] | None = None,
    recommended_forms: list[dict] | None = None,
    faq_refs: list[str] | None = None,
    faqs: list[dict] | None = None,
    procedure_detail: dict | None = None,
    suggested_questions: list[dict] | None = None,
    conversation_route: str | None = None,
    active_document: dict | None = None,
    related_documents: list[dict] | None = None,
    memory_usage: dict | None = None,
    timing_summary: dict | None = None,
    answer_sections: list[dict] | None = None,
    forms_unavailable: bool | None = None,
    rag_trace: dict | None = None,
    grounding_status: str | None = None,
    answer_status: str | None = None,
    fallback_tier: str | None = None,
    canonical_domain: str | None = None,
    evidence_count: int | None = None,
    coverage_warning: str | None = None,
    blocked_reason: str | None = None,
    outcome: str | None = None,
    reason_code: str | None = None,
    retryable: bool | None = None,
    scope: str | None = None,
    persistence_degraded: bool = False,
    release_id: str | None = None,
    manifest_hash: str | None = None,
    turn_id: str | None = None,
    attachments: list[dict] | None = None,
) -> dict[str, Any] | None:
    attachments = list(attachments or [])
    if suggested_questions:
        attachments = [
            item for item in attachments
            if not (isinstance(item, dict) and item.get("kind") == "chat_suggestions_v1")
        ]
        attachments.append(
            {"kind": "chat_suggestions_v1", "value": suggested_questions}
        )
    if conversation_route or active_document or related_documents or memory_usage or timing_summary:
        attachments = [
            item for item in attachments
            if not (
                isinstance(item, dict)
                and item.get("kind") == "conversation_context_v1"
            )
        ]
        attachments.append(
            {
                "kind": "conversation_context_v1",
                "value": {
                    "conversation_route": conversation_route,
                    "active_document": active_document,
                    "related_documents": related_documents or [],
                    "memory_usage": memory_usage,
                    "timing_summary": timing_summary,
                },
            }
        )
    existing = await get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        is_admin=is_admin,
        include_messages=False,
    )
    if not existing:
        return None

    # The API persists assistant answers and older clients may also retry the
    # same snapshot from the browser. Make assistant persistence idempotent at
    # the conversation boundary, not only at the HTTP request boundary.
    message_status = status or ("complete" if role == "assistant" else None)
    normalized_turn_id = str(turn_id or "").strip()[:160] or None
    if (role == "assistant" and message_status in {"complete", "error"}) or (role == "user" and normalized_turn_id):
        full_existing = None
        if normalized_turn_id and await _use_surreal() and (real_user_id or is_admin):
            conversation_ref = ensure_record_id(
                full_record_id("conversation", conversation_id) or conversation_id
            )
            existing_turn = await repo_query(
                "SELECT * FROM conversation_message WHERE conversation = $conversation "
                "AND turn_id = $turn_id LIMIT 1;",
                {"conversation": conversation_ref, "turn_id": normalized_turn_id},
            )
            if existing_turn:
                prior = existing_turn[0] if isinstance(existing_turn, list) else existing_turn
                if isinstance(prior, dict):
                    return _message_to_public(prior)
        else:
            full_existing = await get_conversation(
                conversation_id,
                owner_key=owner_key,
                real_user_id=real_user_id,
                role_context=role_context,
                is_admin=is_admin,
                include_messages=True,
            )
        normalized_content = " ".join(str(content or "").split()).strip()
        for prior in reversed((full_existing or {}).get("messages") or []):
            if normalized_turn_id and str(prior.get("turn_id") or "").strip() == normalized_turn_id:
                return _message_to_public(prior)
            # A completed user turn is a hard idempotency boundary. Two
            # different questions may legitimately produce the same fallback
            # text; collapsing those answers leaves an unmatched user message
            # after reload. Browser retry deduplication only needs to inspect
            # the immediately preceding assistant snapshot.
            if prior.get("role") == "user":
                break
            if prior.get("role") != "assistant" or prior.get("status") != message_status:
                continue
            prior_content = " ".join(str(prior.get("content") or "").split()).strip()
            if not normalized_turn_id and normalized_content and prior_content == normalized_content:
                presentation_items = [
                    item for item in (attachments or [])
                    if isinstance(item, dict)
                    and item.get("kind") in {
                        "legal_answer_presentation",
                        "conversation_context_v1",
                    }
                ]
                if presentation_items:
                    previous_attachments = [
                        item for item in (prior.get("attachments") or [])
                        if not (
                            isinstance(item, dict)
                            and item.get("kind") in {
                                "legal_answer_presentation",
                                "conversation_context_v1",
                            }
                        )
                    ]
                    merged_attachments = [*previous_attachments, *presentation_items]
                    if await _use_surreal() and (real_user_id or is_admin):
                        prior_id = full_record_id(
                            "conversation_message", str(prior.get("id") or "")
                        )
                        if prior_id:
                            await repo_update(
                                "conversation_message",
                                prior_id,
                                {"attachments": merged_attachments},
                            )
                    else:
                        raw = _load_json_conversation(owner_key, conversation_id)
                        if raw:
                            for stored in reversed(raw.get("messages") or []):
                                if str(record_id_str(stored.get("id")) or stored.get("id")) == str(prior.get("id")):
                                    stored["attachments"] = merged_attachments
                                    break
                            _save_json_conversation(owner_key, conversation_id, raw)
                    prior = {**prior, "attachments": merged_attachments}
                if suggested_questions and not prior.get("suggested_questions"):
                    if await _use_surreal() and (real_user_id or is_admin):
                        prior_id = full_record_id(
                            "conversation_message", str(prior.get("id") or "")
                        )
                        if prior_id:
                            try:
                                await repo_update(
                                    "conversation_message",
                                    prior_id,
                                    {"suggested_questions": suggested_questions},
                                )
                            except Exception:
                                pass
                    else:
                        raw = _load_json_conversation(owner_key, conversation_id)
                        if raw:
                            for stored in reversed(raw.get("messages") or []):
                                if str(record_id_str(stored.get("id")) or stored.get("id")) == str(prior.get("id")):
                                    stored["suggested_questions"] = suggested_questions
                                    break
                            _save_json_conversation(owner_key, conversation_id, raw)
                    prior = {**prior, "suggested_questions": suggested_questions}
                return _message_to_public(prior)
            break

    now = utcnow()
    snapshot = {
        "citations_snapshot": citations,
        "recommended_forms": recommended_forms,
        "faq_refs": faq_refs,
        "faqs": faqs,
        "procedure_detail": procedure_detail,
        "suggested_questions": suggested_questions,
        "conversation_route": conversation_route,
        "active_document": active_document,
        "related_documents": related_documents,
        "memory_usage": memory_usage,
        "timing_summary": timing_summary,
        "answer_sections": answer_sections,
        "forms_unavailable": forms_unavailable,
        "rag_trace": rag_trace,
        "grounding_status": grounding_status,
        "answer_status": answer_status,
        "fallback_tier": fallback_tier,
        "outcome": outcome,
        "reason_code": reason_code,
        "retryable": retryable,
        "scope": scope,
        "persistence_degraded": persistence_degraded,
        "release_id": release_id,
        "manifest_hash": manifest_hash,
        "turn_id": normalized_turn_id,
        "canonical_domain": canonical_domain,
        "evidence_count": evidence_count,
        "coverage_warning": coverage_warning,
        "blocked_reason": blocked_reason,
        "attachments": attachments,
        "status": message_status,
    }

    if await _use_surreal() and (real_user_id or is_admin):
        full_id = full_record_id("conversation", conversation_id)
        conversation_ref = ensure_record_id(full_id or conversation_id)

        async def _find_existing_turn() -> dict[str, Any] | None:
            """Return a concurrently committed assistant turn, if any."""

            if not normalized_turn_id:
                return None
            rows = await repo_query(
                "SELECT * FROM conversation_message WHERE conversation = $conversation "
                "AND turn_id = $turn_id LIMIT 1;",
                {
                    "conversation": conversation_ref,
                    "turn_id": normalized_turn_id,
                },
            )
            prior = rows[0] if isinstance(rows, list) and rows else rows
            return _message_to_public(prior) if isinstance(prior, dict) else None

        payload = {
            "conversation": conversation_ref,
            "sender_user": ensure_record_id(full_record_id("user_account", real_user_id) or real_user_id)
            if real_user_id
            else None,
            "sender_role": role,
            "content": content,
            "status": message_status,
            "attachments": attachments,
            "citations_snapshot": citations,
            "recommended_forms": recommended_forms,
            "faq_refs": faq_refs,
            "faqs": faqs,
            "procedure_detail": procedure_detail,
            "suggested_questions": suggested_questions,
            "answer_sections": answer_sections,
            "forms_unavailable": forms_unavailable,
            "rag_trace": rag_trace,
            "grounding_status": grounding_status,
            "answer_status": answer_status,
            "fallback_tier": fallback_tier,
            "outcome": outcome,
            "reason_code": reason_code,
            "retryable": retryable,
            "scope": scope,
            "persistence_degraded": persistence_degraded,
            "release_id": release_id,
            "manifest_hash": manifest_hash,
            "turn_id": normalized_turn_id,
            "canonical_domain": canonical_domain,
            "evidence_count": evidence_count,
            "coverage_warning": coverage_warning,
            "blocked_reason": blocked_reason,
            "timing_summary": timing_summary,
            "created_at": now,
        }
        try:
            created = await repo_create("conversation_message", payload)
        except Exception as exc:
            # Two retries can pass the read-before-write check at the same
            # time. The database unique index is authoritative; recover the
            # winner instead of writing a second answer or dropping into the
            # metadata compatibility insert.
            if normalized_turn_id:
                prior = await _find_existing_turn()
                if prior is not None:
                    return prior
            # Legal snapshots contain evolving citation/form fields. Older
            # Surreal schemas can reject one nested field and otherwise lose
            # the whole assistant message. Preserve the answer first; the
            # current response still carries its full trace/citations.
            logger.warning(
                "Conversation message metadata was rejected for {}: {}. "
                "Retrying with the durable answer-only payload.",
                conversation_id,
                exc,
            )
            safe_payload = {
                "conversation": ensure_record_id(full_id or conversation_id),
                "sender_user": ensure_record_id(
                    full_record_id("user_account", real_user_id) or real_user_id
                ) if real_user_id else None,
                "sender_role": role,
                "content": content,
                "status": message_status,
                "attachments": attachments or None,
                # Keep the turn key in the compatibility payload as well.
                # Without it, a schema-metadata rejection would silently
                # re-open the duplicate-message race that the unique index
                # is meant to close.
                "turn_id": normalized_turn_id,
                "created_at": now,
            }
            try:
                created = await repo_create("conversation_message", safe_payload)
            except Exception:
                # Keep the original exception as the useful diagnostic if the
                # database itself is unavailable or the minimal schema fails.
                raise exc
        msg = created[0] if isinstance(created, list) else created

        update_data: dict[str, Any] = {
            "last_message_at": now,
            "expires_at": retention_expires_at(now),
        }
        if role == "user" and (existing.get("title") in (None, "", DEFAULT_TITLE)):
            update_data["title"] = auto_title(content)
        await repo_update("conversation", full_id or conversation_id, update_data)
        public = _message_to_public(
            {
                **msg,
                **snapshot,
                "role": role,
            }
        )
        return public

    data = _load_json_conversation(owner_key, conversation_id)
    if not data:
        return None
    msg = {
        "id": str(uuid.uuid4())[:12],
        "role": role,
        "sender_role": role,
        "content": content,
        "created_at": now.isoformat(),
        **snapshot,
    }
    data.setdefault("messages", []).append(msg)
    data["message_count"] = len(data["messages"])
    data["last_message_at"] = now.isoformat()
    data["updated_at"] = now.isoformat()
    data["expires_at"] = retention_expires_at(now).isoformat()
    if role == "user" and data.get("title") in (None, "", DEFAULT_TITLE):
        data["title"] = auto_title(content)
    _save_json_conversation(owner_key, conversation_id, data)
    return _message_to_public(msg)


async def recover_missing_assistant_message(
    conversation_id: str,
    *,
    owner_key: str,
    real_user_id: str | None = None,
    role_context: str | None = None,
) -> bool:
    """Repair old conversations that saved the question but not the answer.

    Earlier Ask requests logged the completed answer to ``user_ask_history``
    before conversation persistence was made reliable. This is a legacy repair
    helper, not part of the normal conversation read path. For an exact
    question owned by the same account, restore only an audit answer created
    after that specific user turn. No model call or cross-user lookup is
    involved.
    """
    if not real_user_id or not await _use_surreal():
        return False

    conversation_ref = ensure_record_id(
        full_record_id("conversation", conversation_id) or conversation_id
    )
    messages = await repo_query(
        "SELECT * FROM conversation_message WHERE conversation = $conversation ORDER BY created_at ASC;",
        {"conversation": conversation_ref},
    )
    owner_ref = ensure_record_id(
        full_record_id("user_account", real_user_id) or real_user_id
    )
    existing_answers = {
        str(msg.get("content") or "").strip()
        for msg in messages
        if (msg.get("sender_role") or msg.get("role")) == "assistant"
    }
    restored_count = 0
    for index, user_message in enumerate(messages):
        if (user_message.get("sender_role") or user_message.get("role")) != "user":
            continue
        # Recovery belongs to one conversation turn, not to every historical
        # answer with the same question text.  Once any assistant response
        # follows this user message (up to the next user message), the turn is
        # complete and must never receive a legacy answer from audit history.
        following_turn = messages[index + 1 :]
        next_user_offset = next(
            (
                offset
                for offset, message in enumerate(following_turn)
                if (message.get("sender_role") or message.get("role")) == "user"
            ),
            len(following_turn),
        )
        if any(
            (message.get("sender_role") or message.get("role")) == "assistant"
            for message in following_turn[:next_user_offset]
        ):
            continue
        question = str(user_message.get("content") or "").strip()
        question_created = _as_utc_datetime(user_message.get("created_at"))
        if not question or question_created is None:
            continue
        history_rows = await repo_query(
            """
            SELECT * FROM user_ask_history
            WHERE owner_user = $owner AND question = $question
            ORDER BY created DESC LIMIT 10;
            """,
            {"owner": owner_ref, "question": question},
        )
        history = next(
            (
                row for row in history_rows
                if str(row.get("answer") or "").strip()
                and str(row.get("answer") or "").strip() not in existing_answers
                and (
                    (history_created := _as_utc_datetime(
                        row.get("created") or row.get("created_at")
                    ))
                    is not None
                    and history_created >= question_created
                )
            ),
            None,
        )
        answer = str((history or {}).get("answer") or "").strip()
        if not answer:
            continue
        restored = await add_message(
            conversation_id,
            owner_key=owner_key,
            role="assistant",
            content=answer,
            real_user_id=real_user_id,
            role_context=role_context,
            status="complete",
            rag_trace=(history or {}).get("rag_trace"),
            grounding_status=(history or {}).get("grounding_status"),
        )
        if restored:
            restored_count += 1
            existing_answers.add(answer)

    if restored_count:
        logger.info(
            "Recovered {} missing assistant message(s) for conversation {}",
            restored_count,
            conversation_id,
        )
    return restored_count > 0


def build_token_limited_context(
    messages: list[dict],
    *,
    max_chars: int = DEFAULT_CONTEXT_MAX_CHARS,
    max_messages: int = DEFAULT_CONTEXT_MAX_MESSAGES,
) -> list[dict[str, Any]]:
    """Return recent messages, prioritizing conclusions/citations, under a char budget."""
    if not messages:
        return []

    selected: list[dict[str, Any]] = []
    used_chars = 0
    # Walk newest -> oldest, then reverse.
    for msg in reversed(messages[-max_messages * 2 :]):
        role = msg.get("role") or msg.get("sender_role") or "assistant"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        # Prefer prior conclusions and natural citations over full raw dumps.
        if role == "assistant":
            content = content[:1200]
            cites = msg.get("citations") or msg.get("citations_snapshot") or []
            if cites:
                cite_bits = []
                for cite in cites[:3]:
                    law = cite.get("law_number") or ""
                    art = cite.get("article_number") or ""
                    label = law
                    if art:
                        label = f"{law}, Điều {art}" if law else f"Điều {art}"
                    if label:
                        cite_bits.append(label)
                if cite_bits:
                    content = f"{content}\nCăn cứ đã dùng: {'; '.join(cite_bits)}"
        else:
            content = content[:500]

        piece = {
            "role": role,
            "content": content,
            "citations": msg.get("citations") or msg.get("citations_snapshot"),
            "grounding_status": msg.get("grounding_status"),
            # Keep backend-owned source binding in the bounded context. It is
            # metadata used by the router for a document follow-up, not extra
            # prompt evidence and therefore does not inflate the content budget.
            "active_document": msg.get("active_document"),
            "related_documents": msg.get("related_documents") or [],
            "canonical_domain": msg.get("canonical_domain"),
        }
        piece_len = len(content)
        if selected and used_chars + piece_len > max_chars:
            break
        selected.append(piece)
        used_chars += piece_len
        if len(selected) >= max_messages:
            break

    selected.reverse()
    return selected


def format_context_for_prompt(context_messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for msg in context_messages:
        role_label = "Người dùng" if msg.get("role") == "user" else "Trợ lý"
        lines.append(f"{role_label}: {msg.get('content') or ''}")
    return "\n".join(lines)


async def get_followup_context(
    conversation_id: str,
    *,
    owner_key: str,
    real_user_id: str | None = None,
    role_context: str | None = None,
    is_admin: bool = False,
    max_chars: int = DEFAULT_CONTEXT_MAX_CHARS,
    max_messages: int = DEFAULT_CONTEXT_MAX_MESSAGES,
) -> list[dict[str, Any]]:
    conversation = await get_conversation(
        conversation_id,
        owner_key=owner_key,
        real_user_id=real_user_id,
        role_context=role_context,
        is_admin=is_admin,
        include_messages=True,
        message_limit=max(30, min(200, max_messages * 3)),
    )
    if not conversation:
        return []
    return build_token_limited_context(
        conversation.get("messages") or [],
        max_chars=max_chars,
        max_messages=max_messages,
    )
