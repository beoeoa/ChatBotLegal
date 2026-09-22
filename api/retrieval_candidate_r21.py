"""Shadow-only Retrieval r21 policies; never imported by frozen r20 serving."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from scripts.kaggle_retrieval_v2_benchmark_common import fuse, normalize_exact


_LAW_TOKEN = re.compile(
    r"\b\d{1,5}\s*/\s*\d{4}\s*/\s*[A-ZĐ][A-ZĐ0-9-]*(?:\s*-[A-ZĐ0-9-]+)*\b",
    re.IGNORECASE,
)
_ARTICLE_TOKEN = re.compile(r"\b(?:điều|dieu)\s+([0-9]+[a-z]?)\b", re.IGNORECASE)


def explicit_law_article_keys(query: str, *, maximum_distance: int = 120) -> list[str]:
    """Pair nearby explicit law/article tokens without a Cartesian product."""
    laws = [
        (match.start(), match.end(), normalize_exact(match.group(0)))
        for match in _LAW_TOKEN.finditer(str(query or ""))
    ]
    articles = [
        (match.start(), match.end(), normalize_exact(match.group(1)))
        for match in _ARTICLE_TOKEN.finditer(str(query or ""))
    ]
    pairs: list[tuple[int, str]] = []
    for article_start, article_end, article in articles:
        nearby = []
        for law_start, law_end, law in laws:
            distance = (
                law_start - article_end
                if law_start >= article_end
                else article_start - law_end
                if article_start >= law_end
                else 0
            )
            if 0 <= distance <= maximum_distance:
                # Vietnamese citations most commonly use ``Điều X [của]
                # văn bản Y``. Prefer the following law token; use a preceding
                # token only for the inverse ``văn bản Y, Điều X`` form.
                direction = 0 if law_start >= article_end else 1
                nearby.append((direction, distance, law_start, law))
        if nearby:
            _, _, law_start, law = min(nearby)
            pairs.append((min(article_start, law_start), f"{law}|{article}"))
    return list(dict.fromkeys(value for _, value in sorted(pairs)))


def diversify_legal_identities(
    candidates: Sequence[Mapping[str, Any]], *, window: int = 20, slots: int = 10
) -> list[dict[str, Any]]:
    """Reserve breadth in the final evidence without widening retrieval.

    Only the already-ranked top ``window`` is considered.  The first candidate
    for each distinct law/article identity is reserved before duplicate chunks;
    all remaining candidates retain their original relative order.  This avoids
    one long Article occupying every final evidence slot while keeping its
    additional chunks available for deterministic parent/packet hydration.
    """
    bounded = [dict(item) for item in candidates[:window]]
    tail = [dict(item) for item in candidates[window:]]
    reserved: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in bounded:
        identity = (
            normalize_exact(item.get("law_number")),
            normalize_exact(item.get("article_number")),
        )
        if (
            len(reserved) < slots
            and identity != ("", "")
            and identity not in seen
        ):
            reserved.append(item)
            seen.add(identity)
        else:
            deferred.append(item)
    return [*reserved, *deferred, *tail]


def exact_candidates_for_r21(
    exact: Sequence[Mapping[str, Any]],
    vector: Sequence[Mapping[str, Any]],
    lexical: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep exact precision while preventing law-number-only candidate floods.

    A law+article identity remains a deterministic exact hit. A law-number-only
    hit receives the exact bonus only when the same immutable chunk also has
    semantic or lexical support. Article-only identities remain audit-only.
    This policy uses no Golden labels, case IDs, expected sources or answers.
    """
    supported_ids = {
        str(item.get("chunk_revision_id") or "")
        for item in [*vector, *lexical]
        if str(item.get("chunk_revision_id") or "")
    }
    selected: list[dict[str, Any]] = []
    for item in exact:
        specificity = str(item.get("exact_specificity") or "")
        identifier = str(item.get("chunk_revision_id") or "")
        if specificity == "law_article" or (
            specificity == "law_number" and identifier in supported_ids
        ):
            selected.append(dict(item))
    return selected


def _prepend_unique(
    preferred: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in [*preferred, *candidates]:
        item = dict(raw)
        identifier = str(item.get("chunk_revision_id") or "")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        output.append(item)
    return output


def rerank_cache_row_r21(
    row: Mapping[str, Any], *, full_query_exact: Sequence[Mapping[str, Any]] = ()
) -> list[dict[str, Any]]:
    """Replay only the r21 exact-bonus policy from a frozen full-branch row."""
    case = row.get("case") or {}
    if not bool(case.get("answer_required")):
        return []
    # The cache stores flattened issue branches. Preserve the byte-identical
    # r20 result for multi-issue rows so this experiment changes one variable.
    if len(case.get("issue_groups") or []) > 1:
        anchored = _prepend_unique(full_query_exact, row.get("candidates") or [])
        return diversify_legal_identities(anchored)
    config = row.get("selected_config") or {}
    vector = [dict(item) for item in row.get("vector_candidates") or []][
        : int(config.get("vector_top_k") or 80)
    ]
    lexical = [dict(item) for item in row.get("lexical_candidates") or []][
        : int(config.get("lexical_top_k") or 80)
    ]
    exact = exact_candidates_for_r21(
        [
            *[dict(item) for item in full_query_exact],
            *[dict(item) for item in row.get("exact_candidates") or []],
        ],
        vector,
        lexical,
    )
    fused = fuse(
        exact,
        vector,
        lexical,
        strategy=str(config.get("fusion_strategy") or "weighted"),
        vector_weight=float(config.get("vector_weight") or 0.7),
        lexical_weight=float(config.get("lexical_weight") or 0.3),
    )
    return diversify_legal_identities(fused)


__all__ = [
    "diversify_legal_identities",
    "exact_candidates_for_r21",
    "explicit_law_article_keys",
    "rerank_cache_row_r21",
]
