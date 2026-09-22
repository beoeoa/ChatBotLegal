"""Durable local answer snapshots, replayed by the existing API process.

No provider credentials or request headers are stored. Ownership is checked
again by conversation persistence during replay; the spool is not an API.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger


def root() -> Path:
    return Path(os.environ.get("CHAT_OUTBOX_DIR") or Path(__file__).resolve().parents[1] / "data" / "conversation-outbox")


def enqueue(*, user_id: str, role: str, turn_id: str, ask: dict, response: dict) -> Path:
    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    identity = json.dumps([user_id, role, ask.get("conversation_id"), turn_id])
    path = directory / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
    payload = {"user_id": user_id, "role": role, "turn_id": turn_id, "ask": ask, "response": response}
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def acknowledge(path: Path) -> None:
    path.unlink(missing_ok=True)


async def drain_once(replay: Callable[[dict[str, Any]], Awaitable[bool]]) -> int:
    if not root().exists():
        return 0
    completed = 0
    for path in sorted(root().glob("*.json"), key=lambda p: p.stat().st_mtime)[:20]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            async with asyncio.timeout(10):
                saved = await replay(payload)
            if saved:
                acknowledge(path)
                completed += 1
        except Exception as exc:
            logger.warning("Conversation outbox retry pending reason={}", type(exc).__name__)
    return completed


async def recovery_loop() -> None:
    from api import conversation_service
    from api.models import AskRequest, AskResponse
    from api.routers.search import _persist_conversation_answer
    from starlette.requests import Request

    async def replay(payload):
        user_id, role = payload["user_id"], payload["role"]
        request = Request({"type": "http", "method": "POST", "path": "/internal/outbox", "headers": []})
        request.state.user_id, request.state.user_role = user_id, role
        ask = AskRequest(**payload["ask"])
        ask.role, ask.idempotency_key = role, payload["turn_id"]
        return await _persist_conversation_answer(ask, request, AskResponse(**payload["response"]), user_id=user_id)

    while True:
        try:
            # Do not acknowledge JSON fallback writes as database recovery.
            if root().exists() and any(root().glob("*.json")):
                async with asyncio.timeout(3):
                    available = await conversation_service._use_surreal()
                if available:
                    await drain_once(replay)
        except Exception as exc:
            logger.warning("Conversation outbox recovery unavailable reason={}", type(exc).__name__)
        await asyncio.sleep(5)
