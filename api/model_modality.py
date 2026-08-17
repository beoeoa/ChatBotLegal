"""Fail-closed model modality validation shared by configuration and runtime."""

from __future__ import annotations

import math
from typing import Any, Sequence

from open_notebook.ai.model_discovery import classify_model_type


MODEL_TYPES = {"language", "embedding", "text_to_speech", "speech_to_text"}
_FIXED_PROVIDER_TYPES = {
    "anthropic": {"language"},
    "deepseek": {"language"},
    "openrouter": {"language"},
    "voyage": {"embedding"},
    "huggingface": {"embedding"},
    "dashscope": {"language"},
    "minimax": {"language"},
    "elevenlabs": {"text_to_speech", "speech_to_text"},
    "deepgram": {"text_to_speech"},
}
_NAME_CLASSIFIED_PROVIDERS = {
    "openai",
    "google",
    "ollama",
    "mistral",
    "groq",
    "deepseek",
    "xai",
    "voyage",
    "elevenlabs",
    "deepgram",
    "dashscope",
    "minimax",
}
_OPAQUE_DEPLOYMENT_PROVIDERS = {"azure", "openai_compatible", "vertex"}


def infer_discovered_model_modality(*, provider: str, model_name: str) -> str:
    """Return the safest deterministic modality for provider discovery output."""

    provider_key = str(provider or "").strip().casefold().replace("-", "_")
    fixed = _FIXED_PROVIDER_TYPES.get(provider_key)
    if fixed and len(fixed) == 1:
        return next(iter(fixed))
    if provider_key == "openrouter":
        return "language"
    if provider_key == "vertex":
        return classify_model_type(model_name, "google")
    if provider_key in _NAME_CLASSIFIED_PROVIDERS:
        return classify_model_type(model_name, provider_key)
    return "language"


def validate_provider_model_modality(
    *, provider: str, model_name: str, requested_type: str
) -> str:
    """Reject known provider/model combinations that cannot emit the requested type."""

    provider_key = str(provider or "").strip().casefold().replace("-", "_")
    model = str(model_name or "").strip()
    requested = str(requested_type or "").strip().casefold()
    if requested not in MODEL_TYPES:
        raise ValueError("model_modality_invalid")
    if not provider_key or not model:
        raise ValueError("model_identity_missing")
    fixed = _FIXED_PROVIDER_TYPES.get(provider_key)
    if fixed is not None and requested not in fixed:
        raise ValueError(
            f"model_modality_mismatch:{provider_key}:{model}:{requested}"
        )
    if provider_key in _OPAQUE_DEPLOYMENT_PROVIDERS:
        return requested
    if provider_key in _NAME_CLASSIFIED_PROVIDERS:
        inferred = classify_model_type(model, provider_key)
        if inferred != requested:
            raise ValueError(
                f"model_modality_mismatch:{provider_key}:{model}:{requested}:{inferred}"
            )
    return requested


def validate_embedding_output(
    output: Any, *, expected_count: int
) -> list[list[float]]:
    """Validate that a provider actually returned a finite embedding matrix."""

    if not isinstance(output, Sequence) or isinstance(output, (str, bytes)):
        raise ValueError("embedding_output_not_matrix")
    if len(output) != int(expected_count):
        raise ValueError("embedding_output_count_mismatch")
    vectors: list[list[float]] = []
    dimensions: int | None = None
    for row in output:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or not row:
            raise ValueError("embedding_output_not_vector")
        try:
            vector = [float(value) for value in row]
        except (TypeError, ValueError) as exc:
            raise ValueError("embedding_output_non_numeric") from exc
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("embedding_output_non_finite")
        if dimensions is None:
            dimensions = len(vector)
        elif len(vector) != dimensions:
            raise ValueError("embedding_output_dimension_mismatch")
        vectors.append(vector)
    if not vectors or not dimensions:
        raise ValueError("embedding_output_empty")
    return vectors
