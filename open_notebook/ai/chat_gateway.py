"""Owned async Chat Completions transport; no SDK chunk aggregation or retries.

Connections are shared, invocation options are not. Resolve credentials once per
turn, so rotation takes effect on the next turn without caching stale adapters.
Only configured OpenAI-compatible providers use this transport. Other protocols
remain explicit adapters behind the same async interface.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx

from api.chat_execution import current_turn, remaining_timeout

_CLIENTS: dict[tuple, httpx.AsyncClient] = {}
_BASE_URLS = {"openrouter": "https://openrouter.ai/api/v1", "deepseek": "https://api.deepseek.com/v1", "openai": "https://api.openai.com/v1"}


def supports_provider(provider: str) -> bool:
    return provider in _BASE_URLS


def _message(payload: dict, *, stream: bool, request_id: str | None = None):
    if payload.get("error"):
        # Never include provider error bodies; they can echo credentials/prompts.
        raise RuntimeError("provider_stream_error")
    choice = next(iter(payload.get("choices") or []), {})
    content = choice.get("delta" if stream else "message") or {}
    return SimpleNamespace(
        content=content.get("content") or "",
        id=payload.get("id"),
        usage_metadata=None,
        response_metadata={k: v for k, v in {
            "model": payload.get("model"), "id": payload.get("id") or request_id,
            "finish_reason": choice.get("finish_reason"), "usage": payload.get("usage"),
            "provider_name": payload.get("provider"),
        }.items() if v is not None},
    )


@dataclass
class ChatAdapter:
    client: httpx.AsyncClient = field(repr=False)
    model_name: str
    provider: str
    options: dict[str, Any] = field(repr=False)
    streaming: bool = True

    def _payload(self, prompt: Any, stream: bool) -> dict:
        # Public Ask builders produce one plain prompt. Message arrays are
        # supported for callers that already separate system/user instructions.
        messages = (prompt.to_messages() if callable(getattr(prompt, "to_messages", None)) else
                    prompt if isinstance(prompt, list) else [{"role": "user", "content": str(prompt)}])
        payload = {"model": self.model_name, "messages": messages, "stream": stream}
        for key in ("temperature", "max_tokens", "top_p"):
            if self.options.get(key) is not None:
                payload[key] = self.options[key]
        if self.options.get("reasoning") is not None:
            reasoning = self.options["reasoning"]
            if self.provider == "deepseek":
                payload["thinking"] = {"type": "disabled" if reasoning.get("enabled") is False else "enabled"}
                if reasoning.get("effort"):
                    payload["reasoning_effort"] = reasoning["effort"]
                if payload["thinking"]["type"] == "enabled":
                    payload.pop("temperature", None)
                    payload.pop("top_p", None)
            else:
                payload["reasoning"] = reasoning
        if stream and self.provider == "openrouter":
            payload["stream_options"] = {"include_usage": True}
        if self.provider == "openrouter":
            # Otherwise an endpoint may silently ignore a model's declared
            # reasoning control and consume the answer budget on thinking.
            payload["provider"] = {"require_parameters": True}
            sort = os.getenv("CHAT_OPENROUTER_PROVIDER_SORT", "latency").strip().lower()
            if sort in {"latency", "throughput", "price"}:
                payload["provider"]["sort"] = sort
        return payload

    async def ainvoke(self, prompt: Any, **kwargs):
        timeout = remaining_timeout(float(self.options.get("timeout", 75)))
        async with asyncio.timeout(timeout):
            response = await self.client.post("chat/completions", json=self._payload(prompt, False), timeout=timeout)
            response.raise_for_status()
            return _message(response.json(), stream=False, request_id=response.headers.get("x-request-id"))

    async def astream(self, prompt: Any, **kwargs):
        if not self.streaming:
            yield await self.ainvoke(prompt, **kwargs)
            return
        timeout = remaining_timeout(float(self.options.get("timeout", 75)))
        async with asyncio.timeout(timeout):
            async with self.client.stream("POST", "chat/completions", json=self._payload(prompt, True), timeout=timeout) as response:
                response.raise_for_status()
                data: list[str] = []
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        data.append(line[5:].lstrip())
                    elif not line and data:
                        body = "\n".join(data)
                        data.clear()
                        if body == "[DONE]":
                            return
                        yield _message(json.loads(body), stream=True, request_id=response.headers.get("x-request-id"))
                if data and "\n".join(data) != "[DONE]":
                    yield _message(json.loads("\n".join(data)), stream=True)


async def resolve_chat_model(model_id: str):
    from open_notebook.ai.models import Model
    turn = current_turn.get()
    if turn and model_id in turn.resolved_models:
        return turn.resolved_models[model_id]
    model = await Model.get(model_id)
    if model.type != "language":
        raise ValueError("selected_model_is_not_language")
    config = {}
    if model.credential:
        credential = await model.get_credential_obj()
        if credential is None:
            raise ValueError("selected_model_credential_unavailable")
        config = credential.to_esperanto_config()
    else:
        from open_notebook.ai.key_provider import provision_provider_keys
        await provision_provider_keys(model.provider)
    resolved = (model, config)
    if turn:
        turn.resolved_models[model_id] = resolved
    return resolved


async def provision_chat_adapter(model_id: str, options: dict):
    model, config = await resolve_chat_model(model_id)
    provider = model.provider.lower()
    if not supports_provider(provider):
        return None
    api_key = config.get("api_key") or os.getenv(provider.upper() + "_API_KEY", "")
    if not api_key:
        raise ValueError("selected_model_credential_unavailable")
    endpoint = (config.get("base_url") or os.getenv(provider.upper() + "_BASE_URL") or _BASE_URLS[provider]).rstrip("/") + "/"
    # Hash is only an internal cache key; never report it or the credential.
    key = (id(asyncio.get_running_loop()), endpoint, hashlib.sha256(api_key.encode()).digest())
    client = _CLIENTS.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(base_url=endpoint, headers={"Authorization": f"Bearer {api_key}"}, limits=httpx.Limits(max_connections=32, max_keepalive_connections=16))
        _CLIENTS[key] = client
    from api.model_runtime_contract import capabilities_for_model, normalize_generation_options
    profile = capabilities_for_model(model)
    from urllib.parse import urlsplit
    if profile.endpoint_host and urlsplit(endpoint).netloc != profile.endpoint_host:
        from api.model_runtime_contract import ModelCapabilities
        profile = ModelCapabilities(provider=provider, model=model.name)
    normalized = normalize_generation_options(options, profile)
    return ChatAdapter(client, model.name, provider, normalized, bool(normalized.get("streaming", True)))


async def close_chat_clients():
    clients = list(_CLIENTS.values())
    _CLIENTS.clear()
    await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)
