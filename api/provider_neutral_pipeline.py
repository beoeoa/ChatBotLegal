"""Provider-neutral orchestration policy for the public Ask pipeline.

The user's selected model is an answer renderer.  Routing and retrieval are
backend-owned.  Provider-specific JSON mode remains an explicit experiment and
is never a prerequisite for a selectable chat model.
"""

from __future__ import annotations

import os
from typing import Literal, Mapping

SemanticPlannerMode = Literal["deterministic", "model_json"]
AnswerEnvelopeMode = Literal["markdown", "json"]
SelectedPlannerMode = Literal["off", "ambiguous", "expanded"]

_TRUE_VALUES = {"1", "true", "yes", "on"}
_MODEL_PLANNER_VALUES = {"model", "model_json", "json", "legacy"}


def semantic_planner_mode(
    environ: Mapping[str, str] | None = None,
) -> SemanticPlannerMode:
    """Return the routing mode, defaulting to the deterministic backend plan.

    ``CHAT_SEMANTIC_TURN_PLAN_ENABLED=true`` remains a compatibility opt-in for
    existing deployments.  An unset legacy flag no longer enables a model call.
    """

    values = os.environ if environ is None else environ
    configured = str(values.get("CHAT_SEMANTIC_TURN_PLAN_MODE", "")).strip().casefold()
    if configured:
        return "model_json" if configured in _MODEL_PLANNER_VALUES else "deterministic"
    legacy = values.get("CHAT_SEMANTIC_TURN_PLAN_ENABLED")
    if legacy is not None and str(legacy).strip().casefold() in _TRUE_VALUES:
        return "model_json"
    return "deterministic"


def should_invoke_model_planner(
    *,
    resolved_quick_route: bool,
    environ: Mapping[str, str] | None = None,
) -> bool:
    return not resolved_quick_route and semantic_planner_mode(environ) == "model_json"


def answer_envelope_mode(
    environ: Mapping[str, str] | None = None,
) -> AnswerEnvelopeMode:
    """Keep provider output as Markdown unless legacy JSON is explicitly enabled."""

    values = os.environ if environ is None else environ
    configured = str(
        values.get("CHAT_ANSWER_ENVELOPE_MODE", "markdown")
    ).strip().casefold()
    return "json" if configured in {"json", "structured", "legacy"} else "markdown"


def uses_structured_answer_envelope(
    environ: Mapping[str, str] | None = None,
) -> bool:
    return answer_envelope_mode(environ) == "json"


def selected_model_planner_mode(
    answer_depth: str | None,
    environ: Mapping[str, str] | None = None,
) -> SelectedPlannerMode:
    """Return the optional selected-model advisory mode for a public turn.

    Quick requests preserve their one-call contract. Balanced and deep turns
    may consult the same model selected for the final answer, but only as an
    advisory step: invalid output must fall back to deterministic routing.
    Operators can disable the advisory call globally without changing legal
    retrieval or model selection.
    """

    # Quick is the public one-call preference.  A global operator value such as
    # ``ambiguous`` must not silently turn it into a two-model-call path.
    depth = str(answer_depth or "balanced").strip().casefold()
    if depth == "quick":
        return "off"

    values = os.environ if environ is None else environ
    configured = str(
        values.get("CHAT_SELECTED_MODEL_PLANNER_MODE", "auto")
    ).strip().casefold()
    if configured in {"0", "false", "off", "none", "disabled"}:
        return "off"
    if configured in {"ambiguous", "balanced"}:
        return "ambiguous"
    if configured in {"expanded", "deep", "always"}:
        return "expanded"

    if depth == "deep":
        return "expanded"
    return "ambiguous"


def answer_reasoning_budget(answer_depth: str | None, max_tokens: int) -> int:
    """Reserve useful reasoning while leaving room for the visible answer.

    OpenRouter normalizes this budget for models that expose reasoning. The
    proportions express one product policy across providers instead of tuning
    the prompt for a preferred model.
    """

    available = max(0, int(max_tokens))
    depth = str(answer_depth or "balanced").strip().casefold()
    ratio, cap = {
        "quick": (0.10, 128),
        "balanced": (0.25, 768),
        "deep": (0.40, 1600),
    }.get(depth, (0.25, 768))
    return min(cap, max(32, int(available * ratio)))


__all__ = [
    "answer_envelope_mode",
    "answer_reasoning_budget",
    "semantic_planner_mode",
    "selected_model_planner_mode",
    "should_invoke_model_planner",
    "uses_structured_answer_envelope",
]
