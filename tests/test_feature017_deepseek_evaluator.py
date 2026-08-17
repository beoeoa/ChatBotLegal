from __future__ import annotations

import json

from scripts.run_feature017_deepseek_golden1000 import (
    CHECKPOINT_SCHEMA_VERSION,
    EVALUATOR_VERSION,
    _build_run_identity,
    _evaluate,
    _load_checkpoint,
    _reevaluate_checkpoint_rows,
    _safe_error_projection,
    _select_cases,
    _sha256,
    _write_json_atomic,
)


def test_evaluator_checkpoint_is_atomic_and_role_bound(tmp_path):
    checkpoint = tmp_path / "golden.checkpoint.json"
    golden = tmp_path / "golden.json"
    golden.write_text('{"cases": [{"case_id": "case-1"}]}', encoding="utf-8")
    run_identity = _build_run_identity(
        base_url="http://127.0.0.1:5055",
        role="citizen",
        golden_path=golden,
        cases=[{"case_id": "case-1"}],
    )
    payload = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "run_identity": run_identity,
        "role": "citizen",
        "golden_file": str(golden),
        "completed": 25,
        "results": [None, {"case_id": "case-2"}],
    }

    _write_json_atomic(checkpoint, payload)
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["completed"] == 25
    assert _load_checkpoint(checkpoint, run_identity=run_identity) == payload["results"]


def test_evaluator_rejects_checkpoint_for_different_role(tmp_path):
    checkpoint = tmp_path / "golden.checkpoint.json"
    golden = tmp_path / "golden.json"
    golden.write_text('{"cases": [{"case_id": "case-1"}]}', encoding="utf-8")
    citizen_identity = _build_run_identity(
        base_url="http://127.0.0.1:5055",
        role="citizen",
        golden_path=golden,
        cases=[{"case_id": "case-1"}],
    )
    officer_identity = _build_run_identity(
        base_url="http://127.0.0.1:5055",
        role="officer",
        golden_path=golden,
        cases=[{"case_id": "case-1"}],
    )
    _write_json_atomic(
        checkpoint,
        {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "run_identity": citizen_identity,
            "role": "citizen",
            "golden_file": str(golden),
            "results": [],
        },
    )

    try:
        _load_checkpoint(checkpoint, run_identity=officer_identity)
    except RuntimeError as exc:
        assert str(exc) == "FEATURE017_CHECKPOINT_INPUT_MISMATCH"
    else:
        raise AssertionError("role mismatch must not reuse checkpoint")


def test_evaluator_rejects_checkpoint_for_changed_golden_or_target(tmp_path):
    checkpoint = tmp_path / "golden.checkpoint.json"
    golden = tmp_path / "golden.json"
    cases = [{"case_id": "case-1"}]
    golden.write_text('{"cases": [{"case_id": "case-1"}]}', encoding="utf-8")
    original = _build_run_identity(
        base_url="http://127.0.0.1:5055",
        role="citizen",
        golden_path=golden,
        cases=cases,
    )
    _write_json_atomic(
        checkpoint,
        {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "evaluator_version": EVALUATOR_VERSION,
            "run_identity": original,
            "results": [],
        },
    )

    changed_target = _build_run_identity(
        base_url="https://staging.example.invalid",
        role="citizen",
        golden_path=golden,
        cases=cases,
    )
    try:
        _load_checkpoint(checkpoint, run_identity=changed_target)
    except RuntimeError as exc:
        assert str(exc) == "FEATURE017_CHECKPOINT_INPUT_MISMATCH"
    else:
        raise AssertionError("target mismatch must not reuse checkpoint")

    golden.write_text('{"cases": [{"case_id": "case-1", "changed": true}]}', encoding="utf-8")
    changed_golden = _build_run_identity(
        base_url="http://127.0.0.1:5055",
        role="citizen",
        golden_path=golden,
        cases=cases,
    )
    try:
        _load_checkpoint(checkpoint, run_identity=changed_golden)
    except RuntimeError as exc:
        assert str(exc) == "FEATURE017_CHECKPOINT_INPUT_MISMATCH"
    else:
        raise AssertionError("Golden hash mismatch must not reuse checkpoint")


def test_resume_re_evaluates_rows_with_current_evaluator():
    cases = [
        {
            "case_id": "case-1",
            "expected_form_ids": [],
            "forbidden_form_ids": [],
            "expected_answer_mode": "grounded_answer",
            "expected_clarification": False,
        }
    ]
    rows = [
        {
            "case_id": "case-1",
            "http_status": 200,
            "answer_present": True,
            "answer_mode": "normal",
            "grounding_status": "fully_grounded",
            "actual_form_ids": [],
            "clarifying_question_count": 0,
            "quality_flags": [],
            "answer_completeness_status": "complete",
            "error": None,
            "evaluation_errors": ["ANSWER_MODE_MISMATCH"],
        }
    ]

    refreshed = _reevaluate_checkpoint_rows(rows, cases=cases)

    assert refreshed[0]["evaluation_errors"] == []


def test_non_200_error_projection_is_content_free():
    payload = {
        "detail": {
            "code": "ASK_FAILED",
            "message": "internal detail that must not enter release evidence",
            "retryable": True,
        }
    }

    assert _safe_error_projection(payload, status=500) == {
        "code": "ASK_FAILED",
        "retryable": True,
    }


def test_evaluator_keeps_form_and_clarification_gates():
    case = {
        "expected_form_ids": ["f1"],
        "forbidden_form_ids": ["f2"],
        "expected_answer_mode": "grounded_answer",
        "expected_clarification": True,
    }
    payload = {
        "answer": "Có căn cứ.",
        "recommended_forms": [{"form_id": "f1"}],
        "answer_mode": "normal",
        "clarifying_questions": ["Bạn muốn thủ tục nào?"],
    }
    assert _evaluate(case, payload, 200) == []


def test_evaluator_keeps_grounded_claims_when_coverage_is_advisory():
    case = {
        "expected_form_ids": [],
        "forbidden_form_ids": [],
        "expected_answer_mode": "source_view_only",
    }
    payload = {
        "answer": "Phần căn cứ đã xác minh.",
        "recommended_forms": [],
        "answer_mode": "normal",
        "grounding_status": "fully_grounded",
        "answer_completeness": {"status": "incomplete"},
        "quality_flags": ["missing_forms", "missing_conclusion"],
    }
    assert _evaluate(case, payload, 200) == []


def test_evaluator_hashes_content_for_canary_reports():
    assert len(_sha256("question")) == 64
    assert _sha256("question") != _sha256("answer")


def test_balanced_case_selection_covers_each_domain():
    cases = [
        {"domain": domain, "case_id": f"{domain}-{index}"}
        for domain in ("a", "b", "c", "d", "e")
        for index in range(4)
    ]
    selected = _select_cases(cases, limit=10, balanced_domains=True)
    assert [item["domain"] for item in selected].count("a") == 2
    assert len(selected) == 10


def test_evaluator_accepts_legacy_login_without_identifier():
    """The local shared-password runtime does not require a username."""
    import inspect

    source = inspect.getsource(__import__("scripts.run_feature017_deepseek_golden1000", fromlist=["execute"]).execute)
    assert 'login_payload = {"password": password, "role": role}' in source
