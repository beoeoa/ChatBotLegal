"""Deterministic policy for legal-answer fallback when an AI provider fails.

The policy never turns provider availability into legal evidence.  It only
decides whether already verified evidence can be rendered by the deterministic
extractive pipeline or whether the user must be limited to source viewing.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from api.ask_progress import cloud_fallback_reason
from api.legal_exact_retrieval import plan_exact_lookup

NORMAL = "normal"
VERIFIED_SOURCE_CONDENSED = "verified_source_condensed"
SOURCE_VIEW_ONLY = "source_view_only"


def answer_fallback_enabled() -> bool:
    """Return whether provider/deterministic answer fallback is enabled.

    This is an explicitly opt-in local diagnostic switch. Grounding and legal
    claim validation remain enforced even when the switch is disabled.
    """

    return str(os.getenv("LEGAL_ANSWER_FALLBACK_ENABLED", "true")).strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def classify_structured_provider_error(exc: BaseException) -> str:
    """Return one stable reason without collapsing provider/output failures."""

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "provider_timeout"
    provider_reason = cloud_fallback_reason(exc)
    if provider_reason:
        return provider_reason
    text = str(exc).casefold()
    if isinstance(exc, (ValueError, ValidationError)) and any(
        marker in text
        for marker in (
            "not_valid_structured_json",
            "invalid structured",
            "invalid json",
            "model output",
            "validation error",
        )
    ):
        return "invalid_output"
    return "validation_failed"


def _trace_is_fully_verified(quality_trace: Mapping[str, Any]) -> bool:
    quality_gate = quality_trace.get("quality_gate")
    accepted = int(quality_trace.get("accepted_claim_count") or 0)
    rejected = int(quality_trace.get("rejected_claim_count") or 0)
    displayed = int(quality_trace.get("displayed_legal_claim_count") or 0)
    displayed_with_evidence = int(
        quality_trace.get("displayed_claims_with_valid_evidence") or 0
    )
    # Coverage is an advisory facet-level signal (T088). A provider failure
    # may leave one requested facet unresolved while deterministic rendering
    # still exposes independently grounded claims from another facet. The
    # condensed mode therefore requires every *displayed* claim to be bound,
    # but does not require every planned issue to have a claim. Exact Article
    # completeness remains gated separately below.
    issues = quality_trace.get("issues")
    issue_bindings_are_safe = bool(isinstance(issues, list) and issues) and all(
        isinstance(issue, Mapping)
        and int(issue.get("rejected_claim_count") or 0) == 0
        for issue in issues
    )
    return bool(
        isinstance(quality_gate, Mapping)
        and quality_gate.get("pass") is True
        and float(quality_trace.get("claim_grounding_ratio") or 0.0) >= 1.0
        and accepted > 0
        and rejected == 0
        and displayed > 0
        and displayed_with_evidence == displayed
        and issue_bindings_are_safe
    )


def _has_complete_exact_article_context(
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> bool:
    for row in evidence_by_id.values():
        if str(row.get("exact_article_packet_status") or "") == "complete":
            return True
        if (
            str(row.get("parent_context_reason") or "")
            in {"complete", "complete_exact_article"}
            and not bool(row.get("parent_context_truncated"))
            and bool(str(row.get("parent_context") or "").strip())
        ):
            return True
    return False


def choose_verified_fallback_mode(
    *,
    question: str,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    quality_trace: Mapping[str, Any],
) -> str:
    """Choose condensed output only when deterministic validation proves it."""

    if not evidence_by_id or not _trace_is_fully_verified(quality_trace):
        return SOURCE_VIEW_ONLY
    exact = plan_exact_lookup(question)
    if exact.law_number and exact.article_number:
        return (
            VERIFIED_SOURCE_CONDENSED
            if _has_complete_exact_article_context(evidence_by_id)
            else SOURCE_VIEW_ONLY
        )
    return VERIFIED_SOURCE_CONDENSED


def fallback_mode_message(mode: str) -> str:
    if mode == VERIFIED_SOURCE_CONDENSED:
        return "Nguồn đã xác minh nhưng câu trả lời đang ở chế độ rút gọn."
    if mode == SOURCE_VIEW_ONLY:
        return (
            "Nguồn đã xác minh nhưng hệ thống chưa thể tổng hợp an toàn. "
            "Vui lòng xem văn bản gốc và thử lại."
        )
    return ""


__all__ = [
    "NORMAL",
    "SOURCE_VIEW_ONLY",
    "VERIFIED_SOURCE_CONDENSED",
    "answer_fallback_enabled",
    "choose_verified_fallback_mode",
    "classify_structured_provider_error",
    "fallback_mode_message",
]
