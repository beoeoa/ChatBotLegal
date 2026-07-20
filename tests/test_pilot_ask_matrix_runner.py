import asyncio
import itertools

import pytest

from scripts import run_pilot_ask_matrix as runner


def _inputs():
    cases = []
    records = []
    for domain, role, scenario_class in itertools.product(
        runner.DOMAINS, runner.ROLES, runner.SCENARIO_CLASSES
    ):
        review_id = f"{domain}:{role}:{scenario_class}"
        cases.append(
            {
                "review_id": review_id,
                "domain": domain,
                "role": role,
                "scenario_class": scenario_class,
            }
        )
        records.append(
            {
                "review_id": review_id,
                "domain": domain,
                "role": role,
                "question": "Synthetic question",
                "expert_review_status": "approved",
                "expert_name": "Expert A",
                "reviewed_at": "2026-07-16T00:00:00+00:00",
                "expert_score": 9.5,
                "expected_documents": ["60/2014/QH13"],
                "expected_articles": ["Điều 16"],
                "official_form_ids": ["form-1"],
            }
        )
    return {
        "version": "1.0",
        "domains": list(runner.DOMAINS),
        "roles": list(runner.ROLES),
        "scenario_classes": list(runner.SCENARIO_CLASSES),
        "cases": cases,
    }, {"records": records}


def test_cli_is_dry_run_by_default_and_live_execution_is_explicit():
    parser = runner.build_parser()

    assert parser.parse_args([]).mode == "dry-run"
    assert parser.parse_args(["--execute"]).mode == "execute"


def test_full_plan_covers_both_endpoints_and_concurrency_1_5_10_20():
    manifest, experts = _inputs()
    plan = runner.build_execution_plan(manifest, experts, run_id="test-run")

    assert len(plan) == 240
    assert {item["endpoint"] for item in plan} == set(runner.ENDPOINTS)
    assert {item["concurrency"] for item in plan} == {1, 5, 10, 20}
    assert len({item["idempotency_key"] for item in plan}) == 240


def test_dry_run_never_calls_requester():
    manifest, experts = _inputs()
    called = False

    async def requester(_task):
        nonlocal called
        called = True
        return {}

    report = asyncio.run(
        runner.run_matrix(
            manifest,
            experts,
            mode="dry-run",
            requester=requester,
            run_id="dry-run",
        )
    )

    assert report["mode"] == "dry-run"
    assert report["planned_attempts"] == 240
    assert report["executed_attempts"] == 0
    assert called is False


def test_execute_requires_role_tokens_from_environment(monkeypatch):
    monkeypatch.delenv("PILOT_CITIZEN_TOKEN", raising=False)
    monkeypatch.delenv("PILOT_OFFICER_TOKEN", raising=False)

    with pytest.raises(runner.RunnerConfigurationError, match="PILOT_CITIZEN_TOKEN"):
        runner.resolve_role_tokens(runner.ROLES)


def test_execute_rejects_invalid_or_non_finite_expert_score():
    manifest, experts = _inputs()
    review_id = experts["records"][0]["review_id"]
    experts["records"][0]["expert_score"] = float("nan")

    assert review_id in runner.unapproved_review_ids(manifest, experts)


@pytest.mark.asyncio
async def test_executor_respects_each_batch_concurrency_limit():
    active = 0
    observed = 0
    tasks = [
        {"review_id": f"case-{index}", "concurrency": 5}
        for index in range(20)
    ]

    async def request_one(task):
        nonlocal active, observed
        active += 1
        observed = max(observed, active)
        await asyncio.sleep(0.001)
        active -= 1
        return {**task, "status": "completed"}

    results = await runner.execute_batch(tasks, concurrency=5, requester=request_one)

    assert len(results) == 20
    assert 1 < observed <= 5


def test_progress_parser_returns_only_final_response_and_structured_error():
    body = (
        'event: accepted\ndata: {"trace_id":"t1"}\n\n'
        'event: final\ndata: {"response":{"answer":"ok","citations":[]}}\n\n'
        'event: complete\ndata: {"trace_id":"t1"}\n\n'
    )
    assert runner.parse_progress_response(body)["answer"] == "ok"

    error = 'event: error\ndata: {"code":"AI_PROVIDER_UNAVAILABLE"}\n\n'
    with pytest.raises(runner.ProgressResponseError, match="AI_PROVIDER_UNAVAILABLE"):
        runner.parse_progress_response(error)

    missing_complete = (
        'event: final\ndata: {"response":{"answer":"ok"}}\n\n'
    )
    with pytest.raises(runner.ProgressResponseError, match="COMPLETE_MISSING"):
        runner.parse_progress_response(missing_complete)


def test_persisted_assessment_is_privacy_safe_and_contains_repair_observability():
    task = {
        "review_id": "case:citizen",
        "domain": "ho_tich_chung_thuc",
        "role": "citizen",
        "scenario_class": "routine",
        "endpoint": "/api/search/ask/simple",
        "concurrency": 1,
        "question": "private question must stay in memory",
        "expected_documents": ["60/2014/QH13"],
        "expected_articles": [],
        "official_form_ids": [],
    }
    response = {
        "answer": "private answer must not be persisted",
        "citations": [
            {
                "doc_id": "doc-1",
                "law_number": "60/2014/QH13",
                "internal_url": "/legal-docs/doc-1",
            }
        ],
        "quality_flags": [],
        "claim_validation": [],
        "repair_used": False,
    }

    result = runner.completed_result(task, response, elapsed_seconds=1.2)
    runner.assert_report_privacy({"results": [result]})

    assert "question" not in result
    assert "answer" not in result
    assert "citations" not in result
    assert result["repair_observed"] is True
    assert result["citation_ok"] is True
