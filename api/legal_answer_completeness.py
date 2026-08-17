"""Deterministic answer-completeness assessment for public legal answers.

Grounding and completeness are deliberately separate concerns:

* grounding proves that displayed legal claims are supported by current sources;
* completeness proves that the answer fulfilled the structure requested by the user.

This module never calls a model and never changes source metadata.  It is
conservative by design: an answer is called complete only when every requested
check can be proven from the final answer and the request-scoped evidence.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
import statistics
import unicodedata
from typing import Any, Mapping, Sequence

from api.legal_exact_retrieval import normalize_exact_identifier, plan_exact_lookup


_STRUCTURAL_REQUEST_MARKERS = (
    "noi dung chinh",
    "cac y chinh",
    "tung quy tac",
    "chi tiet co he thong",
    "trinh bay lan luot",
)
_ORDER_REQUEST_MARKERS = (
    "lan luot",
    "theo thu tu",
    "tung quy tac",
    "co he thong",
)
_PLAIN_LANGUAGE_REQUEST_MARKERS = (
    "de hieu",
    "noi de hieu",
    "tu de hieu",
    "cho nguoi dan",
)
_PRACTICAL_REQUEST_MARKERS = (
    "y nghia thuc te",
    "van dung thuc te",
    "trong thuc te",
    "can luu y gi",
    "de khong hieu sai",
)
_PLAIN_LANGUAGE_ANSWER_MARKERS = (
    "hieu don gian",
    "noi cach khac",
    "co nghia la",
    "nguoi dan",
    "ban can",
    "de de hieu",
)
_PRACTICAL_ANSWER_MARKERS = (
    "y nghia thuc te",
    "trong thuc te",
    "dieu nay co nghia",
    "nguoi dan can",
    "ban can",
    "khi thuc hien",
    "can luu y",
)
_UNIT_MARKER_RE = re.compile(
    r"(?m)^\s*(?P<marker>(?:\d{1,3}|[a-zA-ZđĐ])\s*[.)])\s+"
)
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
_STOPWORDS = {
    "cac",
    "cho",
    "cua",
    "dieu",
    "duoc",
    "hoac",
    "khi",
    "khong",
    "mot",
    "nay",
    "nhung",
    "phai",
    "quy",
    "tai",
    "theo",
    "thi",
    "trong",
    "truong",
    "van",
    "viec",
    "voi",
}


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(_fold(value))
        if token not in _STOPWORDS and not token.isdigit()
    }


def _contains_any(value: str, markers: Sequence[str]) -> bool:
    return any(marker in value for marker in markers)


def _matching_exact_sources(
    question: str,
    sources: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    plan = plan_exact_lookup(question)
    if not plan.law_number or not plan.article_number:
        return []
    matches: list[dict[str, Any]] = []
    for raw in sources:
        law_number = normalize_exact_identifier(raw.get("law_number"))
        article_number = normalize_exact_identifier(raw.get("article_number"))
        if law_number == plan.law_number and article_number == plan.article_number:
            matches.append(dict(raw))
    return matches


def _canonical_provision_text(
    sources: Sequence[Mapping[str, Any]],
) -> tuple[str, bool]:
    """Return the best full-provision text and whether it is provably complete."""

    parents: list[tuple[str, bool]] = []
    for source in sources:
        parent = str(
            source.get("clean_parent_context")
            or source.get("parent_context")
            or ""
        ).strip()
        if parent:
            parents.append((parent, bool(source.get("parent_context_truncated"))))
    if parents:
        parent, truncated = max(parents, key=lambda item: len(item[0]))
        return parent, not truncated

    ordered = sorted(
        sources,
        key=lambda item: (
            int(item.get("chunk_index") or 0),
            str(item.get("chunk_id") or ""),
        ),
    )
    parts: list[str] = []
    seen: set[str] = set()
    for source in ordered:
        content = str(
            source.get("clean_matched_child_content")
            or source.get("clean_content")
            or source.get("content")
            or ""
        ).strip()
        folded = _fold(content)
        if not content or folded in seen:
            continue
        seen.add(folded)
        parts.append(content)
    # Multiple child chunks may still be a bounded retrieval subset.  Without
    # an untruncated parent projection we can measure coverage, but cannot call
    # the full provision complete with certainty.
    return "\n".join(parts), len(parts) == 1


@dataclass(frozen=True)
class _StructuralUnit:
    marker: str
    text: str
    tokens: frozenset[str]


def _structural_units(text: str) -> list[_StructuralUnit]:
    matches = list(_UNIT_MARKER_RE.finditer(str(text or "")))
    if not matches:
        clean = str(text or "").strip()
        return [
            _StructuralUnit("body", clean, frozenset(_tokens(clean)))
        ] if clean else []
    units: list[_StructuralUnit] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        if not body:
            continue
        units.append(
            _StructuralUnit(
                marker=re.sub(r"\s+", "", match.group("marker")).casefold(),
                text=body,
                tokens=frozenset(_tokens(body)),
            )
        )
    return units


def _unit_signatures(units: Sequence[_StructuralUnit]) -> list[set[str]]:
    counts = Counter(token for unit in units for token in set(unit.tokens))
    maximum_frequency = max(1, math.ceil(len(units) / 2))
    signatures: list[set[str]] = []
    for unit in units:
        rare = {token for token in unit.tokens if counts[token] <= maximum_frequency}
        signatures.append(rare if len(rare) >= 2 else set(unit.tokens))
    return signatures


def _covered_unit_positions(
    answer: str,
    signatures: Sequence[set[str]],
) -> tuple[list[bool], list[int | None]]:
    folded_answer = _fold(answer)
    answer_tokens = _tokens(answer)
    covered: list[bool] = []
    positions: list[int | None] = []
    for signature in signatures:
        overlap = signature & answer_tokens
        minimum_overlap = 2 if len(signature) <= 6 else 3
        ratio = len(overlap) / max(1, min(len(signature), 10))
        is_covered = len(overlap) >= minimum_overlap and ratio >= 0.25
        covered.append(is_covered)
        token_positions = [folded_answer.find(token) for token in overlap]
        token_positions = [position for position in token_positions if position >= 0]
        positions.append(
            int(statistics.median(token_positions))
            if is_covered and token_positions
            else None
        )
    return covered, positions


def _trace_coverage_ratio(trace: Mapping[str, Any] | None) -> float | None:
    if not isinstance(trace, Mapping):
        return None
    claim_validation = trace.get("claim_validation")
    candidates = [claim_validation, trace]
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        value = candidate.get("coverage_ratio")
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
    return None


def assess_answer_completeness(
    *,
    question: str,
    answer: str,
    sources: Sequence[Mapping[str, Any]] = (),
    orchestration_trace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess fulfilment independently from legal grounding.

    The returned projection is public-safe: it contains only counts, booleans,
    ratios and stable reason codes; never source text or internal identifiers.
    """

    folded_question = _fold(question)
    folded_answer = _fold(answer)
    exact_sources = _matching_exact_sources(question, sources)
    exact_plan = plan_exact_lookup(question)
    requires_structural_coverage = bool(
        exact_plan.law_number
        and exact_plan.article_number
        and _contains_any(folded_question, _STRUCTURAL_REQUEST_MARKERS)
    )
    requires_order = _contains_any(folded_question, _ORDER_REQUEST_MARKERS)
    requires_plain_language = _contains_any(
        folded_question, _PLAIN_LANGUAGE_REQUEST_MARKERS
    )
    requires_practical_meaning = _contains_any(
        folded_question, _PRACTICAL_REQUEST_MARKERS
    )

    source_complete = False
    units: list[_StructuralUnit] = []
    covered_flags: list[bool] = []
    covered_positions: list[int | None] = []
    coverage_ratio = _trace_coverage_ratio(orchestration_trace)
    if requires_structural_coverage:
        provision_text, source_complete = _canonical_provision_text(exact_sources)
        units = _structural_units(provision_text)
        signatures = _unit_signatures(units)
        covered_flags, covered_positions = _covered_unit_positions(
            answer, signatures
        )
        coverage_ratio = (
            sum(covered_flags) / len(covered_flags) if covered_flags else 0.0
        )

    required_checks = ["coverage"]
    checks: dict[str, bool] = {
        "coverage": bool(
            coverage_ratio is not None
            and coverage_ratio >= 0.9
            and (not requires_structural_coverage or source_complete)
        )
    }
    if requires_order:
        required_checks.append("order")
        ordered_positions = [
            position for position in covered_positions if position is not None
        ]
        checks["order"] = bool(
            checks["coverage"]
            and ordered_positions
            and ordered_positions == sorted(ordered_positions)
        )
    if requires_plain_language:
        required_checks.append("plain_language")
        checks["plain_language"] = _contains_any(
            folded_answer, _PLAIN_LANGUAGE_ANSWER_MARKERS
        )
    if requires_practical_meaning:
        required_checks.append("practical_meaning")
        checks["practical_meaning"] = _contains_any(
            folded_answer, _PRACTICAL_ANSWER_MARKERS
        )

    assessment_available = bool(
        answer.strip()
        and coverage_ratio is not None
        and (not requires_structural_coverage or units)
    )
    status = (
        "complete"
        if assessment_available and all(checks.get(name, False) for name in required_checks)
        else "incomplete"
        if assessment_available
        else "not_assessed"
    )
    reason_codes = [
        f"missing_{name}" for name in required_checks if not checks.get(name, False)
    ]
    if requires_structural_coverage and not source_complete:
        reason_codes.append("source_structure_unproven")
    if not assessment_available:
        reason_codes.append("assessment_evidence_unavailable")

    return {
        "status": status,
        "coverage_ratio": round(float(coverage_ratio or 0.0), 4),
        "source_unit_count": len(units),
        "covered_unit_count": sum(covered_flags),
        "required_checks": required_checks,
        "checks": checks,
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }


__all__ = ["assess_answer_completeness"]
