from __future__ import annotations

from pathlib import Path

import pytest

from api.legal_form_catalog import FormCatalog
from scripts.evaluate_form_resolution_matrix import evaluate_matrix


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def matrix_report() -> dict:
    """Run the 3,120-case deterministic matrix once per test module."""

    return evaluate_matrix(
        FormCatalog.load_default(),
        write=False,
    )


def test_deterministic_form_resolution_matrix_has_at_least_3120_cases(matrix_report):
    report = matrix_report

    assert report["case_count"] >= 3120
    assert report["procedure_count"] >= 52
    assert report["wrong_procedure_count"] == 0
    assert report["wrong_form_count"] == 0
    assert report["expired_or_superseded_exposure_count"] == 0
    assert report["role_leakage_count"] == 0
    assert report["pending_reference_seed_exposure_count"] == 0
    assert report["procedure_top1_rate"] >= 0.99
    assert report["lookup_p95_ms"] <= 200
    assert report["model_call_count"] == 0


def test_matrix_report_contains_no_question_or_answer_text(matrix_report):
    report = matrix_report

    assert "cases" not in report
    assert "questions" not in report
    assert report["privacy"]["contains_question_text"] is False
    assert report["privacy"]["contains_answer_text"] is False
