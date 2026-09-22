"""Shadow-only candidate policies for Retrieval r22."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from scripts.kaggle_retrieval_v2_benchmark_common import normalize_exact


def expand_legal_query_r22(
    query: str, *, aliases: Sequence[tuple[str, str]]
) -> str:
    """Apply approved aliases with symmetric Unicode/diacritic normalization."""
    normalized_query = normalize_exact(query).casefold()
    additions = [
        canonical
        for phrase, canonical in aliases
        if normalize_exact(phrase).casefold() in normalized_query
    ]
    unique = list(dict.fromkeys(additions))
    return query if not unique else query + " " + " ".join(unique)


def candidate_law_priors_r22(
    *branches: Sequence[Mapping[str, Any]], limit: int = 6
) -> list[str]:
    """Select document routes by earliest independent branch evidence.

    r20 counted repeated chunks, so a long unrelated document could occupy all
    three routes. r22 collapses repeated chunks by normalized law/article and
    allows six bounded document routes. Expected sources and case IDs are never
    consulted.
    """
    evidence: dict[str, dict[str, Any]] = {}
    global_position = 0
    for branch_index, branch in enumerate(branches):
        seen_in_branch: set[tuple[str, str]] = set()
        for rank, candidate in enumerate(branch[:80], start=1):
            global_position += 1
            raw_law = str(candidate.get("law_number") or "").strip()
            law = normalize_exact(raw_law)
            article = normalize_exact(candidate.get("article_number"))
            identity = (law, article)
            if not law or identity in seen_in_branch:
                continue
            seen_in_branch.add(identity)
            row = evidence.setdefault(
                law,
                {
                    "raw": raw_law,
                    "best_rank": rank,
                    "first_position": global_position,
                    "branches": set(),
                },
            )
            row["best_rank"] = min(int(row["best_rank"]), rank)
            row["first_position"] = min(int(row["first_position"]), global_position)
            row["branches"].add(branch_index)
    ordered = sorted(
        evidence.values(),
        key=lambda row: (
            int(row["best_rank"]),
            -len(row["branches"]),
            int(row["first_position"]),
            normalize_exact(row["raw"]),
        ),
    )
    return [str(row["raw"]) for row in ordered[: max(1, int(limit))]]


def reserve_article_identities_before_top_k(
    candidates: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    unique_slots: int | None = None,
    pool_multiplier: int = 4,
) -> list[dict[str, Any]]:
    """Reserve distinct law/article identities before truncating a branch.

    The input must already be ranked. Only a bounded prefix is inspected, and
    no score is recalculated. At most one representative per identity occupies
    the reserved prefix; remaining slots retain their original relative order.
    This prevents long Articles from consuming an entire lexical candidate
    window while preserving their additional chunks for hydration.
    """
    safe_limit = max(1, int(limit))
    target_unique = max(
        1,
        min(
            safe_limit,
            int(unique_slots) if unique_slots is not None else safe_limit // 4,
        ),
    )
    pool_size = min(
        len(candidates), safe_limit * max(1, int(pool_multiplier))
    )
    pool = [dict(item) for item in candidates[:pool_size]]
    tail = [dict(item) for item in candidates[pool_size:]]
    reserved: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in pool:
        identity = (
            normalize_exact(item.get("law_number")),
            normalize_exact(item.get("article_number")),
        )
        if (
            len(reserved) < target_unique
            and identity != ("", "")
            and identity not in seen
        ):
            reserved.append(item)
            seen.add(identity)
        else:
            deferred.append(item)
    return [*reserved, *deferred, *tail][:safe_limit]


__all__ = [
    "candidate_law_priors_r22",
    "expand_legal_query_r22",
    "reserve_article_identities_before_top_k",
]
