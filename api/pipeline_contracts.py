"""Frozen Phase B serving contracts: conversation route + legal plan + ACL scope.

PipelineDecisionV1 is the classified plan. AuthorizationScopeV1 is built only
from the backend snapshot / request role. The LLM router must never decide
ACL, department, or effectivity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from api.legal_answer_router import LegalAnswerRoute, route_legal_answer
from api.unified_router import RouterDecision, decide_router

ConversationRouteV1 = Literal[
    "legal_query",
    "document_followup",
    "chat_meta",
    "out_of_scope",
]
RouterSourceV1 = Literal["rule", "llm", "fail_open"]

# Keys the 0.5B classifier is forbidden to emit. Stripped if present.
LLM_ROUTER_FORBIDDEN_KEYS = frozenset(
    {
        "acl",
        "account_domain",
        "allowed_domains",
        "authorization",
        "department",
        "effectivity",
        "effectivity_status",
        "grants",
        "grants_checksum",
        "organization_unit_id",
        "primary_unit_id",
        "unit_id",
    }
)


@dataclass(frozen=True)
class PipelineLegalIssueV1:
    issue_id: str
    query_text: str
    domain: str | None
    intent: str | None


@dataclass(frozen=True)
class PipelineDecisionV1:
    """Classified conversation + legal lookup plan. No ACL/department/effectivity."""

    conversation_route: ConversationRouteV1
    legal_issues: tuple[PipelineLegalIssueV1, ...]
    canonical_domain: str | None
    facets: tuple[str, ...]
    temporal_scope: str | None
    active_document_id: str | None
    confidence: float
    source: RouterSourceV1
    independent_queries: tuple[str, ...] = ()
    legal_route: str | None = None
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "conversation_route": self.conversation_route,
            "legal_issues": [
                {
                    "issue_id": item.issue_id,
                    "query_text": item.query_text,
                    "domain": item.domain,
                    "intent": item.intent,
                }
                for item in self.legal_issues
            ],
            "canonical_domain": self.canonical_domain,
            "facets": list(self.facets),
            "temporal_scope": self.temporal_scope,
            "active_document_id": self.active_document_id,
            "confidence": self.confidence,
            "source": self.source,
            "independent_queries": list(self.independent_queries),
            "legal_route": self.legal_route,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AuthorizationScopeV1:
    """Backend-only authorization. Memory must not mutate this object."""

    role: str
    primary_unit_id: str | None
    grants_checksum: str
    allowed_domains: tuple[str, ...]


def strip_llm_router_forbidden_fields(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Drop department / ACL / effectivity keys if a model emits them."""

    if not isinstance(payload, Mapping):
        return {}
    forbidden = {key.casefold() for key in LLM_ROUTER_FORBIDDEN_KEYS}
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if str(key).casefold() in forbidden:
            continue
        if isinstance(value, Mapping):
            cleaned[str(key)] = strip_llm_router_forbidden_fields(value)
        elif isinstance(value, list):
            cleaned[str(key)] = [
                strip_llm_router_forbidden_fields(item) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            cleaned[str(key)] = value
    return cleaned


def llm_schema_contains_acl_fields(schema: Mapping[str, Any] | None) -> list[str]:
    """Return forbidden property names found in an LLM JSON schema."""

    found: list[str] = []
    forbidden = {key.casefold() for key in LLM_ROUTER_FORBIDDEN_KEYS}

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            properties = node.get("properties")
            if isinstance(properties, Mapping):
                for key in properties:
                    if str(key).casefold() in forbidden:
                        found.append(str(key))
                for child in properties.values():
                    walk(child)
            required = node.get("required")
            if isinstance(required, list):
                for key in required:
                    if str(key).casefold() in forbidden:
                        found.append(str(key))
            for key, child in node.items():
                if key == "properties":
                    continue
                if str(key).casefold() in forbidden:
                    found.append(str(key))
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema or {})
    return sorted(set(found))


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _active_document_id(active_document: Mapping[str, Any] | None) -> str | None:
    if not isinstance(active_document, Mapping):
        return None
    for key in ("document_id", "id", "law_number"):
        value = _compact(active_document.get(key))
        if value:
            return value
    return None


def _grants_checksum(
    *,
    role: str,
    primary_unit_id: str | None,
    allowed_domains: Sequence[str],
) -> str:
    payload = json.dumps(
        {
            "role": _compact(role).casefold() or "citizen",
            "primary_unit_id": _compact(primary_unit_id),
            "allowed_domains": list(allowed_domains),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_authorization_scope_v1(
    *,
    role: str = "citizen",
    allowed_domains: Sequence[str] | None = None,
    primary_unit_id: str | None = None,
    snapshot: Any | None = None,
) -> AuthorizationScopeV1:
    """Build a frozen ACL snapshot from backend data only.

    RuntimePolicySnapshot is optional. When missing, the scope is derived from
    the request role and allowed_domains. The returned object is frozen; callers
    must not mutate it from conversation memory.
    """

    resolved_role = _compact(role).casefold() or "citizen"
    resolved_unit = _compact(primary_unit_id) or None
    resolved_domains = tuple(
        dict.fromkeys(_compact(item) for item in (allowed_domains or ()) if _compact(item))
    )
    checksum: str | None = None
    if snapshot is not None:
        resolved_role = _compact(getattr(snapshot, "role", resolved_role)).casefold() or resolved_role
        resolved_unit = (
            _compact(
                getattr(snapshot, "primary_unit_id", None)
                or getattr(snapshot, "organization_unit_id", None)
                or resolved_unit
            )
            or None
        )
        snapshot_domains = getattr(snapshot, "allowed_domains", None)
        if snapshot_domains:
            resolved_domains = tuple(
                dict.fromkeys(
                    _compact(item) for item in snapshot_domains if _compact(item)
                )
            )
        checksum = _compact(getattr(snapshot, "grants_checksum", None)) or None
    if not checksum:
        checksum = _grants_checksum(
            role=resolved_role,
            primary_unit_id=resolved_unit,
            allowed_domains=resolved_domains,
        )
    return AuthorizationScopeV1(
        role=resolved_role,
        primary_unit_id=resolved_unit,
        grants_checksum=checksum,
        allowed_domains=resolved_domains,
    )


def _issues_from_legal_answer(
    legal_answer: LegalAnswerRoute | None,
) -> tuple[tuple[PipelineLegalIssueV1, ...], tuple[str, ...], tuple[str, ...], str | None, str | None]:
    if legal_answer is None:
        return (), (), (), None, None
    decision = legal_answer.decision
    raw_issues = tuple(getattr(legal_answer, "issues", ()) or ())
    if not raw_issues and decision is not None:
        raw_issues = tuple(getattr(decision, "issues", ()) or ())
    issues: list[PipelineLegalIssueV1] = []
    queries: list[str] = []
    for item in raw_issues:
        query = _compact(getattr(item, "query_text", None) or getattr(item, "text", None))
        issue_id = _compact(getattr(item, "issue_id", None)) or f"issue-{len(issues) + 1}"
        domain = _compact(getattr(item, "domain", None)) or None
        intent = _compact(getattr(item, "intent", None)) or None
        issues.append(
            PipelineLegalIssueV1(
                issue_id=issue_id,
                query_text=query,
                domain=domain,
                intent=intent,
            )
        )
        if query:
            queries.append(query)
    facets = tuple(getattr(decision, "facets", ()) or ()) if decision is not None else ()
    domain = None
    temporal = None
    if decision is not None:
        domain = _compact(decision.canonical_domain) or None
        temporal = _compact(decision.temporal_scope) or None
    return tuple(issues), tuple(dict.fromkeys(queries)), facets, domain, temporal


def pipeline_decision_from_router(
    decision: RouterDecision,
    *,
    active_document: Mapping[str, Any] | None = None,
    question: str | None = None,
    role: str = "citizen",
) -> PipelineDecisionV1:
    """Map unified_router.RouterDecision onto PipelineDecisionV1.

    When the conversation route is legal_query, the legal plan is taken from
    ``route_legal_answer`` / LegalQueryDecisionV1 without account_domain
    overwrite. Officer ACL is AuthorizationScopeV1, applied later.
    """

    route: ConversationRouteV1 = decision.conversation_route  # type: ignore[assignment]
    if route not in {"legal_query", "document_followup", "chat_meta", "out_of_scope"}:
        route = "legal_query"
    legal_answer = decision.legal_answer
    if route == "legal_query" and legal_answer is None and question:
        legal_answer = route_legal_answer(
            question,
            remediation=True,
            role=role or "citizen",
        )
    issues: tuple[PipelineLegalIssueV1, ...] = ()
    queries: tuple[str, ...] = ()
    facets: tuple[str, ...] = ()
    domain = decision.canonical_domain
    temporal = decision.temporal_scope
    legal_route = decision.legal_route
    if route == "legal_query":
        issues, queries, facets, planned_domain, planned_temporal = _issues_from_legal_answer(
            legal_answer
        )
        domain = planned_domain or domain
        temporal = planned_temporal or temporal
        if legal_route is None and legal_answer is not None:
            legal_route = str(legal_answer.answer_route or "") or None
    else:
        domain = None
        temporal = None
        legal_route = None
    active_id = _active_document_id(active_document)
    if not active_id and decision.turn is not None:
        active_id = _compact(getattr(decision.turn, "active_document_id", None)) or None
    source: RouterSourceV1 = decision.source  # type: ignore[assignment]
    if source not in {"rule", "llm", "fail_open"}:
        source = "fail_open"
    return PipelineDecisionV1(
        conversation_route=route,
        legal_issues=issues,
        canonical_domain=domain,
        facets=facets,
        temporal_scope=temporal,
        active_document_id=active_id,
        confidence=float(decision.confidence),
        source=source,
        independent_queries=queries,
        legal_route=legal_route,
        reason=str(decision.reason or ""),
    )


def decide_pipeline(
    question: str,
    *,
    role: str = "citizen",
    history: Sequence[Mapping[str, Any]] | None = None,
    active_document: Mapping[str, Any] | None = None,
    allowed_domains: Sequence[str] | None = None,
) -> PipelineDecisionV1:
    """Rule-first pipeline decision. ``allowed_domains`` is ACL-only, not classify."""

    decision = decide_router(
        question,
        role=role,
        history=history,
        active_document=active_document,
        allowed_domains=allowed_domains,
    )
    return pipeline_decision_from_router(
        decision,
        active_document=active_document,
        question=question,
        role=role,
    )
