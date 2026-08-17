"""Deterministic quality and ranking helpers for the legal corpus.

The helpers in this module do not mutate the corpus.  They produce sidecar
decisions, normalized text and bounded ranking selections that can be audited
and rolled back independently from source records.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

QUALITY_VERSION = "legal-chunk-quality-v1"
_SPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_vietnamese_search_text(value: Any) -> str:
    """Return the exact accent-insensitive representation used by retrieval."""

    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = text.replace("đ", "d")
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return " ".join(_TOKEN_RE.findall(text))


def content_hash(content: Any) -> str:
    normalized = normalize_vietnamese_search_text(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def clean_article_title(article_number: Any, title: Any) -> str:
    """Keep an article number and a short genuine title, never prior body text."""

    number = _SPACE_RE.sub(" ", str(article_number or "")).strip()
    number = re.sub(r"(?i)^điều\s+", "", number).strip(" .:")
    prefix = f"Điều {number}" if number else "Điều"
    clean = _SPACE_RE.sub(" ", str(title or "")).strip(" .:")
    clean = re.sub(r"(?i)^điều\s+[0-9]+[a-z]?\s*[.:\-]?\s*", "", clean).strip()
    # Long titles, multiple sentence-like clauses and embedded article markers
    # are characteristic of the observed parser spill-over. Fail closed to the
    # article number instead of embedding guessed text.
    suspicious = (
        not clean
        or len(clean) > 180
        or clean.count(". ") > 1
        or bool(re.search(r"(?i)\bđiều\s+[0-9]+[a-z]?\b", clean))
        or len(clean.split()) > 28
    )
    return prefix if suspicious else f"{prefix}. {clean}"


@dataclass(frozen=True)
class ChunkQualityAssessment:
    chunk_id: int | str
    quality_version: str
    eligible: bool
    canonical_chunk_id: int | str | None
    content_hash: str
    cleaned_article_title: str
    quality_reasons: tuple[str, ...]


def assess_chunk_quality(
    *,
    chunk_id: int | str,
    content: Any,
    article_number: Any,
    article_title: Any,
    canonical_chunk_id: int | str | None = None,
    has_required_metadata: bool = True,
    effectivity_known: bool = True,
) -> ChunkQualityAssessment:
    reasons: list[str] = []
    normalized = normalize_vietnamese_search_text(content)
    if not normalized:
        reasons.append("empty_content")
    if canonical_chunk_id is not None and str(canonical_chunk_id) != str(chunk_id):
        reasons.append("exact_duplicate")
    if not has_required_metadata:
        reasons.append("missing_required_metadata")
    if not effectivity_known:
        reasons.append("unknown_effectivity")
    cleaned_title = clean_article_title(article_number, article_title)
    if cleaned_title != f"Điều {str(article_number or '').removeprefix('Điều ').strip()}" and len(str(article_title or "")) > 180:
        reasons.append("noisy_article_title")
    elif len(str(article_title or "")) > 180:
        reasons.append("noisy_article_title")
    return ChunkQualityAssessment(
        chunk_id=chunk_id,
        quality_version=QUALITY_VERSION,
        eligible=not any(
            reason in {
                "empty_content",
                "exact_duplicate",
                "missing_required_metadata",
                "unknown_effectivity",
            }
            for reason in reasons
        ),
        canonical_chunk_id=canonical_chunk_id,
        content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        cleaned_article_title=cleaned_title,
        quality_reasons=tuple(reasons),
    )


def reciprocal_rank_fusion(
    *,
    vector_ranked: Sequence[Mapping[str, Any]],
    lexical_ranked: Sequence[Mapping[str, Any]],
    key: str = "chunk_id",
    rank_constant: int = 60,
) -> list[dict[str, Any]]:
    """Fuse independent rankings without assuming comparable raw scores."""

    merged: dict[str, dict[str, Any]] = {}
    first_seen: dict[str, int] = {}
    order = 0
    for source_name, rows in (("vector", vector_ranked), ("lexical", lexical_ranked)):
        for rank, row in enumerate(rows, start=1):
            identifier = str(row.get(key) or "")
            if not identifier:
                continue
            if identifier not in merged:
                merged[identifier] = dict(row)
                merged[identifier]["rrf_score"] = 0.0
                merged[identifier]["retrieval_sources"] = []
                first_seen[identifier] = order
                order += 1
            merged[identifier]["rrf_score"] += 1.0 / (rank_constant + rank)
            merged[identifier]["retrieval_sources"].append(source_name)
    return sorted(
        merged.values(),
        key=lambda item: (-float(item["rrf_score"]), first_seen[str(item[key])]),
    )


def hybrid_fusion_score(
    *,
    strategy: str,
    vector_score: float,
    normalized_lexical_score: float,
    rrf_score: float,
    vector_weight: float,
    lexical_weight: float,
) -> float:
    """Return one auditable M5 fusion score without mixing strategies."""

    if strategy == "rrf":
        return float(rrf_score)
    if strategy == "weighted":
        return (
            float(vector_score) * float(vector_weight)
            + float(normalized_lexical_score) * float(lexical_weight)
        )
    raise ValueError(f"unsupported_hybrid_fusion_strategy:{strategy}")


def rank_candidates_rrf_v2(
    candidates: Sequence[Mapping[str, Any]],
    *,
    key: str = "chunk_id",
) -> list[dict[str, Any]]:
    """Rank a hydrated candidate set using independent vector/BM25 ranks.

    Raw vector and BM25 values are deliberately never added together. Lexical
    and topic boosts remain zero-valued compatibility fields. Stable structural
    identity breaks ties so repeated runs return the same order.
    """

    rows = [dict(item) for item in candidates]

    def identity(item: Mapping[str, Any]) -> str:
        return str(
            item.get(key)
            or item.get("canonical_chunk_id")
            or item.get("source_id")
            or ""
        )

    exact = sorted(
        (item for item in rows if item.get("retrieval_source") == "exact_metadata"),
        key=lambda item: (
            int(item.get("chunk_index") or 0),
            identity(item),
        ),
    )
    non_exact = [item for item in rows if item.get("retrieval_source") != "exact_metadata"]
    vector_ranked = sorted(
        (
            item
            for item in non_exact
            if "vector" in set(item.get("retrieval_sources") or [item.get("retrieval_source")])
        ),
        key=lambda item: (-float(item.get("vector_score") or 0.0), identity(item)),
    )
    bm25_ranked = sorted(
        (item for item in non_exact if float(item.get("bm25_score") or 0.0) > 0.0),
        key=lambda item: (-float(item.get("bm25_score") or 0.0), identity(item)),
    )
    fused = reciprocal_rank_fusion(
        vector_ranked=vector_ranked,
        lexical_ranked=bm25_ranked,
        key=key,
    )
    fused_ids = {identity(item) for item in fused}
    remaining = sorted(
        (item for item in non_exact if identity(item) not in fused_ids),
        key=identity,
    )
    exact_base = max((float(item.get("rrf_score") or 0.0) for item in fused), default=0.0)
    ranked: list[dict[str, Any]] = []
    for offset, item in enumerate(exact):
        exact_score = exact_base + 1.0 - (offset * 0.000001)
        ranked.append(
            {
                **item,
                "rrf_score": round(exact_score, 12),
                "score": round(exact_score, 12),
                "lexical_boost": 0.0,
                "topic_boost": 0.0,
                "ranking_strategy": "rrf_v2",
            }
        )
    for item in [*fused, *remaining]:
        rrf_score = float(item.get("rrf_score") or 0.0)
        ranked.append(
            {
                **item,
                "rrf_score": round(rrf_score, 12),
                "score": round(rrf_score, 12),
                "lexical_boost": 0.0,
                "topic_boost": 0.0,
                "ranking_strategy": "rrf_v2",
            }
        )
    return ranked


def diversify_ranked_candidates(
    candidates: Iterable[Mapping[str, Any]],
    *,
    limit: int,
    max_per_article: int = 2,
    max_per_document: int = 3,
) -> list[dict[str, Any]]:
    """Select a bounded context with document and article diversity."""

    if limit < 1:
        return []
    ranked = sorted(
        (dict(item) for item in candidates),
        key=lambda item: float(item.get("rerank_score") or item.get("score") or item.get("rrf_score") or 0),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    doc_counts: dict[str, int] = {}
    article_counts: dict[tuple[str, str], int] = {}
    for item in ranked:
        document = str(item.get("document_id") or item.get("law_number") or item.get("document_title") or "unknown")
        article = str(item.get("article_id") or item.get("article_number") or item.get("chunk_id") or "")
        article_key = (document, article)
        if (
            doc_counts.get(document, 0) >= max_per_document
            or article_counts.get(article_key, 0) >= max_per_article
        ):
            continue
        selected.append(item)
        doc_counts[document] = doc_counts.get(document, 0) + 1
        article_counts[article_key] = article_counts.get(article_key, 0) + 1
        if len(selected) == limit:
            return selected
    return selected
