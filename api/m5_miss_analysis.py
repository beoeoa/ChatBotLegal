"""Deterministic miss-level analysis for checksum-bound M5 candidate caches.

The analyzer is intentionally read-only.  It does not open Chroma, PostgreSQL,
or a model and never infers legal truth beyond the approved development cases
embedded in the cache.  Branch-level conclusions are marked unresolved when
the selected cache truncates vector or lexical observations.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Any, Iterable, Mapping, Sequence

from scripts.kaggle_retrieval_v2_benchmark_common import source_matches


SCHEMA_VERSION = "legal-retrieval-m5-miss-level-v1"


def _sources(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(source)
        for group in case.get("positive_source_groups") or []
        for source in group.get("sources") or []
        if isinstance(source, Mapping)
    ]


def _first_match_rank(
    candidates: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
    *,
    limit: int | None = None,
) -> int | None:
    bounded = candidates if limit is None else candidates[:limit]
    for index, candidate in enumerate(bounded, start=1):
        if any(source_matches(candidate, source) for source in sources):
            return index
    return None


def _branch_match_rank(
    candidates: Sequence[Mapping[str, Any]],
    sources: Sequence[Mapping[str, Any]],
    branch: str,
    top_k: int,
) -> int | None:
    ranks = [
        int(candidate.get("branch_ranks", {}).get(branch))
        for candidate in candidates
        if candidate.get("branch_ranks", {}).get(branch) is not None
        and int(candidate.get("branch_ranks", {}).get(branch)) <= top_k
        and any(source_matches(candidate, source) for source in sources)
    ]
    return min(ranks) if ranks else None


def _branch_complete(
    candidates: Sequence[Mapping[str, Any]], branch: str, top_k: int
) -> bool:
    if top_k <= 0:
        return True
    observed = {
        int(candidate.get("branch_ranks", {}).get(branch))
        for candidate in candidates
        if candidate.get("branch_ranks", {}).get(branch) is not None
        and int(candidate.get("branch_ranks", {}).get(branch)) <= top_k
    }
    return observed == set(range(1, top_k + 1))


def _group_hits(
    case: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]], limit: int
) -> dict[str, bool]:
    return {
        str(group.get("group_id") or ""): _first_match_rank(
            candidates,
            [dict(source) for source in group.get("sources") or []],
            limit=limit,
        )
        is not None
        for group in case.get("positive_source_groups") or []
    }


def _required_group_ids(case: Mapping[str, Any]) -> set[str]:
    required = {
        str(group_id)
        for issue in case.get("issue_groups") or []
        for group_id in issue.get("required_source_group_ids") or []
        if str(group_id)
    }
    if required:
        return required
    return {
        str(group.get("group_id") or "")
        for group in case.get("positive_source_groups") or []
        if str(group.get("group_id") or "")
    }


def _ratio(numerator: int | float, denominator: int) -> float | None:
    return round(float(numerator) / denominator, 6) if denominator else None


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]


def _candidate_identity(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rank": candidate.get("_rank"),
        "chunk_revision_id": candidate.get("chunk_revision_id"),
        "law_number": candidate.get("law_number"),
        "article_number": candidate.get("article_number"),
        "document_id": candidate.get("document_id"),
        "article_id": candidate.get("article_id"),
        "retrieval_sources": list(candidate.get("retrieval_sources") or []),
        "branch_ranks": dict(candidate.get("branch_ranks") or {}),
    }


def _case_analysis(raw: Mapping[str, Any]) -> dict[str, Any]:
    case = dict(raw.get("case") or {})
    config = dict(raw.get("selected_config") or {})
    candidates = [dict(value) for value in raw.get("candidates") or []]
    exact_candidates = [dict(value) for value in raw.get("exact_candidates") or []]
    vector_candidates = [dict(value) for value in raw.get("vector_candidates") or []]
    lexical_candidates = [dict(value) for value in raw.get("lexical_candidates") or []]
    branch_storage = dict(raw.get("branch_storage") or {})
    sources = _sources(case)
    answer_required = bool(case.get("answer_required"))
    expected_refusal = bool(case.get("expected_refusal"))
    vector_top_k = int(config.get("vector_top_k") or 0)
    lexical_top_k = int(config.get("lexical_top_k") or 0)

    top10_rank = _first_match_rank(candidates, sources, limit=10) if sources else None
    top50_rank = _first_match_rank(candidates, sources, limit=50) if sources else None
    cached_rank = _first_match_rank(candidates, sources) if sources else None
    exact_rank = _first_match_rank(exact_candidates, sources) if sources else None
    full_branches = bool(branch_storage.get("full_pre_fusion_branches_stored"))
    if full_branches:
        vector_rank = _first_match_rank(vector_candidates, sources) if sources else None
        lexical_rank = _first_match_rank(lexical_candidates, sources) if sources else None
        vector_complete = True
        lexical_complete = True
    else:
        vector_rank = _branch_match_rank(candidates, sources, "vector", vector_top_k) if sources else None
        lexical_rank = _branch_match_rank(candidates, sources, "lexical", lexical_top_k) if sources else None
        vector_complete = _branch_complete(candidates, "vector", vector_top_k)
        lexical_complete = _branch_complete(candidates, "lexical", lexical_top_k)
    branch_complete = vector_complete and lexical_complete

    group_hits_10 = _group_hits(case, candidates, 10)
    group_hits_50 = _group_hits(case, candidates, 50)
    required_groups = _required_group_ids(case)
    required_hit_count = sum(bool(group_hits_10.get(group_id)) for group_id in required_groups)
    group_coverage = _ratio(required_hit_count, len(required_groups))
    multi_issue = len(case.get("issue_groups") or []) > 1 or "multi_issue" in set(case.get("tags") or [])

    exact_expected = "exact_law_article" in set(case.get("tags") or [])
    if not answer_required:
        root_cause = "expected_refusal" if expected_refusal else "not_answer_required"
    elif not sources:
        root_cause = "evaluation_mismatch"
    elif top10_rank is not None:
        root_cause = "hit"
    elif top50_rank is not None:
        root_cause = "fusion_rank_loss_top10"
    elif cached_rank is not None:
        root_cause = "fusion_rank_loss_top50"
    elif exact_expected and exact_rank is None:
        root_cause = "exact_lookup_failure"
    elif exact_rank is not None or vector_rank is not None or lexical_rank is not None:
        root_cause = "fusion_rank_loss_top100"
    elif branch_complete:
        root_cause = "candidate_miss"
    else:
        root_cause = "candidate_miss_unresolved_cache_truncation"

    expected_laws = sorted({str(source.get("law_number") or "") for source in sources})
    same_law_wrong_article = [
        {**candidate, "_rank": index}
        for index, candidate in enumerate(candidates, start=1)
        if any(
            str(candidate.get("law_number") or "").casefold() == law.casefold()
            for law in expected_laws
        )
        and not any(source_matches(candidate, source) for source in sources)
    ]

    return {
        "case_id": str(case.get("case_id") or ""),
        "case_sha256": case.get("case_sha256"),
        "split": str(case.get("split") or ""),
        "domain": str(case.get("domain") or ""),
        "intent": str((case.get("query_classification") or {}).get("intent") or "UNKNOWN"),
        "temporal_scope": str(case.get("temporal_scope") or "unknown"),
        "legal_as_of": case.get("legal_as_of"),
        "tags": list(case.get("tags") or []),
        "question": str(case.get("question") or ""),
        "answer_required": answer_required,
        "expected_refusal": expected_refusal,
        "expected_sources": [
            {
                "law_number": source.get("law_number"),
                "article": source.get("article"),
                "paragraph": source.get("paragraph"),
                "point": source.get("point"),
                "official_url": source.get("official_url"),
            }
            for source in sources
        ],
        "primary_root_cause": root_cause,
        "hit_at_10": top10_rank is not None,
        "hit_rank_at_10": top10_rank,
        "candidate_hit_at_50": top50_rank is not None,
        "cached_hit_rank": cached_rank,
        "exact_hit_rank": exact_rank,
        "vector_hit_rank": vector_rank,
        "lexical_hit_rank": lexical_rank,
        "vector_observation_complete": vector_complete,
        "lexical_observation_complete": lexical_complete,
        "branch_observation_complete": branch_complete,
        "required_source_group_ids": sorted(required_groups),
        "source_group_hits_at_10": group_hits_10,
        "source_group_hits_at_50": group_hits_50,
        "required_source_group_coverage_at_10": group_coverage,
        "multi_issue": multi_issue,
        "multi_issue_incomplete": bool(multi_issue and (group_coverage or 0.0) < 1.0),
        "same_law_wrong_article_count": len(same_law_wrong_article),
        "nearest_same_law_wrong_article": _candidate_identity(same_law_wrong_article[0]) if same_law_wrong_article else None,
        "top_candidates": [
            _candidate_identity({**candidate, "_rank": index})
            for index, candidate in enumerate(candidates[:10], start=1)
        ],
        "retrieval_latency_ms": float(raw.get("retrieval_latency_ms") or 0.0),
    }


def _slice(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    answer = [case for case in cases if case.get("answer_required")]
    refusal = [case for case in cases if case.get("expected_refusal")]
    hits = [case for case in answer if case.get("hit_at_10")]
    reciprocal = sum(1.0 / int(case["hit_rank_at_10"]) for case in hits)
    return {
        "case_count": len(cases),
        "answer_required_count": len(answer),
        "expected_refusal_count": len(refusal),
        "recall_at_10": _ratio(len(hits), len(answer)),
        "candidate_recall_at_50": _ratio(
            sum(bool(case.get("candidate_hit_at_50")) for case in answer), len(answer)
        ),
        "mrr_at_10": _ratio(reciprocal, len(answer)),
        "miss_count": sum(not bool(case.get("hit_at_10")) for case in answer),
        "multi_issue_incomplete_count": sum(
            bool(case.get("multi_issue_incomplete")) for case in answer
        ),
    }


def _bucket_summaries(
    cases: Sequence[Mapping[str, Any]], field: str
) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for case in cases:
        buckets[str(case.get(field) or "unknown")].append(case)
    return {key: _slice(values) for key, values in sorted(buckets.items())}


def _miss_clusters(misses: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    domain_causes = Counter(
        (str(case.get("domain")), str(case.get("primary_root_cause")))
        for case in misses
    )
    laws = Counter(
        str(source.get("law_number") or "")
        for case in misses
        for source in case.get("expected_sources") or []
    )
    provisions = Counter(
        (str(source.get("law_number") or ""), str(source.get("article") or "*"))
        for case in misses
        for source in case.get("expected_sources") or []
    )
    return {
        "domain_root_cause": [
            {"domain": key[0], "root_cause": key[1], "count": count}
            for key, count in domain_causes.most_common()
        ],
        "expected_law": [
            {"law_number": key, "count": count} for key, count in laws.most_common()
        ],
        "expected_provision": [
            {"law_number": key[0], "article": key[1], "count": count}
            for key, count in provisions.most_common()
        ],
    }


def analyze_cache_rows(
    rows: Iterable[Mapping[str, Any]], *, expected_case_count: int | None = None
) -> dict[str, Any]:
    raw_rows = [dict(row) for row in rows]
    if expected_case_count is not None and len(raw_rows) != expected_case_count:
        raise ValueError(
            f"candidate_cache_case_count_mismatch:{len(raw_rows)}!={expected_case_count}"
        )
    case_ids = [str((row.get("case") or {}).get("case_id") or "") for row in raw_rows]
    if not all(case_ids) or len(case_ids) != len(set(case_ids)):
        raise ValueError("candidate_cache_case_ids_missing_or_duplicate")
    selected_ids = {str(row.get("selected_experiment") or "") for row in raw_rows}
    if len(selected_ids) != 1 or "" in selected_ids:
        raise ValueError("candidate_cache_selected_experiment_mismatch")
    if any(str((row.get("case") or {}).get("split")) == "production-holdout" for row in raw_rows):
        raise ValueError("sealed_holdout_content_forbidden")

    cases = [_case_analysis(row) for row in raw_rows]
    answer = [case for case in cases if case.get("answer_required")]
    misses = [case for case in answer if not case.get("hit_at_10")]
    causes = Counter(str(case.get("primary_root_cause")) for case in misses)
    latency = [float(case.get("retrieval_latency_ms") or 0.0) for case in cases]
    observed_intents = sorted({str(case.get("intent")) for case in answer})
    report = {
        "schema_version": SCHEMA_VERSION,
        "selected_experiment": next(iter(selected_ids)),
        **_slice(cases),
        "root_causes": dict(sorted(causes.items())),
        "miss_clusters": _miss_clusters(misses),
        "unresolved_cache_truncation_count": causes.get(
            "candidate_miss_unresolved_cache_truncation", 0
        ),
        "per_split": _bucket_summaries(cases, "split"),
        "per_domain": _bucket_summaries(answer, "domain"),
        "per_intent": _bucket_summaries(answer, "intent"),
        "per_temporal_scope": _bucket_summaries(answer, "temporal_scope"),
        "latency_ms": {
            "p50": _percentile(latency, 0.50),
            "p95": _percentile(latency, 0.95),
            "max": max(latency, default=0.0),
        },
        "limitations": [
            "The v6r6 cache stores fused Top-100, not complete vector and lexical branches.",
            "candidate_miss_unresolved_cache_truncation is not evidence of source absence.",
            "Per-case source/article availability requires the approved manifest or exact index, not this cache alone.",
        ],
        "diagnostic_warnings": (
            [
                "All answer-required cases have one classified intent; this cache cannot validate per-intent routing diversity."
            ]
            if len(observed_intents) <= 1 and answer
            else []
        ),
        "misses": misses,
        "multi_issue_incomplete_cases": [
            case for case in answer if case.get("multi_issue_incomplete")
        ],
        "cases": cases,
    }
    return report


__all__ = ["SCHEMA_VERSION", "analyze_cache_rows"]
