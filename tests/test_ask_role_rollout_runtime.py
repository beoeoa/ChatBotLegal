from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.auth import PasswordAuthMiddleware
from api.models import AskResponse
from api.routers import search


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc).isoformat()
ROLES = ("admin", "officer", "citizen")


def _write_state(path: Path, stage: int) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "stage": stage,
                "enabled_roles": list(ROLES[:stage]),
                "stage_started_at": NOW if stage else None,
                "updated_at": NOW if stage else None,
                "history": [],
            }
        ),
        encoding="utf-8",
    )


def _app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    async def real_users_exist() -> bool:
        return True

    async def session_user(token: str):
        role = {
            "admin-session": "admin",
            "officer-session": "officer",
            "citizen-session": "citizen",
        }.get(token)
        if role is None:
            return None
        return {
            "role": role,
            "user": {
                "id": f"user_account:{role}",
                "username": f"{role}01",
                "email": None,
                "profile": {},
            },
        }

    async def execute(
        ask_request,
        _request,
        *,
        progress=None,
        trace_id_override=None,
    ):
        if progress is not None:
            await progress("status", {"stage": "generating"})
        return AskResponse(
            answer="validated",
            question=ask_request.question,
            grounding_status="fully_grounded",
            trace_id=trace_id_override,
        )

    monkeypatch.setattr("api.auth.has_real_users", real_users_exist)
    monkeypatch.setattr("api.auth.get_user_from_session_token", session_user)
    monkeypatch.setattr(search, "_execute_ask_simple", execute)
    app = FastAPI()
    app.include_router(search.router, prefix="/api")
    app.add_middleware(PasswordAuthMiddleware, excluded_paths=[])
    return app


def _headers(role: str, *, spoofed_header: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {role}-session",
        "X-User-Role": spoofed_header or role,
    }


@pytest.mark.parametrize(
    ("stage", "role", "expected_status"),
    [
        (0, "admin", 403),
        (0, "officer", 403),
        (0, "citizen", 403),
        (1, "admin", 200),
        (1, "officer", 403),
        (1, "citizen", 403),
        (2, "admin", 200),
        (2, "officer", 200),
        (2, "citizen", 403),
        (3, "admin", 200),
        (3, "officer", 200),
        (3, "citizen", 200),
    ],
)
def test_progress_runtime_gate_enforces_exact_role_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stage: int,
    role: str,
    expected_status: int,
) -> None:
    state = tmp_path / "rollout.json"
    _write_state(state, stage)
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH", str(state))
    client = TestClient(_app(monkeypatch))

    response = client.post(
        "/api/search/ask/progress",
        headers=_headers(role),
        json={"question": "Test role rollout"},
    )

    assert response.status_code == expected_status
    if expected_status == 403:
        assert response.json()["detail"]["code"] == "ASK_ROLE_ROLLOUT_ROLE_DISABLED"
        assert response.json()["detail"]["message"] == (
            "Tính năng hỏi đáp chưa được mở cho vai trò này."
        )


def test_authenticated_session_role_wins_over_spoofed_body_and_header(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = tmp_path / "rollout.json"
    _write_state(state, 1)
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH", str(state))
    client = TestClient(_app(monkeypatch))

    citizen_spoof = client.post(
        "/api/search/ask/progress",
        headers=_headers("citizen", spoofed_header="admin"),
        json={"question": "Spoof admin", "role": "admin"},
    )
    admin_downgrade = client.post(
        "/api/search/ask/progress",
        headers=_headers("admin", spoofed_header="citizen"),
        json={"question": "Authenticated admin", "role": "citizen"},
    )

    assert citizen_spoof.status_code == 403
    assert admin_downgrade.status_code == 200


@pytest.mark.parametrize("path", ["/api/search/ask", "/api/search/ask/simple"])
def test_existing_ask_paths_remain_available_during_progress_canary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
) -> None:
    state = tmp_path / "rollout.json"
    _write_state(state, 1)
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH", str(state))
    client = TestClient(_app(monkeypatch))

    response = client.post(
        path,
        headers=_headers("citizen", spoofed_header="admin"),
        json={"question": "Bypass attempt", "role": "admin"},
    )

    assert response.status_code == 200


def test_progress_gate_rejects_shared_password_role_header(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def real_users_exist() -> bool:
        return True

    async def no_session(_token: str):
        return None

    async def execute(*_args, **_kwargs):
        raise AssertionError("blocked legacy auth must not execute Ask")

    monkeypatch.setattr("api.auth.has_real_users", real_users_exist)
    monkeypatch.setattr("api.auth.get_user_from_session_token", no_session)
    monkeypatch.setattr(
        "api.auth.allowed_roles_for_password",
        lambda token: list(ROLES) if token == "legacy-test-secret" else [],
    )
    monkeypatch.setattr(search, "_execute_ask_simple", execute)
    state = tmp_path / "rollout.json"
    _write_state(state, 1)
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH", str(state))
    app = FastAPI()
    app.include_router(search.router, prefix="/api")
    app.add_middleware(PasswordAuthMiddleware, excluded_paths=[])
    client = TestClient(app)

    response = client.post(
        "/api/search/ask/progress",
        headers={
            "Authorization": "Bearer legacy-test-secret",
            "X-User-Role": "admin",
        },
        json={"question": "Header-selected admin", "role": "admin"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "ASK_ROLE_ROLLOUT_AUTH_REQUIRED"


@pytest.mark.parametrize(
    "state_contents",
    [
        None,
        "{}",
        "not-json",
        json.dumps(
            {
                "schema_version": "1.0",
                "stage": True,
                "enabled_roles": ["admin"],
                "stage_started_at": NOW,
                "updated_at": NOW,
                "history": [],
            }
        ),
        json.dumps(
            {
                "schema_version": "1.0",
                "stage": 2,
                "enabled_roles": ["officer", "admin"],
                "stage_started_at": NOW,
                "updated_at": NOW,
                "history": [],
            }
        ),
    ],
)
def test_enabled_gate_fails_closed_for_missing_or_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    state_contents: str | None,
) -> None:
    state = tmp_path / "rollout.json"
    if state_contents is not None:
        state.write_text(state_contents, encoding="utf-8")
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH", str(state))
    client = TestClient(_app(monkeypatch))

    response = client.post(
        "/api/search/ask/progress",
        headers=_headers("admin"),
        json={"question": "Invalid state"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ASK_ROLE_ROLLOUT_STATE_INVALID"


def test_stage_zero_rollback_is_observed_on_the_next_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state = tmp_path / "rollout.json"
    _write_state(state, 3)
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH", str(state))
    client = TestClient(_app(monkeypatch))

    before = client.post(
        "/api/search/ask/progress",
        headers=_headers("citizen"),
        json={"question": "Before rollback"},
    )
    _write_state(state, 0)
    after = client.post(
        "/api/search/ask/progress",
        headers=_headers("citizen"),
        json={"question": "After rollback"},
    )

    assert before.status_code == 200
    assert after.status_code == 403


def test_disabled_progress_route_does_not_consult_rollout_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "false")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED", "true")
    monkeypatch.setenv(
        "LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH", str(tmp_path / "missing.json")
    )
    client = TestClient(_app(monkeypatch))

    response = client.post(
        "/api/search/ask/progress",
        headers=_headers("admin"),
        json={"question": "Route disabled"},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ASK_PROGRESS_DISABLED"


def test_no_auth_mode_cannot_promote_itself_with_role_header(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def no_real_users() -> bool:
        return False

    monkeypatch.setattr("api.auth.has_real_users", no_real_users)
    monkeypatch.setattr("api.auth.configured_role_passwords", lambda: {})
    state = tmp_path / "rollout.json"
    _write_state(state, 1)
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED", "true")
    monkeypatch.setenv("LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH", str(state))
    app = FastAPI()
    app.include_router(search.router, prefix="/api")
    app.add_middleware(PasswordAuthMiddleware, excluded_paths=[])
    client = TestClient(app)

    response = client.post(
        "/api/search/ask/progress",
        headers={"X-User-Role": "admin"},
        json={"question": "Unauthenticated admin", "role": "admin"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "ASK_ROLE_ROLLOUT_AUTH_REQUIRED"


def test_rollout_flag_is_off_by_default_and_does_not_read_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("LEGAL_ASK_PROGRESS_ENABLED", "true")
    monkeypatch.delenv("LEGAL_ASK_ROLE_ROLLOUT_ENFORCED", raising=False)
    monkeypatch.setenv(
        "LEGAL_ASK_ROLE_ROLLOUT_STATE_PATH", str(tmp_path / "missing.json")
    )
    client = TestClient(_app(monkeypatch))

    response = client.post(
        "/api/search/ask/progress",
        headers=_headers("citizen"),
        json={"question": "Backward compatible"},
    )

    assert response.status_code == 200
