from __future__ import annotations

import pytest

from api.legal_retrieval_evaluation import (
    METRIC_CONTRACT_VERSION,
    build_retrieval_metrics,
    build_source_gap_manifest,
    evaluate_candidate_stage_gate,
    match_expected_sources,
    match_expected_source_group_hits,
    summarize_stage_diagnostics,
    validate_case_temporal_alignment,
)
from scripts.run_retrieval_quality_v2 import _quality_gates


def test_answer_metrics_exclude_expected_refusals_but_keep_false_blocks_as_misses():
    cases = [
        {
            "case_id": "case-1",
            "domain": "Hộ tịch/chứng thực",
            "expected_refusal": False,
            "expected_sources": [{"law_number": "60/2014/QH13", "article": "16"}],
        },
        {
            "case_id": "case-2",
            "domain": "Hộ tịch/chứng thực",
            "expected_refusal": False,
            "expected_sources": [{"law_number": "60/2014/QH13", "article": "17"}],
        },
        {
            "case_id": "case-3",
            "domain": "Hộ tịch/chứng thực",
            "expected_refusal": True,
            "expected_sources": [],
        },
    ]
    rows = [
        {"case_id": "case-1", "hit_at_10": True, "hit_rank": 2},
        {
            "case_id": "case-2",
            "hit_at_10": False,
            "hit_rank": None,
            "response_status": "clarification_required",
            "error_code": "TEMPORAL_AS_OF_CONFLICT",
        },
        {
            "case_id": "case-3",
            "hit_at_10": False,
            "hit_rank": None,
            "correct_refusal": True,
        },
    ]

    metrics = build_retrieval_metrics(cases, rows)

    assert metrics["metric_contract_version"] == METRIC_CONTRACT_VERSION
    assert metrics["answer_required_count"] == 2
    assert metrics["expected_refusal_count"] == 1
    assert metrics["recall_at_10"] == 0.5
    assert metrics["mrr_at_10"] == 0.25
    assert metrics["correct_refusal_rate"] == 1.0
    assert metrics["false_blocked_answer_count"] == 1
    assert metrics["per_domain"]["ho_tich_chung_thuc"]["recall_at_10"] == 0.5


def test_multi_issue_metrics_require_each_source_group():
    cases = [
        {
            "case_id": "multi-1",
            "domain": "Cư trú/an ninh",
            "expected_refusal": False,
            "expected_sources": [
                {
                    "law_number": "68/2020/QH14",
                    "article": "7",
                    "reason": "Vấn đề 1 - nguồn thứ nhất",
                },
                {
                    "law_number": "26/2023/QH15",
                    "article": "5",
                    "reason": "Vấn đề 2 - nguồn thứ hai",
                },
            ],
        }
    ]
    rows = [
        {
            "case_id": "multi-1",
            "hit_at_10": True,
            "hit_rank": 1,
            "expected_source_coverage": 0.5,
            "issue_hits": [True, False],
        }
    ]

    metrics = build_retrieval_metrics(cases, rows)

    assert metrics["issue_count"] == 2
    assert metrics["issue_recall_at_10"] == 0.5
    assert metrics["all_required_sources_coverage"] == 0.0


def test_law_level_expected_source_matches_any_article_from_the_same_law():
    case = {
        "expected_sources": [{"law_number": "82/2020/NĐ-CP", "article": None}],
    }
    results = [{"law_number": "82/2020/NĐ-CP", "article_number": "12"}]

    match = match_expected_sources(results, case)

    assert match["hit_at_10"] is True
    assert match["hit_rank"] == 1


def test_one_issue_packet_can_cover_multiple_approved_source_groups():
    case = {
        "expected_sources": [
            {
                "law_number": "116/2026/TT-BCA",
                "article": "14",
                "reason": "Vấn đề 1 - nguồn hiện hành",
            },
            {
                "law_number": "68/2020/QH14",
                "article": "8",
                "reason": "Vấn đề 2 - luật cư trú",
            },
        ]
    }
    packet = [
        {"law_number": "116/2026/TT-BCA", "article_number": "14"},
        {"law_number": "68/2020/QH14", "article_number": "8"},
    ]

    assert match_expected_source_group_hits(case, [packet], packet) == [True, True]


def test_query_date_mismatch_is_reported_as_dataset_error_not_retrieval_miss():
    case = {
        "case_id": "temporal-dataset-1",
        "domain": "Cư trú/an ninh",
        "legal_as_of": "2026-08-11",
        "questions": {
            "citizen": "Quy định áp dụng trước ngày 01/07/2026 là gì?",
        },
        "expected_refusal": False,
        "expected_sources": [{"law_number": "116/2026/TT-BCA", "article": "3"}],
    }

    error = validate_case_temporal_alignment(case)
    metrics = build_retrieval_metrics(
        [case],
        [{"case_id": case["case_id"], "hit_at_10": False, "dataset_error": error}],
    )

    assert error == {
        "case_id": "temporal-dataset-1",
        "code": "DATASET_LEGAL_AS_OF_QUERY_DATE_MISMATCH",
        "legal_as_of": "2026-08-11",
        "query_dates": ["2026-07-01"],
        "mismatched_query_dates": ["2026-07-01"],
    }
    assert metrics["dataset_case_count"] == 1
    assert metrics["valid_case_count"] == 0
    assert metrics["dataset_error_count"] == 1
    assert metrics["answer_required_count"] == 0
    assert metrics["miss_count"] == 0


def test_source_gap_manifest_reports_each_missing_reference_without_mutating_scope():
    candidate = {
        "candidate_collection": "candidate-v1",
        "manifest_sha256": "a" * 64,
        "documents": [
            {
                "document_id": 1,
                "law_number": "60/2014/QH13",
                "expected_chunk_ids": [10],
            }
        ],
    }
    datasets = {
        "hard-negative-100": {
            "examples": [
                {
                    "case_id": "web-1",
                    "domain": "Hộ tịch/chứng thực",
                    "legal_as_of": "2026-08-10",
                    "question": "Câu hỏi",
                    "positive_sources": [
                        {"law_number": "60/2014/QH13", "article": "16"},
                        {"law_number": "123/2015/NĐ-CP", "article": "10"},
                    ],
                }
            ]
        }
    }

    manifest = build_source_gap_manifest(candidate, datasets)

    assert manifest["schema_version"] == "legal-retrieval-source-gap-v1"
    assert manifest["active_pointer_changed"] is False
    assert manifest["candidate_collection_mutated"] is False
    assert manifest["missing_reference_count"] == 1
    assert manifest["missing_law_count"] == 1
    assert manifest["missing_references"][0]["law_number"] == "123/2015/NĐ-CP"
    assert manifest["missing_references"][0]["required_action"] == "legal_review_required"


def test_stage_diagnostics_assign_one_primary_root_cause_per_miss():
    rows = [
        {
            "case_id": "a",
            "domain": "Hộ tịch/chứng thực",
            "source_available": False,
            "candidate_hit_at_50": False,
            "final_hit_at_10": False,
        },
        {
            "case_id": "b",
            "domain": "Hộ tịch/chứng thực",
            "source_available": True,
            "article_chunk_available": False,
            "candidate_hit_at_50": False,
            "final_hit_at_10": False,
        },
        {
            "case_id": "c",
            "domain": "Hộ tịch/chứng thực",
            "source_available": True,
            "article_chunk_available": True,
            "candidate_hit_at_50": True,
            "fusion_hit_at_50": True,
            "final_hit_at_10": False,
        },
    ]

    summary = summarize_stage_diagnostics(rows)

    assert summary["case_count"] == 3
    assert summary["candidate_recall_at_50"] == pytest.approx(1 / 3, abs=1e-6)
    assert summary["root_causes"] == {
        "article_chunk_absent": 1,
        "fusion_rank_loss": 1,
        "source_absent": 1,
    }


def test_exact_recall_uses_only_queries_with_explicit_identifier_lookup():
    rows = [
        {"case_id": "exact", "exact_expected": True, "exact_hit_at_50": True},
        {"case_id": "natural", "exact_expected": False, "exact_hit_at_50": False},
    ]

    summary = summarize_stage_diagnostics(rows)

    assert summary["exact_expected_count"] == 1
    assert summary["exact_recall_at_50"] == 1.0


def test_candidate_stage_gate_blocks_reranker_until_every_domain_passes():
    passing = {
        "candidate_recall_at_50": 0.99,
        "per_domain": {
            "a": {"candidate_recall_at_50": 0.98},
            "b": {"candidate_recall_at_50": 1.0},
        },
        "outside_manifest_result_count": 0,
        "invalid_evidence_result_count": 0,
        "active_pointer_changed": False,
    }
    failing = {
        **passing,
        "per_domain": {
            "a": {"candidate_recall_at_50": 0.97},
            "b": {"candidate_recall_at_50": 1.0},
        },
    }

    assert evaluate_candidate_stage_gate(passing)["passed"] is True
    assert evaluate_candidate_stage_gate(failing)["passed"] is False
    assert "per_domain_candidate_recall_at_50" in evaluate_candidate_stage_gate(failing)["failed_gates"]

    unresolved_gap = {
        "missing_reference_count": 1,
        "candidate_collection_mutated": False,
        "active_pointer_changed": False,
    }
    gated = evaluate_candidate_stage_gate(passing, unresolved_gap)
    assert gated["passed"] is False
    assert "approved_source_gap_zero" in gated["failed_gates"]

    unresolved_articles = {
        "law_absent_reference_count": 0,
        "article_absent_reference_count": 1,
        "database_mutated": False,
        "candidate_collection_mutated": False,
        "active_pointer_changed": False,
    }
    article_gated = evaluate_candidate_stage_gate(
        passing,
        {"missing_reference_count": 0},
        unresolved_articles,
    )
    assert article_gated["passed"] is False
    assert (
        "approved_source_article_gap_zero" in article_gated["failed_gates"]
    )


def test_quality_gate_fails_when_dataset_has_temporal_contract_errors():
    run = {
        "dataset_error_count": 1,
        "recall_at_10": 1.0,
        "mrr_at_10": 1.0,
        "correct_refusal_rate": 1.0,
        "outside_manifest_result_count": 0,
        "invalid_evidence_result_count": 0,
        "errors": 0,
        "p95_retrieval_ms": 100.0,
        "per_domain": {"a": {"answer_required_count": 1, "recall_at_10": 1.0}},
    }

    gates = _quality_gates(run)

    assert gates["dataset_integrity"] is False
    assert all(value for name, value in gates.items() if name != "dataset_integrity")
