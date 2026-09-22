"""Provider-neutral capability contract for language-model adapters.

Capabilities are keyed by the exact provider/model identity. Unknown models
fall back to a conservative provider profile; runtime code never infers
transport or reasoning behavior from substrings in a display name.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ModelCapabilities:
    provider: str
    model: str
    transport: str = "openai_chat"
    streaming: bool = True
    structured_output: bool = False
    reasoning_control: str = "none"
    finish_reason: bool = True
    usage_metadata: bool = True
    context_limit: int | None = None
    output_limit: int | None = None
    profile_source: str = "provider_default"
    supported_parameters: tuple[str, ...] | None = None
    reasoning: dict = field(default_factory=dict)
    verified_at: str | None = None
    endpoint_host: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _key(provider: str, model: str) -> str:
    return f"{str(provider or '').strip().casefold()}::{str(model or '').strip().casefold()}"


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "config" / "model-capabilities.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def capabilities_for(provider: str, model: str) -> ModelCapabilities:
    provider_id = str(provider or "configured").strip().casefold()
    model_id = str(model or "configured").strip()
    catalog = _catalog()
    defaults = dict((catalog.get("provider_defaults") or {}).get(provider_id) or {})
    exact = dict((catalog.get("models") or {}).get(_key(provider_id, model_id)) or {})
    values = {**defaults, **exact}
    return ModelCapabilities(
        provider=provider_id,
        model=model_id,
        transport=str(values.get("transport") or ("ollama_chat" if provider_id == "ollama" else "openai_chat")),
        streaming=bool(values.get("streaming", True)),
        structured_output=bool(values.get("structured_output", False)),
        reasoning_control=str(values.get("reasoning_control") or "none"),
        finish_reason=bool(values.get("finish_reason", True)),
        usage_metadata=bool(values.get("usage_metadata", True)),
        context_limit=int(values["context_limit"]) if values.get("context_limit") else None,
        output_limit=int(values["output_limit"]) if values.get("output_limit") else None,
        profile_source=values.get("profile_source") or ("exact" if exact else "provider_default"),
        supported_parameters=tuple(values["supported_parameters"]) if "supported_parameters" in values else None,
        reasoning=dict(values.get("reasoning") or {}),
        verified_at=values.get("verified_at"), endpoint_host=values.get("endpoint_host"),
    )


def capabilities_for_model(model: Any) -> ModelCapabilities:
    provider = str(getattr(model, "provider", None) or "configured")
    name = str(
        getattr(model, "name", None)
        or getattr(model, "model_name", None)
        or getattr(model, "id", None)
        or "configured"
    )
    return capabilities_for(provider, name)


def reported_identity_match(requested: str | None, reported: str | None) -> bool | None:
    """Different response labels may be provider aliases; do not infer a swap."""
    if requested and reported and requested.casefold() == reported.casefold():
        return True
    return None


def normalize_generation_options(
    options: Mapping[str, Any],
    capabilities: ModelCapabilities,
) -> dict[str, Any]:
    """Drop optional parameters the selected model has not declared."""

    normalized = dict(options)
    if not capabilities.streaming:
        normalized["streaming"] = False
    if not capabilities.structured_output:
        normalized.pop("structured", None)
    if capabilities.reasoning_control != "max_tokens":
        normalized.pop("reasoning_budget", None)
    supported = capabilities.supported_parameters
    if supported is not None:
        for parameter in ("temperature", "top_p"):
            if parameter not in supported:
                normalized.pop(parameter, None)
    reasoning = capabilities.reasoning
    depth = str(normalized.get("answer_depth") or "balanced")
    if reasoning and "reasoning" not in normalized:
        efforts = reasoning.get("supported_efforts") or []
        if depth == "quick" and reasoning.get("mandatory") is False:
            normalized["reasoning"] = ({"effort": "none"} if "none" in efforts else {"enabled": False})
        elif efforts:
            preferred = ("high", "medium", "low") if depth == "deep" else ("low", "minimal", "medium", "high")
            effort = next((v for v in preferred if v in efforts), None)
            if effort:
                normalized["reasoning"] = {"effort": effort, "exclude": True}
        elif reasoning.get("supports_max_tokens"):
            visible = int(normalized.get("max_tokens") or 1200)
            normalized["reasoning"] = {"max_tokens": max(256, visible // 2), "exclude": True}
    # Output includes reasoning. Preserve visible budget separately and expand
    # only once, even if normalization is called at both caller and adapter.
    if normalized.get("reasoning") and normalized["reasoning"].get("enabled") is not False and normalized["reasoning"].get("effort") != "none" and "visible_output_tokens" not in normalized:
        visible = int(normalized.get("max_tokens") or 1200)
        normalized["visible_output_tokens"] = visible
        normalized["max_tokens"] = visible + int(normalized["reasoning"].get("max_tokens") or visible * (3 if depth == "deep" else 1))
    if capabilities.output_limit and normalized.get("max_tokens"):
        normalized["max_tokens"] = min(
            int(normalized["max_tokens"]), capabilities.output_limit
        )
    return normalized


__all__ = [
    "ModelCapabilities",
    "capabilities_for",
    "capabilities_for_model",
    "normalize_generation_options",
]
