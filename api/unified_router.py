"""Unified rule-first conversation + legal router for Ask serving.

Deterministic policy gates run first. The optional small LLM router (the
existing V2 helper, gated by CHAT_LLM_ROUTER_V2_ENABLED) is only consulted
when those gates fail open on a short/ambiguous remainder. Officer domain
ACL is a later backend check and must not rewrite the classified domain.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping, Sequence

from loguru import logger

from api import conversational_orchestrator as orchestrator
from api.legal_answer_router import (
    LegalAnswerRoute,
    route_legal_answer,
)
from api.legal_domains import canonicalize_legal_domain

_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_ROLES = {"citizen", "officer"}
_LLM_MIN_CONFIDENCE = 0.6

ConversationRoute = orchestrator.ConversationRoute
RouterSource = Literal["rule", "llm", "fail_open"]


@dataclass(frozen=True)
class RouterDecision:
    conversation_route: ConversationRoute
    legal_route: str | None
    canonical_domain: str | None
    temporal_scope: str | None
    clarifying_questions: tuple[str, ...]
    confidence: float
    source: RouterSource
    reason: str
    active_document_required: bool = False
    active_document_available: bool = False
    legal_answer: LegalAnswerRoute | None = None
    turn: orchestrator.ConversationTurnDecisionV1 | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "conversation_route": self.conversation_route,
            "legal_route": self.legal_route,
            "canonical_domain": self.canonical_domain,
            "temporal_scope": self.temporal_scope,
            "clarifying_questions": list(self.clarifying_questions),
            "confidence": self.confidence,
            "source": self.source,
            "reason": self.reason,
            "active_document_required": self.active_document_required,
            "active_document_available": self.active_document_available,
            "route": self.conversation_route,
            "router_fallback": self.source == "fail_open",
        }


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _env_flag(name: str, default: str, *, environ: Mapping[str, str] | None) -> bool:
    values = os.environ if environ is None else environ
    return _compact(values.get(name, default)).casefold() in _TRUE_VALUES


def is_unified_router_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Default-on for citizen/officer so Ask actually uses RouterDecision."""

    if not _env_flag("CHAT_UNIFIED_ROUTER_V1_ENABLED", "true", environ=environ):
        return False
    normalized = _compact(role or "citizen").casefold()
    return normalized in _ALLOWED_ROLES


def is_unified_router_shadow_enabled(
    role: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Compute + log the unified decision without changing serving."""

    if is_unified_router_enabled(role, environ=environ):
        return False
    if not _env_flag("CHAT_UNIFIED_ROUTER_V1_SHADOW", "false", environ=environ):
        return False
    normalized = _compact(role or "citizen").casefold()
    return normalized in _ALLOWED_ROLES


def check_officer_domain_acl(
    decision: RouterDecision,
    allowed_domains: Sequence[str] | None,
) -> tuple[bool, str | None]:
    """Backend ACL after classification. Does not rewrite the decision."""

    if decision.conversation_route != "legal_query":
        return True, None
    domain = canonicalize_legal_domain(decision.canonical_domain) or _compact(
        decision.canonical_domain
    )
    if not allowed_domains or not domain or domain == "unknown":
        return True, None
    allowed = {
        canonicalize_legal_domain(item)
        for item in allowed_domains
        if canonicalize_legal_domain(item)
    }
    if not allowed or domain in allowed:
        return True, None
    return (
        False,
        "Câu hỏi thuộc lĩnh vực ngoài phạm vi phân công của tài khoản cán bộ.",
    )


def should_invoke_router_llm(question: str, decision: RouterDecision) -> bool:
    """True only for short/ambiguous remainders after rule fail-open."""

    if decision.source != "fail_open":
        return False
    if decision.conversation_route != "legal_query":
        return False
    current = _compact(question)
    if not current or len(current) > 120:
        return False
    folded = orchestrator._fold(current)
    if orchestrator._is_standalone_legal_identifier_query(current):
        return False
    if orchestrator._topic_domains(current):
        return False
    return bool(
        len(folded) <= 80
        or orchestrator._is_contextual_legal_followup(current)
    )


def merge_llm_intent(
    rule_decision: RouterDecision,
    llm_intent: orchestrator.ConversationIntentDecisionV2,
) -> RouterDecision:
    """Adopt a parsed V2 LLM route, else keep fail-open legal_query."""

    current = ""
    if rule_decision.turn is not None:
        current = _compact(getattr(rule_decision.turn, "current_question", "") or "")
    llm_route = getattr(llm_intent, "route", None)
    if (
        current
        and llm_route in {"chat_meta", "out_of_scope", "document_followup"}
        and (
            orchestrator._is_standalone_legal_identifier_query(current)
            or orchestrator._has_vietnamese_law_number(current)
            or orchestrator._topic_domains(current)
        )
        and rule_decision.conversation_route == "legal_query"
    ):
        merged = replace(
            rule_decision,
            source="rule",
            reason="llm_veto_legal_identity",
            confidence=max(float(rule_decision.confidence or 0.0), 0.86),
        )
        _log_decision(merged)
        return merged

    if (
        bool(getattr(llm_intent, "router_fallback", False))
        or float(getattr(llm_intent, "confidence", 0.0) or 0.0) < _LLM_MIN_CONFIDENCE
        or llm_intent.route not in {
            "chat_meta",
            "document_followup",
            "legal_query",
            "out_of_scope",
        }
    ):
        merged = replace(
            rule_decision,
            source="fail_open",
            reason="llm_fail_open",
            confidence=0.4,
        )
        _log_decision(merged)
        return merged

    route: ConversationRoute = llm_intent.route  # type: ignore[assignment]
    legal_route = rule_decision.legal_route if route == "legal_query" else None
    legal_answer = rule_decision.legal_answer if route == "legal_query" else None
    domain = rule_decision.canonical_domain if route == "legal_query" else None
    temporal = rule_decision.temporal_scope if route == "legal_query" else None
    clarifying = (
        rule_decision.clarifying_questions if route == "legal_query" else ()
    )
    turn = rule_decision.turn
    if turn is not None:
        turn = replace(
            turn,
            route=route,
            reason_code=str(llm_intent.reason_code or "llm_router"),
            active_document_required=bool(llm_intent.active_document_required)
            or route == "document_followup",
            active_document_available=bool(llm_intent.active_document_available),
        )
    merged = RouterDecision(
        conversation_route=route,
        legal_route=legal_route,
        canonical_domain=domain,
        temporal_scope=temporal,
        clarifying_questions=clarifying,
        confidence=float(llm_intent.confidence),
        source="llm",
        reason=str(llm_intent.reason_code or "llm_router"),
        active_document_required=bool(llm_intent.active_document_required)
        or route == "document_followup",
        active_document_available=bool(llm_intent.active_document_available),
        legal_answer=legal_answer,
        turn=turn,
    )
    _log_decision(merged)
    return merged


def _log_decision(decision: RouterDecision) -> None:
    logger.info(
        "unified_router_decision conversation_route={} legal_route={} source={} domain={}",
        decision.conversation_route,
        decision.legal_route,
        decision.source,
        decision.canonical_domain,
    )


def _legal_route_name(routed: LegalAnswerRoute) -> str | None:
    if routed.clarifying_questions and not routed.issues:
        return "clarification"
    if routed.decision_reason == "missing_facts_require_clarification":
        return "clarification"
    return str(routed.answer_route or "") or None




def attach_decision_to_ask_request(ask_request: Any, decision: RouterDecision) -> None:
    """Stamp the already-computed route onto AskRequest extra fields."""

    payload = decision.to_payload()
    ask_request.conversation_intent = payload
    ask_request.conversation_intent_v2 = payload
    from api.pipeline_contracts import pipeline_decision_from_router

    ask_request.pipeline_decision = pipeline_decision_from_router(decision)
    if decision.conversation_route == "document_followup" and decision.active_document_available:
        ask_request.unified_router_skip_legal_planner = True
    elif decision.conversation_route == "legal_query" and decision.legal_answer is not None:
        ask_request.shared_answer_route = decision.legal_answer


def is_clarification_only(decision: RouterDecision) -> bool:
    if decision.conversation_route != "legal_query":
        return False
    if decision.legal_route != "clarification":
        return False
    issues = getattr(decision.legal_answer, "issues", ()) or ()
    return not issues

def decide_router(
    question: str,
    *,
    role: str = "citizen",
    history: Sequence[Mapping[str, Any]] | None = None,
    active_document: Mapping[str, Any] | None = None,
    allowed_domains: Sequence[str] | None = None,
    requested_domain: str | None = None,
    enforce_account_scope: bool = False,
) -> RouterDecision:
    """Rule-first unified decision. Does not call an LLM.

    ``allowed_domains`` is recorded for the later ACL helper.  When an officer
    has exactly one assigned domain and the request does not explicitly select
    another domain, that account scope is also passed to the shared legal
    planner so the precomputed route cannot bypass the same boundary.
    """

    current = _compact(question)
    history_messages = tuple(history or ())
    active = dict(active_document) if active_document else None
    turn = orchestrator.decide_conversation_turn(
        current,
        active_document=active,
        history_messages=history_messages,
    )
    explicit = orchestrator._explicit_conversation_route(current)
    standalone = orchestrator._is_standalone_legal_identifier_query(current)
    has_law_number = orchestrator._has_vietnamese_law_number(current)

    conversation_route: ConversationRoute = turn.route
    reason = turn.reason_code
    source: RouterSource = "rule"
    confidence = 0.92
    document_required = turn.active_document_required
    document_available = turn.active_document_available

    # Gate order: meta → out_of_scope → law number / standalone identifier
    # (source=rule, before follow-up deixis and before LLM) → short
    # active-document follow-up → document deixis → topic keywords →
    # insufficient facts (via legal planner) → fail-open remainder.
    if explicit == "chat_meta" or turn.route == "chat_meta":
        conversation_route = "chat_meta"
        reason = turn.reason_code if turn.route == "chat_meta" else "explicit_history_or_meta_request"
    elif explicit == "out_of_scope" or turn.route == "out_of_scope":
        conversation_route = "out_of_scope"
        reason = "explicit_non_legal_request"
    elif standalone or has_law_number:
        conversation_route = "legal_query"
        reason = "standalone_legal_identifier"
        source = "rule"
        document_required = False
    elif orchestrator._is_short_active_document_followup(current, active):
        conversation_route = "document_followup"
        reason = "active_document_followup"
        source = "rule"
        document_required = True
    elif explicit == "document_followup" or turn.route == "document_followup":
        conversation_route = "document_followup"
        reason = turn.reason_code if turn.route == "document_followup" else "explicit_document_followup"
        document_required = True
    elif turn.route == "legal_query" and orchestrator._topic_domains(current):
        conversation_route = "legal_query"
        reason = "legal_topic_keywords"
        source = "rule"
        confidence = 0.88
    elif turn.route == "legal_query" and turn.reason_code == "default_legal_query":
        conversation_route = "legal_query"
        reason = "default_legal_query"
        source = "fail_open"
        confidence = 0.4
    else:
        conversation_route = turn.route
        reason = turn.reason_code

    legal_route: str | None = None
    canonical_domain: str | None = None
    temporal_scope: str | None = None
    clarifying: tuple[str, ...] = ()
    legal_answer: LegalAnswerRoute | None = None

    if conversation_route == "legal_query":
        account_domain = None
        if (
            str(role or "").casefold() == "officer"
            and enforce_account_scope
            and not requested_domain
            and len(tuple(allowed_domains or ())) == 1
        ):
            account_domain = str(tuple(allowed_domains or ())[0]).strip() or None
        legal_answer = route_legal_answer(
            current,
            remediation=True,
            role=role or "citizen",
            requested_domain=requested_domain,
            account_domain=account_domain,
        )
        legal_route = _legal_route_name(legal_answer)
        if legal_answer.decision is not None:
            canonical_domain = legal_answer.decision.canonical_domain
            temporal_scope = legal_answer.decision.temporal_scope
            clarifying = tuple(legal_answer.decision.clarifying_questions or ())
        else:
            clarifying = tuple(legal_answer.clarifying_questions or ())
        if legal_route == "clarification":
            reason = "insufficient_facts_clarification"
            source = "rule"
            confidence = 0.86

    if turn is not None and (
        turn.route != conversation_route
        or turn.active_document_required != document_required
    ):
        turn = replace(
            turn,
            route=conversation_route,
            reason_code=reason,
            active_document_required=document_required,
            active_document_available=document_available,
        )

    decision = RouterDecision(
        conversation_route=conversation_route,
        legal_route=legal_route,
        canonical_domain=canonical_domain,
        temporal_scope=temporal_scope,
        clarifying_questions=clarifying,
        confidence=confidence,
        source=source,
        reason=reason,
        active_document_required=document_required,
        active_document_available=document_available,
        legal_answer=legal_answer,
        turn=turn,
    )
    _log_decision(decision)
    return decision
