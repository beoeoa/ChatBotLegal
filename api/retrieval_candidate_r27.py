"""Shadow-only Retrieval r27 coverage policies.

The module is intentionally query/candidate-only.  It must not inspect Golden
labels, expected sources, case IDs or answer text.  Production remains pinned
to r26 until a checksum-bound benchmark accepts this candidate.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from scripts.kaggle_retrieval_v2_benchmark_common import normalize_exact


R27_CANDIDATE_PROFILE = "r27-shadow-coverage-retry"

_LAW_TOKEN = re.compile(
    r"\b\d{1,5}\s*/\s*\d{4}\s*/\s*[A-ZĐ][A-ZĐ0-9-]*(?:\s*-[A-ZĐ0-9-]+)*\b",
    re.IGNORECASE,
)
_ARTICLE_LIST = re.compile(
    r"\b(?:điều|dieu)\s+"
    r"([0-9]+[a-z]?(?:\s*(?:,|;|/|\bvà\b|\bva\b)\s*[0-9]+[a-z]?)*)(?!\w)",
    re.IGNORECASE,
)
_ARTICLE_VALUE = re.compile(r"[0-9]+[a-z]?", re.IGNORECASE)
_QUOTED_FACET = re.compile(r"[“\"]([^”\"]{8,})[”\"]")
_SUBJECT_FACET = re.compile(
    r"(?:\bvề\b|\bthực\s+hiện\b|\bliên\s+quan\s+đến\b)\s+"
    r"(.+?)(?=(?:,\s*(?:điều\s+luật|nội\s+dung|quy\s+định|cách\s+xử\s+lý)\b)"
    r"|\bxác\s+định\s+bước\b|[?;]|$)",
    re.IGNORECASE | re.DOTALL,
)
_TOKEN = re.compile(r"[0-9a-zA-ZÀ-ỹĐđ]+", re.UNICODE)
_STOPWORDS = {
    "cua", "của", "cho", "toi", "tôi", "can", "cần", "biet", "biết",
    "va", "và", "voi", "với", "the", "thế", "nao", "nào", "duoc", "được",
    "quy", "dinh", "định", "ve", "về", "theo", "tai", "tại", "mot", "một",
}


def explicit_law_article_keys_r27(
    query: str, *, maximum_distance: int = 140
) -> list[str]:
    """Parse explicit law/article citations, including one-law article lists.

    Every article list is paired only with its closest law token.  This avoids
    the unsafe Cartesian product produced by pairing every article with every
    law mentioned in a comparison question.
    """

    text = str(query or "")
    laws = [
        (match.start(), match.end(), normalize_exact(match.group(0)))
        for match in _LAW_TOKEN.finditer(text)
    ]
    groups = [
        (
            match.start(),
            match.end(),
            [normalize_exact(value) for value in _ARTICLE_VALUE.findall(match.group(1))],
        )
        for match in _ARTICLE_LIST.finditer(text)
    ]
    paired: list[tuple[int, int, str]] = []
    for article_start, article_end, articles in groups:
        nearby: list[tuple[int, int, int, str]] = []
        for law_start, law_end, law in laws:
            distance = (
                law_start - article_end
                if law_start >= article_end
                else article_start - law_end
                if article_start >= law_end
                else 0
            )
            if 0 <= distance <= maximum_distance:
                direction = 0 if law_start >= article_end else 1
                nearby.append((direction, distance, law_start, law))
        if not nearby:
            continue
        _, _, law_start, law = min(nearby)
        for article_index, article in enumerate(articles):
            paired.append(
                (min(article_start, law_start), article_index, f"{law}|{article}")
            )
    return list(dict.fromkeys(value for _, _, value in sorted(paired)))


def explicit_law_numbers_r27(query: str) -> list[str]:
    """Return only law numbers authored by the user, in textual order."""

    return list(
        dict.fromkeys(normalize_exact(match.group(0)) for match in _LAW_TOKEN.finditer(str(query or "")))
    )


def extract_query_facets_r27(query: str, *, maximum: int = 6) -> list[str]:
    """Extract user-authored issue facets without consulting benchmark labels."""

    text = str(query or "")
    quoted = [match.group(1).strip() for match in _QUOTED_FACET.finditer(text)]
    if quoted:
        return list(dict.fromkeys(quoted))[:maximum]

    # A plain legal question often names the subject after ``về`` or
    # ``thực hiện`` and then appends answer boilerplate.  Treat that bounded
    # subject as one facet so an exact heading already in the candidate pool
    # can be promoted.  The expression is deliberately conservative: it does
    # not split arbitrary conjunctions or infer a source identifier.
    subject = _SUBJECT_FACET.search(text)
    if subject:
        value = re.sub(r"^\s*\d+[.)]\s*", "", subject.group(1))
        value = re.sub(r"\s+", " ", value).strip(" .,:;-")
        if len(_content_tokens(value)) >= 2:
            return [value][:maximum]

    # Semicolons and explicit numbered bullets are strong issue boundaries.
    parts = re.split(r"\s*(?:;|\n\s*\d+[.)]\s+)\s*", text)
    facets = [part.strip(" .,:;-") for part in parts if len(part.strip()) >= 12]
    return list(dict.fromkeys(facets))[:maximum] if len(facets) > 1 else []


def _content_tokens(value: object) -> set[str]:
    tokens = {
        normalize_exact(token).casefold()
        for token in _TOKEN.findall(str(value or ""))
    }
    return {token for token in tokens if len(token) >= 3 and token not in _STOPWORDS}


def _candidate_identity(candidate: Mapping[str, Any]) -> tuple[str, str]:
    return (
        normalize_exact(candidate.get("law_number")),
        normalize_exact(candidate.get("article_number")),
    )


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("chunk_revision_id") or "")


def rerank_candidates_r27(
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    *,
    candidate_window: int = 60,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Reserve Top-K coverage for strong query-derived anchors.

    The function never creates evidence. It only moves candidates already in
    the bounded pool and otherwise preserves their relative order.
    """

    base = [dict(item) for item in candidates]
    if not base:
        return []

    anchors: list[dict[str, Any]] = []
    seen: set[str] = set()
    by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    for item in base[:candidate_window]:
        identity = _candidate_identity(item)
        if identity != ("", "") and identity not in by_identity:
            by_identity[identity] = item

    for key in explicit_law_article_keys_r27(query):
        law, article = key.split("|", 1)
        item = by_identity.get((law, article))
        if item is not None and _candidate_id(item) not in seen:
            anchors.append(item)
            seen.add(_candidate_id(item))

    for facet in extract_query_facets_r27(query):
        facet_tokens = _content_tokens(facet)
        if len(facet_tokens) < 2:
            continue
        best: tuple[float, int, dict[str, Any]] | None = None
        for index, item in enumerate(base[:candidate_window]):
            identifier = _candidate_id(item)
            if not identifier or identifier in seen:
                continue
            searchable = " ".join(
                str(item.get(field) or "")
                for field in ("structural_path", "article_title", "document_title")
            )
            overlap = len(facet_tokens & _content_tokens(searchable))
            ratio = overlap / len(facet_tokens)
            if overlap < 2 or ratio < 0.5:
                continue
            # An exact normalized heading match is a stronger, still
            # query-derived signal than generic token overlap.  It promotes
            # an authoritative article already present in the pool without
            # inventing a source or consulting benchmark labels.
            phrase_match = normalize_exact(facet) in normalize_exact(searchable)
            score = (2.0 if phrase_match else 0.0) + ratio + overlap / 100.0
            if best is None or (score, -index) > (best[0], -best[1]):
                best = (score, index, item)
        if best is not None:
            anchors.append(best[2])
            seen.add(_candidate_id(best[2]))

    if not anchors:
        return base

    # Do not let anchors enlarge Top-K: reserve at most half of it, then retain
    # the original ranking for all unreserved candidates.
    anchors = anchors[: max(1, top_k // 2)]
    anchor_ids = {_candidate_id(item) for item in anchors}
    return [*anchors, *(item for item in base if _candidate_id(item) not in anchor_ids)]


__all__ = [
    "R27_CANDIDATE_PROFILE",
    "explicit_law_article_keys_r27",
    "explicit_law_numbers_r27",
    "extract_query_facets_r27",
    "rerank_candidates_r27",
]
