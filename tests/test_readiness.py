import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from api import readiness


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
async def test_model_readiness_resolves_configuration_without_generation(monkeypatch):
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
