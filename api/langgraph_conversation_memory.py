"""LangGraph checkpoint bridge for the owner-scoped Chat tổng conversation.

The legal chat keeps its existing conversation store as the source of record.
This module adds the same durable, thread-scoped state mechanism used by the
Notebook/source chat: a LangGraph checkpoint stores the ordered transcript by
``thread_id``.  It deliberately does not store retrieval evidence or derive
legal facts from assistant prose.  The existing deterministic context builder
continues to compact the checkpoint history before a model call.

The implementation is intentionally small and additive.  It can be disabled
with ``CHAT_LANGGRAPH_CHECKPOINT_V1_ENABLED=false`` without affecting the
conversation database, retrieval, or model providers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Annotated, Any, Mapping, Sequence, TypedDict

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE

CHECKPOINT_VERSION = "chat-langgraph-checkpoint-v1"
SUMMARY_VERSION = "chat-langgraph-llm-summary-v1"
_ALLOWED_ROLES = {"citizen", "officer"}


class ConversationCheckpointState(TypedDict):
    """Minimal state persisted by the Chat tổng LangGraph.

    Only conversation messages are checkpointed.  Legal evidence, citations,
    memory items, and provider output metadata remain in their existing stores.
    """

    messages: Annotated[list, add_messages]
    verified_state: dict[str, Any]
    state_revision: int
    context_snapshot: dict[str, Any]
    llm_summary: dict[str, Any]
    llm_summary_revision: int
    llm_summary_through_message_id: str
    llm_summary_checksum: str
    llm_summary_model_id: str


class ConversationSummaryGraphState(TypedDict):
    """Ephemeral input/output for the LangGraph summarization node."""

    previous_summary: dict[str, Any]
    messages_to_summarize: list[dict[str, str]]
    allowed_message_ids: list[str]
    raw_model_output: str


_VERIFIED_STATE_KEYS = {
    "version",
    "conversation_id",
    "role_context",
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
    "digest_revision",
    "digest_through_message_id",
    "digest_checksum",
    "compaction_summary_mode",
    "compaction_summary_model_id",
    "source_turn_ids",
    "revision",
}

_CONTEXT_SNAPSHOT_KEYS = {
    "history_mode",
    "messages_considered",
    "messages_included",
    "history_tokens",
    "input_token_budget",
    "history_token_budget",
    "older_messages_compacted",
    "state_revision",
    "digest_revision",
    "digest_through_message_id",
    "compaction_item_checksum",
    "context_checksum",
}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _role_allow_list(value: str | None) -> set[str]:
    return {
        part.strip().casefold()
        for part in str(value or "").split(",")
        if part.strip()
    }


def is_enabled(role: str | None, environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the checkpoint bridge is enabled for this role."""

    env = environ or os.environ
    normalized_role = str(role or "").strip().casefold()
    if normalized_role not in _ALLOWED_ROLES:
        return False
    if not _truthy(env.get("CHAT_LANGGRAPH_CHECKPOINT_V1_ENABLED")):
        return False
    allowed = _role_allow_list(env.get("CHAT_LANGGRAPH_CHECKPOINT_V1_ROLES"))
    return not allowed or normalized_role in allowed


def is_llm_summary_enabled(
    role: str | None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether LangGraph may call an LLM for conversation compaction."""

    env = environ or os.environ
    normalized_role = str(role or "").strip().casefold()
    if not is_enabled(normalized_role, env):
        return False
    if not _truthy(env.get("CHAT_LANGGRAPH_LLM_SUMMARY_V1_ENABLED")):
        return False
    allowed = _role_allow_list(
        env.get("CHAT_LANGGRAPH_LLM_SUMMARY_V1_ROLES")
    )
    return not allowed or normalized_role in allowed


def build_thread_id(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
) -> str:
    """Build a non-PII, owner/role-isolated LangGraph thread ID.

    A conversation ID alone is not sufficient because JSON fallback and legacy
    IDs can be reused across owners.  Hashing also keeps credentials/usernames
    out of the checkpoint database and telemetry.
    """

    material = "\x1f".join(
        (
            str(owner_key or "").strip(),
            str(role or "").strip().casefold(),
            str(conversation_id or "").strip(),
        )
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"chat:{CHECKPOINT_VERSION}:{digest}"


def _message_id(message: Mapping[str, Any], index: int) -> str:
    value = message.get("id") or message.get("message_id")
    if value:
        # Preserve the public conversation-message ID. Router/context code
        # uses this ID for referenced_turn_ids and related metadata; owner
        # isolation is provided by the hashed thread_id, not an ID rewrite.
        return str(value)
    stable = {
        "index": index,
        "role": str(message.get("role") or "user"),
        "content": _content_to_text(message.get("content")),
        "created_at": str(message.get("created_at") or ""),
    }
    digest = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"fallback:{digest}"


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(content)


def _json_safe(value: Any) -> Any:
    """Return a checkpoint-serializable copy without provider objects."""

    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return None


def _verified_state_snapshot(state: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, Mapping):
        return {}
    return {
        key: _json_safe(state.get(key))
        for key in _VERIFIED_STATE_KEYS
        if key in state
    }


def _context_snapshot(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: _json_safe(value.get(key))
        for key in _CONTEXT_SNAPSHOT_KEYS
        if key in value
    }


def _to_langchain_message(message: Mapping[str, Any], index: int) -> Any:
    role = str(message.get("role") or "user").strip().casefold()
    content = _content_to_text(message.get("content"))
    message_id = _message_id(message, index)
    if role == "assistant":
        return AIMessage(content=content, id=message_id)
    if role == "system":
        return SystemMessage(content=content, id=message_id)
    return HumanMessage(content=content, id=message_id)


def _from_langchain_message(message: Any) -> dict[str, str]:
    role = "assistant" if isinstance(message, AIMessage) else "system" if isinstance(message, SystemMessage) else "user"
    return {
        "id": str(getattr(message, "id", "") or ""),
        "role": role,
        "content": _content_to_text(getattr(message, "content", "")),
    }


def _noop_node(state: ConversationCheckpointState) -> dict[str, Any]:
    """Pass-through node; graph persistence is the purpose of this graph."""

    return {}


class _CheckpointStore:
    """Lazy, process-local LangGraph/SQLite store.

    SqliteSaver is synchronous.  A single lock protects the shared connection;
    async callers run operations in a worker thread so the API event loop is
    never blocked by SQLite serialization or checkpoint writes.
    """

    def __init__(self, path: str | os.PathLike[str]):
        self.path = str(path)
        self._connection: sqlite3.Connection | None = None
        self._graph: Any | None = None
        self._lock = threading.RLock()

    def _ensure_graph(self) -> Any:
        with self._lock:
            if self._graph is not None:
                return self._graph
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            saver = SqliteSaver(self._connection)
            saver.setup()
            builder = StateGraph(ConversationCheckpointState)
            builder.add_node("checkpoint", _noop_node)
            builder.add_edge(START, "checkpoint")
            builder.add_edge("checkpoint", END)
            self._graph = builder.compile(checkpointer=saver)
            return self._graph

    @staticmethod
    def _config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    def get_sync(self, thread_id: str) -> list[dict[str, str]] | None:
        with self._lock:
            graph = self._ensure_graph()
            state = graph.get_state(self._config(thread_id))
            if not state or not isinstance(state.values, Mapping):
                return None
            messages = state.values.get("messages") or []
            return [_from_langchain_message(item) for item in messages]

    def get_checkpoint_sync(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            graph = self._ensure_graph()
            state = graph.get_state(self._config(thread_id))
            if not state or not isinstance(state.values, Mapping) or not state.values:
                return None
            return {
                "version": CHECKPOINT_VERSION,
                "messages": [
                    _from_langchain_message(item)
                    for item in state.values.get("messages") or []
                ],
                "verified_state": dict(state.values.get("verified_state") or {}),
                "state_revision": int(state.values.get("state_revision") or 0),
                "context_snapshot": dict(state.values.get("context_snapshot") or {}),
                "llm_summary": dict(state.values.get("llm_summary") or {}),
                "llm_summary_revision": int(
                    state.values.get("llm_summary_revision") or 0
                ),
                "llm_summary_through_message_id": str(
                    state.values.get("llm_summary_through_message_id") or ""
                ),
                "llm_summary_checksum": str(
                    state.values.get("llm_summary_checksum") or ""
                ),
                "llm_summary_model_id": str(
                    state.values.get("llm_summary_model_id") or ""
                ),
            }

    def sync_sync(
        self,
        thread_id: str,
        history: list[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        desired = [_to_langchain_message(item, index) for index, item in enumerate(history)]
        with self._lock:
            graph = self._ensure_graph()
            config = self._config(thread_id)
            current_state = graph.get_state(config)
            current = list((current_state.values or {}).get("messages") or []) if current_state else []
            current_by_id = {
                str(getattr(item, "id", "")): item
                for item in current
                if getattr(item, "id", None)
            }
            same = len(current) == len(desired) and all(
                str(getattr(old, "id", "")) == str(getattr(new, "id", ""))
                and _content_to_text(getattr(old, "content", ""))
                == _content_to_text(getattr(new, "content", ""))
                for old, new in zip(current, desired)
            )
            if not same:
                current_ids = [str(getattr(item, "id", "")) for item in current]
                desired_ids = [str(getattr(item, "id", "")) for item in desired]
                append_only = (
                    len(desired_ids) >= len(current_ids)
                    and desired_ids[: len(current_ids)] == current_ids
                    and all(
                        _content_to_text(getattr(old, "content", ""))
                        == _content_to_text(getattr(new, "content", ""))
                        for old, new in zip(current, desired)
                    )
                )
                if append_only:
                    # Normal traffic appends one or two messages. Sending only
                    # the delta avoids reprocessing a 1,000-turn transcript.
                    updates = desired[len(current) :]
                else:
                    # Source edits/deletions/reordering are uncommon. Rebuild
                    # the ordered state exactly when they do occur.
                    updates = [
                        *[
                            RemoveMessage(id=message_id)
                            for message_id in current_by_id
                        ],
                        *desired,
                    ]
                if updates:
                    graph.invoke({"messages": updates}, config=config)
            return self.get_sync(thread_id) or []

    def sync_state_sync(
        self,
        thread_id: str,
        *,
        verified_state: Mapping[str, Any] | None,
        context_snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        state_value = _verified_state_snapshot(verified_state)
        revision = int(state_value.get("revision") or 0)
        context_value = _context_snapshot(context_snapshot)
        with self._lock:
            graph = self._ensure_graph()
            graph.invoke(
                {
                    "verified_state": state_value,
                    "state_revision": revision,
                    "context_snapshot": context_value,
                },
                config=self._config(thread_id),
            )
            return self.get_checkpoint_sync(thread_id) or {
                "version": CHECKPOINT_VERSION,
                "messages": [],
                "verified_state": state_value,
                "state_revision": revision,
                "context_snapshot": context_value,
            }

    def upsert_messages_sync(
        self,
        thread_id: str,
        messages: list[Mapping[str, Any]],
    ) -> None:
        """Apply a recent authoritative message window as a LangGraph delta."""

        if not messages:
            return
        updates = [
            _to_langchain_message(item, index)
            for index, item in enumerate(messages)
        ]
        with self._lock:
            graph = self._ensure_graph()
            graph.invoke({"messages": updates}, config=self._config(thread_id))

    def sync_llm_summary_sync(
        self,
        thread_id: str,
        *,
        summary: Mapping[str, Any],
        through_message_id: str,
        model_id: str,
    ) -> dict[str, Any]:
        """Persist a validated running summary without replacing transcript."""

        summary_value = _json_safe(dict(summary)) or {}
        checksum_payload = {
            "version": SUMMARY_VERSION,
            "summary": summary_value,
            "through_message_id": str(through_message_id or ""),
            "model_id": str(model_id or ""),
        }
        checksum = hashlib.sha256(
            json.dumps(
                checksum_payload,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with self._lock:
            graph = self._ensure_graph()
            current = self.get_checkpoint_sync(thread_id) or {}
            revision = int(current.get("llm_summary_revision") or 0) + 1
            graph.invoke(
                {
                    "llm_summary": summary_value,
                    "llm_summary_revision": revision,
                    "llm_summary_through_message_id": str(
                        through_message_id or ""
                    ),
                    "llm_summary_checksum": checksum,
                    "llm_summary_model_id": str(model_id or ""),
                },
                config=self._config(thread_id),
            )
            return self.get_checkpoint_sync(thread_id) or {}


_DEFAULT_STORE: _CheckpointStore | None = None
_DEFAULT_STORE_LOCK = threading.Lock()


def _default_store() -> _CheckpointStore:
    global _DEFAULT_STORE
    with _DEFAULT_STORE_LOCK:
        if _DEFAULT_STORE is None:
            _DEFAULT_STORE = _CheckpointStore(LANGGRAPH_CHECKPOINT_FILE)
        return _DEFAULT_STORE


def sync_history_sync(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    history: list[Mapping[str, Any]],
    path: str | os.PathLike[str] | None = None,
) -> list[dict[str, str]]:
    """Synchronize the database transcript into a LangGraph checkpoint."""

    thread_id = build_thread_id(conversation_id, owner_key=owner_key, role=role)
    store = _CheckpointStore(path) if path is not None else _default_store()
    return store.sync_sync(thread_id, history)


def get_history_sync(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    path: str | os.PathLike[str] | None = None,
) -> list[dict[str, str]] | None:
    thread_id = build_thread_id(conversation_id, owner_key=owner_key, role=role)
    store = _CheckpointStore(path) if path is not None else _default_store()
    return store.get_sync(thread_id)


def sync_state_sync(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    verified_state: Mapping[str, Any] | None,
    context_snapshot: Mapping[str, Any] | None = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Checkpoint backend-verified memory and bounded compaction metadata."""

    thread_id = build_thread_id(conversation_id, owner_key=owner_key, role=role)
    store = _CheckpointStore(path) if path is not None else _default_store()
    return store.sync_state_sync(
        thread_id,
        verified_state=verified_state,
        context_snapshot=context_snapshot,
    )


def get_checkpoint_sync(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    thread_id = build_thread_id(conversation_id, owner_key=owner_key, role=role)
    store = _CheckpointStore(path) if path is not None else _default_store()
    return store.get_checkpoint_sync(thread_id)


def upsert_messages_sync(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    messages: list[Mapping[str, Any]],
    path: str | os.PathLike[str] | None = None,
) -> None:
    thread_id = build_thread_id(conversation_id, owner_key=owner_key, role=role)
    store = _CheckpointStore(path) if path is not None else _default_store()
    store.upsert_messages_sync(thread_id, messages)


async def sync_history(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    history: list[Mapping[str, Any]],
    path: str | os.PathLike[str] | None = None,
) -> list[dict[str, str]]:
    return await asyncio.to_thread(
        sync_history_sync,
        conversation_id,
        owner_key=owner_key,
        role=role,
        history=history,
        path=path,
    )


async def get_history(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
) -> list[dict[str, str]] | None:
    return await asyncio.to_thread(
        get_history_sync,
        conversation_id,
        owner_key=owner_key,
        role=role,
    )


async def upsert_messages(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    messages: list[Mapping[str, Any]],
) -> None:
    await asyncio.to_thread(
        upsert_messages_sync,
        conversation_id,
        owner_key=owner_key,
        role=role,
        messages=messages,
    )


async def append_message(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    message: Mapping[str, Any],
) -> list[dict[str, str]] | None:
    """Append one persisted message without treating its prose as evidence."""

    await asyncio.to_thread(
        upsert_messages_sync,
        conversation_id,
        owner_key=owner_key,
        role=role,
        messages=[dict(message)],
    )
    return None


async def sync_state(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    verified_state: Mapping[str, Any] | None,
    context_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        sync_state_sync,
        conversation_id,
        owner_key=owner_key,
        role=role,
        verified_state=verified_state,
        context_snapshot=context_snapshot,
    )


def _summary_int_env(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
    environ: Mapping[str, str] | None = None,
) -> int:
    values = environ or os.environ
    try:
        return min(maximum, max(minimum, int(values.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _summary_float_env(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
    environ: Mapping[str, str] | None = None,
) -> float:
    values = environ or os.environ
    try:
        return min(maximum, max(minimum, float(values.get(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _summary_message_role(message: Mapping[str, Any]) -> str:
    role = str(
        message.get("role") or message.get("sender_role") or "user"
    ).strip().casefold()
    return "assistant" if role == "assistant" else "user"


def _summary_message_payload(
    message: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, str] | None:
    status = str(message.get("status") or "complete").strip().casefold()
    if status in {"pending", "cancelled", "error"}:
        return None
    content = " ".join(_content_to_text(message.get("content")).split())
    if not content:
        return None
    return {
        "id": _message_id(message, index),
        "role": _summary_message_role(message),
        # One pathological message must not consume the whole summarization
        # request. The complete transcript remains in the source store.
        "content": content[:2_000],
    }


def _summary_prompt(
    *,
    previous_summary: Mapping[str, Any] | None,
    messages: Sequence[Mapping[str, str]],
) -> str:
    transcript = "\n".join(
        f"[{item.get('id')}] "
        f"{'Người dùng' if item.get('role') == 'user' else 'Trợ lý'}: "
        f"{item.get('content')}"
        for item in messages
    )
    previous = json.dumps(
        dict(previous_summary or {}),
        ensure_ascii=False,
        sort_keys=True,
    )
    return f"""Bạn là node nén hội thoại của LangGraph.

Hãy cập nhật bản tóm tắt đang chạy từ phần lịch sử mới. Đây chỉ là ngữ cảnh
hội thoại, KHÔNG PHẢI căn cứ pháp luật. Không biến lời cũ của trợ lý thành sự
thật pháp lý. Chỉ lưu user_facts khi chính người dùng đã nói, kèm đúng ID tin
nhắn người dùng. Không lưu URL, số hiệu/Điều/Khoản/Điểm, thời hạn, phí, mức tiền,
cơ quan có thẩm quyền hoặc kết luận pháp luật.

`open_questions` chỉ chứa câu hỏi người dùng đã nêu nhưng chưa được giải quyết
hoặc việc người dùng nói rõ muốn làm tiếp; không tự tạo câu hỏi gợi ý.

BẢN TÓM TẮT TRƯỚC:
{previous}

PHẦN HỘI THOẠI MỚI CẦN NÉN:
{transcript}

Chỉ trả một JSON object, không Markdown, theo schema:
{{
  "topic_summary": "tối đa 1200 ký tự",
  "current_goal": "tối đa 300 ký tự",
  "topics": ["tối đa 8 chủ đề"],
  "user_facts": [
    {{"text": "dữ kiện", "source_message_id": "ID", "status": "user_stated"}}
  ],
  "open_questions": ["tối đa 8 câu"],
  "referenced_turn_ids": ["ID tin nhắn"]
}}"""


def _summary_response_text(value: Any) -> str:
    content = getattr(value, "content", value)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content or "")


def _parse_summary_output(
    raw: Any,
    *,
    messages: Sequence[Mapping[str, str]],
    previous_summary: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    text = _summary_response_text(raw).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, Mapping):
        return None

    # Reuse the existing legal-memory safety contract. Only message IDs from
    # this owner/role-scoped thread can be referenced.
    from api.chat_memory_service import (
        _merge_conversation_digest_v2,
        validate_conversation_patch_v2,
    )

    previous_ids = [
        str(item)
        for item in (previous_summary or {}).get("referenced_turn_ids") or []
        if str(item).strip()
    ]
    previous_user_ids = [
        str(item.get("source_message_id") or "")
        for item in (previous_summary or {}).get("user_facts") or []
        if isinstance(item, Mapping)
        and str(item.get("source_message_id") or "").strip()
    ]
    allowed_ids = list(
        dict.fromkeys(
            [
                *(str(item.get("id") or "") for item in messages),
                *previous_ids,
                *previous_user_ids,
            ]
        )
    )
    current_user_ids = {
        str(item.get("id") or "")
        for item in messages
        if item.get("role") == "user"
    }
    normalized_payload = dict(payload)
    normalized_facts: list[Any] = []
    for raw_fact in payload.get("user_facts") or []:
        if not isinstance(raw_fact, Mapping):
            normalized_facts.append(raw_fact)
            continue
        fact = dict(raw_fact)
        source_id = str(fact.get("source_message_id") or "")
        # Small local models often omit a constant enum field even when they
        # bind the fact to a valid user message. Fill only this transport
        # constant; never invent text or provenance.
        if (
            not str(fact.get("status") or "").strip()
            and source_id in current_user_ids | set(previous_user_ids)
        ):
            fact["status"] = "user_stated"
        normalized_facts.append(fact)
    normalized_payload["user_facts"] = normalized_facts
    validated = validate_conversation_patch_v2(
        normalized_payload,
        allowed_message_ids=allowed_ids,
    )
    if validated is None:
        return None
    user_ids = current_user_ids | set(previous_user_ids)
    if any(
        str(item.get("source_message_id") or "") not in user_ids
        for item in validated.get("user_facts") or []
    ):
        return None
    return _merge_conversation_digest_v2(previous_summary, validated)


async def _run_summary_graph(
    *,
    llm: Any,
    previous_summary: Mapping[str, Any] | None,
    messages: Sequence[Mapping[str, str]],
) -> dict[str, Any] | None:
    """Invoke the summarization model from inside a LangGraph node."""

    async def summarize_node(
        state: ConversationSummaryGraphState,
    ) -> dict[str, Any]:
        prompt = _summary_prompt(
            previous_summary=state.get("previous_summary") or {},
            messages=state.get("messages_to_summarize") or [],
        )
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "Bạn nén hội thoại thành JSON an toàn. Không trả lời "
                        "câu hỏi pháp luật trong node này."
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        return {"raw_model_output": _summary_response_text(response)}

    builder = StateGraph(ConversationSummaryGraphState)
    builder.add_node("summarize", summarize_node)
    builder.add_edge(START, "summarize")
    builder.add_edge("summarize", END)
    graph = builder.compile()
    result = await graph.ainvoke(
        {
            "previous_summary": dict(previous_summary or {}),
            "messages_to_summarize": [dict(item) for item in messages],
            "allowed_message_ids": [
                str(item.get("id") or "") for item in messages
            ],
            "raw_model_output": "",
        }
    )
    return _parse_summary_output(
        result.get("raw_model_output"),
        messages=messages,
        previous_summary=previous_summary,
    )


async def summarize_history_with_langgraph(
    conversation_id: str,
    *,
    owner_key: str,
    role: str,
    history: Sequence[Mapping[str, Any]],
    model_id: str,
    llm: Any | None = None,
    environ: Mapping[str, str] | None = None,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Incrementally summarize old messages and persist the running summary.

    The LLM call occurs inside a LangGraph node. Transcript messages are never
    deleted; the resulting summary is an additional checkpoint field. If the
    model or parser fails, callers keep the deterministic compaction path.
    """

    if not is_llm_summary_enabled(role, environ):
        return None
    prepared = [
        item
        for index, raw in enumerate(history)
        if (item := _summary_message_payload(raw, index=index)) is not None
    ]
    # Preserve the six newest user/assistant turns verbatim in model context.
    older = prepared[:-12]
    if not older:
        return None

    thread_id = build_thread_id(
        conversation_id,
        owner_key=owner_key,
        role=role,
    )
    store = _CheckpointStore(path) if path is not None else _default_store()
    checkpoint = await asyncio.to_thread(store.get_checkpoint_sync, thread_id)
    checkpoint = checkpoint or {}
    previous_summary = (
        dict(checkpoint.get("llm_summary") or {})
        if isinstance(checkpoint.get("llm_summary"), Mapping)
        else {}
    )
    through_id = str(
        checkpoint.get("llm_summary_through_message_id") or ""
    )
    start = 0
    if through_id:
        matched = next(
            (
                index
                for index, item in enumerate(older)
                if str(item.get("id") or "") == through_id
            ),
            None,
        )
        if matched is None:
            # The source transcript was edited or pruned. Rebuild from the
            # authoritative order instead of extending a stale summary.
            previous_summary = {}
        else:
            start = matched + 1
    pending = older[start:]
    minimum_new = _summary_int_env(
        "CHAT_LANGGRAPH_SUMMARY_MIN_NEW_MESSAGES",
        4,
        minimum=1,
        maximum=40,
        environ=environ,
    )
    if previous_summary and len(pending) < minimum_new:
        return checkpoint
    if not pending:
        return checkpoint if previous_summary else None

    max_messages = _summary_int_env(
        "CHAT_LANGGRAPH_SUMMARY_MAX_MESSAGES_PER_CALL",
        80,
        minimum=4,
        maximum=200,
        environ=environ,
    )
    max_chars = _summary_int_env(
        "CHAT_LANGGRAPH_SUMMARY_MAX_INPUT_CHARS",
        24_000,
        minimum=4_000,
        maximum=80_000,
        environ=environ,
    )
    selected: list[dict[str, str]] = []
    used_chars = 0
    for item in pending[:max_messages]:
        item_chars = len(str(item.get("content") or "")) + 80
        if selected and used_chars + item_chars > max_chars:
            break
        selected.append(dict(item))
        used_chars += item_chars
    if not selected:
        return checkpoint if previous_summary else None

    if llm is None:
        from open_notebook.ai.models import Model, model_manager

        model_record = await Model.get(model_id)
        model_kwargs: dict[str, Any] = {"temperature": 0}
        if str(getattr(model_record, "provider", "")).casefold() == "ollama":
            # Esperanto maps this portable structured-output setting to
            # ChatOllama(format="json"). Keeping it here (rather than in the
            # prompt) prevents local Qwen variants from wrapping or explaining
            # the summary envelope.
            model_kwargs.update(
                {
                    "structured": {"type": "json_object"},
                    "max_tokens": 768,
                    "num_ctx": 8192,
                    "keep_alive": "10m",
                }
            )
        model = await model_manager.get_model(model_id, **model_kwargs)
        if model is None:
            return checkpoint if previous_summary else None
        llm = model.to_langchain()
    timeout = _summary_float_env(
        "CHAT_LANGGRAPH_SUMMARY_TIMEOUT_SECONDS",
        12.0,
        minimum=3.0,
        maximum=60.0,
        environ=environ,
    )
    try:
        summary = await asyncio.wait_for(
            _run_summary_graph(
                llm=llm,
                previous_summary=previous_summary,
                messages=selected,
            ),
            timeout=timeout,
        )
    except Exception:
        return checkpoint if previous_summary else None
    if summary is None:
        return checkpoint if previous_summary else None
    return await asyncio.to_thread(
        store.sync_llm_summary_sync,
        thread_id,
        summary=summary,
        through_message_id=str(selected[-1].get("id") or ""),
        model_id=str(model_id or ""),
    )
