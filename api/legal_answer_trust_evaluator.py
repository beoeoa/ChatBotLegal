"""Deterministic rubric evaluator for legally reviewed QA cases.

This module does not ask a language model to judge another model.  It evaluates
only explicit source, validity, claim and fallback evidence from a structured
run.  In particular, legacy ``has_sources`` flags are intentionally ignored.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping


def _norm(value: Any) -> str:
    return re.sub(
        r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))
    ).strip().casefold()


def _citation_value(citation: Mapping[str, Any], field: str) -> str:
    aliases = {
        "article": ("article", "article_number"),
        "clause": ("clause", "clause_number"),
        "point": ("point", "point_number"),
        "law_number": ("law_number", "document_number"),
    }
    for alias in aliases[field]:
        value = citation.get(alias)
        if value is not None:
            return _norm(value)
    return ""


def _source_matches(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    for field in ("law_number", "article", "clause", "point"):
        wanted = _norm(expected.get(field))
        if wanted and wanted != _citation_value(actual, field):
            return False
    return True


def _validated_claim_ids(run: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for claim in run.get("claim_validation") or []:
        if not isinstance(claim, Mapping):
            continue
        if _norm(claim.get("status")) in {"verified", "supported", "accepted"}:
            claim_id = _norm(claim.get("claim_id"))
            if claim_id:
                result.add(claim_id)
    return result


def evaluate_qa_run(
    golden: Mapping[str, Any], run: Mapping[str, Any]
) -> dict[str, Any]:
    citations = [
        item for item in (run.get("citations") or []) if isinstance(item, Mapping)
    ]
    expected_sources = [
        item
        for item in (golden.get("expected_sources") or [])
        if isinstance(item, Mapping)
    ]
    forbidden_sources = [
        item
        for item in (golden.get("forbidden_sources") or [])
        if isinstance(item, Mapping)
    ]
    required_claims = sorted(
        [
            item
            for item in (golden.get("required_claims") or [])
            if isinstance(item, Mapping)
        ],
        key=lambda item: int(item.get("order") or 0),
    )
    source_view_refusal = bool(golden.get("expected_refusal")) and _norm(
        golden.get("expected_answer_mode")
    ) == "source_view_only"

    correct_source = not expected_sources or all(
        any(_source_matches(expected, actual) for actual in citations)
        for expected in expected_sources
    )
    correct_article = not expected_sources or all(
        not _norm(expected.get("article"))
        or any(_source_matches(expected, actual) for actual in citations)
        for expected in expected_sources
    )
    no_forbidden_source = not any(
        _source_matches(forbidden, actual)
        for forbidden in forbidden_sources
        for actual in citations
    )

    validity_states = {
        _norm(item.get("effective_status") or item.get("validity_status"))
        for item in citations
    }
    current_validity = source_view_refusal or all(
        state == "effective" for state in validity_states
    )

    verified_claim_ids = _validated_claim_ids(run)
    critical_ids = {
        _norm(item.get("claim_id"))
        for item in required_claims
        if item.get("critical", True)
    }
    critical_claim_coverage = not critical_ids or critical_ids.issubset(
        verified_claim_ids
    )

    answer = _norm(run.get("answer"))
    positions = [answer.find(_norm(item.get("text"))) for item in required_claims]
    claim_order = not positions or (
        all(position >= 0 for position in positions)
        and positions == sorted(positions)
    )

    verification_levels = {
        _norm(item.get("verification_level") or item.get("citation_level"))
        for item in citations
    }
    has_claim_grade_evidence = bool(citations) and all(
        level in {"content_quote", "physical_span"} for level in verification_levels
    )
    no_fabrication = source_view_refusal or (
        no_forbidden_source
        and (not required_claims or has_claim_grade_evidence)
        and critical_claim_coverage
    )
    fallback_label = _norm(run.get("answer_mode")) == _norm(
        golden.get("expected_answer_mode")
    )
    practical_explanation = bool(answer) and (
        source_view_refusal or len(answer.split()) >= 6
    )

    rubrics = {
        "correct_source": correct_source,
        "correct_article": correct_article,
        "current_validity": current_validity,
        "no_forbidden_source": no_forbidden_source,
        "critical_claim_coverage": critical_claim_coverage,
        "claim_order": claim_order,
        "practical_explanation": practical_explanation,
        "no_fabrication": no_fabrication,
        "fallback_label": fallback_label,
    }
    reason_codes = [
        f"failed_{name}" for name, passed in rubrics.items() if not passed
    ]
    return {
        "case_id": golden.get("case_id"),
        "passed": all(rubrics.values()),
        "rubrics": rubrics,
        "reason_codes": reason_codes,
        "metrics": {
            "expected_source_count": len(expected_sources),
            "citation_count": len(citations),
            "critical_claim_count": len(critical_ids),
            "verified_critical_claim_count": len(
                critical_ids.intersection(verified_claim_ids)
            ),
        },
    }
