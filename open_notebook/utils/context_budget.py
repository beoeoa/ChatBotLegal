"""Deterministic notebook context retrieval and token budgeting."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from open_notebook.utils.token_utils import token_count


_QUERY_STOPWORDS = {
    "anh",
    "ban",
    "cho",
    "co",
    "cua",
    "duoc",
    "gi",
    "hay",
    "khong",
    "la",
    "mot",
    "nay",
    "nhung",
    "quy",
    "toi",
    "trong",
    "van",
    "ve",
}


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d").replace("Đ", "D").casefold()


def _query_terms(query: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", _fold(query))
        if len(term) >= 3 and term not in _QUERY_STOPWORDS
    }


def query_relevant_excerpt(
    text: str,
    query: str,
    *,
    max_chars: int = 12_000,
) -> str:
    """Select deterministic source passages related to the current question.

    Newly linked legal records can have full text before insight generation or
    vector indexing finishes. This local ranking path makes that text usable
    immediately without an extra model call and without sending the entire
    document to a local chat model.
    """

    normalized = str(text or "").strip()
    if not normalized or len(normalized) <= max_chars:
        return normalized

    paragraphs = [
        piece.strip()
        for piece in re.split(r"\n{2,}", normalized)
        if piece.strip()
    ]
    chunks: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= 2_400:
            chunks.append(paragraph)
            continue
        sentences = [
            piece.strip()
            for piece in re.split(r"(?<=[.!?;:])\s+|\n+", paragraph)
            if piece.strip()
        ]
        current = ""
        for sentence in sentences:
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > 2_400:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)

    if not chunks:
        return normalized[:max_chars].rstrip()

    terms = _query_terms(query)
    folded_query = _fold(query).strip()

    def score(index_and_chunk: tuple[int, str]) -> tuple[int, int, int, int]:
        index, chunk = index_and_chunk
        folded = _fold(chunk)
        term_hits = sum(folded.count(term) for term in terms)
        phrase_hit = 1 if len(folded_query) >= 8 and folded_query in folded else 0
        heading_bonus = 1 if index == 0 or chunk.lstrip().startswith(("#", "Điều ")) else 0
        return (-phrase_hit, -term_hits, -heading_bonus, index)

    ranked = sorted(enumerate(chunks), key=score)
    selected: list[tuple[int, str]] = []
    used = 0
    # Preserve document identity even when the question matches a later clause.
    lead = chunks[0]
    lead_budget = min(len(lead), min(1_600, max_chars // 4))
    selected.append((0, lead[:lead_budget].rstrip()))
    used += lead_budget

    for index, chunk in ranked:
        if index == 0:
            continue
        separator_cost = 2
        remaining = max_chars - used - separator_cost
        if remaining < 200:
            break
        chosen = chunk if len(chunk) <= remaining else chunk[:remaining].rstrip()
        selected.append((index, chosen))
        used += len(chosen) + separator_cost
        if len(chosen) < len(chunk):
            break

    selected.sort(key=lambda item: item[0])
    return "\n\n".join(chunk for _, chunk in selected if chunk).strip()


def bounded_context(
    value: Any,
    *,
    query: str = "",
    max_tokens: int = 18_000,
) -> tuple[Any, int, bool]:
    """Return query-ranked chunks instead of serializing a huge notebook."""
    rendered = json.dumps(value, ensure_ascii=False, default=str)
    count = token_count(rendered)
    if count <= max_tokens:
        return value, count, False

    terms = _query_terms(query)
    pieces = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|\n{2,}", rendered)
        if part.strip()
    ]
    ranked = sorted(
        enumerate(pieces),
        key=lambda item: (
            -sum(term in _fold(item[1]) for term in terms),
            item[0],
        ),
    )
    selected: list[tuple[int, str]] = []
    used = 0
    for index, piece in ranked:
        piece_tokens = token_count(piece)
        if used + piece_tokens > max_tokens:
            remaining_chars = max(0, (max_tokens - used) * 4)
            if remaining_chars >= 400:
                selected.append((index, piece[:remaining_chars]))
            break
        selected.append((index, piece))
        used += piece_tokens
    selected.sort(key=lambda item: item[0])
    chunks = [text for _, text in selected]
    result = {
        "retrieved_chunks": chunks,
        "context_policy": {
            "mode": "query_relevant_chunks",
            "max_tokens": max_tokens,
            "original_tokens": count,
        },
    }
    return result, token_count(json.dumps(result, ensure_ascii=False)), True


__all__ = ["bounded_context", "query_relevant_excerpt"]
