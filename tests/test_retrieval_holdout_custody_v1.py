from __future__ import annotations

from copy import deepcopy

from api.retrieval_holdout_contracts import (
    aggregate_holdout_receipt,
    validate_public_holdout_envelope,
)
from scripts.validate_production_holdout_artifact_v1 import validate_artifacts
from api.retrieval_release_contracts import canonical_sha256
from scripts.validate_retrieval_eval_suite_v1 import validate_suite


def _envelope() -> dict:
    return {
        "schema_version": "production-holdout-envelope-v1",
        "dataset_version": "holdout-v1",
        "split": "production-holdout",
        "case_count": 500,
        "domain_counts": {
            "Hộ tịch/chứng thực": 100,
            "Đất đai/xây dựng/môi trường": 100,
            "Cư trú/căn cước/an ninh": 100,
            "Khiếu nại/tố cáo/tiếp công dân/xử phạt": 100,
            "An sinh/y tế/giáo dục": 100,
        },
        "content_sha256": "a" * 64,
        "case_ids_sha256": "b" * 64,
        "source_snapshot_sha256": "c" * 64,
        "manifest_sha256": "d" * 64,
        "quota_policy_sha256": "1" * 64,
        "quota_attestation_sha256": "2" * 64,
        "cross_split_leakage_audit_sha256": "3" * 64,
        "official_source_approval_manifest_sha256": "4" * 64,
        "custody_ref": "LEGAL-QA:holdout-v1",
        "custodian_id": "legal-qa-independent",
        "sealed_at": "2026-08-16T00:00:00Z",
        "reviewer_approval_count": 500,
        "eligible_for_single_run": True,
        "consumed_at": None,
        "consumed_candidate_sha256": None,
    }


def test_public_holdout_envelope_contains_no_cases_questions_or_answers():
    envelope = _envelope()
    assert validate_public_holdout_envelope(envelope) == []

    leaked = deepcopy(envelope)
    leaked["cases"] = [{"question": "nội dung bí mật", "positive_source_groups": []}]
    errors = validate_public_holdout_envelope(leaked)
    assert "forbidden_public_key:cases" in errors
    assert "forbidden_public_key:question" in errors
    assert "forbidden_public_key:positive_source_groups" in errors


def test_holdout_receipt_is_aggregate_only_and_failure_consumes_version():
    internal_summary = {
        "case_count": 500,
        "answer_required_count": 400,
        "expected_refusal_count": 100,
        "recall_at_10": 0.94,
        "mrr_at_10": 0.89,
        "correct_refusal_rate": 0.99,
        "multi_issue_case_count": 75,
        "multi_issue_all_required_sources_coverage": 0.93,
        "exact_law_article_case_count": 100,
        "exact_law_article_lookup_recall_at_50": 1.0,
        "exact_law_article_final_recall_at_10": 0.99,
        "latency_ms": {"p95": 2000.0},
        "per_domain": {
            "Hộ tịch/chứng thực": {
                "answer_required_count": 80,
                "recall_at_10": 0.95,
                "mrr_at_10": 0.91,
            }
        },
        "misses": [{"case_id": "secret-1", "reason": "candidate_miss"}],
        "dataset_errors": [{"case_id": "secret-2", "code": "error"}],
    }
    gates = {"recall_at_10": False, "safety": True}
    receipt = aggregate_holdout_receipt(
        envelope=_envelope(),
        candidate_sha256="e" * 64,
        summary=internal_summary,
        gates=gates,
    )

    serialized = str(receipt)
    assert "secret-1" not in serialized
    assert "secret-2" not in serialized
    assert "misses" not in receipt["metrics"]
    assert "dataset_errors" not in receipt["metrics"]
    assert receipt["status"] == "FAIL"
    assert receipt["holdout_reusable"] is False
    assert receipt["next_holdout_version_required"] is True


def test_consumed_holdout_cannot_be_reused_for_any_candidate():
    envelope = _envelope()
    envelope["eligible_for_single_run"] = False
    envelope["consumed_at"] = "2026-08-16T01:00:00Z"
    envelope["consumed_candidate_sha256"] = "f" * 64

    errors = validate_public_holdout_envelope(envelope, require_unused=True)
    assert "holdout_already_consumed" in errors


def test_public_validator_binds_receipt_to_exact_envelope():
    envelope = _envelope()
    receipt = aggregate_holdout_receipt(
        envelope=envelope,
        candidate_sha256="e" * 64,
        summary={"case_count": 500, "recall_at_10": 1.0},
        gates={"recall_at_10": True},
    )
    assert validate_artifacts(envelope, receipt)["status"] == "PASS"

    receipt["manifest_sha256"] = "f" * 64
    report = validate_artifacts(envelope, receipt)
    assert report["status"] == "FAIL"
    assert "receipt_envelope_binding_mismatch:manifest_sha256" in report["errors"]


def test_development_case_checksum_and_normalized_duplicate_are_fail_closed():
    def make_case(case_id: str, question: str) -> dict:
        case = {
            "case_id": case_id,
            "split": "golden-regression",
            "domain": "Hộ tịch/chứng thực",
            "tags": ["procedure"],
            "question": question,
            "legal_as_of": "2026-08-16",
            "temporal_scope": "current",
            "answer_required": True,
            "expected_refusal": False,
            "refusal_category": "none",
            "positive_source_groups": [{
                "group_id": "group-1",
                "sources": [{
                    "law_number": "60/2014/QH13",
                    "article": "16",
                    "official_url": "https://vbpl.vn/example",
                    "jurisdiction": "Vietnam",
                    "validity_from": "2016-01-01",
                    "validity_to": None,
                }],
            }],
            "hard_negative_sources": [],
            "issue_groups": [{
                "issue_id": "issue-1",
                "required_source_group_ids": ["group-1"],
            }],
            "reviewer_approval": {
                "reviewer_id": "legal-qa",
                "reviewed_at": "2026-08-16T00:00:00Z",
                "decision": "approved",
                "evidence_sha256": "a" * 64,
            },
        }
        case["case_sha256"] = canonical_sha256(case)
        return case

    first = make_case("g-1", "Thủ tục đăng ký khai sinh?")
    second = make_case("g-2", "  THỦ TỤC   ĐĂNG KÝ KHAI SINH  ")
    second["case_sha256"] = "f" * 64
    report = validate_suite({
        "schema_version": "retrieval-eval-suite-v1",
        "dataset_version": "draft-v1",
        "source_snapshot_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "review_policy": {
            "golden_development_allowed": True,
            "hard_negative_development_allowed": True,
            "holdout_sealed": False,
        },
        "cases": [first, second],
        "suite_sha256": "d" * 64,
    }, require_complete=False)
    assert "case_sha256_mismatch:g-2" in report["errors"]
    assert any(error.startswith("normalized_question_duplicate:") for error in report["errors"])
