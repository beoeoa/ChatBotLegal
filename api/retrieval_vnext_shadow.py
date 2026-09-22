"""Phase C retrieval vNext on a SHADOW index only.

Enabled by LEGAL_RETRIEVAL_VNEXT_SHADOW=true. This module never writes the
live serving pointer and must not rank against the active collection.
Pipeline per issue: exact lane, hybrid pool, rerank ~50, cap 8 evidence
units, at most one coverage expansion. Org-unit never drops central law.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from api.legal_exact_retrieval import plan_exact_lookup
from api.pipeline_contracts import (
    EvidencePacketStatusV1,
    EvidencePacketV1,
    EvidenceUnitV1,
)
from api.retrieval_candidate_r26 import expand_legal_query_r26
from api.retrieval_candidate_r27 import (
    explicit_law_article_keys_r27,
    explicit_law_numbers_r27,
    extract_query_facets_r27,
    rerank_candidates_r27,
)
from scripts.kaggle_retrieval_v2_benchmark_common import normalize_exact


LIVE_POINTER_COLLECTION = "legal_chunks_vnlegal_lal_haiphong_unified_v1"
LIVE_POINTER_FILE = Path("release-data/legal/chroma_store/active_core_collection.txt")
# Build-script name from scripts/build_retrieval_release_v2_shadow.py.
BUILD_SCRIPT_SHADOW_COLLECTION = "legal_chunks_retrieval_release_v2_shadow"
# Independent collection already present on disk (kaggle v6r1 shadow).
DEFAULT_SHADOW_COLLECTION = "legal_chunks_retrieval_v2_current_kaggle_v6r1_shadow"
DEFAULT_SHADOW_TEMPORAL_COLLECTION = (
    "legal_chunks_retrieval_v2_temporal_kaggle_v6r1_shadow"
)

RERANK_WINDOW = 50
MAX_EVIDENCE_UNITS = 8
CENTRAL_SCOPE_HINTS = frozenset(
    {
        "central",
        "national",
        "shared",
        "cross_cutting",
        "cross-cutting",
        "toan_quoc",
        "trung_uong",
        "chung",
        "toan quoc",
    }
)
_QH_TOKEN = re.compile(r"\bQH\d*\b")
_ND_TOKEN = re.compile(r"\bND\b|\bCP\b")
_TT_TOKEN = re.compile(r"\bTT\b")
_QD_TOKEN = re.compile(r"\bQD\b")


def _env_flag(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def vnext_shadow_enabled(values: Mapping[str, str] | None = None) -> bool:
    if values is None:
        return _env_flag("LEGAL_RETRIEVAL_VNEXT_SHADOW")
    return str(values.get("LEGAL_RETRIEVAL_VNEXT_SHADOW") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def shadow_collection_name(values: Mapping[str, str] | None = None) -> str:
    getter = (values or os.environ).get
    configured = str(
        getter("LEGAL_RETRIEVAL_VNEXT_SHADOW_COLLECTION")
        or getter("LEGAL_CHROMA_COLLECTION")
        or DEFAULT_SHADOW_COLLECTION
    ).strip()
    return configured or DEFAULT_SHADOW_COLLECTION


def read_live_pointer(root: Path | None = None) -> str:
    path = (root or Path(".")) / LIVE_POINTER_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return LIVE_POINTER_COLLECTION


def refuse_live_collection(collection_name: str, *, root: Path | None = None) -> bool:
    """True when the named collection is the live serving pointer."""

    name = str(collection_name or "").strip()
    live = read_live_pointer(root) or LIVE_POINTER_COLLECTION
    return name in {LIVE_POINTER_COLLECTION, live}


def vnext_may_run_on_collection(collection_name: str, *, root: Path | None = None) -> bool:
    name = str(collection_name or "").strip()
    if not name or refuse_live_collection(name, root=root):
        return False
    return True


def packet_may_go_to_llm(packet: EvidencePacketV1 | Mapping[str, Any] | None) -> bool:
    if packet is None:
        return False
    if isinstance(packet, EvidencePacketV1):
        return packet.status == "complete" and not packet.cannot_verify
    status = str(packet.get("status") or "").strip().casefold()
    return status == "complete" and not bool(packet.get("cannot_verify"))


def is_central_or_shared_law(item: Mapping[str, Any]) -> bool:
    """Central / national / unassigned / cross-cutting instruments stay retrievable."""

    scope = " ".join(
        str(item.get(key) or "")
        for key in (
            "scope",
            "jurisdiction",
            "serving_scope",
            "document_scope",
            "authority_level",
        )
    ).casefold()
    folded_scope = " ".join(scope.replace("_", " ").replace("-", " ").split())
    if any(hint in folded_scope for hint in CENTRAL_SCOPE_HINTS):
        return True
    law = normalize_exact(item.get("law_number"))
    if _QH_TOKEN.search(law) or "QH" in law.split():
        return True
    if _ND_TOKEN.search(law):
        return True
    units = item.get("organization_unit_ids") or item.get("organization_unit_id")
    primary = item.get("primary_organization_unit_id")
    if not units and not primary:
        return True
    return False


def legal_instrument_rank(law_number: Any) -> int:
    """Lower is higher legal rank. Applied before org-unit preference."""

    law = normalize_exact(law_number)
    if not law:
        return 9
    if _QH_TOKEN.search(law) or "PL" in law.split():
        return 0
    if _ND_TOKEN.search(law):
        return 1
    if _TT_TOKEN.search(law):
        return 2
    if _QD_TOKEN.search(law):
        return 3
    return 4


def _as_of_ok(item: Mapping[str, Any], as_of: str | None) -> bool:
    if not as_of:
        return True
    start = str(item.get("effective_from") or "")[:10]
    end = str(item.get("effective_to") or "")[:10]
    day = str(as_of)[:10]
    if start and start > day:
        return False
    if end and end <= day:
        return False
    return True


def effectivity_rank(item: Mapping[str, Any], *, as_of: str | None = None) -> int:
    state = str(item.get("document_serving_state") or "").strip()
    if not _as_of_ok(item, as_of):
        return 3
    if state == "current_retrievable":
        return 0
    if state in {"effective", ""}:
        return 1
    if state == "historical_only":
        return 2
    return 3


def _unit_id(item: Mapping[str, Any]) -> str:
    law = normalize_exact(item.get("law_number"))
    article = normalize_exact(
        item.get("article_number") or item.get("article") or ""
    )
    chunk = str(item.get("chunk_revision_id") or item.get("chunk_id") or "")
    if law or article:
        return f"{law}|{article}"
    return chunk or f"row-{id(item)}"


def _candidate_id(item: Mapping[str, Any]) -> str:
    return str(
        item.get("chunk_revision_id")
        or item.get("chunk_id")
        or item.get("source_id")
        or ""
    )


def _exact_keys_for_query(query: str) -> set[str]:
    plan = plan_exact_lookup(query)
    keys: set[str] = set()
    for law, article in plan.article_law_pairs:
        keys.add(f"{normalize_exact(law)}|{normalize_exact(article)}")
    for law in plan.law_numbers or ((plan.law_number,) if plan.law_number else ()):
        keys.add(normalize_exact(law))
    for article in plan.article_numbers or (
        (plan.article_number,) if plan.article_number else ()
    ):
        keys.add(f"|{normalize_exact(article)}")
    for key in explicit_law_article_keys_r27(query):
        keys.add(key)
    for law in explicit_law_numbers_r27(query):
        keys.add(law)
    for code in plan.form_codes:
        keys.add(normalize_exact(code))
    if plan.procedure_id:
        keys.add(normalize_exact(plan.procedure_id))
    return {key for key in keys if key and key != "|"}


def is_exact_lane_hit(query: str, item: Mapping[str, Any]) -> bool:
    keys = _exact_keys_for_query(query)
    if not keys:
        return False
    identity = _unit_id(item)
    law = normalize_exact(item.get("law_number"))
    article = normalize_exact(item.get("article_number") or item.get("article") or "")
    form = normalize_exact(item.get("form_code") or item.get("procedure_id") or "")
    path = normalize_exact(item.get("structural_path") or "")
    haystack = {identity, law, f"|{article}" if article else "", form, path}
    return any(key in haystack or (key and key in path) for key in keys if key)


def exact_lane_candidates(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Prefer law numbers, articles, procedures, and forms already in the pool."""

    hits: list[dict[str, Any]] = []
    rest: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates:
        item = dict(raw)
        identifier = _candidate_id(item) or _unit_id(item)
        if identifier in seen:
            continue
        seen.add(identifier)
        if is_exact_lane_hit(query, item):
            item["exact_lane"] = True
            hits.append(item)
        else:
            rest.append(item)
    return [*hits, *rest]


def apply_org_unit_ranking(
    candidates: Sequence[Mapping[str, Any]],
    *,
    query: str,
    organization_unit_id: str | None = None,
    audience: str | None = None,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    """Rank only. Never drop central/shared/cross-cutting law."""

    unit_id = str(organization_unit_id or "").strip()
    rows = [dict(item) for item in candidates]
    if not rows:
        return []

    def sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
        exact = 0 if is_exact_lane_hit(query, item) else 1
        org_mismatch = 0
        if unit_id and str(audience or "").casefold() in {"officer", "admin"}:
            primary = str(item.get("primary_organization_unit_id") or "").strip()
            assigned = item.get("organization_unit_ids") or []
            if isinstance(assigned, str):
                assigned = [assigned]
            matched = primary == unit_id or unit_id in {str(x) for x in assigned}
            # Central law is never treated as a mismatch.
            if not matched and not is_central_or_shared_law(item):
                org_mismatch = 1
        return (
            effectivity_rank(item, as_of=as_of),
            legal_instrument_rank(item.get("law_number")),
            exact,
            org_mismatch,
            -float(item.get("score") or 0.0),
            _unit_id(item),
        )

    return sorted(rows, key=sort_key)


def rerank_vnext_pool(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    window: int = RERANK_WINDOW,
) -> list[dict[str, Any]]:
    ordered = exact_lane_candidates(query, candidates)
    reranked = rerank_candidates_r27(
        query,
        ordered,
        candidate_window=window,
        top_k=MAX_EVIDENCE_UNITS,
    )
    return reranked[: max(1, int(window))]


def select_evidence_units(
    candidates: Sequence[Mapping[str, Any]],
    *,
    query: str,
    maximum: int = MAX_EVIDENCE_UNITS,
) -> list[dict[str, Any]]:
    """Collapse chunks into at most ``maximum`` complete (law, article) units."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for raw in candidates:
        item = dict(raw)
        key = _unit_id(item)
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)
    units: list[dict[str, Any]] = []
    for key in order:
        rows = grouped[key]
        rows.sort(key=lambda item: int(item.get("chunk_index") or 0))
        head = dict(rows[0])
        head["unit_id"] = key
        head["chunk_ids"] = tuple(
            _candidate_id(item) for item in rows if _candidate_id(item)
        )
        head["exact_match"] = is_exact_lane_hit(query, head)
        head["is_central_or_shared"] = is_central_or_shared_law(head)
        units.append(head)
        if len(units) >= maximum:
            break
    return units


def _unit_to_contract(item: Mapping[str, Any], *, query: str) -> EvidenceUnitV1:
    return EvidenceUnitV1(
        unit_id=str(item.get("unit_id") or _unit_id(item)),
        law_number=str(item.get("law_number") or ""),
        article_number=str(item.get("article_number") or item.get("article") or ""),
        chunk_ids=tuple(item.get("chunk_ids") or ()),
        domain=str(item.get("domain_slug") or item.get("domain") or "") or None,
        effective_from=str(item.get("effective_from") or "") or None,
        effective_to=str(item.get("effective_to") or "") or None,
        document_serving_state=str(item.get("document_serving_state") or "") or None,
        is_central_or_shared=bool(
            item.get("is_central_or_shared", is_central_or_shared_law(item))
        ),
        exact_match=bool(item.get("exact_match", is_exact_lane_hit(query, item))),
        score=float(item.get("score") or 0.0),
        content=str(item.get("content") or item.get("matched_child_content") or ""),
        source_url=str(item.get("source_url") or "") or None,
        replacement_of=str(item.get("replacement_of") or item.get("replaces") or "")
        or None,
        replaced_by=str(item.get("replaced_by") or item.get("replacedBy") or "")
        or None,
    )


def _covered_facet_blob(units: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    for item in units:
        parts.extend(
            str(item.get(field) or "")
            for field in (
                "law_number",
                "article_number",
                "structural_path",
                "article_title",
                "document_title",
                "content",
                "facets",
            )
        )
        parts.extend(str(value) for value in (item.get("facets") or ()))
    return normalize_exact(" ".join(parts))


def missing_coverage_facets(
    units: Sequence[Mapping[str, Any]],
    *,
    query: str,
    facets: Sequence[str] = (),
) -> tuple[str, ...]:
    required = tuple(
        dict.fromkeys(
            [item for item in facets if str(item or "").strip()]
            or extract_query_facets_r27(query)
        )
    )
    if not required:
        plan = plan_exact_lookup(query)
        required = tuple(
            f"{law}|{article}" for law, article in plan.article_law_pairs
        )
    blob = _covered_facet_blob(units)
    missing: list[str] = []
    for facet in required:
        tokens = [
            token
            for token in normalize_exact(facet).split()
            if len(token) >= 3
        ]
        if not tokens:
            continue
        overlap = sum(1 for token in tokens if token in blob)
        if overlap < max(1, (len(tokens) + 1) // 2):
            missing.append(facet)
    return tuple(missing)


def classify_packet_status(
    units: Sequence[Mapping[str, Any]],
    *,
    query: str,
    missing_facets: Sequence[str],
) -> tuple[EvidencePacketStatusV1, bool, str]:
    if not units:
        return "insufficient", True, "no_evidence_units"
    plan = plan_exact_lookup(query)
    needs_exact = bool(
        plan.requires_exact_metadata_lookup or explicit_law_article_keys_r27(query)
    )
    if needs_exact and not any(is_exact_lane_hit(query, item) for item in units):
        return "insufficient", True, "exact_identifier_not_retrieved"
    if missing_facets:
        return "partial", True, "missing_facets"
    return "complete", False, "coverage_ok"


def build_evidence_packet_v1(
    *,
    issue_id: str,
    query: str,
    units: Sequence[Mapping[str, Any]],
    missing_facets: Sequence[str] = (),
    expansion_used: bool = False,
    candidate_count: int = 0,
    rerank_window: int = RERANK_WINDOW,
) -> EvidencePacketV1:
    status, cannot_verify, reason = classify_packet_status(
        units, query=query, missing_facets=missing_facets
    )
    capped = list(units)[:MAX_EVIDENCE_UNITS]
    return EvidencePacketV1(
        issue_id=issue_id or "issue-1",
        status=status,
        units=tuple(_unit_to_contract(item, query=query) for item in capped),
        missing_facets=tuple(missing_facets),
        expansion_used=bool(expansion_used),
        cannot_verify=cannot_verify,
        reason=reason,
        candidate_count=int(candidate_count),
        rerank_window=int(rerank_window),
    )


def retrieve_issue_vnext(
    *,
    query: str,
    exact_candidates: Sequence[Mapping[str, Any]] = (),
    hybrid_candidates: Sequence[Mapping[str, Any]] = (),
    facets: Sequence[str] = (),
    issue_id: str = "issue-1",
    organization_unit_id: str | None = None,
    audience: str | None = None,
    as_of: str | None = None,
    expand_fn: Callable[[tuple[str, ...]], Sequence[Mapping[str, Any]]] | None = None,
) -> tuple[EvidencePacketV1, list[dict[str, Any]]]:
    """Exact then hybrid, rerank ~50, cap 8, at most one expansion."""

    def _select(pool: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        ranked = rerank_vnext_pool(query, pool, window=RERANK_WINDOW)
        ranked = apply_org_unit_ranking(
            ranked,
            query=query,
            organization_unit_id=organization_unit_id,
            audience=audience,
            as_of=as_of,
        )
        return select_evidence_units(ranked, query=query, maximum=MAX_EVIDENCE_UNITS)

    merged: list[dict[str, Any]] = [dict(item) for item in exact_candidates]
    merged.extend(dict(item) for item in hybrid_candidates)
    units = _select(merged)
    required = tuple(facets) or extract_query_facets_r27(query)
    if not required:
        required = tuple(_exact_keys_for_query(query))
    missing = missing_coverage_facets(units, query=query, facets=required)
    expansion_used = False
    if missing and expand_fn is not None:
        extra = [dict(item) for item in expand_fn(missing)]
        expansion_used = True
        merged.extend(extra)
        units = _select(merged)
        missing = missing_coverage_facets(units, query=query, facets=required)
    packet = build_evidence_packet_v1(
        issue_id=issue_id,
        query=query,
        units=units,
        missing_facets=missing,
        expansion_used=expansion_used,
        candidate_count=len(merged),
        rerank_window=RERANK_WINDOW,
    )
    return packet, units


def apply_vnext_to_search_output(
    output: Mapping[str, Any],
    *,
    query: str,
    issue_id: str = "issue-1",
    facets: Sequence[str] = (),
    organization_unit_id: str | None = None,
    audience: str | None = None,
    as_of: str | None = None,
    collection_name: str | None = None,
    expand_fn: Callable[[tuple[str, ...]], Sequence[Mapping[str, Any]]] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Post-process a shadow search response. No-op on the live collection."""

    result = dict(output)
    name = str(collection_name or "")
    if name and not vnext_may_run_on_collection(name, root=root):
        result.setdefault("trace", {})
        if not isinstance(result["trace"], dict):
            result["trace"] = {}
        result["trace"]["vnext_shadow"] = {
            "applied": False,
            "reason": "refuses_live_or_pointer_collection",
            "collection": name,
        }
        return result
    rows = [dict(item) for item in (result.get("results") or [])]
    exact_rows = [
        item
        for item in rows
        if "exact" in {str(src).casefold() for src in (item.get("retrieval_sources") or [])}
        or item.get("exact_lane")
        or is_exact_lane_hit(query, item)
    ]
    packet, units = retrieve_issue_vnext(
        query=query,
        exact_candidates=exact_rows,
        hybrid_candidates=rows,
        facets=facets,
        issue_id=issue_id,
        organization_unit_id=organization_unit_id,
        audience=audience,
        as_of=as_of,
        expand_fn=expand_fn,
    )
    result["results"] = units
    result["evidence_packet"] = packet.to_payload()
    result["status"] = (
        "ok"
        if packet.status == "complete"
        else "partial"
        if packet.status == "partial"
        else "insufficient"
    )
    result.setdefault("trace", {})
    if not isinstance(result["trace"], dict):
        result["trace"] = {}
    result["trace"]["vnext_shadow"] = {
        "applied": True,
        "collection": name or shadow_collection_name(),
        "rerank_window": RERANK_WINDOW,
        "max_units": MAX_EVIDENCE_UNITS,
        "expansion_used": packet.expansion_used,
        "packet_status": packet.status,
        "live_pointer_written": False,
    }
    return result


def expand_query_for_hybrid(query: str) -> str:
    return expand_legal_query_r26(query, aliases=())


__all__ = [
    "BUILD_SCRIPT_SHADOW_COLLECTION",
    "DEFAULT_SHADOW_COLLECTION",
    "DEFAULT_SHADOW_TEMPORAL_COLLECTION",
    "LIVE_POINTER_COLLECTION",
    "MAX_EVIDENCE_UNITS",
    "RERANK_WINDOW",
    "apply_org_unit_ranking",
    "apply_vnext_to_search_output",
    "build_evidence_packet_v1",
    "exact_lane_candidates",
    "expand_query_for_hybrid",
    "is_central_or_shared_law",
    "is_exact_lane_hit",
    "legal_instrument_rank",
    "packet_may_go_to_llm",
    "read_live_pointer",
    "refuse_live_collection",
    "retrieve_issue_vnext",
    "rerank_vnext_pool",
    "select_evidence_units",
    "shadow_collection_name",
    "vnext_may_run_on_collection",
    "vnext_shadow_enabled",
]
