"""Concrete runtime budgets for the three public answer-depth choices."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Mapping

AnswerDepth = Literal["quick", "balanced", "deep"]


@dataclass(frozen=True)
class AnswerDepthProfile:
    name: AnswerDepth
    evidence_rows: int
    evidence_chars: int
    output_tokens_min: int
    output_tokens_max: int
    generation_timeout_seconds: float
    total_timeout_seconds: float
    allow_recovery: bool


_PROFILES = {
    "quick": AnswerDepthProfile(
        name="quick",
        evidence_rows=8,
        evidence_chars=8_000,
        output_tokens_min=700,
        output_tokens_max=1_200,
        generation_timeout_seconds=20.0,
        total_timeout_seconds=40.0,
        allow_recovery=False,
    ),
    "balanced": AnswerDepthProfile(
        name="balanced",
        evidence_rows=10,
        evidence_chars=16_000,
        output_tokens_min=1_600,
        output_tokens_max=2_400,
        generation_timeout_seconds=30.0,
        total_timeout_seconds=60.0,
        allow_recovery=True,
    ),
    "deep": AnswerDepthProfile(
        name="deep",
        evidence_rows=24,
        evidence_chars=32_000,
        output_tokens_min=2_400,
        output_tokens_max=4_000,
        generation_timeout_seconds=45.0,
        total_timeout_seconds=90.0,
        allow_recovery=True,
    ),
}


def answer_depth_profile(value: str | None) -> AnswerDepthProfile:
    return _PROFILES.get(str(value or "balanced").strip().casefold(), _PROFILES["balanced"])


def direct_rag_total_timeout_seconds(
    answer_depth: str | None,
    environ: Mapping[str, str] | None = None,
) -> float:
    """Return the one end-to-end Direct RAG deadline for a public turn.

    The answer-depth contract is a lower bound. An operator can grant more
    time, but a stale lower environment value must not silently collapse the
    balanced/deep profiles. The browser supports at most 120 seconds.
    """

    values = os.environ if environ is None else environ
    profile = answer_depth_profile(answer_depth)
    try:
        configured = float(
            values.get("LEGAL_DIRECT_RAG_TOTAL_TIMEOUT_SECONDS", "45")
        )
    except (TypeError, ValueError):
        configured = 45.0
    return min(120.0, max(10.0, configured, profile.total_timeout_seconds))


__all__ = [
    "AnswerDepthProfile",
    "answer_depth_profile",
    "direct_rag_total_timeout_seconds",
]
