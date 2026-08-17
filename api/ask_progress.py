"""Safe progress streaming and provider-fallback policy for legal Ask.

The progress contract deliberately carries lifecycle metadata and verified
sources only.  Generated answer text is emitted exactly once, in ``final``,
after the existing Ask pipeline has completed validation and persistence.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import httpx
from fastapi import HTTPException

from api.models import AskResponse, CitationDisplayItem
from api.legal_section_grounding import format_public_citation
from open_notebook.exceptions import (
    ExternalServiceError,
    LegalRetrievalUnavailableError,
    NetworkError,
    RateLimitError,
)


ProgressCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
ProgressExecutor = Callable[[ProgressCallback], Awaitable[AskResponse]]

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_ALLOWED_STATUS_STAGES = frozenset(
    {"retrieving", "generating", "validating", "persisting"}
)
_SOURCE_FIELDS = frozenset(
    {
        "law_number",
        "document_title",
        "article_number",
        "article_title",
        "effective_status",
        "effective_date",
        "issuing_agency",
        "scope",
        "source_url",
        "url",
        "title",
        "score",
    }
)
_INTERNAL_RESPONSE_KEYS = frozenset(
    {"chunk_id", "source_id", "packet_id", "evidence_id", "request_id", "issue_id"}
)


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def ask_progress_enabled() -> bool:
    """Return whether the opt-in progress endpoint is enabled."""

    return _env_flag("LEGAL_ASK_PROGRESS_ENABLED", default=False)


def local_fallback_enabled() -> bool:
    """Return whether transient cloud failures may use the local model."""

    return _env_flag("LEGAL_LOCAL_FALLBACK_ENABLED", default=True)


def emit_ask_progress(
    callback: ProgressCallback | None,
    event: str,
    payload: dict[str, Any],
) -> Awaitable[None]:
    """Call a progress callback, or return a completed no-op awaitable."""

    async def _emit() -> None:
        if callback is not None:
            await callback(event, payload)

    return _emit()


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def cloud_fallback_reason(exc: BaseException) -> str | None:
    """Classify only transient *model-provider* failures for local fallback.

    Retrieval failures and insufficient evidence are intentionally excluded:
    a local model cannot replace missing, current legal evidence.
    """

    chain = _exception_chain(exc)
    if any(isinstance(item, LegalRetrievalUnavailableError) for item in chain):
        return None
    if any("insufficient_evidence" in str(item).lower() for item in chain):
        return None

    for item in chain:
        if isinstance(item, httpx.HTTPStatusError):
            status = item.response.status_code
            if status == 429:
                return "provider_rate_limit"
            if status in {500, 502, 503, 504}:
                return "provider_5xx"
            continue
        if isinstance(item, httpx.TimeoutException):
            return "provider_timeout"
        if isinstance(item, httpx.ConnectError):
            return "provider_connect_error"
        if isinstance(item, RateLimitError):
            return "provider_rate_limit"
        if isinstance(item, NetworkError):
            return "provider_network_error"
        if isinstance(item, ExternalServiceError):
            message = str(item).lower()
            if any(
                marker in message
                for marker in (
                    " 500",
                    "(500",
                    " 502",
                    "(502",
                    " 503",
                    "(503",
                    " 504",
                    "(504",
                    "service unavailable",
                    "temporarily unavailable",
                    "provider unavailable",
                    "overloaded",
                    "internal server error",
                )
            ):
                return "provider_5xx"
    return None


def encode_sse(event: str, payload: dict[str, Any]) -> str:
    """Encode one named SSE event as compact UTF-8-safe JSON."""

    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {data}\n\n"


def _encode_progress_event(
    event: str,
    payload: dict[str, Any],
    *,
    compatibility_data_envelope: bool,
) -> str:
    if not compatibility_data_envelope:
        return encode_sse(event, payload)
    envelope = {"type": event}
    envelope.update(payload)
    data = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"data: {data}\n\n"


def _safe_sources(payload: dict[str, Any]) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    raw_citations = payload.get("citations")
    if isinstance(raw_citations, list):
        for citation in raw_citations:
            if isinstance(citation, dict):
                citations.append(
                    {
                        key: value
                        for key, value in citation.items()
                        if key in _SOURCE_FIELDS and value is not None
                    }
                )
    result: dict[str, Any] = {"citations": citations}
    if isinstance(payload.get("retrieval_timing_ms"), (int, float)):
        result["retrieval_timing_ms"] = max(0, round(float(payload["retrieval_timing_ms"]), 1))
    return result


def _strip_internal_response_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_internal_response_fields(nested)
            for key, nested in value.items()
            if str(key).casefold() not in _INTERNAL_RESPONSE_KEYS
        }
    if isinstance(value, list):
        return [_strip_internal_response_fields(item) for item in value]
    return value


def _project_public_citation(value: dict[str, Any]) -> dict[str, Any]:
    """Keep proof already verified by the claim pipeline exactly once.

    Structured answer sections contain ``CitationDisplayItem`` values created
    only after request/issue/quote validation. Re-running the provenance
    verifier here loses its source text (intentionally absent from the public
    object) and incorrectly downgrades valid proof to ``metadata_only``.
    Raw retrieval citations still go through the normal formatter.
    """

    if (
        value.get("verification_status") == "verified"
        and value.get("verification_level") in {"content_quote", "physical_span"}
        and isinstance(value.get("proof"), dict)
    ):
        try:
            return CitationDisplayItem.model_validate(value).model_dump(
                mode="json", exclude_none=True
            )
        except (TypeError, ValueError):
            # An invalid pre-projected object must never inherit a verified
            # label merely because it supplied those strings.
            pass
    return format_public_citation(value)


def safe_public_response_payload(
    payload: dict[str, Any],
    *,
    include_admin_trace: bool,
) -> dict[str, Any]:
    """Allow-list public citations and remove retrieval/provenance IDs."""

    safe = dict(payload)
    safe["citations"] = [
        _project_public_citation(item)
        for item in (payload.get("citations") or [])
        if isinstance(item, dict)
    ]
    sections: list[dict[str, Any]] = []
    for raw in payload.get("answer_sections") or []:
        if hasattr(raw, "model_dump"):
            raw = raw.model_dump(mode="json")
        if not isinstance(raw, dict):
            continue
        section = {
            key: value
            for key, value in raw.items()
            if key in {
                "issue_id",
                "title",
                "status",
                "answer",
                "guidance",
                "limitation",
                "clarifying_question",
                "facet",
                "priority",
                "claim_types",
            }
        }
        section["citations"] = [
            _project_public_citation(item)
            for item in (raw.get("citations") or [])
            if isinstance(item, dict)
        ]
        sections.append(section)
    safe["answer_sections"] = sections if payload.get("answer_sections") is not None else None
    safe["rag_trace"] = (
        _strip_internal_response_fields(payload.get("rag_trace"))
        if include_admin_trace
        else None
    )
    return safe


def _safe_error(exc: BaseException, *, trace_id: str) -> dict[str, Any]:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return {
                "code": str(detail.get("code") or "ASK_HTTP_ERROR"),
                "message": str(
                    detail.get("message")
                    or "Hệ thống chưa thể hoàn tất câu trả lời. Vui lòng thử lại."
                ),
                "retryable": bool(detail.get("retryable", exc.status_code >= 500)),
                "trace_id": trace_id,
            }
        return {
            "code": "ASK_HTTP_ERROR",
            "message": (
                "Yêu cầu chưa hợp lệ. Vui lòng kiểm tra thông tin đã nhập."
                if exc.status_code < 500
                else "Hệ thống chưa thể hoàn tất câu trả lời. Vui lòng thử lại."
            ),
            "retryable": exc.status_code >= 500,
            "trace_id": trace_id,
        }

    if isinstance(exc, RateLimitError):
        code, message, retryable = (
            "AI_PROVIDER_QUOTA_OR_RATE_LIMIT",
            "Nhà cung cấp AI đang giới hạn yêu cầu. Vui lòng thử lại sau.",
            True,
        )
    elif isinstance(exc, (NetworkError, ExternalServiceError, httpx.HTTPError)):
        code, message, retryable = (
            "AI_PROVIDER_UNAVAILABLE",
            "Dịch vụ AI tạm thời gián đoạn. Vui lòng thử lại.",
            True,
        )
    else:
        code, message, retryable = (
            "ASK_FAILED",
            "Hệ thống chưa thể hoàn tất câu trả lời. Vui lòng thử lại.",
            True,
        )
    return {
        "code": code,
        "message": message,
        "retryable": retryable,
        "trace_id": trace_id,
    }


async def stream_ask_progress(
    execute: ProgressExecutor,
    *,
    trace_id: str,
    conversation_id: str | None,
    idempotency_key: str | None,
    effective_role: str | None,
    compatibility_data_envelope: bool = False,
) -> AsyncGenerator[str, None]:
    """Run Ask while emitting the validated, ordered progress contract."""

    started = time.perf_counter()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def progress(event: str, payload: dict[str, Any]) -> None:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        if event == "status":
            stage = str(payload.get("stage") or "")
            if stage in _ALLOWED_STATUS_STAGES:
                await queue.put(("event", ("status", {"stage": stage, "elapsed_ms": elapsed_ms})))
        elif event == "sources":
            source_payload = _safe_sources(payload)
            source_payload["elapsed_ms"] = elapsed_ms
            await queue.put(("event", ("sources", source_payload)))

    async def runner() -> None:
        try:
            response = await execute(progress)
        except asyncio.CancelledError:
            await queue.put(("cancelled", None))
            raise
        except BaseException as exc:  # converted to a safe SSE error below
            await queue.put(("error", exc))
        else:
            await queue.put(("result", response))

    yield _encode_progress_event(
        "accepted",
        {
            "trace_id": trace_id,
            "conversation_id": conversation_id,
            "idempotency_key": idempotency_key,
        },
        compatibility_data_envelope=compatibility_data_envelope,
    )
    yield _encode_progress_event(
        "status",
        {"stage": "retrieving", "elapsed_ms": 0.0},
        compatibility_data_envelope=compatibility_data_envelope,
    )

    task = asyncio.create_task(runner(), name=f"ask-progress-{trace_id[:12]}")
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "event":
                event, data = payload
                yield _encode_progress_event(
                    event,
                    data,
                    compatibility_data_envelope=compatibility_data_envelope,
                )
                continue
            if kind == "cancelled":
                raise asyncio.CancelledError()
            if kind == "error":
                yield _encode_progress_event(
                    "error",
                    _safe_error(payload, trace_id=trace_id),
                    compatibility_data_envelope=compatibility_data_envelope,
                )
                yield _encode_progress_event(
                    "complete",
                    {
                        "trace_id": trace_id,
                        "total_ms": round((time.perf_counter() - started) * 1000, 1),
                        "outcome": "error",
                    },
                    compatibility_data_envelope=compatibility_data_envelope,
                )
                return

            response = payload
            if not isinstance(response, AskResponse):
                contract_error = TypeError(
                    "Ask progress executor returned an invalid response"
                )
                yield _encode_progress_event(
                    "error",
                    _safe_error(contract_error, trace_id=trace_id),
                    compatibility_data_envelope=compatibility_data_envelope,
                )
                yield _encode_progress_event(
                    "complete",
                    {
                        "trace_id": trace_id,
                        "total_ms": round(
                            (time.perf_counter() - started) * 1000,
                            1,
                        ),
                        "outcome": "error",
                    },
                    compatibility_data_envelope=compatibility_data_envelope,
                )
                return
            updates: dict[str, Any] = {"trace_id": trace_id}
            if effective_role != "admin":
                updates["rag_trace"] = None
            response = response.model_copy(update=updates)
            response_payload = safe_public_response_payload(
                response.model_dump(mode="json"),
                include_admin_trace=effective_role == "admin",
            )
            yield _encode_progress_event(
                "final",
                {"response": response_payload},
                compatibility_data_envelope=compatibility_data_envelope,
            )
            if compatibility_data_envelope:
                # A few historical diagnostic scripts read `final_answer`.
                # Keep a post-validation alias while never reviving strategy,
                # token, per-source answer or draft events.
                yield _encode_progress_event(
                    "final_answer",
                    {
                        "content": response.answer,
                        "validated": True,
                        "trace_id": trace_id,
                    },
                    compatibility_data_envelope=True,
                )
            complete_payload: dict[str, Any] = {
                "trace_id": trace_id,
                "total_ms": round((time.perf_counter() - started) * 1000, 1),
                "outcome": "success",
            }
            if compatibility_data_envelope:
                complete_payload.update(
                    {
                        "final_answer": response.answer,
                        "citations": response_payload.get("citations") or [],
                        "grounding_status": response.grounding_status,
                        "domain_mismatch": response.domain_mismatch,
                        "suggested_domain": response.suggested_domain,
                        "procedure_detail": response.procedure_detail,
                        "recommended_forms": response.recommended_forms or [],
                        "faqs": response.faqs or [],
                    }
                )
            yield _encode_progress_event(
                "complete",
                complete_payload,
                compatibility_data_envelope=compatibility_data_envelope,
            )
            return
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:
            # Cancellation is represented by generator cancellation, never an
            # SSE error event or a persisted failed assistant message.
            pass
