from __future__ import annotations

import asyncio

import pytest

from scripts.pilot_quality_gate import attach_role_isolation_gate
from scripts.run_pilot_role_isolation import (
    IsolationConfigurationError,
    assert_isolation_report,
    build_isolation_plan,
    execute_isolation_plan,
    resolve_account_tokens,
    validate_authenticated_identity,
    validate_isolation_input,
)


def _input() -> dict:
    return {
        "schema_version": "1.0",
        "accounts": [
            {
                "slot": "a",
                "account_id": "user_account:officer-a",
                "role": "officer",
                "resources": {
                    "conversation_id": "conversation:conversation-a",
                    "profile_id": "user_account:officer-a",
                    "notebook_id": "notebook:notebook-a",
                    "legal_dossier_id": "legal_case:case-a",
                },
            },
            {
                "slot": "b",
                "account_id": "user_account:officer-b",
                "role": "officer",
                "resources": {
                    "conversation_id": "conversation:conversation-b",
                    "profile_id": "user_account:officer-b",
                    "notebook_id": "notebook:notebook-b",
                    "legal_dossier_id": "legal_case:case-b",
                },
            },
        ],
    }


def test_builds_eight_cross_account_attempts_separate_from_quality_matrix():
    payload = _input()
    assert validate_isolation_input(payload) == []

    plan = build_isolation_plan(payload, run_id="security-run")

    assert len(plan) == 8
    assert {item["resource_type"] for item in plan} == {
        "conversation",
        "profile",
        "notebook",
        "legal_dossier",
    }
    assert {item["direction"] for item in plan} == {"a_to_b", "b_to_a"}
    assert all(item["actor_account_id"] != item["target_account_id"] for item in plan)
    assert all("token" not in item for item in plan)
    assert all(item["accepted_status_codes"] == [403, 404] for item in plan)
    assert next(item for item in plan if item["resource_type"] == "profile")[
        "method"
    ] == "GET"
    assert next(item for item in plan if item["resource_type"] == "profile")[
        "path"
    ].startswith("/api/users/")


def test_input_rejects_admin_placeholder_or_shared_identifiers():
    payload = _input()
    payload["accounts"][0]["role"] = "admin"
    assert "account_role_not_ordinary:a" in validate_isolation_input(payload)

    payload = _input()
    payload["accounts"][1]["account_id"] = payload["accounts"][0]["account_id"]
    assert "account_ids_not_distinct" in validate_isolation_input(payload)

    payload = _input()
    payload["accounts"][1]["resources"]["notebook_id"] = "REPLACE_ME"
    assert "placeholder_resource_id:b:notebook_id" in validate_isolation_input(payload)


def test_tokens_are_environment_only_and_never_added_to_plan():
    with pytest.raises(IsolationConfigurationError, match="ACCOUNT_A_TOKEN"):
        resolve_account_tokens({})

    tokens = resolve_account_tokens(
        {
            "PILOT_ISOLATION_ACCOUNT_A_TOKEN": "secret-a",
            "PILOT_ISOLATION_ACCOUNT_B_TOKEN": "secret-b",
        }
    )
    assert tokens == {"a": "secret-a", "b": "secret-b"}
    assert "secret-a" not in str(build_isolation_plan(_input(), run_id="run"))

    payload = _input()
    payload["accounts"][0]["token"] = "must-not-be-here"
    assert "credential_field_forbidden_in_input:token" in validate_isolation_input(
        payload
    )


def test_injected_tokens_must_match_declared_ordinary_accounts():
    account = _input()["accounts"][0]
    assert validate_authenticated_identity(
        account,
        {"id": account["account_id"], "role": account["role"]},
    ) == []
    assert validate_authenticated_identity(
        account,
        {"id": "user_account:other", "role": "admin"},
    ) == [
        "identity_account_mismatch:a",
        "identity_role_mismatch:a",
        "identity_role_not_ordinary:a",
    ]


def test_execution_accepts_only_403_or_404_and_stores_no_response_content():
    plan = build_isolation_plan(_input(), run_id="security-run")

    async def requester(attempt):
        status = 403 if attempt["direction"] == "a_to_b" else 404
        return status, 1.25

    report = asyncio.run(
        execute_isolation_plan(plan, requester=requester, run_id="security-run")
    )

    assert report["expected_attempts"] == 8
    assert report["executed_attempts"] == 8
    assert report["pass"] is True
    assert all(item["status_code"] in {403, 404} for item in report["results"])
    assert all(set(item).issubset({
        "attempt_id",
        "direction",
        "actor_account_id",
        "target_account_id",
        "resource_type",
        "resource_id",
        "status_code",
        "elapsed_ms",
        "outcome_code",
        "passed",
    }) for item in report["results"])
    assert_isolation_report(report)


def test_any_success_response_fails_closed_without_leaking_body():
    plan = build_isolation_plan(_input(), run_id="security-run")

    async def requester(attempt):
        return (200 if attempt["resource_type"] == "notebook" else 403), 2.0

    report = asyncio.run(
        execute_isolation_plan(plan, requester=requester, run_id="security-run")
    )

    assert report["pass"] is False
    assert any(item["outcome_code"] == "unexpected_status" for item in report["results"])
    with pytest.raises(IsolationConfigurationError):
        assert_isolation_report(report)


def test_release_gate_requires_the_separate_isolation_artifact():
    plan = build_isolation_plan(_input(), run_id="security-run")

    async def denied(_attempt):
        return 403, 1.0

    isolation = asyncio.run(
        execute_isolation_plan(plan, requester=denied, run_id="security-run")
    )
    base_quality = {
        "schema_version": "1.0",
        "pass": True,
        "checks": {"base_quality": True},
        "artifact_fingerprints": {},
    }

    passing = attach_role_isolation_gate(base_quality, isolation)
    assert passing["pass"] is True
    assert passing["checks"]["role_isolation_evidence_passes"] is True
    assert passing["role_isolation"]["actual_attempts"] == 8
    assert passing["artifact_fingerprints"]["role_isolation_sha256"]

    isolation["results"][0]["status_code"] = 200
    isolation["results"][0]["passed"] = False
    failing = attach_role_isolation_gate(base_quality, isolation)
    assert failing["pass"] is False
    assert failing["checks"]["role_isolation_evidence_passes"] is False


def test_network_failure_is_normalized_without_exception_message():
    plan = build_isolation_plan(_input(), run_id="security-run")

    async def requester(_attempt):
        raise RuntimeError("sensitive upstream message")

    report = asyncio.run(
        execute_isolation_plan(plan, requester=requester, run_id="security-run")
    )

    assert report["pass"] is False
    assert all(item["status_code"] is None for item in report["results"])
    assert all(item["outcome_code"] == "request_error" for item in report["results"])
    assert "sensitive upstream message" not in str(report)
