"""Deterministic structural parents and retrieval children for legal text.

The parser is intentionally conservative: only line-level headings create
legal structure. Uncertain text remains a fallback child rather than becoming
an inferred legal provision.
"""

from __future__ import annotations

import re
import hashlib
import unicodedata
from typing import Any


DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 150
DEFAULT_SPLIT_THRESHOLD = 900

# V1 defaults above are retained for rollback compatibility.  V2 uses a
# tokenizer budget rather than character length and is deliberately exposed as
# a separate entry point so a legacy importer cannot silently change release
# identity.
V2_MAX_TOKENS = 512
V2_TOKEN_OVERLAP = 64
V2_QUALITY_POLICY_VERSION = "legal-chunk-quality-v2"

_ARTICLE_HEADING = re.compile(
    r"^[ \t]*Điều[ \t]+(?P<number>\d+[a-zA-Z]?)(?:[ \t]*[.:])?"
    r"[ \t]*(?P<title>[^\r\n]*)$",
    re.IGNORECASE,
)
_APPENDIX_HEADING = re.compile(
    r"^[ \t]*Phụ[ \t]+lục"
    r"(?:[ \t]+(?:số[ \t]+)?(?P<label>(?:[IVXLCDM]+|\d+[a-zA-Z]?)))?"
    r"(?P<rest>[^\r\n]*)$",
    re.IGNORECASE,
)
_CLAUSE_HEADING = re.compile(
    r"(?m)^[ \t]*(?P<number>\d+[a-zA-Z]?)[.](?:[ \t]+|$)"
)
_POINT_HEADING = re.compile(
    r"(?m)^[ \t]*(?P<number>[a-zA-ZđĐ])[)](?:[ \t]+|$)"
)
_PERSISTED_CLAUSE_PATH = re.compile(
    r"(?:^|>)[ \t]*Khoản[ \t]+(?P<number>\d+[a-zA-Z]?)\b",
    re.IGNORECASE,
)
_PERSISTED_POINT_PATH = re.compile(
    r"(?:^|>)[ \t]*Điểm[ \t]+(?P<number>[a-zA-ZđĐ])\b",
    re.IGNORECASE,
)


def _normalize_text(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_v2_text(value: str) -> str:
    """Apply the V2 NFC/newline contract before structural splitting."""

    return unicodedata.normalize("NFC", _normalize_text(value))


def parse_structural_path(heading: str) -> dict[str, str | None]:
    """Recover persisted Khoản/Điểm labels without guessing from body prose."""

    clause = _PERSISTED_CLAUSE_PATH.search(str(heading or ""))
    point = _PERSISTED_POINT_PATH.search(str(heading or ""))
    return {
        "clause_number": clause.group("number") if clause else None,
        "point_number": point.group("number").casefold() if point else None,
    }


def _appendix_heading(line: str, ordinal: int) -> dict[str, str] | None:
    match = _APPENDIX_HEADING.match(line)
    if not match:
        return None
    label = str(match.group("label") or "").strip()
    rest = str(match.group("rest") or "").strip()

    # A bare mixed-case sentence such as "Phụ lục kèm theo ..." is prose,
    # not a reliable structural heading. Labelled or all-uppercase headings
    # remain valid, as do headings using an explicit punctuation separator.
    has_separator = bool(rest[:1] in {".", ":", "-", "–", "—"})
    visible = line.strip()
    if not label and visible != visible.upper() and not has_separator:
        return None

    appendix_label = label.upper() if label else str(ordinal)
    return {
        "article_number": f"PL-{appendix_label}",
        "title": visible,
        "parent_kind": "appendix",
        "parent_number": appendix_label,
    }


def parse_structural_parents(content: str) -> list[dict[str, str]]:
    """Return line-anchored Điều/Phụ lục parents in source order."""

    normalized = _normalize_v2_text(content)
    if not normalized:
        return []

    headings: list[dict[str, Any]] = []
    appendix_ordinal = 0
    offset = 0
    for line_with_ending in normalized.splitlines(keepends=True):
        line = line_with_ending.rstrip("\n")
        article_match = _ARTICLE_HEADING.match(line)
        descriptor: dict[str, str] | None = None
        if article_match:
            number = article_match.group("number").strip()
            short_title = article_match.group("title").strip().lstrip(" .:")
            title = f"Điều {number}"
            if short_title:
                title = f"{title}. {short_title}"
            descriptor = {
                "article_number": number,
                "title": title,
                "parent_kind": "article",
                "parent_number": number,
            }
        else:
            possible_appendix = _appendix_heading(line, appendix_ordinal + 1)
            if possible_appendix:
                appendix_ordinal += 1
                descriptor = possible_appendix
        if descriptor:
            headings.append(
                {
                    **descriptor,
                    "start": offset,
                    "body_start": offset + len(line_with_ending),
                }
            )
        offset += len(line_with_ending)

    # splitlines(keepends=True) omits a final newline, so a heading on the last
    # line has body_start at EOF as intended.
    parents: list[dict[str, str]] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1]["start"] if index + 1 < len(headings) else len(normalized)
        body = normalized[int(heading["body_start"]):int(end)].strip()
        if not body:
            # Preserve operative text placed on the heading line, matching the
            # legacy importer, but never manufacture body text for an empty
            # labelled appendix.
            title_suffix = str(heading["title"]).split(". ", 1)
            body = title_suffix[1].strip() if len(title_suffix) == 2 else ""
        if not body and heading["parent_kind"] == "article" and ". " not in str(heading["title"]):
            continue
        parents.append(
            {
                "article_number": str(heading["article_number"]),
                "title": str(heading["title"]),
                "content": body,
                "parent_kind": str(heading["parent_kind"]),
                "parent_number": str(heading["parent_number"]),
                "source_start_offset": int(heading["start"]),
                "source_end_offset": int(end),
            }
        )
    return parents


def _recursive_split(
    value: str,
    *,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if len(value) <= chunk_size:
        return [value]
    chunks: list[str] = []
    start = 0
    while start < len(value):
        hard_end = min(start + chunk_size, len(value))
        end = hard_end
        if hard_end < len(value):
            candidates = [
                value.rfind("\n", start, hard_end),
                value.rfind(". ", start, hard_end),
                value.rfind("; ", start, hard_end),
                value.rfind(" ", start, hard_end),
            ]
            split_at = max(candidates)
            if split_at > start + chunk_size // 2:
                end = split_at + 1
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(value):
            break
        start = max(end - max(0, overlap), start + 1)
    return chunks


def _append_parts(
    output: list[dict[str, Any]],
    *,
    text: str,
    heading: str,
    child_kind: str,
    parent: dict[str, str],
    clause_number: str | None,
    point_number: str | None,
    chunk_size: int,
    overlap: int,
    split_threshold: int,
) -> None:
    parts = (
        [text.strip()]
        if len(text.strip()) <= split_threshold
        else _recursive_split(text, chunk_size=chunk_size, overlap=overlap)
    )
    for part in parts:
        if not part:
            continue
        output.append(
            {
                "chunk_index": len(output),
                "heading": heading[:500],
                # Keep the legacy bounded display field, but preserve the
                # complete structural heading for V2 quality assessment. A
                # malformed imported title can otherwise be truncated before
                # the passage header policy has a chance to detect it.
                "heading_full": heading,
                "content": part,
                "child_kind": child_kind,
                "parent_kind": parent.get("parent_kind") or "article",
                "parent_number": parent.get("parent_number")
                or parent.get("article_number"),
                "clause_number": clause_number,
                "point_number": point_number,
            }
        )


def split_parent_children(
    parent: dict[str, str],
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    split_threshold: int = DEFAULT_SPLIT_THRESHOLD,
) -> list[dict[str, Any]]:
    """Split one parent at reliable Khoản/Điểm boundaries.

    A point child includes its clause introduction so the compact retrieval
    passage remains meaningful, but it never includes a sibling point.
    """

    content = _normalize_text(parent.get("content") or "")
    if not content:
        return []
    parent_heading = str(parent.get("title") or "Văn bản không có Điều").strip()
    children: list[dict[str, Any]] = []
    clauses = list(_CLAUSE_HEADING.finditer(content))

    if clauses and content[:clauses[0].start()].strip():
        _append_parts(
            children,
            text=content[:clauses[0].start()],
            heading=parent_heading,
            child_kind="lead",
            parent=parent,
            clause_number=None,
            point_number=None,
            chunk_size=chunk_size,
            overlap=overlap,
            split_threshold=split_threshold,
        )

    for clause_index, clause in enumerate(clauses):
        clause_end = (
            clauses[clause_index + 1].start()
            if clause_index + 1 < len(clauses)
            else len(content)
        )
        clause_text = content[clause.start():clause_end].strip()
        clause_number = clause.group("number")
        clause_heading = f"{parent_heading} > Khoản {clause_number}"
        points = list(_POINT_HEADING.finditer(clause_text))
        if not points:
            _append_parts(
                children,
                text=clause_text,
                heading=clause_heading,
                child_kind="clause",
                parent=parent,
                clause_number=clause_number,
                point_number=None,
                chunk_size=chunk_size,
                overlap=overlap,
                split_threshold=split_threshold,
            )
            continue

        clause_intro = clause_text[:points[0].start()].strip()
        for point_index, point in enumerate(points):
            point_end = (
                points[point_index + 1].start()
                if point_index + 1 < len(points)
                else len(clause_text)
            )
            point_text = clause_text[point.start():point_end].strip()
            if clause_intro:
                point_text = f"{clause_intro}\n{point_text}"
            point_number = point.group("number").casefold()
            _append_parts(
                children,
                text=point_text,
                heading=f"{clause_heading} > Điểm {point_number}",
                child_kind="point",
                parent=parent,
                clause_number=clause_number,
                point_number=point_number,
                chunk_size=chunk_size,
                overlap=overlap,
                split_threshold=split_threshold,
            )

    if clauses:
        return children

    # Some provisions/appendices contain Điểm without numbered Khoản.
    points = list(_POINT_HEADING.finditer(content))
    if points:
        if content[:points[0].start()].strip():
            _append_parts(
                children,
                text=content[:points[0].start()],
                heading=parent_heading,
                child_kind="lead",
                parent=parent,
                clause_number=None,
                point_number=None,
                chunk_size=chunk_size,
                overlap=overlap,
                split_threshold=split_threshold,
            )
        for point_index, point in enumerate(points):
            point_end = (
                points[point_index + 1].start()
                if point_index + 1 < len(points)
                else len(content)
            )
            point_number = point.group("number").casefold()
            _append_parts(
                children,
                text=content[point.start():point_end],
                heading=f"{parent_heading} > Điểm {point_number}",
                child_kind="point",
                parent=parent,
                clause_number=None,
                point_number=point_number,
                chunk_size=chunk_size,
                overlap=overlap,
                split_threshold=split_threshold,
            )
        return children

    _append_parts(
        children,
        text=content,
        heading=parent_heading,
        child_kind="fallback",
        parent=parent,
        clause_number=None,
        point_number=None,
        chunk_size=chunk_size,
        overlap=overlap,
        split_threshold=split_threshold,
    )
    return children


def _tokenizer_encode(tokenizer: Any, value: str) -> list[int]:
    try:
        encoded = tokenizer.encode(value, add_special_tokens=False)
    except TypeError:
        encoded = tokenizer.encode(value)
    if hasattr(encoded, "tolist"):
        encoded = encoded.tolist()
    return [int(item) for item in encoded]


def _tokenizer_decode(tokenizer: Any, values: list[int]) -> str:
    try:
        return str(tokenizer.decode(values, skip_special_tokens=False)).strip()
    except TypeError:
        return str(tokenizer.decode(values)).strip()


def _token_windows(
    text: str,
    *,
    tokenizer: Any,
    max_tokens: int,
    overlap: int,
) -> list[tuple[str, int]]:
    token_ids = _tokenizer_encode(tokenizer, text)
    if not token_ids:
        return []
    if len(token_ids) <= max_tokens:
        return [(text.strip(), len(token_ids))]
    output: list[tuple[str, int]] = []
    step = max_tokens - overlap
    for start in range(0, len(token_ids), step):
        window = token_ids[start:start + max_tokens]
        if not window:
            break
        decoded = _tokenizer_decode(tokenizer, window)
        if decoded:
            output.append((decoded, len(window)))
        if start + max_tokens >= len(token_ids):
            break
    return output


def _token_windows_with_passage_prefix(
    text: str,
    *,
    prefix: str,
    heading: str,
    tokenizer: Any,
    max_tokens: int,
    overlap: int,
) -> list[tuple[str, int, str]]:
    """Window child content while counting the complete embedding passage."""

    header = "\n".join(value for value in (prefix.strip(), heading.strip()) if value).strip()
    header_text = f"{header}\n" if header else ""
    header_tokens = len(_tokenizer_encode(tokenizer, header_text))
    content_budget = max_tokens - header_tokens
    if content_budget <= overlap:
        raise ValueError("v2_passage_prefix_exceeds_token_budget")
    token_ids = _tokenizer_encode(tokenizer, text)
    if not token_ids:
        return []

    if len(token_ids) <= content_budget:
        passage = f"{header_text}{text.strip()}" if header_text else text.strip()
        passage_count = len(_tokenizer_encode(tokenizer, passage))
        if passage_count <= max_tokens:
            return [(text.strip(), passage_count, passage)]

    # Decode/re-encode can change the token count for SentencePiece/BPE
    # tokenizers (spaces and byte-boundary normalization are the usual
    # causes). Build each window from the actual decoded passage and shrink
    # the content token slice until the persisted passage is within budget.
    # This prevents the manifest builder from silently dropping otherwise
    # valid legal text after the header is added.
    output: list[tuple[str, int, str]] = []
    start = 0
    while start < len(token_ids):
        end = min(start + content_budget, len(token_ids))
        selected: tuple[str, int, str] | None = None
        while end > start:
            part = _tokenizer_decode(tokenizer, token_ids[start:end])
            if not part:
                end -= 1
                continue
            passage = f"{header_text}{part}" if header_text else part
            passage_count = len(_tokenizer_encode(tokenizer, passage))
            if passage_count <= max_tokens:
                selected = (part, passage_count, passage)
                break
            end -= 1
        if selected is None:
            # The header was already checked above; a one-token failure means
            # the tokenizer cannot round-trip this source safely.
            raise ValueError("v2_passage_window_cannot_fit_token_budget")
        output.append(selected)
        if end >= len(token_ids):
            break
        next_start = max(start + 1, end - overlap)
        # Normally this is exactly content_budget-overlap. When round-trip
        # normalization forced a shorter window, continue from the shortened
        # window while retaining the requested overlap where possible.
        start = next_start
    return output


def _select_passage_header(
    *,
    prefix: str,
    heading: str,
    parent: dict[str, Any],
    tokenizer: Any,
    max_tokens: int,
    overlap: int,
) -> tuple[str, str, str | None]:
    """Choose a complete, non-truncated header that leaves room for overlap.

    Some imported records contain an article title that is actually a copied
    paragraph thousands of characters long.  The original title remains in
    metadata and the structural path; the embedding header falls back to the
    stable article number instead of truncating legal text.
    """

    normalized_prefix = _normalize_v2_text(prefix)
    normalized_heading = _normalize_v2_text(heading)
    parent_number = str(
        parent.get("article_number")
        or parent.get("parent_number")
        or ""
    ).strip()
    if str(parent.get("parent_kind") or "").casefold() == "appendix":
        compact_heading = f"Phụ lục {parent_number}".strip()
    else:
        compact_heading = f"Điều {parent_number}".strip() or "Điều"
    prefix_lines = [line.strip() for line in normalized_prefix.splitlines() if line.strip()]
    law_only = prefix_lines[-1] if prefix_lines else ""
    options = [
        (normalized_prefix, normalized_heading, None),
        (normalized_prefix, compact_heading, "structural_heading_simplified_for_token_budget"),
        (law_only, compact_heading, "document_title_omitted_for_token_budget"),
        ("", compact_heading, "passage_prefix_omitted_for_token_budget"),
        ("", "", "passage_header_omitted_for_token_budget"),
    ]
    for candidate_prefix, candidate_heading, reason in options:
        header = "\n".join(
            value for value in (candidate_prefix.strip(), candidate_heading.strip()) if value
        ).strip()
        header_text = f"{header}\n" if header else ""
        header_tokens = len(_tokenizer_encode(tokenizer, header_text))
        # Leave strictly more than the overlap budget for the child text;
        # otherwise the window helper cannot make forward progress.
        if header_tokens < max_tokens - overlap:
            return candidate_prefix, candidate_heading, reason
    raise ValueError("v2_passage_header_exceeds_token_budget")


def split_parent_children_v2(
    parent: dict[str, Any],
    *,
    tokenizer: Any,
    max_tokens: int = V2_MAX_TOKENS,
    overlap: int = V2_TOKEN_OVERLAP,
    release_id: str | None = None,
    passage_prefix: str | None = None,
) -> list[dict[str, Any]]:
    """Create V2 children with structural boundaries and tokenizer budgets.

    The tokenizer is required on purpose.  Falling back to character counts
    would make a 512-token safety claim unverifiable.  Structural children are
    formed first with no character truncation; only a child that exceeds the
    token budget receives deterministic token-window splitting.
    """

    if tokenizer is None:
        raise ValueError("v2_tokenizer_required")
    if max_tokens < 1 or overlap < 0 or overlap >= max_tokens:
        raise ValueError("invalid_v2_token_budget")
    content = _normalize_v2_text(parent.get("content") or "")
    if not content:
        return []
    normalized_parent = dict(parent)
    normalized_parent["content"] = content
    structural = split_parent_children(
        normalized_parent,
        chunk_size=max(len(content), 1),
        overlap=0,
        split_threshold=max(len(content), 1),
    )
    parent_offset = parent.get("source_start_offset")
    parent_offset = int(parent_offset) if parent_offset is not None else None
    cursor = 0
    output: list[dict[str, Any]] = []
    for child in structural:
        child_text = _normalize_text(child.get("content") or "")
        base_heading = str(
            child.get("heading_full")
            or child.get("heading")
            or parent.get("title")
            or ""
        )
        requested_prefix = _normalize_v2_text(passage_prefix or "")
        effective_prefix, effective_heading, header_reason = _select_passage_header(
            prefix=requested_prefix,
            heading=base_heading,
            parent=normalized_parent,
            tokenizer=tokenizer,
            max_tokens=max_tokens,
            overlap=overlap,
        )
        windows = _token_windows_with_passage_prefix(
            child_text,
            prefix=effective_prefix,
            heading=effective_heading,
            tokenizer=tokenizer,
            max_tokens=max_tokens,
            overlap=overlap,
        )
        if not windows:
            continue
        child_source = content.find(child_text, cursor)
        if child_source >= 0:
            cursor = child_source + len(child_text)
        for window_index, (part, token_count, passage_text) in enumerate(windows):
            relative = content.find(part, max(0, child_source if child_source >= 0 else 0))
            source_start = (
                parent_offset + relative if parent_offset is not None and relative >= 0 else None
            )
            source_end = source_start + len(part) if source_start is not None else None
            heading = (
                f"{base_heading} > Phần {window_index + 1}"
                if len(windows) > 1
                else base_heading
            )
            passage_sha = hashlib.sha256(passage_text.encode("utf-8")).hexdigest()
            output.append(
                {
                    "release_id": release_id,
                    "chunk_index": len(output),
                    "structural_chunk_index": int(child.get("chunk_index") or 0),
                    "heading": heading[:500],
                    "content": part,
                    "child_kind": child.get("child_kind") or "fallback",
                    "parent_kind": child.get("parent_kind") or parent.get("parent_kind"),
                    "parent_number": child.get("parent_number") or parent.get("article_number"),
                    "clause_number": child.get("clause_number"),
                    "point_number": child.get("point_number"),
                    "source_start_offset": source_start,
                    "source_end_offset": source_end,
                    "source_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "passage_sha256": passage_sha,
                    "passage_text": passage_text,
                    "passage_text_sha256": hashlib.sha256(passage_text.encode("utf-8")).hexdigest(),
                    "token_count": token_count,
                    "quality_policy_version": V2_QUALITY_POLICY_VERSION,
                    "quality_assessed": True,
                    "eligible": True,
                    "serving_state": "retrievable",
                    "quality_reasons": (header_reason,) if header_reason else (),
                }
            )
    return output
