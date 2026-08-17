import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from api import readiness
from api.import_worker_status import _reset_for_test, record_import_worker_heartbeat


def _component(healthy: bool, code: str = "ready", **extra):
    return {
        "healthy": healthy,
        "code": code,
        "latency_ms": 1,
        **extra,
    }


@pytest.mark.asyncio
async def test_collect_readiness_requires_database_retrieval_and_cloud_model(
    monkeypatch,
):
    monkeypatch.delenv("LEGAL_EMBED_DEVICE", raising=False)
    monkeypatch.setattr(
        readiness, "check_database", AsyncMock(return_value=_component(True))
    )
    monkeypatch.setattr(
        readiness,
        "check_legal_retrieval",
        AsyncMock(
            return_value=_component(
                True,
                indexed_records=42,
                embedding_device="cuda:0",
            )
        ),
    )
    monkeypatch.setattr(
        readiness,
        "check_model_provider",
        AsyncMock(
            return_value=_component(
                True,
                code="configured",
                provider="openai",
                model="gpt-test",
            )
        ),
    )
    monkeypatch.setattr(
        readiness,
        "check_ollama",
        AsyncMock(return_value=_component(False, code="connection_failed")),
    )

    report = await readiness.collect_readiness()

    assert report["status"] == "ready"
    assert report["components"]["ollama"]["required"] is False
    assert report["embedding_device"] == {
        "requested": "auto",
        "active": "cuda:0",
    }

    readiness.check_database.return_value = _component(False, "connection_failed")
    report = await readiness.collect_readiness()
    assert report["status"] == "not_ready"


@pytest.mark.asyncio
async def test_collect_import_readiness_ignores_chat_model_provider(monkeypatch):
    monkeypatch.setenv("LEGAL_EMBED_DEVICE", "cpu")
    _reset_for_test()
    record_import_worker_heartbeat()
    monkeypatch.setattr(
        readiness, "check_database", AsyncMock(return_value=_component(True))
    )
    monkeypatch.setattr(
        readiness,
        "check_legal_retrieval",
        AsyncMock(return_value=_component(True, embedding_device="cpu")),
    )
    monkeypatch.setattr(
        readiness,
        "check_model_provider",
        AsyncMock(return_value=_component(False, "provider_timeout")),
    )

    report = await readiness.collect_import_readiness()

    assert report["status"] == "ready"
    assert set(report["components"]) == {"database", "legal_retrieval", "import_worker"}
    assert report["components"]["import_worker"]["healthy"] is True
    assert report["embedding_device"] == {"requested": "cpu", "active": "cpu"}
    readiness.check_model_provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_database_failure_does_not_expose_exception_or_credentials(monkeypatch):
    async def fail_query(*_args, **_kwargs):
        raise RuntimeError("ws://root:super-secret@database:8000/rpc?token=leak")

    monkeypatch.setattr(readiness, "repo_query", fail_query)
    component = await readiness.check_database()
    serialized = json.dumps(component)

    assert component["healthy"] is False
    assert component["code"] == "connection_failed"
    assert "super-secret" not in serialized
    assert "token" not in serialized.lower()
    assert "error" not in component


@pytest.mark.asyncio
async def test_database_readiness_uses_surreal_2_compatible_read_only_query(monkeypatch):
    query = AsyncMock(return_value=[1])
    monkeypatch.setattr(readiness, "repo_query", query)

    component = await readiness.check_database()

    assert component["healthy"] is True
    assert component["code"] == "ready"
    query.assert_awaited_once_with("RETURN 1;")


@pytest.mark.asyncio
async def test_legal_retrieval_reports_only_sanitized_embedding_device(monkeypatch):
    monkeypatch.setenv("LEGAL_SEARCH_URL", "http://retrieval.internal:8765")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            json={
                "status": "healthy",
                "indexed_records": 19,
                "device": "cuda:0",
                "model": "C:/private/model/path",
                "api_key": "must-not-be-forwarded",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        component = await readiness.check_legal_retrieval(client=client)

    assert component["healthy"] is True
    assert component["embedding_device"] == "cuda:0"
    assert component["indexed_records"] == 19
    serialized = json.dumps(component)
    assert "private" not in serialized
    assert "api_key" not in serialized
    assert "must-not-be-forwarded" not in serialized


@pytest.mark.asyncio
async def test_legal_retrieval_readiness_requires_every_configured_replica(monkeypatch):
    monkeypatch.setenv(
        "LEGAL_SEARCH_URLS",
        "http://retrieval-a:8765,http://retrieval-b:8765",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "retrieval-b":
            return httpx.Response(503, json={"status": "unavailable"})
        return httpx.Response(
            200,
            json={
                "status": "healthy",
                "indexed_records": 19,
                "database_chunks": 20,
                "collection": "active",
                "model_fingerprint": "a" * 64,
                "device": "cpu",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        component = await readiness.check_legal_retrieval(client=client)

    assert component["healthy"] is False
    assert component["code"] == "replica_unavailable"
    assert component["replica_count"] == 2
    assert component["healthy_replicas"] == 1
    assert "retrieval-a" not in json.dumps(component)


@pytest.mark.asyncio
async def test_legal_retrieval_readiness_rejects_replica_fingerprint_drift(monkeypatch):
    monkeypatch.setenv(
        "LEGAL_SEARCH_URLS",
        "http://retrieval-a:8765,http://retrieval-b:8765",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "healthy",
                "indexed_records": 19,
                "database_chunks": 20,
                "collection": "active",
                "model_fingerprint": (
                    "a" * 64 if request.url.host == "retrieval-a" else "b" * 64
                ),
                "device": "cpu",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        component = await readiness.check_legal_retrieval(client=client)

    assert component["healthy"] is False
    assert component["code"] == "replica_fingerprint_mismatch"
    assert component["replica_count"] == 2


@pytest.mark.asyncio
async def test_model_readiness_resolves_configuration_without_generation(monkeypatch):
    monkeypatch.setenv("READINESS_MODEL_PROBE_ENABLED", "false")
    default_model = SimpleNamespace(
        name="gpt-test",
        provider="openai",
        type="language",
        credential="credential:cloud",
    )
    credential = SimpleNamespace(
        api_key=SecretStr("secret-value"),
        decryption_error=None,
        base_url=None,
        endpoint=None,
        api_version=None,
        endpoint_llm=None,
        project=None,
        location=None,
    )

    monkeypatch.setattr(
        readiness.model_manager,
        "get_defaults",
        AsyncMock(return_value=SimpleNamespace(default_chat_model="model:cloud")),
    )
    monkeypatch.setattr(
        readiness.Model, "get", AsyncMock(return_value=default_model)
    )
    monkeypatch.setattr(
        readiness.Credential, "get", AsyncMock(return_value=credential)
    )

    component = await readiness.check_model_provider()

    assert component["healthy"] is True
    assert component["provider"] == "openai"
    assert component["model"] == "gpt-test"
    assert not hasattr(default_model, "generate")
    assert "secret-value" not in json.dumps(component)


@pytest.mark.asyncio
async def test_model_readiness_probe_reports_provider_payment_failure(monkeypatch):
    default_model = SimpleNamespace(
        name="deepseek-v4-pro",
        provider="deepseek",
        type="language",
        credential="credential:cloud",
    )
    credential = SimpleNamespace(
        api_key=SecretStr("secret-value"),
        decryption_error=None,
        base_url=None,
        endpoint=None,
        api_version=None,
        endpoint_llm=None,
        project=None,
        location=None,
    )
    language_model = SimpleNamespace(
        to_langchain=lambda: SimpleNamespace(
            ainvoke=AsyncMock(
                side_effect=RuntimeError(
                    "402 Insufficient Balance; token=must-not-leak"
                )
            )
        )
    )
    monkeypatch.setenv("READINESS_MODEL_PROBE_ENABLED", "true")
    monkeypatch.setattr(
        readiness.model_manager,
        "get_defaults",
        AsyncMock(return_value=SimpleNamespace(default_chat_model="model:cloud")),
    )
    monkeypatch.setattr(
        readiness.Model, "get", AsyncMock(return_value=default_model)
    )
    monkeypatch.setattr(
        readiness.Credential, "get", AsyncMock(return_value=credential)
    )
    monkeypatch.setattr(
        readiness.model_manager,
        "get_model",
        AsyncMock(return_value=language_model),
    )

    component = await readiness.check_model_provider()

    assert component["healthy"] is False
    assert component["code"] == "provider_payment_required"
    assert "must-not-leak" not in json.dumps(component)
    assert "token" not in json.dumps(component).lower()


def test_health_is_liveness_and_ready_is_public_dependency_gate(monkeypatch):
    from api import main

    async def not_ready():
        return {
            "status": "not_ready",
            "components": {
                "database": _component(False, "connection_failed"),
                "legal_retrieval": _component(True),
                "model_provider": _component(True, "configured"),
                "ollama": {**_component(False, "connection_failed"), "required": False},
            },
            "embedding_device": {"requested": "auto", "active": "unknown"},
        }

    monkeypatch.setattr(main.readiness, "collect_readiness", not_ready)
    with patch("api.auth.has_real_users", AsyncMock(return_value=True)):
        client = TestClient(main.app)
        health_response = client.get("/health")
        ready_response = client.get("/ready")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "healthy"}
    assert ready_response.status_code == 503
    assert ready_response.json()["status"] == "not_ready"


def test_import_ready_is_public_and_does_not_require_answer_model(monkeypatch):
    from api import main

    async def import_ready_report():
        return {
            "status": "ready",
            "components": {
                "database": _component(True),
                "legal_retrieval": _component(True, embedding_device="cpu"),
            },
            "embedding_device": {"requested": "auto", "active": "cpu"},
        }

    monkeypatch.setattr(main.readiness, "collect_import_readiness", import_ready_report)
    with patch("api.auth.has_real_users", AsyncMock(return_value=True)):
        client = TestClient(main.app)
        root_response = client.get("/ready/import")
        proxied_response = client.get("/api/ready/import")

    assert root_response.status_code == 200
    assert root_response.json()["status"] == "ready"
    assert proxied_response.status_code == 200
    assert proxied_response.json()["status"] == "ready"


def test_import_ready_reports_worker_not_started(monkeypatch):
    from api import main

    async def import_not_ready_report():
        return {
            "status": "not_ready",
            "components": {
                "database": _component(True),
                "legal_retrieval": _component(True, embedding_device="cpu"),
                "import_worker": {"healthy": False, "code": "not_started", "required": True},
            },
            "embedding_device": {"requested": "auto", "active": "cpu"},
        }

    monkeypatch.setattr(main.readiness, "collect_import_readiness", import_not_ready_report)
    with patch("api.auth.has_real_users", AsyncMock(return_value=True)):
        client = TestClient(main.app)
        response = client.get("/ready/import")

    assert response.status_code == 503
    assert response.json()["components"]["import_worker"]["code"] == "not_started"
