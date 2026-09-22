"""Public, deterministic quick chat for unauthenticated visitors."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from api.auth import production_mode_enabled
from api.intent_fast_path import (
    intent_match_to_response,
    is_intent_fast_path_eligible,
    load_intent_catalog,
    match_intent_fast_path,
)
from api.procedure_fast_path import detect_procedure_facet, normalize_procedure_query
from api.public_quick_chat_quota import SharedQuickChatQuota
from api.quick_chat_natural_router import (
    QUICK_NATURAL_ROUTER_VERSION,
    natural_clarification_response,
    resolve_quick_chat_natural_question,
)
from api.quick_chat_presentation import (
    apply_followup_action,
    detect_followup_action,
    detect_meta_kind,
    meta_response,
)

router = APIRouter(prefix="/public", tags=["public-quick-chat"])

GUEST_COOKIE = "hp_guest_id"
_COOKIE_TTL_SECONDS = max(
    300, int(os.getenv("PUBLIC_QUICK_CHAT_COOKIE_TTL_SECONDS", "86400"))
)
_MAX_IDEMPOTENCY_ENTRIES = 2048
_SECRET_FALLBACK = "local-public-quick-chat-secret-v1"
MAX_PUBLIC_QUICK_CHAT_QUESTION_CHARS = 800

_TOPICLESS_WORDS = frozenset(
    {
        "a",
        "anh",
        "ban",
        "bao",
        "buoc",
        "cac",
        "can",
        "chi",
        "cho",
        "co",
        "con",
        "cua",
        "dau",
        "den",
        "duoc",
        "dang",
        "em",
        "gi",
        "giay",
        "gom",
        "het",
        "ho",
        "hoi",
        "khi",
        "ket",
        "ky",
        "lam",
        "lau",
        "le",
        "mat",
        "may",
        "minh",
        "nao",
        "ngay",
        "nhieu",
        "noi",
        "nop",
        "o",
        "phi",
        "phai",
        "qua",
        "sao",
        "so",
        "t",
        "tai",
        "the",
        "thi",
        "thoi",
        "thu",
        "thuc",
        "tien",
        "to",
        "toi",
        "trinh",
        "truoc",
        "tuc",
        "hien",
        "u",
        "uk",
        "vay",
        "xin",
    }
)


class QuickChatContextTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=MAX_PUBLIC_QUICK_CHAT_QUESTION_CHARS)


class QuickChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=MAX_PUBLIC_QUICK_CHAT_QUESTION_CHARS)
    idempotency_key: str = Field(min_length=8, max_length=128)
    # The browser may send a small, ephemeral context for follow-up questions.
    # It is deliberately question-only and capped at five turns; the public
    # route does not persist a transcript or reuse the main chatbot memory.
    context: list[QuickChatContextTurn] = Field(default_factory=list, max_length=5)


def _question_with_context(payload: QuickChatRequest) -> str:
    previous = [
        item.question.strip() for item in payload.context[-5:] if item.question.strip()
    ]
    if not previous:
        return payload.question
    # Keep the current question first so facet words such as "lệ phí" remain
    # dominant, then add the short context for procedure resolution.
    return "\n".join([payload.question.strip(), *previous])


def _has_standalone_topic_signal(question: str) -> bool:
    """Reject facet-only text before it can select an arbitrary intent."""

    normalized = normalize_procedure_query(question)
    return any(token not in _TOPICLESS_WORDS for token in normalized.split())


def _match_intent_with_context(payload: QuickChatRequest) -> tuple[Any, str]:
    direct_question = payload.question
    direct = match_intent_fast_path(direct_question, audience="guest")
    if direct.hit or not payload.context:
        return direct, direct_question
    contextual_question = _question_with_context(payload)
    return match_intent_fast_path(
        contextual_question, audience="guest"
    ), contextual_question


def _last_context_intent(payload: QuickChatRequest) -> Any | None:
    """Resolve the latest independently identifiable procedure in context."""

    for turn in reversed(payload.context):
        matched = match_intent_fast_path(turn.question, audience="guest")
        if matched.hit:
            return matched
    return None


def _contextual_facet_match(payload: QuickChatRequest) -> Any | None:
    """Follow a known procedure identity, never concatenate old answers."""

    if not payload.context:
        return None
    normalized = normalize_procedure_query(payload.question)
    contextual = len(normalized.split()) <= 5 or normalized.startswith(
        ("the ", "con ", "vay ", "uk ", "u ", "no ", "thu tuc nay ")
    )
    if not contextual:
        return None
    facet = detect_procedure_facet(payload.question)
    if facet == "overview":
        return None
    prior = _last_context_intent(payload)
    if prior is None or not prior.intent:
        return None
    procedure_id = str(prior.intent.get("procedure_id") or "").strip()
    direct = match_intent_fast_path(payload.question, audience="guest")
    if (
        direct.hit
        and direct.score >= 0.90
        and str((direct.intent or {}).get("procedure_id") or "").strip() != procedure_id
    ):
        # An explicit, high-confidence new procedure overrides old context.
        return direct
    candidates = [
        row
        for row in load_intent_catalog()
        if str(row.get("procedure_id") or "").strip() == procedure_id
        and str(row.get("intent_type") or row.get("answer_facet") or "") == facet
        and is_intent_fast_path_eligible(row, audience="guest")
    ]
    if len(candidates) != 1:
        return None
    # A question built only from the canonical wording of the selected row
    # avoids dragging an earlier facet into the matcher decision.
    return match_intent_fast_path(
        str(candidates[0].get("canonical_question") or ""), audience="guest"
    )


class _Quota:
    """Compatibility wrapper around the process-safe quota backend."""

    def __init__(self) -> None:
        self._shared = SharedQuickChatQuota()

    @staticmethod
    def _limits() -> tuple[int, int, int, int]:
        return (
            max(1, int(os.getenv("PUBLIC_QUICK_CHAT_PER_MINUTE", "10"))),
            max(1, int(os.getenv("PUBLIC_QUICK_CHAT_PER_DAY", "100"))),
            max(1, int(os.getenv("PUBLIC_QUICK_CHAT_GLOBAL_PER_MINUTE", "600"))),
            max(60, int(os.getenv("PUBLIC_QUICK_CHAT_IDEMPOTENCY_TTL_SECONDS", "900"))),
        )

    def reserve(self, key: str, *, now: float | None = None) -> tuple[bool, int, int]:
        minute_limit, day_limit, _, _ = self._limits()
        return self._shared.reserve(
            key,
            minute_limit=minute_limit,
            day_limit=day_limit,
            now=now,
        )

    def reserve_request(
        self,
        key: str,
        *,
        idempotency_key: str,
        now: float | None = None,
    ) -> tuple[bool, int, int, str]:
        minute_limit, day_limit, global_minute_limit, idempotency_ttl = self._limits()
        return self._shared.reserve_with_capacity(
            key,
            minute_limit=minute_limit,
            day_limit=day_limit,
            global_minute_limit=global_minute_limit,
            idempotency_key=idempotency_key,
            idempotency_ttl_seconds=idempotency_ttl,
            now=now,
        )


_quota = _Quota()
_idempotent: OrderedDict[str, dict[str, Any]] = OrderedDict()
_idempotency_lock = threading.Lock()


def _secret() -> bytes:
    explicit = str(os.getenv("PUBLIC_QUICK_CHAT_SECRET") or "").strip()
    master = str(os.getenv("OPEN_NOTEBOOK_ENCRYPTION_KEY") or "").strip()
    configured = explicit or master or _SECRET_FALLBACK
    if production_mode_enabled() and not explicit and not master:
        raise HTTPException(
            status_code=503, detail="public_quick_chat_secret_not_configured"
        )
    # Namespace a shared installation master key so the public-cookie secret
    # is not identical to encryption material used elsewhere.
    if not explicit and master:
        return hmac.new(
            master.encode("utf-8"), b"public-quick-chat-cookie-v1", hashlib.sha256
        ).digest()
    return configured.encode("utf-8")


def _guest_id_from_cookie(value: str | None) -> str | None:
    if not value:
        return None
    try:
        guest_id, issued, signature = value.split(".", 2)
        issued_at = int(issued)
    except (ValueError, TypeError):
        return None
    if not guest_id or abs(int(time.time()) - issued_at) > _COOKIE_TTL_SECONDS:
        return None
    message = f"{guest_id}.{issued_at}".encode("utf-8")
    expected = hmac.new(_secret(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    return guest_id


def _issue_guest_cookie() -> tuple[str, str]:
    guest_id = secrets.token_urlsafe(18)
    issued_at = int(time.time())
    value = f"{guest_id}.{issued_at}"
    signature = hmac.new(_secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()
    return guest_id, f"{value}.{signature}"


def _opaque_ip(request: Request) -> str:
    address = request.client.host if request.client else "unknown"
    return hmac.new(
        _secret(), f"ip:{address}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _origin_allowed(request: Request) -> bool:
    origin = str(request.headers.get("origin") or "").strip()
    if not origin:
        return True
    allowed_raw = str(os.getenv("CORS_ORIGINS") or "").strip()
    if allowed_raw in {"", "*"}:
        host = urlsplit(origin).netloc
        request_host = str(request.headers.get("host") or "").strip()
        # Local development normally sends Origin from the Next.js port while
        # the API receives the request on its own port. Keep the same local
        # origins accepted by the application's default CORS configuration;
        # production deployments should set CORS_ORIGINS explicitly.
        local_frontends = {
            "localhost:3000",
            "127.0.0.1:3000",
            "localhost:3001",
            "127.0.0.1:3001",
        }
        return bool(host) and (host == request_host or host in local_frontends)
    allowed = {
        item.strip().rstrip("/") for item in allowed_raw.split(",") if item.strip()
    }
    return origin.rstrip("/") in allowed


def _cache_key(
    identity: str,
    idempotency_key: str,
    release_id: str,
    payload: QuickChatRequest,
) -> str:
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "question": payload.question,
                "context": [turn.question for turn in payload.context],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return hashlib.sha256(
        f"quick:{identity}:{idempotency_key}:{release_id}:{request_fingerprint}".encode(
            "utf-8"
        )
    ).hexdigest()


def _get_cached(key: str) -> dict[str, Any] | None:
    with _idempotency_lock:
        value = _idempotent.get(key)
        if value is not None:
            _idempotent.move_to_end(key)
        return dict(value) if value else None


def _put_cached(key: str, value: dict[str, Any]) -> None:
    with _idempotency_lock:
        _idempotent[key] = dict(value)
        _idempotent.move_to_end(key)
        while len(_idempotent) > _MAX_IDEMPOTENCY_ENTRIES:
            _idempotent.popitem(last=False)


def _safe_source_only(question: str, *, reason_code: str) -> dict[str, Any]:
    return {
        "answer": "Mình chưa tìm thấy mục thủ tục đã công bố phù hợp với câu hỏi này. Anh/chị có thể nêu rõ tên thủ tục hoặc đăng nhập để hỏi sâu hơn.",
        "question": question,
        "citations": [],
        "grounding_status": "insufficient_evidence",
        "answer_status": "cannot_verify",
        "outcome": "clarification_required",
        "reason_code": reason_code,
        "retryable": False,
        "answer_mode": "public_quick_chat",
        "llm_used": False,
        "source_gap": ["Chưa có mục thủ tục đã phát hành đủ căn cứ cho câu hỏi này."],
        "procedure_detail": None,
        "generation_provenance": {
            "mode": "public_quick_chat",
            "provider_label": "deterministic",
            "model_calls": 0,
        },
    }


def _enforce_zero_llm_contract(
    result: dict[str, Any], *, question: str
) -> dict[str, Any]:
    """Fail closed if a future public path stops proving zero model calls."""

    provenance = result.get("generation_provenance")
    if (
        result.get("llm_used") is False
        and isinstance(provenance, dict)
        and provenance.get("provider_label") == "deterministic"
        and provenance.get("model_calls") == 0
    ):
        return result
    fallback = _safe_source_only(
        question,
        reason_code="PUBLIC_ZERO_LLM_CONTRACT_VIOLATION",
    )
    fallback["source_gap"] = [
        "Phản hồi đã bị chặn vì không chứng minh được chế độ Hỏi nhanh không gọi mô hình."
    ]
    return fallback


def _apply_natural_resolution(
    result: dict[str, Any],
    *,
    family_key: str | None,
    family_label: str | None,
    family_score: float,
    second_family_score: float,
    facet: str,
    reason_code: str,
    suggestions: tuple[str, ...],
) -> dict[str, Any]:
    """Attach bounded router provenance without changing reviewed answer text."""

    existing = [str(item).strip() for item in result.get("suggested_questions") or []]
    merged: list[str] = []
    for item in (*suggestions, *existing):
        if item and item not in merged:
            merged.append(item)
        if len(merged) >= 4:
            break
    result.update(
        {
            "reason_code": reason_code,
            "answer_route": "quick_chat_natural_intent",
            "suggested_questions": merged,
            "natural_language_resolution": {
                "version": QUICK_NATURAL_ROUTER_VERSION,
                "family_key": family_key,
                "family_label": family_label,
                "family_score": family_score,
                "second_family_score": second_family_score,
                "facet": facet,
            },
            "generation_provenance": {
                "mode": "quick_chat_natural_intent",
                "provider_label": "deterministic",
                "model_calls": 0,
            },
        }
    )
    return result


def _mark_telemetry(request: Request, result: dict[str, Any]) -> None:
    """Expose only fixed contract labels to the bounded runtime telemetry."""

    request.state.quick_chat_answer_mode = result.get("answer_mode")
    request.state.quick_chat_answer_status = result.get("answer_status")
    request.state.quick_chat_reason_code = result.get("reason_code")
    request.state.quick_chat_llm_used = result.get("llm_used")


@router.post("/quick-chat")
async def public_quick_chat(
    payload: QuickChatRequest, request: Request
) -> JSONResponse:
    if not _origin_allowed(request):
        raise HTTPException(
            status_code=403, detail="public_quick_chat_origin_forbidden"
        )

    guest_id = _guest_id_from_cookie(request.cookies.get(GUEST_COOKIE))
    had_valid_guest_cookie = bool(guest_id)
    cookie_value: str | None = None
    if not guest_id:
        guest_id, cookie_value = _issue_guest_cookie()
    # A visitor who declines cookies must not receive a fresh quota bucket on
    # every request.  Use the opaque IP bucket until a signed guest cookie is
    # presented; after that, bind the guest bucket to the same opaque IP.
    opaque_ip = _opaque_ip(request)
    quota_key = (
        f"guest:{guest_id}:{opaque_ip}" if had_valid_guest_cookie else f"ip:{opaque_ip}"
    )
    catalog = load_intent_catalog()
    current_release_id = str(catalog[0].get("release_id") or "") if catalog else ""
    # Idempotency must survive the first response setting a cookie: the first
    # request is keyed by opaque IP and later requests are keyed by guest+IP
    # for quota purposes. Keep the request marker on the opaque IP bucket so a
    # retry before/after cookie acceptance is treated identically. The client
    # supplied idempotency key and request fingerprint still prevent accidental
    # collisions between different questions from the same IP.
    cache_identity = opaque_ip
    cache_key = _cache_key(
        cache_identity,
        payload.idempotency_key,
        current_release_id,
        payload,
    )
    cached = _get_cached(cache_key)
    if cached is not None:
        _mark_telemetry(request, cached)
        response = JSONResponse(cached)
        if cookie_value:
            response.set_cookie(
                GUEST_COOKIE,
                cookie_value,
                max_age=_COOKIE_TTL_SECONDS,
                httponly=True,
                secure=production_mode_enabled(),
                samesite="lax",
                path="/",
            )
        return response

    allowed, retry_after, remaining, quota_reason = _quota.reserve_request(
        quota_key,
        idempotency_key=cache_key,
    )
    if not allowed:
        capacity_exceeded = quota_reason == "QUICK_CHAT_CAPACITY_EXCEEDED"
        response = JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "answer": (
                    "Hệ thống Hỏi nhanh đang nhận nhiều yêu cầu. Anh/chị vui lòng thử lại sau ít phút."
                    if capacity_exceeded
                    else "Lượt hỏi nhanh đã tạm hết. Anh/chị vui lòng thử lại sau hoặc đăng nhập để tiếp tục."
                ),
                "outcome": "failed",
                "answer_status": "cannot_verify",
                "answer_mode": "public_quick_chat_quota",
                "reason_code": quota_reason,
                "retryable": True,
                "quota_remaining": 0 if capacity_exceeded else remaining,
                "retry_after_seconds": retry_after,
                "llm_used": False,
                "generation_provenance": {
                    "mode": "public_quick_chat_quota",
                    "provider_label": "deterministic",
                    "model_calls": 0,
                },
            },
        )
        _mark_telemetry(
            request,
            {
                "answer_mode": "public_quick_chat_quota",
                "reason_code": quota_reason,
                "answer_status": "cannot_verify",
                "llm_used": False,
            },
        )
        if cookie_value:
            response.set_cookie(
                GUEST_COOKIE,
                cookie_value,
                max_age=_COOKIE_TTL_SECONDS,
                httponly=True,
                secure=production_mode_enabled(),
                samesite="lax",
                path="/",
            )
        return response

    enabled = (
        str(os.getenv("PUBLIC_QUICK_CHAT_FAST_PATH_ENABLED", "true")).strip().casefold()
    )
    result: dict[str, Any] | None = None
    if enabled not in {"1", "true", "yes", "on"}:
        result = _safe_source_only(
            payload.question, reason_code="PUBLIC_FAST_PATH_DISABLED"
        )
    else:
        meta_kind = detect_meta_kind(payload.question)
        if meta_kind:
            result = meta_response(payload.question, meta_kind)
        else:
            followup_action = detect_followup_action(payload.question)
            prior_match = _last_context_intent(payload) if followup_action else None
            reason = "CONTEXT_NOT_RESOLVED"
            if prior_match is not None:
                result = intent_match_to_response(
                    prior_match, question=payload.question, rich=True
                )
                if result is not None:
                    result = apply_followup_action(result, followup_action or "")
            else:
                intent_match = _contextual_facet_match(payload)
                matched_from_context = intent_match is not None
                if intent_match is None:
                    intent_match, _ = _match_intent_with_context(payload)
                if matched_from_context or _has_standalone_topic_signal(
                    payload.question
                ):
                    result = intent_match_to_response(
                        intent_match, question=payload.question, rich=True
                    )
                    reason = intent_match.reason_code
                else:
                    reason = "QUICK_TOPIC_REQUIRED"
            if result is None:
                # The natural router caches its family index by immutable
                # release_id.  Do not rebuild the 700-row index for every
                # unmatched guest question.
                natural = resolve_quick_chat_natural_question(payload.question)
                if natural.hit and natural.match is not None:
                    result = intent_match_to_response(
                        natural.match,
                        question=payload.question,
                        rich=True,
                    )
                    if result is not None:
                        result = _apply_natural_resolution(
                            result,
                            family_key=natural.family_key,
                            family_label=natural.family_label,
                            family_score=natural.family_score,
                            second_family_score=natural.second_family_score,
                            facet=natural.facet,
                            reason_code=natural.reason_code,
                            suggestions=natural.suggestions,
                        )
                elif natural.needs_clarification:
                    result = natural_clarification_response(payload.question, natural)
                if result is None:
                    result = _safe_source_only(payload.question, reason_code=reason)

    result = _enforce_zero_llm_contract(result, question=payload.question)
    result.update(
        {"guest": True, "quota_remaining": remaining, "retry_after_seconds": None}
    )
    _mark_telemetry(request, result)
    _put_cached(cache_key, result)
    response = JSONResponse(result)
    if cookie_value:
        response.set_cookie(
            GUEST_COOKIE,
            cookie_value,
            max_age=_COOKIE_TTL_SECONDS,
            httponly=True,
            secure=production_mode_enabled(),
            samesite="lax",
            path="/",
        )
    return response
