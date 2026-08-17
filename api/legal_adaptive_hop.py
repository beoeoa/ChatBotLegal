"""Bounded, deterministic legal relationship traversal for Feature 016.

This module never asks an LLM to invent a relation. It follows only verified
edges and reapplies current-validity, authority, jurisdiction and facet gates
before and after every retrieval call.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from collections import Counter
from datetime import date
from typing import Any, Awaitable, Callable, Mapping, Sequence


MAX_HOPS = 2
MAX_QUERIES = 16
ALLOWED_RELATIONSHIPS = {
    "amends",
    "amended_by",
    "replaces",
    "replaced_by",
    "references",
    "referenced_by",
    "parent",
    "child",
    "parent_child",
}
_CURRENT_STATES = {"effective", "active", "current", "in_force"}


def adaptive_hop_enabled() -> bool:
    return str(os.getenv("LEGAL_ADAPTIVE_HOP_ENABLED") or "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _document_id(document: Mapping[str, Any]) -> str:
    return _text(
        document.get("document_id")
        or document.get("id")
        or document.get("legal_document_id")
    )


def _plain(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", _text(value)).casefold()
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d")


def _canonical_relation(value: Any) -> str:
    plain = _plain(value).replace("-", "_").replace(" ", "_")
    aliases = {
        "van_ban_can_cu": "references",
        "dan_chieu": "references",
        "sua_doi_bo_sung": "amends",
        "duoc_sua_doi_bo_sung_boi": "amended_by",
        "thay_the": "replaces",
        "duoc_thay_the_boi": "replaced_by",
        "van_ban_cha": "parent",
        "van_ban_con": "child",
    }
    return aliases.get(plain, plain)


def _iso_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def hard_gate_document(
    document: Mapping[str, Any],
    *,
    legal_as_of: str,
    required_facets: Sequence[str],
    allowed_jurisdictions: Sequence[str],
) -> tuple[bool, str | None]:
    """Apply all mandatory gates; legal_as_of is explicit even for snapshot metadata."""

    as_of = _iso_date(legal_as_of)
    if as_of is None:
        return False, "legal_as_of_invalid"
    state = _text(
        document.get("effective_status")
        or document.get("validity_status")
        or document.get("status")
    ).casefold()
    if state == "partially_effective":
        if document.get("provision_effective") is not True:
            return False, "expired_or_not_current"
    elif state not in _CURRENT_STATES:
        return False, "expired_or_not_current"
    effective_from = _iso_date(
        document.get("article_effective_from") or document.get("effective_date")
    )
    effective_to = _iso_date(
        document.get("article_effective_to") or document.get("expired_date")
    )
    if effective_from and effective_from > as_of:
        return False, "expired_or_not_current"
    if effective_to and effective_to <= as_of:
        return False, "expired_or_not_current"
    if document.get("authority_eligible") is False or document.get(
        "hierarchy_eligible"
    ) is False:
        return False, "hierarchy_ineligible"
    allowed = {_text(value).casefold() for value in allowed_jurisdictions if _text(value)}
    jurisdiction = _text(document.get("jurisdiction") or document.get("scope")).casefold()
    if allowed and jurisdiction not in allowed:
        return False, "jurisdiction_mismatch"
    requested = {_text(value).casefold() for value in required_facets if _text(value)}
    available = {
        _text(value).casefold()
        for value in (
            document.get("facets")
            or document.get("eligible_facets")
            or []
        )
        if _text(value)
    }
    if requested and not requested.issubset(available):
        return False, "facet_mismatch"
    return True, None


def _verified_edge(edge: Mapping[str, Any]) -> bool:
    relation = _canonical_relation(edge.get("relationship_type") or edge.get("type"))
    verified = edge.get("verified") is True or _text(
        edge.get("verification_status")
    ).casefold() == "verified"
    return verified and relation in ALLOWED_RELATIONSHIPS


def _adaptive_query(
    base_query: str,
    *,
    target: Mapping[str, Any],
    edge: Mapping[str, Any],
    previous_evidence: Sequence[Mapping[str, Any]],
) -> str:
    previous = " ".join(
        _text(item.get("content") or item.get("support_quote") or item.get("law_number"))[:240]
        for item in previous_evidence[-4:]
        if _text(item.get("content") or item.get("support_quote") or item.get("law_number"))
    )
    parts = [
        _text(base_query),
        f"quan hệ {_text(edge.get('relationship_type') or edge.get('type'))}",
        f"văn bản {_text(target.get('law_number') or _document_id(target))}",
    ]
    if previous:
        parts.append(f"bằng chứng vòng trước {previous}")
    return " | ".join(part for part in parts if part)


def _evidence_key(item: Mapping[str, Any]) -> str:
    identity = "|".join(
        (
            _document_id(item),
            _text(item.get("article_number")),
            _text(item.get("clause_number")),
            _text(item.get("content") or item.get("support_quote")),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


async def execute_bounded_hops(
    *,
    seed_document_ids: Sequence[str],
    initial_queries: Sequence[str],
    relationships: Sequence[Mapping[str, Any]],
    documents: Mapping[str, Mapping[str, Any]],
    retrieve: Callable[[str, str, int], Awaitable[Sequence[Mapping[str, Any]]]],
    legal_as_of: str,
    required_facets: Sequence[str] = (),
    allowed_jurisdictions: Sequence[str] = (),
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Traverse at most two verified hops and issue at most sixteen queries."""

    active = adaptive_hop_enabled() if enabled is None else bool(enabled)
    seeds = sorted({_text(value) for value in seed_document_ids if _text(value)})
    if not active:
        return {
            "status": "disabled",
            "stop_reason": "disabled_by_config",
            "hop_count": 0,
            "query_count": 0,
            "evidence": [],
            "visited_document_ids": seeds,
            "filtered_reasons": {},
            "ignored_unverified_edges": 0,
        }
    base_queries = [_text(value) for value in initial_queries if _text(value)] or [""]
    visited = set(seeds)
    frontier = list(seeds)
    accepted: list[dict[str, Any]] = []
    accepted_keys: set[str] = set()
    filtered: Counter[str] = Counter()
    ignored_unverified = 0
    query_count = 0
    hop_count = 0
    stop_reason = "cycle_or_frontier_exhausted"

    for hop in range(1, MAX_HOPS + 1):
        candidate_edges: list[Mapping[str, Any]] = []
        for edge in relationships:
            source_id = _text(edge.get("source_document_id") or edge.get("source_id"))
            if source_id not in frontier:
                continue
            if not _verified_edge(edge):
                ignored_unverified += 1
                continue
            target_id = _text(edge.get("target_document_id") or edge.get("target_id"))
            if not target_id or target_id in visited:
                continue
            candidate_edges.append(edge)
        candidate_edges.sort(
            key=lambda edge: (
                _text(edge.get("target_document_id") or edge.get("target_id")),
                _text(edge.get("relationship_type") or edge.get("type")),
            )
        )
        if not candidate_edges:
            stop_reason = "cycle_or_frontier_exhausted"
            break

        hop_count = hop
        next_frontier: list[str] = []
        for edge in candidate_edges:
            target_id = _text(edge.get("target_document_id") or edge.get("target_id"))
            target = dict(documents.get(target_id) or {})
            if not target:
                filtered["target_document_missing"] += 1
                continue
            eligible, reason = hard_gate_document(
                target,
                legal_as_of=legal_as_of,
                required_facets=required_facets,
                allowed_jurisdictions=allowed_jurisdictions,
            )
            if not eligible:
                filtered[str(reason)] += 1
                continue
            target_accepted = False
            for base_query in base_queries:
                if query_count >= MAX_QUERIES:
                    stop_reason = "query_budget_reached"
                    break
                query = _adaptive_query(
                    base_query,
                    target=target,
                    edge=edge,
                    previous_evidence=accepted,
                )
                query_count += 1
                rows = await retrieve(query, target_id, hop)
                for raw in rows or []:
                    item = {**target, **dict(raw)}
                    eligible_result, result_reason = hard_gate_document(
                        item,
                        legal_as_of=legal_as_of,
                        required_facets=required_facets,
                        allowed_jurisdictions=allowed_jurisdictions,
                    )
                    if not eligible_result:
                        filtered[str(result_reason)] += 1
                        continue
                    key = _evidence_key(item)
                    if key not in accepted_keys:
                        accepted_keys.add(key)
                        accepted.append(item)
                    target_accepted = True
                if stop_reason == "query_budget_reached":
                    break
            if target_accepted:
                visited.add(target_id)
                next_frontier.append(target_id)
            if stop_reason == "query_budget_reached":
                break
        if stop_reason == "query_budget_reached":
            break
        frontier = sorted(set(next_frontier))
        if not frontier:
            stop_reason = "cycle_or_frontier_exhausted"
            break
        if hop == MAX_HOPS:
            stop_reason = "hop_budget_reached"

    return {
        "status": "completed",
        "stop_reason": stop_reason,
        "hop_count": hop_count,
        "query_count": query_count,
        "evidence": accepted,
        "visited_document_ids": sorted(visited),
        "filtered_reasons": dict(sorted(filtered.items())),
        "ignored_unverified_edges": ignored_unverified,
        "limits": {"max_hops": MAX_HOPS, "max_queries": MAX_QUERIES},
    }
