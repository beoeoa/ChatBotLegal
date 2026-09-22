"""Deterministic model-facing cleanup for retrieved Vietnamese legal text.

The functions in this module never mutate stored source text.  They return a
projection plus a bounded audit summary so retrieval and prompt code can share
one conservative implementation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_GAZETTE_HEADER_RE = re.compile(
    r"^\s*(?:\d{1,4}\s+)?C[ÔO]NG\s+B[ÁA]O\s*/\s*S[ỐO]\s*[^\n]*$",
    re.IGNORECASE,
)
_GAZETTE_URL_RE = re.compile(
    r"^\s*https?://(?:www\.)?(?:congbao\.[^/\s]+|congbao\.chinhphu\.vn)"
    r"/(?:[^\s]*(?:page|trang|noise)[^\s]*)\s*$",
    re.IGNORECASE,
)
_ISOLATED_PAGE_RE = re.compile(r"^\s*\d{1,4}\s*$")
_GAZETTE_INLINE_TRAILING_RE = re.compile(
    r"\s+(?P<footer>(?:\d{1,4}\s+)?C[ÔO]NG\s+B[ÁA]O\s*/\s*S[ỐO]\s+"
    r"[^\n]*?/\s*NG[ÀA]Y\s+\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
    r"(?:\s+\d{1,4})?)\s*$",
    re.IGNORECASE,
)


def _normalise_projection_lines(lines: list[str]) -> str:
    output: list[str] = []
    blank = False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            if output and not blank:
                output.append("")
            blank = True
            continue
        output.append(line.strip())
        blank = False
    while output and not output[-1]:
        output.pop()
    return "\n".join(output)


def clean_gazette_boilerplate(raw_text: Any) -> dict[str, Any]:
    """Remove only known presentation boilerplate from one text projection.

    Isolated numbers are removed only at a document edge or next to another
    recognised boilerplate line.  This avoids treating a normative standalone
    number inside an Article as a page number.
    """

    original = str(raw_text or "")
    if not original:
        return {
            "clean_text": "",
            "removed_noise": [],
            "needs_review": False,
        }

    inline_removed: list[dict[str, Any]] = []
    inline_match = _GAZETTE_INLINE_TRAILING_RE.search(original)
    if inline_match is not None:
        inline_removed.append(
            {
                "text": inline_match.group("footer").strip(),
                "reason": "gazette_footer_inline",
                "line_number": original[: inline_match.start()].count("\n") + 1,
            }
        )
        original = original[: inline_match.start()].rstrip()

    lines = original.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    reasons: dict[int, str] = {}
    for index, line in enumerate(lines):
        if _GAZETTE_HEADER_RE.fullmatch(line):
            reasons[index] = "gazette_header"
        elif _GAZETTE_URL_RE.fullmatch(line):
            reasons[index] = "repeated_footer_url"

    last_nonempty = max(
        (index for index, line in enumerate(lines) if line.strip()),
        default=-1,
    )
    first_nonempty = next(
        (index for index, line in enumerate(lines) if line.strip()),
        -1,
    )
    for index, line in enumerate(lines):
        if index in reasons or not _ISOLATED_PAGE_RE.fullmatch(line):
            continue
        adjacent_noise = (index - 1 in reasons) or (index + 1 in reasons)
        if index in {first_nonempty, last_nonempty} or adjacent_noise:
            reasons[index] = "isolated_page_number"

    removed = inline_removed + [
        {
            "text": lines[index].strip(),
            "reason": reasons[index],
            "line_number": index + 1,
        }
        for index in sorted(reasons)
    ]
    clean_lines = [line for index, line in enumerate(lines) if index not in reasons]
    return {
        "clean_text": _normalise_projection_lines(clean_lines),
        "removed_noise": removed,
        "needs_review": False,
    }


def project_legal_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    """Return a raw-preserving evidence row with one cleaned capsule."""

    projected = dict(source)
    child_raw = source.get("matched_child_content")
    if child_raw in (None, ""):
        child_raw = source.get("content")
    content_result = clean_gazette_boilerplate(source.get("content"))
    child_is_content = str(child_raw or "") == str(source.get("content") or "")
    child_result = (
        content_result if child_is_content else clean_gazette_boilerplate(child_raw)
    )
    parent_result = clean_gazette_boilerplate(source.get("parent_context"))
    explicit_capsule_result = clean_gazette_boilerplate(
        source.get("evidence_capsule")
    )

    clean_content = content_result["clean_text"]
    clean_child = child_result["clean_text"] or clean_content
    clean_parent = parent_result["clean_text"]
    heading = str(
        source.get("matched_child_heading")
        or source.get("chunk_heading")
        or source.get("parent_context_heading")
        or ""
    ).strip()

    capsule_parts: list[str] = []
    structural_status = str(source.get("structural_unit_status") or "")
    if (
        explicit_capsule_result["clean_text"]
        and structural_status.startswith("complete")
    ):
        # A reviewed structural selector has already hydrated the complete
        # Article/clause. Preserve that unit instead of rebuilding a shorter
        # child-first capsule and losing its governing lead-in.
        capsule_parts.append(explicit_capsule_result["clean_text"])
    else:
        if heading:
            capsule_parts.append(f"Cấu trúc: {heading}")
        if clean_child:
            capsule_parts.append(clean_child)
        if clean_parent and clean_parent.casefold() != clean_child.casefold():
            capsule_parts.append(f"Ngữ cảnh chi phối:\n{clean_parent}")

    removed = list(content_result["removed_noise"])
    if not child_is_content:
        removed.extend(child_result["removed_noise"])
    removed.extend(parent_result["removed_noise"])
    removed.extend(explicit_capsule_result["removed_noise"])
    projected.update(
        {
            "clean_content": clean_content,
            "clean_matched_child_content": clean_child,
            "clean_parent_context": clean_parent,
            "evidence_capsule": "\n\n".join(capsule_parts).strip(),
            "sanitization_summary": {
                "removed_count": len(removed),
                "removed_reasons": sorted({item["reason"] for item in removed}),
                "needs_review": any(
                    result["needs_review"]
                    for result in (
                        content_result,
                        child_result,
                        parent_result,
                        explicit_capsule_result,
                    )
                ),
            },
        }
    )
    return projected


def project_legal_evidence_rows(
    rows: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    projected = [project_legal_evidence(row) for row in rows]
    summaries = [row["sanitization_summary"] for row in projected]
    return projected, {
        "evidence_count": len(projected),
        "removed_count": sum(int(item.get("removed_count") or 0) for item in summaries),
        "removed_reasons": sorted(
            {
                reason
                for item in summaries
                for reason in item.get("removed_reasons") or []
            }
        ),
        "needs_review_count": sum(bool(item.get("needs_review")) for item in summaries),
    }
