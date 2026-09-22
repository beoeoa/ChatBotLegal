"""Single provider-neutral model gateway for chat and notebook generation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from api.model_execution import execute_answer_model
from api.model_runtime_contract import capabilities_for


def canonical_model_id(value: Any, *, provider: str | None = None) -> str:
    """Return one stable provider/model key used by all callers."""
    if isinstance(value, Mapping):
        provider = str(value.get("provider") or provider or "configured")
        value = value.get("canonical_model_id") or value.get("model") or value.get("name") or value.get("id")
    else:
        provider = str(getattr(value, "provider", None) or provider or "configured")
        value = getattr(value, "canonical_model_id", None) or getattr(value, "name", None) or getattr(value, "id", None) or value
    model = str(value or "configured").strip()
    # Persisted records already use a table/provider prefix (`model:…`,
    # `openrouter:…`).  Do not rewrite those IDs when the provider field is
    # absent or comes from a legacy client.
    if ":" in model:
        return model
    return f"{provider.strip().casefold()}:{model}"


@dataclass(frozen=True)
class GatewayResult:
    text: str
    model_id: str
    metadata: dict[str, Any]
    fallback_used: bool = False


class ModelGateway:
    """Own model invocation policy; callers must not implement provider loops."""

    def __init__(self, *, max_provider_fallbacks: int = 1) -> None:
        self.max_provider_fallbacks = max(0, min(1, int(max_provider_fallbacks)))

    async def generate(
        self,
        prompt: str,
        *,
        model_id: str,
        options: Mapping[str, Any] | None = None,
        slots: Any = None,
        emit: Any = None,
        structured: bool = False,
        fallback_model_id: str | None = None,
    ) -> GatewayResult:
        selected = canonical_model_id(model_id)
        request_options = dict(options or {})
        if slots is None:
            import asyncio
            slots = asyncio.Semaphore(1)
        started = time.perf_counter()
        try:
            text, metadata = await execute_answer_model(
                selected,
                prompt,
                options=request_options,
                slots=slots,
                emit=emit,
                structured=structured,
            )
            metadata = {**metadata, "gateway_ms": round((time.perf_counter() - started) * 1000, 1), "canonical_model_id": selected}
            return GatewayResult(text=text, model_id=selected, metadata=metadata)
        except (httpx.TransportError, TimeoutError) as error:
            if not fallback_model_id:
                fallback_model_id = await self._different_provider_model(selected)
            if not fallback_model_id or self.max_provider_fallbacks < 1:
                raise
            alternate = canonical_model_id(fallback_model_id)
            if alternate == selected or alternate.split(":", 1)[0] == selected.split(":", 1)[0]:
                raise
            text, metadata = await execute_answer_model(
                alternate,
                prompt,
                options=request_options,
                slots=slots,
                emit=emit,
                structured=structured,
            )
            metadata = {**metadata, "gateway_ms": round((time.perf_counter() - started) * 1000, 1), "canonical_model_id": alternate, "fallback_reason": type(error).__name__}
            return GatewayResult(text=text, model_id=alternate, metadata=metadata, fallback_used=True)

    @staticmethod
    async def _different_provider_model(primary_model_id: str) -> str | None:
        """Resolve at most one stable fallback from another configured provider."""
        try:
            from open_notebook.ai.models import Model

            models = await Model.get_models_by_type("language")
            primary = next(
                (item for item in models if str(item.id) == primary_model_id), None
            )
            primary_provider = str(getattr(primary, "provider", "") or "").casefold()
            candidates = sorted(models, key=lambda item: str(item.id))
            for item in candidates:
                if str(item.id) == primary_model_id:
                    continue
                provider = str(getattr(item, "provider", "") or "").casefold()
                if provider and provider != primary_provider:
                    return str(item.id)
        except Exception:
            return None
        return None

    @staticmethod
    def capabilities(model_id: str):
        provider, model = canonical_model_id(model_id).split(":", 1)
        return capabilities_for(provider, model)


default_model_gateway = ModelGateway()

__all__ = ["GatewayResult", "ModelGateway", "canonical_model_id", "default_model_gateway"]
