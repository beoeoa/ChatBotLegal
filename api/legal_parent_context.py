"""Pure post-selection parent context projection for legal evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from api.legal_text_cleaning import clean_gazette_boilerplate


DEFAULT_PER_PARENT_CHAR_LIMIT = 12_000
DEFAULT_TOTAL_PARENT_CHAR_LIMIT = 36_000
DEFAULT_SHORT_PARENT_CHAR_LIMIT = 2_500

_CLAUSE_LINE_RE = re.compile(r"^\s*\d+[.)]\s+")


def _identity(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _parent_kind(parent: dict[str, Any]) -> str:
    number = _identity(parent.get("article_number")).casefold()
    heading = _identity(parent.get("parent_heading") or parent.get("article_title")).casefold()
    if number.startswith("pl-") or heading.startswith("phụ lục"):
        return "appendix"
    if number == "0" or "unstructured" in heading:
        return "unstructured"
    return "article"


def _bounded_window(parent: str, child: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(parent) <= limit:
        return parent
    child_text = child.strip()
    anchors = [child_text] if child_text else []
    anchors.extend(
        line
        for line in reversed([part.strip() for part in child_text.splitlines()])
        if len(line) >= 8 and line not in anchors
    )
    match_at = -1
    matched_anchor = child_text
    for anchor in anchors:
        match_at = parent.find(anchor)
        if match_at >= 0:
            matched_anchor = anchor
            break
    if match_at < 0:
        match_at = 0
        matched_anchor = ""
    match_end = min(len(parent), match_at + max(1, len(matched_anchor)))
    span = max(1, match_end - match_at)
    if span >= limit:
        return parent[match_at:match_at + limit]
    remaining = limit - span
    start = max(0, match_at - remaining // 2)
    end = min(len(parent), start + limit)
    start = max(0, end - limit)
    return parent[start:end]


def _adaptive_structural_scope(parent: str, child: str, limit: int) -> tuple[str, str]:
    """Return a clause-centred scope plus its projection kind."""

    if limit <= 0:
        return "", "budget_exhausted"
    lines = parent.splitlines()
    child_lines = [part.strip() for part in child.splitlines() if len(part.strip()) >= 8]
    anchors = list(reversed(child_lines)) or [child.strip()]
    matched_index = -1
    matched_anchor = ""
    for anchor in anchors:
        for index, line in enumerate(lines):
            if anchor and anchor in line:
                matched_index = index
                matched_anchor = anchor
                break
        if matched_index >= 0:
            break
    if matched_index < 0:
        return _bounded_window(parent, child, limit), "bounded_window"

    clause_start = next(
        (
            index
            for index in range(matched_index, -1, -1)
            if _CLAUSE_LINE_RE.match(lines[index])
        ),
        matched_index,
    )
    clause_end = next(
        (
            index
            for index in range(clause_start + 1, len(lines))
            if _CLAUSE_LINE_RE.match(lines[index])
        ),
        len(lines),
    )
    clause = "\n".join(lines[clause_start:clause_end]).strip()
    if len(clause) > limit:
        clause = _bounded_window(clause, matched_anchor or child, limit)

    first_clause = next(
        (index for index, line in enumerate(lines) if _CLAUSE_LINE_RE.match(line)),
        0,
    )
    lead = "\n".join(lines[:first_clause]).strip()
    if lead and len(lead) <= 500 and len(lead) + len(clause) + 1 <= limit:
        clause = f"{lead}\n{clause}"
    return clause[:limit], "structural_scope"


def hydrate_parent_context(
    results: Iterable[dict[str, Any]],
    parent_rows: Iterable[dict[str, Any]],
    *,
    per_parent_char_limit: int = DEFAULT_PER_PARENT_CHAR_LIMIT,
    total_char_limit: int = DEFAULT_TOTAL_PARENT_CHAR_LIMIT,
    short_parent_char_limit: int = DEFAULT_SHORT_PARENT_CHAR_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Attach one bounded parent body per selected parent group.

    Parent rows are keyed by both article and document, preventing a caller or
    stale record from crossing source boundaries. The selected child remains
    intact for every fallback state.
    """

    per_parent_char_limit = max(0, int(per_parent_char_limit))
    total_char_limit = max(0, int(total_char_limit))
    short_parent_char_limit = max(0, int(short_parent_char_limit))
    parents = {
        (_identity(row.get("article_id")), _identity(row.get("document_id"))): dict(row)
        for row in parent_rows
        if _identity(row.get("article_id")) and _identity(row.get("document_id"))
    }
    hydrated: list[dict[str, Any]] = []
    projections: dict[str, dict[str, Any]] = {}
    used_chars = 0
    fallback_groups: set[str] = set()
    truncated_groups: set[str] = set()
    hydrated_groups: set[str] = set()

    for raw_result in results:
        result = dict(raw_result)
        article_id = _identity(result.get("article_id"))
        document_id = _identity(result.get("document_id"))
        group_key = (
            f"article:{article_id}"
            if article_id
            else f"chunk:{_identity(result.get('chunk_id'))}"
        )
        result["matched_child_heading"] = result.get("chunk_heading")
        result["matched_child_content"] = result.get("content")
        result["parent_context_ref"] = group_key

        projection = projections.get(group_key)
        if projection is None:
            parent = parents.get((article_id, document_id))
            base = {
                "parent_context_ref": group_key,
                "parent_context": None,
                "parent_context_chars": 0,
                "parent_context_original_chars": 0,
                "parent_context_truncated": False,
                "parent_context_primary": False,
                "evidence_capsule_kind": "child_only",
                "sanitization_summary": {
                    "removed_count": 0,
                    "removed_reasons": [],
                    "needs_review": False,
                },
            }
            try:
                if parent is None:
                    projection = {**base, "parent_context_reason": "missing_parent"}
                    fallback_groups.add(group_key)
                else:
                    parent_content = str(
                        parent.get("parent_content") or parent.get("content") or ""
                    ).strip()
                    cleaning = clean_gazette_boilerplate(parent_content)
                    clean_parent_content = str(cleaning["clean_text"] or "").strip()
                    parent_heading = (
                        parent.get("parent_heading")
                        or parent.get("article_title")
                        or parent.get("title")
                    )
                    base.update(
                        {
                            "parent_context_kind": _parent_kind(parent),
                            "parent_context_heading": parent_heading,
                            "parent_context_original_chars": len(parent_content),
                            "sanitization_summary": {
                                "removed_count": len(cleaning["removed_noise"]),
                                "removed_reasons": sorted(
                                    {
                                        item["reason"]
                                        for item in cleaning["removed_noise"]
                                    }
                                ),
                                "needs_review": bool(cleaning["needs_review"]),
                            },
                        }
                    )
                    if not clean_parent_content:
                        projection = {**base, "parent_context_reason": "empty_parent"}
                        fallback_groups.add(group_key)
                    else:
                        remaining = max(0, total_char_limit - used_chars)
                        allowed = min(per_parent_char_limit, remaining)
                        if allowed <= 0:
                            projection = {
                                **base,
                                "parent_context_reason": "request_budget_exhausted",
                            }
                            fallback_groups.add(group_key)
                        else:
                            if (
                                len(clean_parent_content) <= short_parent_char_limit
                                and len(clean_parent_content) <= allowed
                            ):
                                projected = clean_parent_content
                                capsule_kind = "complete_short_parent"
                                truncated = False
                            elif len(clean_parent_content) > short_parent_char_limit:
                                projected, capsule_kind = _adaptive_structural_scope(
                                    clean_parent_content,
                                    str(result.get("content") or ""),
                                    allowed,
                                )
                                truncated = projected != clean_parent_content
                            else:
                                projected = _bounded_window(
                                    clean_parent_content,
                                    str(result.get("content") or ""),
                                    allowed,
                                )
                                capsule_kind = "bounded_window"
                                truncated = len(projected) < len(clean_parent_content)
                            projection = {
                                **base,
                                "parent_context": projected,
                                "parent_context_chars": len(projected),
                                "parent_context_truncated": truncated,
                                "parent_context_reason": (
                                    "oversized_parent" if truncated else "complete"
                                ),
                                "parent_context_primary": True,
                                "evidence_capsule_kind": capsule_kind,
                            }
                            used_chars += len(projected)
                            hydrated_groups.add(group_key)
                            if truncated:
                                truncated_groups.add(group_key)
            except Exception:
                projection = {**base, "parent_context_reason": "projection_error"}
                fallback_groups.add(group_key)
            projections[group_key] = projection
            result.update(projection)
        else:
            # Keep group metadata on siblings but carry the potentially large
            # body only on the first selected child.
            result.update(
                {
                    **projection,
                    "parent_context": None,
                    "parent_context_primary": False,
                }
            )
        hydrated.append(result)

    summary = {
        "unique_parent_count": len(projections),
        "hydrated_parent_count": len(hydrated_groups),
        "truncated_parent_count": len(truncated_groups),
        "parent_fallback_count": len(fallback_groups),
    }
    return hydrated, summary


def group_parent_child_evidence(
    results: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group selected children so a parent body appears once in the prompt."""

    groups: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for raw_result in results:
        result = dict(raw_result)
        key = _identity(result.get("parent_context_ref")) or (
            f"chunk:{_identity(result.get('chunk_id') or result.get('id'))}"
        )
        group = by_key.get(key)
        if group is None:
            group = {
                "group_key": key,
                "source": result,
                "parent_context": result.get("clean_parent_context")
                if result.get("clean_parent_context") is not None
                else result.get("parent_context"),
                "parent_context_kind": result.get("parent_context_kind"),
                "parent_context_heading": result.get("parent_context_heading"),
                "parent_context_truncated": bool(result.get("parent_context_truncated")),
                "parent_context_reason": result.get("parent_context_reason"),
                "matched_children": [],
            }
            by_key[key] = group
            groups.append(group)
        elif not group.get("parent_context") and (
            result.get("clean_parent_context") or result.get("parent_context")
        ):
            group["parent_context"] = (
                result.get("clean_parent_context") or result.get("parent_context")
            )
            group["parent_context_heading"] = result.get("parent_context_heading")
            group["parent_context_kind"] = result.get("parent_context_kind")
            group["parent_context_truncated"] = bool(result.get("parent_context_truncated"))
            group["parent_context_reason"] = result.get("parent_context_reason")
        group["matched_children"].append(
            {
                "chunk_id": result.get("chunk_id"),
                "heading": result.get("matched_child_heading")
                or result.get("chunk_heading"),
                "content": result.get("matched_child_content")
                if result.get("clean_matched_child_content") is None
                and result.get("matched_child_content") is not None
                else result.get("clean_matched_child_content")
                if result.get("clean_matched_child_content") is not None
                else result.get("clean_content")
                if result.get("clean_content") is not None
                else result.get("content"),
            }
        )
    return groups
