"""Unified rule-first conversation + legal router for Ask serving.

Deterministic policy gates run first. The optional small LLM router (the
existing V2 helper, gated by CHAT_LLM_ROUTER_V2_ENABLED) is only consulted
when those gates fail open on a short/ambiguous remainder. Officer domain
ACL is a later backend check and must not rewrite the classified domain.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping, Sequence

from loguru import logger

from api import conversational_orchestrator as orchestrator
from api.legal_answer_router import (
    LegalAnswerRoute,
    route_legal_answer,
)
from api.legal_domains import canonicalize_legal_domain
from api.legal_query_understanding import classify_legal_query
from api.administrative_query_signals import administrative_domain, has_administrative_subject, has_administrative_request

_TRUE_VALUES = {"1", "true", "yes", "on"}
_ALLOWED_ROLES = {"citizen", "officer", "admin"}
_LLM_MIN_CONFIDENCE = 0.6
_LEGAL_INSTRUMENT_SIGNAL_RE = re.compile(
    r"\b(?:luat|bo luat|phap lenh|nghi quyet|nghi dinh|quyet dinh|"
    r"thong tu|chi thi|cong van|van ban)\b",
    re.IGNORECASE,
)
_LEGAL_REFERENCE_SIGNAL_RE = re.compile(
    r"\b(?:dieu|khoan|diem|chuong|muc)\s+(?:\d+|[ivxlcdm]+)\b",
    re.IGNORECASE,
)
_LEGAL_ACTION_SIGNAL_RE = re.compile(
    r"\b(?:quy dinh|hieu luc|sua doi|bo sung|thay the|thu hoi|"
    r"ban hanh|ap dung|noi ve|can cu|xu phat)\b",
    re.IGNORECASE,
)
_NORMATIVE_REQUEST_RE = re.compile(
    r"\b(?:chinh sach|che do|quy dinh|nguyen tac|trach nhiem|tham quyen|"
    r"phai lam gi|duoc huong|ho tro|ho so|giay to|dang ky|trinh tu|"
    r"thuc hien|ban hanh|phe duyet|vi pham|bi phat|muc phat|khac phuc|"
    r"xu ly|cap lai|mat|hoa giai|toi da|the nao|ra sao|o dau|"
    r"co duoc|duoc phep|co the|co phai)\b",
    re.IGNORECASE,
)
_NORMATIVE_SUBJECT_RE = re.compile(
    r"\b(?:bo y te|bo giao duc|bo lao dong|bo noi vu|toa an|vien kiem sat|"
    r"kiem toan nha nuoc|hoc sinh|truong pho thong|truong cong lap|"
    r"co so giao duc|ngach du bi|quan nhan du bi|nghia vu quan su|"
    r"xa bien gioi|bien gioi dat lien|quy hoach(?: do thi| nong thon)?|"
    r"do thi|nong thon|cong trinh thuy loi|thuy loi|an toan thuc pham|"
    r"quang cao|to chuc|ca nhan|gcnqsd[dđ]|dktt|tctn|"
    r"tranh chap ranh gioi|hoa giai|tai san rieng|dinh doat|"
    r"nguoi chua thanh nien|vi thanh nien|cha me|nguoi giam ho|"
    r"tu\s+\d{1,2}\s+den|duoi\s+\d{1,2}\s+tuoi)\b",
    re.IGNORECASE,
)
_NON_LEGAL_NORMATIVE_RE = re.compile(
    r"\b(?:chatbot|bao mat|mat khau|cua hang|doi tra|bao hanh|"
    r"cau thu|bong da|bong chuyen|tran dau|the do|the vang|giai dau)\b",
    re.IGNORECASE,
)
_DOCUMENT_DEIXIS_RE = re.compile(
    r"\b(?:van ban|dieu|khoan|nguon|quyet dinh|thong tu|nghi dinh|"
    r"tai lieu)\s+(?:do|nay|tren|vua neu|vua noi|vua dan)\b",
    re.IGNORECASE,
)

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
    domain_source: str | None = None
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
            "domain_source": self.domain_source,
            "active_document_required": self.active_document_required,
            "active_document_available": self.active_document_available,
            "route": self.conversation_route,
            "router_fallback": self.source == "fail_open",
        }


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def looks_like_normative_legal_request(question: str) -> bool:
    """Recognize a natural request for a rule, benefit, procedure or sanction.

    This is intentionally a bounded safety signal, not a domain classifier. It
    lets an unrecognized but clearly normative question enter the legal path;
    the optional Qwen advisory may refine it, but may never downgrade it to
    conversation metadata. Product, retail and sports wording is excluded so
    the broad request vocabulary cannot capture ordinary chat.
    """

    current = _compact(question)
    if not current:
        return False
    folded = orchestrator._fold(current)
    if _NON_LEGAL_NORMATIVE_RE.search(folded):
        return False
    if not _NORMATIVE_REQUEST_RE.search(folded):
        return False
    if _NORMATIVE_SUBJECT_RE.search(folded):
        return True
    return bool(
        re.search(
            r"\b(?:co quan|nguoi dung dau|truong|don vi|co so|"
            r"to chuc nha nuoc|cong lap)\b",
            folded,
        )
    )


def has_legal_routing_signal(question: str) -> bool:
    """Detect legal identity/substance that an advisory model may not veto.

    Existing exact-number parsers intentionally remain the strongest signal.
    This guard covers common abbreviated identifiers such as
    ``Thông tư 135/2026`` and explicit article/clause questions that do not
    contain a complete Vietnamese instrument suffix.
    """

    current = _compact(question)
    if not current:
        return False
    if (
        orchestrator._is_standalone_legal_identifier_query(current)
        or orchestrator._has_vietnamese_law_number(current)
        or orchestrator._topic_domains(current)
        or orchestrator._has_legal_substance(current)
        or has_administrative_request(current)
    ):
        return True
    folded = orchestrator._fold(current)
    if _LEGAL_REFERENCE_SIGNAL_RE.search(folded):
        return True
    return bool(
        _LEGAL_INSTRUMENT_SIGNAL_RE.search(folded)
        and (re.search(r"\b\d{1,4}(?:\s*/\s*\d{2,4})?\b", folded)
             or _LEGAL_ACTION_SIGNAL_RE.search(folded))
    )


def _context_domain_hint(
    history: Sequence[Mapping[str, Any]],
    active_document: Mapping[str, Any] | None,
) -> str | None:
    """Return the latest bounded legal topic for an otherwise generic turn.

    Short follow-ups (``Hồ sơ cụ thể gồm gì?``/``Thời hạn bao lâu?``) often
    contain no domain noun of their own.  The hint is derived from the
    already-loaded conversation window, never from a model, and is used only
    to rank the next retrieval.  It is deliberately ignored when no valid
    canonical domain is available so a missing context cannot become a guess.
    """

    if isinstance(active_document, Mapping):
        for key in (
            "canonical_domain",
            "domain",
            "domain_slug",
            "field_slug",
            "topic_domain",
        ):
            value = canonicalize_legal_domain(active_document.get(key))
            if value:
                return value
    messages = tuple(history or ())
    # The bounded context normally contains only the newest six messages, so
    # the user turn that introduced a topic may already have fallen out while
    # its assistant snapshot still carries the backend-owned domain. Prefer
    # that metadata before classifying prose again.
    for message in reversed(messages):
        for key in ("canonical_domain", "domain", "topic_domain"):
            value = canonicalize_legal_domain(message.get(key))
            if value:
                return value
        candidate = message.get("active_document")
        if isinstance(candidate, Mapping):
            for key in ("canonical_domain", "domain", "topic_domain"):
                value = canonicalize_legal_domain(candidate.get(key))
                if value:
                    return value
    for message in reversed(messages):
        if str(message.get("role") or message.get("sender_role") or "").casefold() not in {
            "user",
            "human",
        }:
            continue
        # Prefer backend-owned metadata already present in the bounded
        # message snapshot.  It avoids reclassifying the same request.
        for key in ("canonical_domain", "domain", "topic_domain"):
            value = canonicalize_legal_domain(message.get(key))
            if value:
                return value
        content = _compact(message.get("content"))
        if not content:
            continue
        administrative = administrative_domain(content)
        if administrative:
            return administrative
        try:
            classification = classify_legal_query(content)
        except Exception:
            continue
        value = canonicalize_legal_domain(classification.get("domain"))
        if value and value != "unknown":
            return value
    return None


def _prefer_context_domain_for_form_facet(question: str) -> bool:
    """Detect a generic form facet whose field words are not a new topic.

    For example, ``căn cước`` and ``nơi cư trú`` are fields on the social
    pension form.  They must not move a field-by-field follow-up into the
    residence domain.  Explicit form codes (CT01/CT02) and explicitly named
    procedures remain self-contained and keep their current-turn domain.
    """

    folded = orchestrator._fold(_compact(question))
    if not re.search(r"\b(?:mau|bieu mau|to khai|file mau|tren mau)\b", folded):
        return False
    if re.search(r"\bct\s*0?\d{1,3}\b", folded):
        return False
    if re.search(
        r"\b(?:dang ky khai sinh|dang ky tam tru|dang ky thuong tru|"
        r"tro cap huu tri xa hoi|chuyen muc dich su dung dat|"
        r"don khieu nai|ghi chu ly hon)\b",
        folded,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:nay|do|vua neu|tren mau|phien ban|hien hanh|hieu luc|"
            r"dang phat hanh|huong dan|kiem tra|tung truong|dien|"
            r"noi dung bat buoc|ho ten|ngay sinh|file mau)\b",
            folded,
        )
    )


def _eligible_document_followup(
    question: str,
    *,
    history: Sequence[Mapping[str, Any]],
    active_document: Mapping[str, Any] | None,
) -> bool:
    """Require a bound source or explicit document deixis.

    A standalone administrative request may contain the noun ``văn bản``
    (for example, a landlord's consent document). That noun alone must never
    divert the first turn away from legal retrieval. An unbound explicit
    reference remains a document follow-up so the answer can ask which source
    was meant instead of searching for an arbitrary document.
    """

    if active_document:
        return True
    return bool(_DOCUMENT_DEIXIS_RE.search(orchestrator._fold(question)))


def _is_fresh_form_catalog_lookup(question: str) -> bool:
    """Keep an explicit form version/source lookup off a stale document."""

    folded = orchestrator._fold(_compact(question))
    return bool(
        re.search(r"\b(?:mau|bieu mau|to khai|ct\s*0?\d{1,3})\b", folded)
        and re.search(
            r"\b(?:phien ban|hien hanh|hieu luc|dang phat hanh|"
            r"van ban nao|nguon chinh thuc|thuoc thong tu)\b",
            folded,
        )
    )


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
    normative_request = looks_like_normative_legal_request(current)
    if not current or len(current) > (320 if normative_request else 120):
        return False
    if has_legal_routing_signal(current) and not normative_request:
        return False
    folded = orchestrator._fold(current)
    if orchestrator._is_standalone_legal_identifier_query(current):
        return False
    if orchestrator._topic_domains(current):
        return False
    return bool(
        normative_request
        or len(folded) <= 80
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
    # The model is an advisory refine step only.  Once deterministic policy
    # has produced a rule decision (including explicit meta/OOS/document and
    # high-signal legal cases), never let a tiny model move that turn to a
    # different branch.  This is the single-precedence invariant that keeps a
    # bad model guess from triggering retrieval or suppressing legal lookup.
    if rule_decision.source == "rule":
        _log_decision(rule_decision)
        return rule_decision
    # Fail-open legal is an intentional safety boundary: an uncertain route
    # must not suppress a legal retrieval simply because a tiny model guessed
    # ``chat_meta``/``out_of_scope``.  Promote the guarded decision to a rule
    # result so operational fallback metrics do not treat this protected path
    # as an unhandled error.  Deterministic meta/OOS cases never reach this
    # branch because they are already source=rule above.
    if (
        rule_decision.source == "fail_open"
        and rule_decision.conversation_route == "legal_query"
        and llm_route in {"chat_meta", "out_of_scope", "document_followup"}
        and current
        and (
            has_legal_routing_signal(current)
            or looks_like_normative_legal_request(current)
            or re.search(r"\b(?:KS|KT|CCCD|GPXD|QSDĐ|CT01)\b", current, re.IGNORECASE)
        )
    ):
        guarded = replace(
            rule_decision,
            source="rule",
            reason="legal_fail_open_guard",
            confidence=max(float(rule_decision.confidence or 0.0), 0.82),
        )
        _log_decision(guarded)
        return guarded
    if (
        current
        and llm_route in {"chat_meta", "out_of_scope", "document_followup"}
        and (
            has_legal_routing_signal(current)
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
        domain_source=(rule_decision.domain_source if route == "legal_query" else None),
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
    if (
        decision.conversation_route == "legal_query"
        and decision.canonical_domain
        and decision.canonical_domain != "unknown"
        and not getattr(ask_request, "domain", None)
    ):
        # This is a request-scoped retrieval hint, not an authorization grant.
        # Explicit officer ACLs remain authoritative in the backend.
        ask_request.domain = decision.canonical_domain
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

    ``allowed_domains`` is recorded for the later ACL helper. A sole officer
    assignment may fill an otherwise unknown domain, but never rewrites a
    classified request; explicit outside-domain requests still reach the ACL.
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
    has_legal_signal = has_legal_routing_signal(current)
    folded_current = orchestrator._fold(current)
    abbreviated_legal_request = bool(
        re.search(r"\b(?:huong dan\s+(?:ks|kt)|tt\s+dkhh)\b", folded_current)
    )
    normative_request = looks_like_normative_legal_request(current)

    conversation_route: ConversationRoute = turn.route
    reason = turn.reason_code
    source: RouterSource = "rule"
    confidence = 0.92
    document_required = turn.active_document_required
    document_available = turn.active_document_available

    # Gate order: explicit meta → out_of_scope → law number / standalone
    # identifier → short active-document follow-up → eligible document deixis
    # → legal floor → inferred meta → topic keywords →
    # high-signal legal substance → insufficient facts (via legal planner) →
    # fail-open remainder.
    if explicit == "chat_meta":
        conversation_route = "chat_meta"
        reason = "explicit_history_or_meta_request"
    elif explicit == "out_of_scope" or turn.route == "out_of_scope":
        conversation_route = "out_of_scope"
        reason = "explicit_non_legal_request"
    elif standalone or has_law_number:
        conversation_route = "legal_query"
        reason = "standalone_legal_identifier"
        source = "rule"
        document_required = False
    elif explicit == "legal_query" and _is_fresh_form_catalog_lookup(current):
        # An explicitly named form plus a request for its version, source or
        # effectivity is a fresh catalog/legal lookup.  It must win over the
        # short active-document heuristic; the previous source may be a
        # neighboring instrument or only a form download projection.
        conversation_route = "legal_query"
        reason = "explicit_legal_lookup"
        source = "rule"
        document_required = False
    elif active and orchestrator._is_short_active_document_followup(current, active):
        conversation_route = "document_followup"
        reason = "active_document_followup"
        source = "rule"
        document_required = True
    elif (
        explicit == "document_followup" or turn.route == "document_followup"
    ) and _eligible_document_followup(
        current,
        history=history_messages,
        active_document=active,
    ):
        conversation_route = "document_followup"
        reason = turn.reason_code if turn.route == "document_followup" else "explicit_document_followup"
        document_required = True
    elif (
        turn.route == "chat_meta"
        and turn.reason_code == "ambiguous_multi_topic_followup"
        and not has_administrative_request(current)
    ):
        conversation_route = "chat_meta"
        reason = turn.reason_code
        source = "rule"
    elif explicit == "legal_query":
        conversation_route = "legal_query"
        reason = "explicit_legal_lookup"
        source = "rule"
        document_required = False
    elif abbreviated_legal_request:
        conversation_route = "legal_query"
        reason = "abbreviated_legal_fail_open"
        source = "fail_open"
        confidence = 0.6
        document_required = False
    elif normative_request:
        # A natural policy/procedure/sanction request may not contain a known
        # domain or instrument identifier. Keep it legal, but leave the
        # bounded domain/facet refinement to the optional Qwen advisory.
        conversation_route = "legal_query"
        reason = "normative_legal_request"
        source = "fail_open"
        confidence = 0.68
        document_required = False
    elif has_legal_signal:
        conversation_route = "legal_query"
        reason = "legal_signal_guard"
        source = "rule"
        confidence = 0.86
        document_required = False
    elif turn.route == "chat_meta":
        conversation_route = "chat_meta"
        reason = turn.reason_code
        source = "rule"
    elif turn.route == "legal_query" and orchestrator._topic_domains(current):
        conversation_route = "legal_query"
        reason = "legal_topic_keywords"
        source = "rule"
        confidence = 0.88
    elif turn.route == "legal_query" and orchestrator._has_legal_substance(current):
        # A legal object/action pair is deterministic evidence even when it
        # does not map to one of the five canonical domain buckets.  Mark it
        # as rule-classified so the optional model is reserved for genuinely
        # ambiguous, domain-neutral requests.
        conversation_route = "legal_query"
        reason = "legal_substance_markers"
        source = "rule"
        confidence = 0.84
    elif turn.route == "legal_query" and turn.reason_code == "default_legal_query":
        # Unknown text is not evidence that a law search is needed. Keep
        # elliptical legal continuations only when a recent USER turn supplies
        # the legal subject; an old retrieved source alone must not capture chat.
        recent_users = [str(item.get("content") or "") for item in history_messages
                        if str(item.get("role") or item.get("sender_role")) == "user"]
        contextual_legal = bool(
            (recent_users and has_legal_routing_signal(recent_users[-1])
             and orchestrator._is_contextual_legal_followup(current))
            or (active and orchestrator._is_contextual_legal_followup(current))
        )
        # Unrecognized eligibility/obligation questions need the existing
        # advisor, even when their subject is outside the legacy topic list.
        # Do not silently label a request for a rule as ordinary conversation.
        asks_for_rule = bool(re.search(
            r"\b(?:do tuoi|dieu kien|bat buoc|duoc phep|co phai|co duoc|"
            r"duoc mien|tam hoan|bao nhieu tuoi|phai nop|can nop)\b",
            orchestrator._fold(current),
        ))
        needs_lookup = contextual_legal or asks_for_rule or normative_request
        conversation_route = "legal_query" if needs_lookup else "chat_meta"
        reason = ("contextual_legal_request" if contextual_legal else
                  "normative_legal_request" if normative_request else
                  "ambiguous_rule_request" if asks_for_rule else
                  "no_legal_evidence_requested")
        source = "fail_open" if needs_lookup else "rule"
        confidence = 0.6
        turn = replace(turn, route=conversation_route, reason_code=reason)
    else:
        conversation_route = turn.route
        reason = turn.reason_code

    if conversation_route == "document_followup":
        # The deterministic conversation helper intentionally does not own
        # source-state hydration.  Once the backend has supplied a bound
        # active document, expose that fact on the unified decision so the
        # serving path can query the document directly instead of falling
        # through to a zero-evidence legal clarification.
        document_available = document_available or bool(active)

    legal_route: str | None = None
    canonical_domain: str | None = None
    domain_source: str | None = None
    temporal_scope: str | None = None
    clarifying: tuple[str, ...] = ()
    legal_answer: LegalAnswerRoute | None = None

    if conversation_route == "legal_query":
        # A short legal facet may not name its topic. Reuse the latest
        # backend-owned conversation domain as a ranking hint, while leaving
        # explicit request signals and officer ACLs authoritative.
        current_domain = None
        try:
            current_domain = canonicalize_legal_domain(
                classify_legal_query(current).get("domain")
            )
        except Exception:
            current_domain = None
        # A curated administrative phrase is stronger than the legacy broad
        # classifier.  This fixes known collisions such as ``mai táng`` being
        # read as land ``tặng`` and ``lưu trữ`` being read as residence
        # ``lưu trú``. It remains a retrieval hint; officer ACLs are enforced
        # independently below.
        current_administrative_domain = administrative_domain(current)
        context_domain = _context_domain_hint(history_messages, active)
        context_form_facet = _prefer_context_domain_for_form_facet(current)
        route_domain_hint = (
            requested_domain
            or (context_domain if context_form_facet else None)
            or current_administrative_domain
            or (
                context_domain
                if (not current_domain or current_domain == "unknown")
                and not has_administrative_subject(current)
                and (
                    orchestrator._is_contextual_legal_followup(current)
                    or context_form_facet
                )
                else None
            )
        )
        if requested_domain:
            domain_source = "request"
        elif (
            route_domain_hint
            and context_domain
            and canonicalize_legal_domain(route_domain_hint)
            == canonicalize_legal_domain(context_domain)
            and not current_administrative_domain
        ):
            domain_source = "memory"
        elif current_administrative_domain or (
            current_domain and current_domain != "unknown"
        ):
            domain_source = "classifier"

        # A single assigned officer domain is a fallback for genuinely
        # unclassified text, not a rewrite of an explicit outside-domain
        # request. The later ACL therefore still returns 403 for the latter.
        account_domain = None
        known_request_domain = canonicalize_legal_domain(route_domain_hint) or (
            current_domain if current_domain and current_domain != "unknown" else None
        )
        if known_request_domain == "unknown":
            known_request_domain = None
        if (
            str(role or "").casefold() == "officer"
            and enforce_account_scope
            and not known_request_domain
            and len(tuple(allowed_domains or ())) == 1
        ):
            account_domain = str(tuple(allowed_domains or ())[0]).strip() or None
            if account_domain:
                domain_source = "account_fallback"
        legal_answer = route_legal_answer(
            current,
            remediation=True,
            role=role or "citizen",
            requested_domain=route_domain_hint,
            account_domain=account_domain,
        )
        legal_route = _legal_route_name(legal_answer)
        if legal_answer.decision is not None:
            canonical_domain = legal_answer.decision.canonical_domain
            temporal_scope = legal_answer.decision.temporal_scope
            clarifying = tuple(legal_answer.decision.clarifying_questions or ())
        else:
            clarifying = tuple(legal_answer.clarifying_questions or ())
        # ``requested_domain`` is a ranking hint in the shared legal planner;
        # a short facet can therefore remain ``unknown`` even when the
        # conversation has a verified active topic. Carry that bounded router
        # hint forward so retrieval and the public contract stay on-topic.
        if (
            (not canonical_domain or canonical_domain == "unknown")
            and route_domain_hint
            and route_domain_hint != "unknown"
        ):
            canonical_domain = route_domain_hint
        if canonical_domain and canonical_domain != "unknown" and not domain_source:
            domain_source = "classifier"
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
        domain_source=domain_source,
        active_document_required=document_required,
        active_document_available=document_available,
        legal_answer=legal_answer,
        turn=turn,
    )
    _log_decision(decision)
    return decision
