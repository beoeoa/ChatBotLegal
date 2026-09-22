"""Read-only, content-free diagnostics for full-answer required-source gaps."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Iterable, Mapping, Sequence

from scripts.kaggle_retrieval_v2_benchmark_common import source_matches


SourceAvailability = Callable[[Mapping[str, Any], str, str], bool]


def _first_rank(
    candidates: Sequence[Mapping[str, Any]], sources: Sequence[Mapping[str, Any]]
) -> int | None:
    for index, candidate in enumerate(candidates, start=1):
        if any(source_matches(candidate, source) for source in sources):
            return index
    return None


def _required_group_ids(case: Mapping[str, Any]) -> set[str]:
    issue_group_ids = {
        str(group_id)
        for issue in case.get("issue_groups") or []
        if isinstance(issue, Mapping)
        for group_id in issue.get("required_source_group_ids") or []
        if str(group_id)
    }
    if issue_group_ids:
        return issue_group_ids
    return {
        str(group.get("group_id") or "")
        for group in case.get("positive_source_groups") or []
        if isinstance(group, Mapping) and str(group.get("group_id") or "")
    }


def _root_cause(
    *,
    source_available_at_date: bool,
    final_rank: int | None,
    exact_rank: int | None,
    vector_rank: int | None,
    lexical_rank: int | None,
) -> str:
    if not source_available_at_date:
        return "source_not_in_manifest_at_date"
    if final_rank is not None and final_rank <= 10:
        return "hit_top10"
    if final_rank is not None and final_rank <= 50:
        return "fusion_rank_loss_top10"
    if final_rank is not None:
        return "fusion_rank_loss_top50"
    if any(rank is not None for rank in (exact_rank, vector_rank, lexical_rank)):
        return "fusion_rank_loss_top100"
    return "candidate_miss"


def analyze_required_group_gaps(
    cache_rows: Iterable[Mapping[str, Any]],
    *,
    failure_case_ids: set[str],
    source_available: SourceAvailability,
) -> dict[str, Any]:
    """Classify every required source group for the supplied failed cases.

    The function consumes only approved case identities and rank metadata. It
    deliberately omits question and passage content from its result.
    """
    rows_by_id = {
        str((row.get("case") or {}).get("case_id") or ""): row
        for row in cache_rows
    }
    missing_rows = sorted(case_id for case_id in failure_case_ids if case_id not in rows_by_id)
    if missing_rows:
        raise ValueError(f"failure_case_missing_from_cache:{','.join(missing_rows)}")

    groups: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    for case_id in sorted(failure_case_ids):
        raw = rows_by_id[case_id]
        case = dict(raw.get("case") or {})
        required_ids = _required_group_ids(case)
        source_groups = {
            str(group.get("group_id") or ""): group
            for group in case.get("positive_source_groups") or []
            if isinstance(group, Mapping)
        }
        final_candidates = [dict(value) for value in raw.get("candidates") or []]
        exact_candidates = [dict(value) for value in raw.get("exact_candidates") or []]
        vector_candidates = [dict(value) for value in raw.get("vector_candidates") or []]
        lexical_candidates = [dict(value) for value in raw.get("lexical_candidates") or []]
        group_rows: list[dict[str, Any]] = []
        for group_id in sorted(required_ids):
            group = source_groups.get(group_id)
            if not isinstance(group, Mapping):
                raise ValueError(f"required_source_group_missing:{case_id}:{group_id}")
            sources = [dict(value) for value in group.get("sources") or [] if isinstance(value, Mapping)]
            legal_as_of = str(case.get("legal_as_of") or "")
            temporal_scope = str(case.get("temporal_scope") or "current")
            available = any(
                source_available(source, legal_as_of, temporal_scope)
                for source in sources
            )
            final_rank = _first_rank(final_candidates, sources)
            exact_rank = _first_rank(exact_candidates, sources)
            vector_rank = _first_rank(vector_candidates, sources)
            lexical_rank = _first_rank(lexical_candidates, sources)
            group_row = {
                "case_id": case_id,
                "group_id": group_id,
                "domain": str(case.get("domain") or "unknown"),
                "legal_as_of": legal_as_of,
                "expected_sources": [
                    {
                        "law_number": source.get("law_number"),
                        "article": source.get("article"),
                    }
                    for source in sources
                ],
                "source_available_at_date": available,
                "final_rank": final_rank,
                "exact_rank": exact_rank,
                "vector_rank": vector_rank,
                "lexical_rank": lexical_rank,
                "root_cause": _root_cause(
                    source_available_at_date=available,
                    final_rank=final_rank,
                    exact_rank=exact_rank,
                    vector_rank=vector_rank,
                    lexical_rank=lexical_rank,
                ),
            }
            groups.append(group_row)
            group_rows.append(group_row)
        hit_count = sum(row["root_cause"] == "hit_top10" for row in group_rows)
        if hit_count == len(group_rows):
            classification = "full_coverage"
        elif hit_count:
            classification = "partial_coverage"
        else:
            classification = "no_required_group_coverage"
        cases.append(
            {
                "case_id": case_id,
                "domain": str(case.get("domain") or "unknown"),
                "required_group_count": len(group_rows),
                "hit_group_count": hit_count,
                "classification": classification,
                "root_causes": sorted(
                    {row["root_cause"] for row in group_rows if row["root_cause"] != "hit_top10"}
                ),
            }
        )

    return {
        "case_count": len(cases),
        "group_count": len(groups),
        "case_classification_counts": dict(
            sorted(Counter(row["classification"] for row in cases).items())
        ),
        "group_root_cause_counts": dict(
            sorted(Counter(row["root_cause"] for row in groups).items())
        ),
        "cases": cases,
        "groups": groups,
    }


__all__ = ["analyze_required_group_gaps"]
