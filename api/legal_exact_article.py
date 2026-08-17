"""Deterministic assembly and integrity checks for an exact legal Article.

An exact ``document + Article`` request is not a relevance-ranking problem.
This module restores every stored child chunk to source order, compares the
packet with the stored parent body, and exposes a fail-closed readiness state
before any answer model may see the evidence.
"""

from __future__ import annotations

import re
import unicodedata
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from api.legal_structural_chunking import parse_structural_path

DEFAULT_MAX_EXACT_ARTICLE_CHUNKS = 1_000
DEFAULT_MAX_EXACT_ARTICLE_CHARS = 12_000

_TERMINAL_ATTACHMENT_RE = re.compile(
    r"(?im)^\s*(?:\|\s*)?(?:nơi\s+nhận\s*:|mẫu\s+[a-z0-9./-]+\s+ban\s+hành\s+kèm)",
)

_CLAUSE_LINE = re.compile(r"^\s*(?P<number>\d+[a-zA-Z]?)[.)](?:\s+|$)")
_POINT_LINE = re.compile(r"^\s*(?P<number>[a-zA-ZđĐ])[)](?:\s+|$)")


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _identity(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _fold_tokens(value: Any) -> tuple[str, ...]:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("đ", "d")
    return tuple(re.findall(r"[a-z0-9]+", text))


def _line_shards(value: Any, *, width: int = 5) -> set[tuple[str, ...]]:
    shards: set[tuple[str, ...]] = set()
    for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        # Official Gazette PDF extraction commonly leaves a bare page number
        # between two legal clauses.  Structural chunking deliberately drops
        # that layout artifact.  It is not a legal unit and must not make an
        # otherwise complete Article fail its parent-coverage integrity gate.
        if re.fullmatch(r"\s*\d{1,4}\s*", line):
            continue
        tokens = _fold_tokens(line)
        if not tokens:
            continue
        if len(tokens) <= width:
            shards.add(tokens)
            continue
        shards.update(
            tuple(tokens[index : index + width])
            for index in range(len(tokens) - width + 1)
        )
    return shards


def _structural_units(value: Any) -> list[str]:
    units: list[str] = []
    current_clause: str | None = None
    for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        clause = _CLAUSE_LINE.match(raw_line)
        if clause:
            current_clause = clause.group("number").casefold()
            label = f"Khoản {current_clause}"
            if label not in units:
                units.append(label)
            continue
        point = _POINT_LINE.match(raw_line)
        if point:
            point_number = point.group("number").casefold()
            label = (
                f"Khoản {current_clause} > Điểm {point_number}"
                if current_clause
                else f"Điểm {point_number}"
            )
            if label not in units:
                units.append(label)
    return units


def _row_structural_units(row: Mapping[str, Any]) -> set[str]:
    units = set(_structural_units(row.get("content")))
    parsed = parse_structural_path(str(row.get("chunk_heading") or ""))
    clause = _identity(parsed.get("clause_number")).casefold()
    point = _identity(parsed.get("point_number")).casefold()
    if clause:
        units.add(f"Khoản {clause}")
    if point:
        units.add(f"Khoản {clause} > Điểm {point}" if clause else f"Điểm {point}")
    return units


def _strip_terminal_attachment_rows(
    ordered: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, bool]:
    """Remove a form/dispatch footer appended to the final legal Article.

    Some official imports attach every form after the signature to the final
    Article. The boundary is deterministic, so exclude that non-Article tail
    at read time without rewriting stored source rows or vectors.
    """

    if not ordered:
        return [], 0, False
    parent_candidates = [
        _identity(row.get("article_content"))
        for row in ordered
        if _identity(row.get("article_content"))
    ]
    canonical_parent = max(parent_candidates, key=len) if parent_candidates else ""
    parent_match = _TERMINAL_ATTACHMENT_RE.search(canonical_parent)
    cleaned_parent = (
        canonical_parent[: parent_match.start()].rstrip()
        if parent_match
        else canonical_parent
    )
    cleaned: list[dict[str, Any]] = []
    boundary_detected = False
    for row in ordered:
        content = _identity(row.get("content"))
        match = _TERMINAL_ATTACHMENT_RE.search(content)
        if match:
            boundary_detected = True
            content = content[: match.start()].rstrip()
            if content:
                clean_row = dict(row)
                clean_row["content"] = content
                if cleaned_parent:
                    clean_row["article_content"] = cleaned_parent
                cleaned.append(clean_row)
            break
        clean_row = dict(row)
        if cleaned_parent:
            clean_row["article_content"] = cleaned_parent
        cleaned.append(clean_row)
    if not boundary_detected and parent_match:
        boundary_detected = True
    if not boundary_detected:
        return ordered, 0, False
    for index, row in enumerate(cleaned):
        row["exact_article_chunk_count"] = len(cleaned)
        row["chunk_index"] = index
    return cleaned, max(0, len(ordered) - len(cleaned)), True


def is_single_exact_article_plan(plan: Any) -> bool:
    pairs = tuple(getattr(plan, "article_law_pairs", ()) or ())
    if len(pairs) == 1:
        return True
    laws = tuple(getattr(plan, "law_numbers", ()) or ())
    articles = tuple(getattr(plan, "article_numbers", ()) or ())
    if not laws and getattr(plan, "law_number", None):
        laws = (getattr(plan, "law_number"),)
    if not articles and getattr(plan, "article_number", None):
        articles = (getattr(plan, "article_number"),)
    return len(laws) == 1 and len(articles) == 1


def build_exact_article_packet(
    rows: Iterable[Mapping[str, Any]],
    *,
    max_chunks: int = DEFAULT_MAX_EXACT_ARTICLE_CHUNKS,
    max_chars: int = DEFAULT_MAX_EXACT_ARTICLE_CHARS,
) -> dict[str, Any]:
    """Return a source-ordered Article packet with explicit missing evidence."""

    materialized = [dict(row) for row in rows]
    ordered = sorted(
        materialized,
        key=lambda row: (
            _integer(row.get("chunk_index"))
            if _integer(row.get("chunk_index")) is not None
            else 2**31,
            _integer(row.get("chunk_id"))
            if _integer(row.get("chunk_id")) is not None
            else 2**31,
        ),
    )
    ordered, excluded_attachment_chunks, attachment_boundary_detected = (
        _strip_terminal_attachment_rows(ordered)
    )
    if not ordered:
        return {
            "status": "incomplete",
            "reason_codes": ["exact_article_not_found"],
            "ordered_rows": [],
            "ordered_chunk_ids": [],
            "loaded_chunk_count": 0,
            "expected_chunk_count": 0,
            "missing_chunk_indexes": [],
            "duplicate_chunk_indexes": [],
            "empty_chunk_ids": [],
            "missing_structural_units": [],
            "missing_content_segments": [],
            "content_coverage_ratio": 0.0,
            "assembled_content": "",
        }

    first = ordered[0]
    identities = {
        (
            _identity(row.get("document_id")),
            _identity(row.get("article_id")),
            _identity(row.get("law_number")).casefold(),
            _identity(row.get("article_number")).casefold(),
        )
        for row in ordered
    }
    parent_values = {
        _identity(row.get("article_content"))
        for row in ordered
        if _identity(row.get("article_content"))
    }
    parent_content = max(parent_values, key=len) if parent_values else ""
    indexes = [
        value
        for row in ordered
        if (value := _integer(row.get("chunk_index"))) is not None
    ]
    index_counts = Counter(indexes)
    duplicate_indexes = sorted(
        index for index, count in index_counts.items() if count > 1
    )
    declared_counts = [
        value
        for row in ordered
        if (value := _integer(row.get("exact_article_chunk_count"))) is not None
    ]
    expected_count = max(declared_counts, default=len(ordered))
    missing_indexes = sorted(set(range(max(0, expected_count))) - set(indexes))
    empty_chunk_ids = [
        _integer(row.get("chunk_id")) or 0
        for row in ordered
        if not _identity(row.get("content"))
    ]

    expected_units = _structural_units(parent_content)
    actual_units: set[str] = set()
    for row in ordered:
        actual_units.update(_row_structural_units(row))
    missing_units = [unit for unit in expected_units if unit not in actual_units]

    parent_shards = _line_shards(parent_content)
    child_shards: set[tuple[str, ...]] = set()
    for row in ordered:
        child_shards.update(_line_shards(row.get("content")))
    missing_shards = parent_shards - child_shards
    coverage_ratio = (
        (len(parent_shards) - len(missing_shards)) / len(parent_shards)
        if parent_shards
        else 0.0
    )
    missing_segments: list[str] = []
    if missing_shards:
        for line in parent_content.splitlines():
            line_shards = _line_shards(line)
            if line_shards and not line_shards.issubset(child_shards):
                missing_segments.append(line.strip()[:160])
            if len(missing_segments) >= 20:
                break
    if not missing_shards:
        # Legacy chunks can start midway through a Khoản, so a persisted
        # heading may say only ``Điểm g`` while the parent path is
        # ``Khoản 1 > Điểm g``. Full line-shard coverage is stronger evidence
        # that the unit is present; do not create a metadata-only false gap.
        missing_units = []

    reason_codes: list[str] = []
    if len(identities) != 1:
        reason_codes.append("mixed_document_or_article_identity")
    if len(parent_values) > 1:
        reason_codes.append("conflicting_parent_content")
    if len(set(declared_counts)) > 1:
        reason_codes.append("conflicting_declared_chunk_count")
    if not parent_content:
        reason_codes.append("missing_parent_content")
    if len(ordered) != expected_count or missing_indexes:
        reason_codes.append("missing_chunk_indexes")
    if duplicate_indexes:
        reason_codes.append("duplicate_chunk_indexes")
    if empty_chunk_ids:
        reason_codes.append("empty_chunks")
    if missing_units:
        reason_codes.append("missing_structural_units")
    if parent_shards and missing_shards:
        reason_codes.append("incomplete_parent_coverage")
    if len(ordered) > max(1, int(max_chunks)):
        reason_codes.append("too_many_article_chunks")
    if len(parent_content) > max(1, int(max_chars)):
        reason_codes.append("article_context_too_large")

    return {
        "status": "complete" if not reason_codes else "incomplete",
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "packet_ref": (
            f"document:{_identity(first.get('document_id'))}:"
            f"article:{_identity(first.get('article_id'))}"
        ),
        "document_id": first.get("document_id"),
        "article_id": first.get("article_id"),
        "law_number": first.get("law_number"),
        "article_number": first.get("article_number"),
        "ordered_rows": ordered,
        "ordered_chunk_ids": [row.get("chunk_id") for row in ordered],
        "loaded_chunk_count": len(ordered),
        "expected_chunk_count": expected_count,
        "missing_chunk_indexes": missing_indexes,
        "duplicate_chunk_indexes": duplicate_indexes,
        "empty_chunk_ids": empty_chunk_ids,
        "missing_structural_units": missing_units,
        "missing_content_segments": missing_segments,
        "content_coverage_ratio": round(coverage_ratio, 4),
        "attachment_boundary_detected": attachment_boundary_detected,
        "excluded_attachment_chunk_count": excluded_attachment_chunks,
        # The stored Article body is the canonical source-order reconstruction.
        # Every stored chunk must cover it before the packet can become complete.
        "assembled_content": parent_content,
    }


def _requests_full_article(value: Any) -> bool:
    tokens = " ".join(_fold_tokens(value))
    return any(
        marker in tokens
        for marker in (
            "day du dieu",
            "toan bo dieu",
            "nguyen van dieu",
            "dieu nay gom nhung noi dung",
            "gom nhung noi dung nao",
            "giu dung thu tu khoan diem",
        )
    )


def _exact_article_focus_query(value: Any) -> str:
    """Prefer the reviewed ``phần N: ...`` facet for long-Article windows.

    Multi-issue Golden questions often quote a document title followed by a
    short passage in parentheses.  Scoring the whole title and routing prose
    can select a generic opening window of a very long amendment Article and
    miss that passage.  The facet is only a retrieval-window hint; identity,
    packet completeness and all downstream gates remain unchanged.
    """

    text = str(value or "")
    facets = re.findall(
        r"\(\s*phần\s+\d+\s*:\s*(?P<facet>[^)]*)\)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if facets:
        focused = " ".join(item.strip() for item in facets if item.strip())
        if focused:
            return focused
    return text


def _long_article_chunk_scores(
    ordered: list[Mapping[str, Any]], query: Any
) -> list[float]:
    query_tokens = [token for token in _fold_tokens(query) if len(token) > 1]
    if not query_tokens:
        return [0.0 for _ in ordered]
    token_sets = [set(_fold_tokens(row.get("content"))) for row in ordered]
    document_frequency = Counter(
        token for token_set in token_sets for token in token_set
    )
    query_set = set(query_tokens)
    denominator = sum(
        math.log((len(ordered) + 1) / (document_frequency[token] + 1)) + 1
        for token in query_set
    ) or 1.0
    scores: list[float] = []
    for token_set in token_sets:
        overlap = query_set.intersection(token_set)
        weighted = sum(
            math.log((len(ordered) + 1) / (document_frequency[token] + 1)) + 1
            for token in overlap
        )
        scores.append(weighted / denominator)
    return scores


def build_exact_article_serving_packet(
    rows: Iterable[Mapping[str, Any]],
    *,
    query: Any,
    max_chunks: int = DEFAULT_MAX_EXACT_ARTICLE_CHUNKS,
    max_chars: int = DEFAULT_MAX_EXACT_ARTICLE_CHARS,
) -> dict[str, Any]:
    """Verify every child, then serve a bounded window for a very long Article.

    A request for the complete Article still fails closed when the source body
    cannot fit in one generation context.  A request for one concrete rule may
    use a source-ordered relevant window only after *all* stored chunks passed
    the normal identity, order, structural-unit and parent-coverage checks.
    """

    packet = build_exact_article_packet(
        rows,
        max_chunks=max_chunks,
        max_chars=max_chars,
    )
    if packet.get("status") == "complete":
        return {
            **packet,
            "serving_mode": "complete_article",
            "full_article_character_count": len(
                str(packet.get("assembled_content") or "")
            ),
            "selected_chunk_ids": list(packet.get("ordered_chunk_ids") or []),
            "omitted_chunk_count": 0,
            "context_coverage_ratio": 1.0,
        }
    if set(packet.get("reason_codes") or []) != {"article_context_too_large"}:
        return packet
    if _requests_full_article(query):
        return {
            **packet,
            "serving_mode": "full_article_too_large_fail_closed",
        }

    ordered = list(packet.get("ordered_rows") or [])
    scores = _long_article_chunk_scores(
        ordered,
        _exact_article_focus_query(query),
    )
    if not ordered or not scores or max(scores) <= 0:
        return {
            **packet,
            "serving_mode": "long_article_relevant_window_not_found",
        }
    best = max(range(len(scores)), key=lambda index: (scores[index], -index))
    # Reserve space for the explicit scope notice added by the answer layer.
    context_budget = max(200, int(max_chars) - 700)
    selected = {best}
    used = len(_identity(ordered[best].get("content")))
    if used > context_budget:
        return {
            **packet,
            "serving_mode": "long_article_chunk_too_large_fail_closed",
        }
    left = best - 1
    right = best + 1
    while left >= 0 or right < len(ordered):
        candidates = [index for index in (left, right) if 0 <= index < len(ordered)]
        if not candidates:
            break
        candidate = max(candidates, key=lambda index: (scores[index], -abs(index-best)))
        addition = len(_identity(ordered[candidate].get("content"))) + 2
        if used + addition > context_budget:
            other = next((index for index in candidates if index != candidate), None)
            if other is None:
                break
            addition = len(_identity(ordered[other].get("content"))) + 2
            if used + addition > context_budget:
                break
            candidate = other
        selected.add(candidate)
        used += addition
        if candidate == left:
            left -= 1
        if candidate == right:
            right += 1

    selected_indexes = sorted(selected)
    selected_rows = [ordered[index] for index in selected_indexes]
    selected_content = "\n\n".join(
        _identity(row.get("content")) for row in selected_rows
    ).strip()
    if not selected_content:
        return packet
    return {
        **packet,
        "status": "complete",
        "reason_codes": [],
        "serving_mode": "bounded_long_article_window",
        "serving_notes": [
            "all_article_chunks_integrity_checked",
            "answer_context_is_relevant_window_not_full_article",
        ],
        "assembled_content": selected_content,
        "full_article_character_count": len(
            max(
                (
                    _identity(row.get("article_content"))
                    for row in ordered
                    if _identity(row.get("article_content"))
                ),
                key=len,
                default="",
            )
        ),
        "selected_chunk_ids": [row.get("chunk_id") for row in selected_rows],
        "selected_chunk_indexes": [row.get("chunk_index") for row in selected_rows],
        "omitted_chunk_count": len(ordered) - len(selected_rows),
        "context_coverage_ratio": round(len(selected_rows) / len(ordered), 4),
    }


def attach_exact_article_packet(
    rows: Iterable[Mapping[str, Any]],
    packet: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Attach one complete Article context while retaining every child chunk."""

    ordered = [dict(row) for row in rows]
    packet_ref = _identity(packet.get("packet_ref"))
    assembled = _identity(packet.get("assembled_content"))
    serving_mode = _identity(packet.get("serving_mode")) or "complete_article"
    attached: list[dict[str, Any]] = []
    for position, source in enumerate(ordered):
        row = dict(source)
        primary = position == 0 and packet.get("status") == "complete"
        row.update(
            {
                "matched_child_heading": row.get("chunk_heading"),
                "matched_child_content": row.get("content"),
                "parent_context_ref": packet_ref,
                "parent_context": assembled if primary else None,
                "parent_context_chars": len(assembled) if primary else 0,
                "parent_context_original_chars": len(assembled),
                "parent_context_truncated": False,
                "parent_context_reason": (
                    "complete_exact_article"
                    if packet.get("status") == "complete"
                    else "incomplete_exact_article"
                ),
                "parent_context_primary": primary,
                "exact_article_packet_ref": packet_ref,
                "exact_article_packet_status": packet.get("status"),
                "exact_article_packet_reason_codes": list(
                    packet.get("reason_codes") or []
                ),
                "exact_article_loaded_chunk_count": packet.get(
                    "loaded_chunk_count"
                ),
                "exact_article_expected_chunk_count": packet.get(
                    "expected_chunk_count"
                ),
                "exact_article_order": position,
                "exact_article_assembled_content": assembled if primary else None,
                "exact_article_serving_mode": serving_mode,
                "exact_article_full_article_character_count": packet.get(
                    "full_article_character_count", len(assembled)
                ),
                "exact_article_selected_chunk_ids": list(
                    packet.get("selected_chunk_ids") or []
                ),
                "exact_article_omitted_chunk_count": packet.get(
                    "omitted_chunk_count", 0
                ),
            }
        )
        row["parent_context_original_chars"] = int(
            packet.get("full_article_character_count") or len(assembled)
        )
        attached.append(row)
    return attached


def public_exact_article_packet(packet: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Return integrity metadata only; never expose the full Article in traces."""

    if not packet:
        return None
    allowed = (
        "status",
        "reason_codes",
        "packet_ref",
        "document_id",
        "article_id",
        "law_number",
        "article_number",
        "ordered_chunk_ids",
        "loaded_chunk_count",
        "expected_chunk_count",
        "missing_chunk_indexes",
        "duplicate_chunk_indexes",
        "empty_chunk_ids",
        "missing_structural_units",
        "content_coverage_ratio",
        "serving_mode",
        "serving_notes",
        "full_article_character_count",
        "selected_chunk_ids",
        "selected_chunk_indexes",
        "omitted_chunk_count",
        "context_coverage_ratio",
        "attachment_boundary_detected",
        "excluded_attachment_chunk_count",
    )
    return {key: packet.get(key) for key in allowed}


__all__ = [
    "attach_exact_article_packet",
    "build_exact_article_packet",
    "build_exact_article_serving_packet",
    "is_single_exact_article_plan",
    "public_exact_article_packet",
]
