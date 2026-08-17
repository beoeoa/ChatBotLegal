from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from api.auth_rate_limit import AuthRateLimitMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        AuthRateLimitMiddleware,
        limits={"/api/auth/login": (2, 60)},
    )

    @app.post("/api/auth/login")
    async def login(request: Request):
        body = await request.json()
        if body.get("password") == "valid":
            return {"authenticated": True}
        return JSONResponse(status_code=401, content={"detail": "invalid"})

    return app


def test_auth_rate_limit_blocks_repeated_login_attempts_without_echoing_input() -> None:
    client = TestClient(_app())

    client.post(
        "/api/auth/login",
        json={"identifier": "private@example.test", "password": "secret-one"},
    )
    client.post(
        "/api/auth/login",
        json={"identifier": "private@example.test", "password": "secret-two"},
    )
    blocked = client.post(
        "/api/auth/login",
        json={"identifier": "private@example.test", "password": "secret-three"},
    )

    assert blocked.status_code == 429
    assert blocked.json()["code"] == "auth_rate_limited"
    body = blocked.text
    assert "private@example.test" not in body
    assert "secret" not in body
    assert int(blocked.headers["Retry-After"]) > 0


def test_successful_login_clears_the_attempt_window() -> None:
    client = TestClient(_app())

    client.post("/api/auth/login", json={"password": "invalid"})
    success = client.post("/api/auth/login", json={"password": "valid"})
    after_success = client.post("/api/auth/login", json={"password": "invalid"})

    assert success.status_code == 200
    assert after_success.status_code != 429
