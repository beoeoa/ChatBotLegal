"""Privacy-safe dependency readiness checks for the API process.

`/health` intentionally remains a process liveness probe.  This module checks
the dependencies required before the API should receive Ask traffic without
generating model output or returning raw exception/configuration values.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable

import httpx

from api.credentials_service import PROVIDER_ENV_CONFIG
from api.import_worker_status import import_worker_component
from api.legal_validity_registry import default_registry, default_snapshot_cache
from open_notebook.ai.models import Model, model_manager
from open_notebook.database.repository import repo_query
from open_notebook.domain.credential import Credential

# Ask requests need a configured model provider in addition to the data plane.
# Importing and embedding approved legal records does not: it only depends on
# the database and the legal-retrieval service. Keep the two contracts explicit
# so a provider outage does not incorrectly block a verified legal import.
REQUIRED_COMPONENTS = ("database", "legal_retrieval", "model_provider")
IMPORT_REQUIRED_COMPONENTS = ("database", "legal_retrieval", "import_worker")
LOCAL_MODEL_PROVIDERS = {"ollama", "huggingface"}
RECOMMENDED_LOCAL_MODEL = "qwen2.5:3b"
_SAFE_DEVICE = re.compile(r"^(?:auto|cpu|mps|cuda(?::\d+)?)$", re.IGNORECASE)
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().casefold() in _TRUE_VALUES


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
            timeout=_timeout_seconds("READINESS_DATABASE_TIMEOUT_SECONDS", 8.0),
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
    """Require every configured immutable retrieval replica to be consistent."""
    started = perf_counter()
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=_timeout_seconds("READINESS_RETRIEVAL_TIMEOUT_SECONDS", 5.0)
        )

    replica_setting = str(os.getenv("LEGAL_SEARCH_URLS") or "").strip()
    candidates = (
        replica_setting.split(",")
        if replica_setting
        else [
            os.getenv(
                "LEGAL_SEARCH_URL",
                os.getenv("LEGAL_RETRIEVAL_V2_URL", "http://127.0.0.1:8766"),
            )
        ]
    )
    base_urls = tuple(
        dict.fromkeys(
            str(value or "").strip().rstrip("/")
            for value in candidates
            if str(value or "").strip()
        )
    )
    if not base_urls:
        return _component(
            healthy=False,
            code="configuration_missing",
            started=started,
            replica_count=0,
            healthy_replicas=0,
            embedding_device="unknown",
        )

    async def probe(base_url: str) -> tuple[str, dict[str, Any] | None]:
        try:
            response = await client.get(f"{base_url}/health")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("status") not in {
                "healthy",
                "ready",
            }:
                return "invalid_response", None
            return "ready", data
        except httpx.TimeoutException:
            return "timeout", None
        except httpx.HTTPStatusError:
            return "http_error", None
        except Exception:
            return "connection_failed", None

    try:
        probes = await asyncio.gather(*(probe(url) for url in base_urls))
        healthy_data = [data for code, data in probes if code == "ready" and data]
        if len(healthy_data) != len(base_urls):
            single_code = probes[0][0] if len(base_urls) == 1 else "replica_unavailable"
            return _component(
                healthy=False,
                code=single_code,
                started=started,
                replica_count=len(base_urls),
                healthy_replicas=len(healthy_data),
                embedding_device="unknown",
            )

        fingerprints = {
            (
                data.get("model_fingerprint"),
                data.get("collection"),
                data.get("indexed_records"),
                data.get("database_chunks"),
            )
            for data in healthy_data
        }
        if len(fingerprints) != 1:
            return _component(
                healthy=False,
                code="replica_fingerprint_mismatch",
                started=started,
                replica_count=len(base_urls),
                healthy_replicas=len(healthy_data),
                embedding_device="unknown",
            )

        data = healthy_data[0]
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
            replica_count=len(base_urls),
            healthy_replicas=len(healthy_data),
            replica_fingerprint_match=True,
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
    """Resolve the default cloud model and optionally perform a bounded probe.

    Configuration-only readiness remains the safe default for tests and
    installations that do not want a provider call. Release environments set
    ``READINESS_MODEL_PROBE_ENABLED=true`` so revoked credentials, exhausted
    balance and provider outages cannot be reported as healthy.
    """
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
            # A configured local model is a valid answer plane in both the
            # portable bundle and a normal local installation. Readiness must
            # reflect the model selected by the administrator instead of
            # requiring an unrelated cloud provider or one special packaging
            # flag.
            if provider == "ollama":
                local = await check_ollama(selected_model=public_model)
                if not local.get("healthy"):
                    return _component(
                        healthy=False,
                        code="local_provider_unavailable",
                        started=started,
                        provider=public_provider,
                        model=public_model,
                    )
                if not local.get("selected_installed"):
                    return _component(
                        healthy=False,
                        code="local_model_not_installed",
                        started=started,
                        provider=public_provider,
                        model=public_model,
                    )
                return _component(
                    healthy=True,
                    code="local_ready",
                    started=started,
                    provider=public_provider,
                    model=public_model,
                )
            # Hugging Face/local adapters do not expose a common bounded health
            # endpoint. They are accepted only when the configured model can be
            # provisioned within the normal readiness budget.
            try:
                provisioned = await asyncio.wait_for(
                    model_manager.get_model(model_id, max_tokens=1, temperature=0),
                    timeout=_timeout_seconds("READINESS_MODEL_TIMEOUT_SECONDS", 3.0),
                )
            except TimeoutError:
                return _component(
                    healthy=False,
                    code="local_provider_timeout",
                    started=started,
                    provider=public_provider,
                    model=public_model,
                )
            except Exception:
                provisioned = None
            if provisioned is not None:
                return _component(
                    healthy=True,
                    code="local_ready",
                    started=started,
                    provider=public_provider,
                    model=public_model,
                )
            return _component(
                healthy=False,
                code="local_model_unavailable",
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
        probe_enabled = (
            str(os.getenv("READINESS_MODEL_PROBE_ENABLED") or "")
            .strip()
            .casefold()
            in _TRUE_VALUES
        )
        if probe_enabled:
            try:
                provisioned = await asyncio.wait_for(
                    model_manager.get_model(
                        model_id,
                        max_tokens=1,
                        temperature=0,
                    ),
                    timeout=_timeout_seconds(
                        "READINESS_MODEL_TIMEOUT_SECONDS", 3.0
                    ),
                )
                if provisioned is None:
                    return _component(
                        healthy=False,
                        code="model_provision_failed",
                        started=started,
                        provider=public_provider,
                        model=public_model,
                    )
                langchain_model = provisioned.to_langchain()
                await asyncio.wait_for(
                    langchain_model.ainvoke("OK"),
                    timeout=_timeout_seconds(
                        "READINESS_MODEL_PROBE_TIMEOUT_SECONDS", 8.0
                    ),
                )
            except TimeoutError:
                return _component(
                    healthy=False,
                    code="provider_timeout",
                    started=started,
                    provider=public_provider,
                    model=public_model,
                )
            except Exception as exc:
                # Classify only stable provider categories. Never expose the
                # raw exception because it may contain credentials or prompts.
                message = str(exc).casefold()
                if (
                    "402" in message
                    or "insufficient balance" in message
                    or "payment required" in message
                    or "quota" in message
                ):
                    code = "provider_payment_required"
                elif "401" in message or "unauthorized" in message:
                    code = "provider_auth_failed"
                elif "403" in message or "forbidden" in message:
                    code = "provider_forbidden"
                else:
                    code = "provider_probe_failed"
                return _component(
                    healthy=False,
                    code=code,
                    started=started,
                    provider=public_provider,
                    model=public_model,
                )
        return _component(
            healthy=True,
            code="ready" if probe_enabled else "configured",
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
    *,
    client: httpx.AsyncClient | None = None,
    selected_model: str | None = None,
) -> dict[str, Any]:
    """Report Ollama health and whether the selected local model is installed."""
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
        selected = _safe_label(selected_model)
        return _component(
            healthy=True,
            code="ready",
            started=started,
            recommended_model=RECOMMENDED_LOCAL_MODEL,
            recommended_installed=RECOMMENDED_LOCAL_MODEL in installed,
            selected_model=selected,
            selected_installed=(selected in installed if selected else None),
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


def check_legal_validity_sync(
    *,
    snapshot_loader: Callable[[], dict[str, Any] | None] = default_snapshot_cache.load,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarize serving-snapshot freshness without exposing paths or exceptions."""

    mode = os.getenv("LEGAL_VALIDITY_SYNC_MODE", "protect").strip().casefold()
    mode = mode if mode in {"observe", "protect", "strict"} else "protect"
    required = mode == "strict"
    enabled = os.getenv("LEGAL_VALIDITY_SYNC_ENABLED", "true").strip().casefold() in _TRUE_VALUES
    if not enabled:
        return {
            "healthy": False,
            "code": "validity_sync_disabled",
            "required": required,
            "status": "disabled",
            "age_seconds": None,
        }
    try:
        stale_after = float(os.getenv("LEGAL_VALIDITY_STALE_AFTER_SECONDS", "21600"))
    except (TypeError, ValueError):
        stale_after = 21600.0
    stale_after = min(max(stale_after, 60.0), 7 * 24 * 60 * 60.0)
    try:
        snapshot = snapshot_loader()
    except Exception:
        snapshot = None
    health = default_registry.snapshot_health(
        snapshot,
        now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc),
        stale_after_seconds=stale_after,
    )
    status = str(health.get("status") or "missing")
    return {
        "healthy": status == "healthy",
        "code": "ok" if status == "healthy" else str(
            health.get("reason_code") or "validity_snapshot_unavailable"
        ),
        "required": required,
        "status": status,
        "age_seconds": health.get("age_seconds"),
    }


async def collect_readiness() -> dict[str, Any]:
    public_quick_chat_only = _truthy(
        os.getenv("PUBLIC_QUICK_CHAT_ONLY_MODE")
    )
    if public_quick_chat_only:
        database, legal_retrieval, ollama = await asyncio.gather(
            check_database(),
            check_legal_retrieval(),
            check_ollama(),
        )
        model_provider = _component(
            healthy=False,
            code="not_required_for_public_quick_chat",
            started=perf_counter(),
            required=False,
        )
        required_components = ("database", "legal_retrieval")
    else:
        database, legal_retrieval, model_provider, ollama = await asyncio.gather(
            check_database(),
            check_legal_retrieval(),
            check_model_provider(),
            check_ollama(),
        )
        model_provider = {**model_provider, "required": True}
        required_components = REQUIRED_COMPONENTS
    components = {
        "database": database,
        "legal_retrieval": legal_retrieval,
        "model_provider": model_provider,
        "ollama": {**ollama, "required": False},
        "legal_validity_sync": check_legal_validity_sync(),
    }
    ready = all(components[name].get("healthy") for name in required_components)
    if components["legal_validity_sync"].get("required"):
        ready = ready and bool(components["legal_validity_sync"].get("healthy"))
    requested_device = _safe_device(os.getenv("LEGAL_EMBED_DEVICE", "auto"))
    active_device = _safe_device(legal_retrieval.get("embedding_device"))
    return {
        "status": "ready" if ready else "not_ready",
        "deployment_scope": (
            "public_quick_chat_only" if public_quick_chat_only else "full_answer_plane"
        ),
        "components": components,
        "embedding_device": {
            "requested": requested_device,
            "active": active_device,
        },
    }


async def collect_import_readiness() -> dict[str, Any]:
    """Report whether the legal-import data plane can safely accept work.

    This deliberately excludes the chat model and optional Ollama fallback.
    The import worker performs deterministic parsing and embedding through the
    retrieval service; a cloud-answering outage must not prevent a verified
    legal record from being queued for import.
    """
    database, legal_retrieval = await asyncio.gather(
        check_database(),
        check_legal_retrieval(),
    )
    components = {
        "database": database,
        "legal_retrieval": legal_retrieval,
        "import_worker": import_worker_component(),
    }
    ready = all(
        components[name].get("healthy") for name in IMPORT_REQUIRED_COMPONENTS
    )
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


async def collect_answer_readiness() -> dict[str, Any]:
    """Named counterpart of the legacy ``/ready`` answer-traffic probe."""
    return await collect_readiness()
