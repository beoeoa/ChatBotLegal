"""One answer invocation policy shared by sourced answers and conversation."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from api.answer_streaming import InterruptedAnswer, collect_message_metadata, stream_model_answer
from api.chat_execution import current_turn, remaining_timeout


async def execute_answer_model(model_id, prompt, *, options, slots, emit=None, structured=False):
    from open_notebook.ai.provision import provision_langchain_model
    started = time.perf_counter()
    deadline = started + remaining_timeout(float(options.get("timeout", 75)))
    attempts = []
    raw = ""
    first_visible = None
    async def forward(event, payload):
        turn = current_turn.get()
        if turn and turn.first_visible_ms is None and event == "answer_delta" and payload.get("text"):
            turn.first_visible_ms = turn.elapsed_ms()
        if emit:
            await emit(event, payload)
    error = None
    # Retry and cross-provider policy belongs exclusively to ModelGateway.
    # One execution never repeats an empty/refused/length-limited response.
    for number in range(1, 2):
        attempt: dict[str, Any] = {"attempt": number, "requested_model_id": str(model_id)}
        turn = current_turn.get()
        attempt["started_ms"] = turn.elapsed_ms() if turn else round((time.perf_counter() - started) * 1000, 1)
        attempts.append(attempt)
        if turn:
            turn.attempts.append(attempt)
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError
        invocation_options = {**options, "timeout": remaining, "streaming": emit is not None and number == 1,
                              "allow_fallback": False, "max_retries": 0}
        attempt["streaming"] = invocation_options["streaming"]
        attempt["output_limit_requested"] = invocation_options.get("max_tokens")
        provision_started = time.perf_counter()
        try:
            async with asyncio.timeout(remaining):
                model = await provision_langchain_model(prompt, model_id, "chat", **invocation_options)
                attempt["effective_options"] = {k: v for k, v in getattr(model, "options", {}).items() if k in {"max_tokens", "temperature", "reasoning", "answer_depth", "visible_output_tokens"}}
                attempt["model_provision_ms"] = round((time.perf_counter() - provision_started) * 1000, 1)
                call_started = time.perf_counter()
                if emit is not None and number == 1 and getattr(model, "streaming", True):
                    raw = await stream_model_answer(model, prompt, timeout=max(0.001, deadline-time.perf_counter()-0.05),
                        slots=slots, emit=forward, structured=structured, telemetry=attempt)
                    if attempt.get("first_visible_ms") is not None:
                        first_visible = round((call_started-started)*1000 + attempt["first_visible_ms"], 1)
                else:
                    from api.legal_structured_answer import invoke_blocking_model_with_deadline
                    async with slots:
                        attempt["queue_wait_ms"] = round((time.perf_counter()-call_started)*1000, 1)
                        message = await invoke_blocking_model_with_deadline(model, prompt,
                            timeout=deadline-time.perf_counter(), structured_output=structured)
                    attempt.update(collect_message_metadata(message))
                    content = getattr(message, "content", "")
                    raw = content if isinstance(content, str) else "".join(
                        item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text")
                    attempt["generation_total_ms"] = round((time.perf_counter()-call_started)*1000, 1)
                    if raw.strip():
                        first_visible = round((time.perf_counter()-started)*1000, 1)
            error = None
        except InterruptedAnswer as exc:
            raw = exc.text
            error = None
            attempt["finish_reason"] = "interrupted"
        except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            error = exc
            attempt["error_type"] = type(exc).__name__
        finally:
            attempt["completed_ms"] = turn.elapsed_ms() if turn else round((time.perf_counter()-started)*1000, 1)
        if raw.strip():
            break
        finish = attempt.get("finish_reason")
        # Budget exhaustion and refusals are not transport failures. Repeating
        # the same prompt/budget cannot repair them and used to consume 75s.
        if finish in {"length", "max_tokens", "content_filter", "refusal"}:
            break
        if number == 2 or deadline-time.perf_counter() <= 1.0:
            break
        attempt["retry_reason"] = "connection_error" if error else "empty_response"
    if error:
        raise error
    last = attempts[-1]
    failure = "PROVIDER_STREAM_INTERRUPTED" if last.get("finish_reason") == "interrupted" else None
    if not raw.strip():
        failure = "PROVIDER_OUTPUT_EXHAUSTED" if last.get("finish_reason") in {"length", "max_tokens"} else (
            "PROVIDER_REFUSAL" if last.get("finish_reason") in {"refusal", "content_filter"} else "PROVIDER_EMPTY_RESPONSE")
    return raw, {**last, "attempts": attempts, "retry_count": len(attempts)-1,
        "first_content_ms": first_visible, "first_visible_ms": first_visible,
        "generation_total_ms": round((time.perf_counter()-started)*1000, 1),
        "provider_error_code": failure,
        "model_provision_ms": round(sum(a.get("model_provision_ms", 0) for a in attempts), 1)}
