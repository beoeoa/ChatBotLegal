"""Incremental public answer projection; never expose JSON status fields or reasoning."""
import asyncio
import json
import time
from typing import Any, MutableMapping

import httpx

from loguru import logger


class InterruptedAnswer(Exception):
    """Transport ended after public prose was received; keep that prose."""

    def __init__(self, text: str):
        super().__init__("provider_stream_interrupted")
        self.text = text


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _merge_usage(target: MutableMapping[str, Any], value: Any) -> None:
    usage = _mapping(value)
    if not usage:
        return
    normalized = {
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens")),
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens")),
        "total_tokens": usage.get("total_tokens"),
    }
    output_details = _mapping(usage.get("output_token_details") or usage.get("completion_tokens_details"))
    normalized["reasoning_tokens"] = output_details.get(
        "reasoning", output_details.get("reasoning_tokens")
    )
    for key, item in normalized.items():
        if item is not None:
            target[key] = item


def collect_message_metadata(message: Any) -> dict[str, Any]:
    """Return provider-neutral completion metadata without answer/reasoning text."""

    response = _mapping(getattr(message, "response_metadata", None))
    message_id = getattr(message, "id", None)
    provider_id = response.get("id") or response.get("generation_id")
    if not provider_id and message_id and not str(message_id).startswith(("lc_run", "run-")):
        provider_id = message_id
    metadata: dict[str, Any] = {
        "finish_reason": response.get("finish_reason") or response.get("stop_reason"),
        "actual_model": response.get("model_name") or response.get("model"),
        "provider_generation_id": provider_id,
        "actual_provider": response.get("provider_name"),
    }
    _merge_usage(metadata, getattr(message, "usage_metadata", None))
    _merge_usage(metadata, response.get("token_usage") or response.get("usage"))
    return {key: value for key, value in metadata.items() if value is not None}


def partial_answer(raw: str, *, structured: bool) -> str:
    text = raw.lstrip()
    if not structured and not text.startswith(("{", "```")):
        return text if "<think" not in text else ""
    if text.startswith("```json"):
        text = text[7:].lstrip()
    elif text.startswith("```"):
        text = text[3:].lstrip()
    if not text.startswith("{"):
        return ""
    # Walk root properties; never pick an answer key inside reasoning/metadata.
    decoder = json.JSONDecoder()
    pos = 1
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        try:
            key, pos = decoder.raw_decode(text, pos)
        except ValueError:
            return ""
        if not isinstance(key, str):
            return ""
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text) or text[pos] != ":":
            return ""
        pos += 1
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if key == "answer":
            if pos >= len(text) or text[pos] != '"':
                return ""
            start = pos + 1
            break
        try:
            _, pos = decoder.raw_decode(text, pos)
        except ValueError:
            return ""
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text) or text[pos] != ",":
            return ""
        pos += 1
    escaped = False
    end = start
    while end < len(text):
        c = text[end]
        if c == '"' and not escaped:
            break
        if c == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
        end += 1
    body = text[start:end]
    # Truncated JSON escapes/unicode belong to the next provider chunk.
    for cut in range(min(6, len(body)) + 1):
        candidate = body[:len(body)-cut] if cut else body
        try:
            decoded = json.loads('"' + candidate + '"')
            # Do not publish half of a UTF-16 surrogate pair across SSE packets.
            if decoded and 0xD800 <= ord(decoded[-1]) <= 0xDBFF:
                decoded = decoded[:-1]
            return decoded
        except ValueError:
            continue
    return ""


async def stream_model_answer(
    model,
    prompt,
    *,
    timeout,
    slots,
    emit,
    structured,
    telemetry: MutableMapping[str, Any] | None = None,
):
    from api.legal_structured_answer import structured_model_invocation_options
    # LangChain treats instance streaming=False as a hard opt-out even for
    # astream(). Copy configuration, sharing clients but not changing the cached
    # adapter used by concurrent non-streaming calls.
    if getattr(model, "streaming", None) is False and hasattr(model, "model_copy"):
        model = model.model_copy(update={"streaming": True})
    invocation_options = structured_model_invocation_options(
        model,
        structured=structured,
    )
    raw = ""
    previous = ""
    emitted_at = 0.0
    started = time.monotonic()
    chunks = 0
    first_chunk_ms = None
    first_visible_ms = None
    queue_started = time.monotonic()
    queue_wait_ms = None
    stream_metadata: dict[str, Any] = {}
    try:
        async with asyncio.timeout(timeout):
            async with slots:
                queue_wait_ms = round((time.monotonic() - queue_started) * 1000)
                async for chunk in model.astream(prompt, **invocation_options):
                    stream_metadata.update(collect_message_metadata(chunk))
                    value = getattr(chunk, "content", "")
                    if isinstance(value, list):
                        value = "".join(item.get("text", "") for item in value if isinstance(item, dict) and item.get("type") == "text")
                    if not isinstance(value, str):
                        continue
                    if value:
                        chunks += 1
                        if first_chunk_ms is None:
                            first_chunk_ms = round((time.monotonic() - started) * 1000)
                    raw += value
                    visible = partial_answer(raw, structured=structured)
                    now = time.monotonic()
                    if visible and first_visible_ms is None:
                        first_visible_ms = round((now - started) * 1000)
                    if visible.startswith(previous) and len(visible) > len(previous) and now - emitted_at >= 0.06:
                        await emit("answer_delta", {"text": visible[len(previous):], "provisional": True})
                        previous = visible
                        emitted_at = now
                visible = partial_answer(raw, structured=structured)
                if visible.startswith(previous) and visible != previous:
                    await emit("answer_delta", {"text": visible[len(previous):], "provisional": True})
    except (TimeoutError, httpx.TransportError) as exc:
        if partial_answer(raw, structured=structured).strip():
            stream_metadata["finish_reason"] = "interrupted"
            raise InterruptedAnswer(raw) from exc
        raise
    finally:
        total_ms = round((time.monotonic() - started) * 1000)
        if telemetry is not None:
            telemetry.update(stream_metadata)
            telemetry.update(
                {
                    "queue_wait_ms": queue_wait_ms,
                    "first_content_ms": first_chunk_ms,
                    "first_visible_ms": first_visible_ms,
                    "generation_total_ms": total_ms,
                    "content_chunks": chunks,
                }
            )
    logger.info(
        'answer_stream_completed content_chunks={} first_content_ms={} total_ms={} finish_reason={}',
        chunks, first_chunk_ms, total_ms, stream_metadata.get("finish_reason"),
    )
    return raw
