from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import gateway_rate_limit as gate


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("GATEWAY_RATE_LIMIT_TOKEN", "g" * 32)
    gate.gateway_limiter = gate.GatewayRateLimiter()
    app = FastAPI()
    app.include_router(gate.router)
    return TestClient(app)


def test_gateway_gate_fails_closed_without_shared_token(monkeypatch):
    client = _client(monkeypatch)
    headers = {
        "X-Gateway-Client-IP": "203.0.113.5",
        "X-Original-URI": "/api/search/ask",
        "X-Original-Method": "POST",
    }
    assert client.get("/internal/gateway-rate-limit", headers=headers).status_code == 403
    headers["X-Gateway-Token"] = "g" * 32
    assert client.get("/internal/gateway-rate-limit", headers=headers).status_code == 204


def test_gateway_gate_rate_limits_by_opaque_client_and_class(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setitem(gate.RATE_CLASSES, "auth", (2, 60))
    headers = {
        "X-Gateway-Token": "g" * 32,
        "X-Gateway-Client-IP": "198.51.100.8",
        "X-Original-URI": "/api/auth/login?ignored=content",
        "X-Original-Method": "POST",
    }
    assert client.get("/internal/gateway-rate-limit", headers=headers).status_code == 204
    assert client.get("/internal/gateway-rate-limit", headers=headers).status_code == 204
    blocked = client.get("/internal/gateway-rate-limit", headers=headers)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert "198.51.100.8" not in blocked.text
    assert "ignored" not in blocked.text
