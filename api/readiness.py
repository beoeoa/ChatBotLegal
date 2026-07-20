"""Privacy-safe dependency readiness checks for the API process.

`/health` intentionally remains a process liveness probe.  This module checks
the dependencies required before the API should receive Ask traffic without
generating model output or returning raw exception/configuration values.
"""

from __future__ import annotations

import asyncio
import os
import re
from time import perf_counter
from typing import Any

import httpx

from api.credentials_service import PROVIDER_ENV_CONFIG
from open_notebook.ai.models import Model, model_manager
from open_notebook.database.repository import repo_query
from open_notebook.domain.credential import Credential


REQUIRED_COMPONENTS = ("database", "legal_retrieval", "model_provider")
LOCAL_MODEL_PROVIDERS = {"ollama", "huggingface"}
RECOMMENDED_LOCAL_MODEL = "qwen2.5:3b"
_SAFE_DEVICE = re.compile(r"^(?:auto|cpu|mps|cuda(?::\d+)?)$", re.IGNORECASE)
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")


def _timeout_seconds(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return min(max(value, 0.1), 30.0)


def _latency_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


def _safe_device(value: Any) -> str:
    candidate = str(value or "unknown").strip().lower()
    return candidate if _SAFE_DEVICE.fullmatch(candidate) else "unknown"


def _safe_label(value: Any) -> str | None:
    candidate = str(value or "").strip()
    return candidate if _SAFE_LABEL.fullmatch(candidate) else None


def _component(
    *,
    healthy: bool,
    code: str,
    started: float,
    **public_details: Any,
) -> dict[str, Any]:
    return {
        "healthy": healthy,
        "code": code,
        "latency_ms": _latency_ms(started),
        **{key: value for key, value in public_details.items() if value is not None},
    }


async def check_database() -> dict[str, Any]:
    """Run a bounded, read-only SurrealDB query."""
    started = perf_counter()
    try:
        result = await asyncio.wait_for(
            # SurrealDB 2.6 accepts a scalar RETURN statement.  The SQL-style
            # ``AS ready`` alias is invalid SurrealQL and made healthy
            # databases fail the readiness gate.
            repo_query("RETURN 1;"),
            timeout=_timeout_seconds("READINESS_DATABASE_TIMEOUT_SECONDS", 2.0),
        )
        if not result:
            return _component(
                healthy=False,
                code="empty_response",
                started=started,
            )
        return _component(healthy=True, code="ready", started=started)
    except TimeoutError:
        return _component(healthy=False, code="timeout", started=started)
    except Exception:
        # Never forward the raw exception: connection errors may contain a DSN.
        return _component(
            healthy=False,
            code="connection_failed",
            started=started,
        )


async def check_legal_retrieval(
    *, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Check the retrieval service and expose only an allow-list of fields."""
    started = perf_counter()
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=_timeout_seconds("READINESS_RETRIEVAL_TIMEOUT_SECONDS", 5.0)
        )

    try:
        base_url = os.getenv(
            "LEGAL_SEARCH_URL", "http://host.docker.internal:8765"
        ).rstrip("/")
        response = await client.get(f"{base_url}/health")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("status") not in {
            "healthy",
            "ready",
        }:
            return _component(
                healthy=False,
                code="invalid_response",
                started=started,
                embedding_device="unknown",
            )

        indexed_records = data.get("indexed_records")
        if not isinstance(indexed_records, int) or indexed_records < 0:
            indexed_records = None
        active_device = _safe_device(
            data.get("embedding_device") or data.get("device")
        )
        return _component(
            healthy=True,
            code="ready",
            started=started,
            indexed_records=indexed_records,
            embedding_device=active_device,
        )
    except httpx.TimeoutException:
        return _component(
            healthy=False,
            code="timeout",
            started=started,
            embedding_device="unknown",
        )
    except httpx.HTTPStatusError:
        return _component(
            healthy=False,
            code="http_error",
            started=started,
            embedding_device="unknown",
        )
    except Exception:
        return _component(
            healthy=False,
            code="connection_failed",
            started=started,
            embedding_device="unknown",
        )
    finally:
        if owns_client:
            await client.aclose()


def _environment_configured(provider: str) -> bool:
    requirements = PROVIDER_ENV_CONFIG.get(provider)
    if not requirements:
        return False
    required = requirements.get("required", [])
    required_any = requirements.get("required_any", [])
    if required and not all(os.getenv(name) for name in required):
        return False
    if required_any and not any(os.getenv(name) for name in required_any):
        return False
    return bool(required or required_any)


def _credential_configured(provider: str, credential: Any) -> bool:
    if credential is None or getattr(credential, "decryption_error", None):
        return False
    if provider == "vertex":
        return bool(
            getattr(credential, "project", None)
            and getattr(credential, "location", None)
        )
    if provider == "azure":
        endpoint = (
            getattr(credential, "endpoint", None)
            or getattr(credential, "base_url", None)
            or getattr(credential, "endpoint_llm", None)
        )
        return bool(
            getattr(credential, "api_key", None)
            and endpoint
            and getattr(credential, "api_version", None)
        )
    if provider in {"openai-compatible", "openai_compatible"}:
        return bool(
            getattr(credential, "base_url", None)
            or getattr(credential, "api_key", None)
        )
    return bool(getattr(credential, "api_key", None))


async def _model_configuration_available(model: Model, provider: str) -> bool:
    if _environment_configured(provider):
        return True

    credential_id = getattr(model, "credential", None)
    if credential_id:
        try:
            credential = await Credential.get(str(credential_id))
        except Exception:
            return False
        return _credential_configured(provider, credential)

    try:
        credentials = await Credential.get_by_provider(provider)
    except Exception:
        return False
    return any(_credential_configured(provider, item) for item in credentials)


async def check_model_provider() -> dict[str, Any]:
    """Resolve the configured default cloud model without invoking generation."""
    started = perf_counter()
    try:
        defaults = await asyncio.wait_for(
            model_manager.get_defaults(),
            timeout=_timeout_seconds("READINESS_MODEL_TIMEOUT_SECONDS", 3.0),
        )
        model_id = str(getattr(defaults, "default_chat_model", None) or "").strip()
        if not model_id:
            return _component(
                healthy=False,
                code="default_model_missing",
                started=started,
            )

        model = await asyncio.wait_for(
            Model.get(model_id),
            timeout=_timeout_seconds("READINESS_MODEL_TIMEOUT_SECONDS", 3.0),
        )
        provider = str(getattr(model, "provider", "") or "").strip().lower()
        model_type = str(getattr(model, "type", "") or "").strip().lower()
        public_provider = _safe_label(provider)
        public_model = _safe_label(getattr(model, "name", None))

        if model_type != "language":
            return _component(
                healthy=False,
                code="invalid_model_type",
                started=started,
                provider=public_provider,
                model=public_model,
            )
        if provider in LOCAL_MODEL_PROVIDERS:
            return _component(
                healthy=False,
                code="cloud_model_required",
                started=started,
                provider=public_provider,
                model=public_model,
            )
        if not provider or not await _model_configuration_available(model, provider):
            return _component(
                healthy=False,
                code="provider_not_configured",
                started=started,
                provider=public_provider,
                model=public_model,
            )
        return _component(
            healthy=True,
            code="configured",
            started=started,
            provider=public_provider,
            model=public_model,
        )
    except TimeoutError:
        return _component(healthy=False, code="timeout", started=started)
    except Exception:
        return _component(
            healthy=False,
            code="model_resolution_failed",
            started=started,
        )


async def check_ollama(
    *, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Report optional local fallback status without affecting readiness."""
    started = perf_counter()
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=_timeout_seconds("READINESS_OLLAMA_TIMEOUT_SECONDS", 2.0)
        )
    try:
        base_url = (
            os.getenv("OLLAMA_URL")
            or os.getenv("OLLAMA_API_BASE")
            or "http://host.docker.internal:11434"
        ).rstrip("/")
        response = await client.get(f"{base_url}/api/tags")
        response.raise_for_status()
        data = response.json()
        installed = {
            str(item.get("name") or "")
            for item in data.get("models", [])
            if isinstance(item, dict)
        }
        return _component(
            healthy=True,
            code="ready",
            started=started,
            recommended_model=RECOMMENDED_LOCAL_MODEL,
            recommended_installed=RECOMMENDED_LOCAL_MODEL in installed,
        )
    except httpx.TimeoutException:
        return _component(healthy=False, code="timeout", started=started)
    except httpx.HTTPStatusError:
        return _component(healthy=False, code="http_error", started=started)
    except Exception:
        return _component(
            healthy=False,
            code="connection_failed",
            started=started,
        )
    finally:
        if owns_client:
            await client.aclose()


async def collect_readiness() -> dict[str, Any]:
    database, legal_retrieval, model_provider, ollama = await asyncio.gather(
        check_database(),
        check_legal_retrieval(),
        check_model_provider(),
        check_ollama(),
    )
    components = {
        "database": database,
        "legal_retrieval": legal_retrieval,
        "model_provider": model_provider,
        "ollama": {**ollama, "required": False},
    }
    ready = all(components[name].get("healthy") for name in REQUIRED_COMPONENTS)
    requested_device = _safe_device(os.getenv("LEGAL_EMBED_DEVICE", "auto"))
    active_device = _safe_device(legal_retrieval.get("embedding_device"))
    return {
        "status": "ready" if ready else "not_ready",
        "components": components,
        "embedding_device": {
            "requested": requested_device,
            "active": active_device,
        },
    }
