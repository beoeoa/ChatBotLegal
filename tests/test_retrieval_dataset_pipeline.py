import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from scripts.evaluate_retrieval_dataset import (
    _cache_key,
    _load_dataset,
    _load_expected_sources,
    _load_verified_retrieval_report,
    build_report,
    classify_case,
    enqueue_detected_source_gaps,
    evaluate_model_response,
    evaluate_retrieval,
    normalize_question,
    select_model_sample_ids,
    validate_privacy_safe_report,
)


def _row(**overrides):
    return {
        "rank": 1,
        "document_id": 10,
        "law_number": "60/2014/QH13",
        "article_number": "13",
        "source_url": "https://vbpl.vn/source",
        "effective_status": "active",
        "effective_date": "2014-07-01",
        "expired_date": None,
        "domain": "ho_tich_chung_thuc",
        **overrides,
    }


def test_normalized_question_cache_key_includes_legal_as_of():
    assert normalize_question("  Đăng ký   khai sinh? ") == "đăng ký khai sinh?"
    assert normalize_question("đăng ký khai sinh?") == normalize_question(
        "  ĐĂNG KÝ KHAI SINH? "
    )


def test_cache_key_changes_with_dataset_and_retrieval_version():
    base = _cache_key(
        "Đăng ký khai sinh",
        "2026-07-27",
        dataset_version="dataset-v1",
        retrieval_version="retrieval-v1",
    )
    assert base != _cache_key(
        "Đăng ký khai sinh",
        "2026-07-27",
        dataset_version="dataset-v2",
        retrieval_version="retrieval-v1",
    )
    assert base != _cache_key(
        "Đăng ký khai sinh",
        "2026-07-27",
        dataset_version="dataset-v1",
        retrieval_version="retrieval-v2",
    )


def test_model_response_gate_requires_90_percent_coverage_and_full_grounding():
    payload = {
        "answer_sections": [
            {
                "status": "sufficiently_evidenced",
                "answer": "Nội dung đã xác minh.",
                "citations": [
                    {
                        "document_title": "Luật Hộ tịch",
                        "source_url": "https://vbpl.vn/source",
                    }
                ],
            }
        ],
        "citations": [
            {
                "document_title": "Luật Hộ tịch",
                "source_url": "https://vbpl.vn/source",
            }
        ],
        "recommended_forms": [],
        "forms_unavailable": False,
        "rag_trace": {
            "section_orchestration": {
                "metric": {"error_category": "none"},
                "claim_validation": {
                    "coverage_ratio": 0.9,
                    "claim_grounding_ratio": 1.0,
                    "displayed_legal_claim_count": 2,
                    "displayed_claims_with_valid_evidence": 2,
                    "quality_gate": {"pass": True},
                },
            }
        },
    }

    passed = evaluate_model_response(payload)
    failed = evaluate_model_response(
        {
            **payload,
            "rag_trace": {
                "section_orchestration": {
                    "metric": {"error_category": "validation_failed"},
                    "claim_validation": {
                        "coverage_ratio": 0.8999,
                        "claim_grounding_ratio": 1.0,
                        "displayed_legal_claim_count": 1,
                        "displayed_claims_with_valid_evidence": 1,
                        "quality_gate": {"pass": False},
                    },
                }
            },
        }
    )

    assert passed["case_pass"] is True
    assert passed["citation_validity_rate"] == 1.0
    assert failed["case_pass"] is False
    assert failed["fallback"] is True


def test_model_response_gate_uses_public_citizen_structured_contract():
    payload = {
        "answer_mode": "verified_source_condensed",
        "grounding_status": "fully_grounded",
        "answer_sections": [
            {
                "status": "sufficiently_evidenced",
                "claim_types": ["rule"],
                "citations": [
                    {
                        "source_url": "https://vbpl.vn/source",
                        "verification_status": "verified",
                        "verification_level": "content_quote",
                    }
                ],
            }
        ],
        "citations": [],
        "recommended_forms": [],
    }

    result = evaluate_model_response(payload)

    assert result["case_pass"] is True
    assert result["coverage_ratio"] == 1.0
    assert result["grounded_claim_rate"] == 1.0
    assert result["citation_validity_rate"] == 1.0
    assert result["fallback"] is True


def test_model_response_gate_accepts_deterministic_clarification_without_claim():
    payload = {
        "answer_mode": "source_view_only",
        "grounding_status": "insufficient_evidence",
        "clarifying_questions": ["Vui lòng bổ sung tên thủ tục."],
        "answer_sections": [
            {
                "status": "insufficiently_evidenced",
                "clarifying_question": "Vui lòng bổ sung tên thủ tục.",
                "citations": [],
            }
        ],
        "citations": [],
        "recommended_forms": [],
    }

    result = evaluate_model_response(payload)

    assert result["case_pass"] is True
    assert result["coverage_ratio"] == 1.0
    assert result["displayed_claim_count"] == 0


def test_full_answer_sampling_selects_every_case():
    rows = [
        {
            "case_id": f"case-{index}",
            "domain": "civil",
            "classification": "FOUND_AND_RETRIEVED",
            "uncertain": False,
        }
        for index in range(20)
    ]

    selected = select_model_sample_ids(
        rows,
        sample_percent=100,
        seed=7,
        full_answer=True,
    )

    assert all("all_cases" in reasons for reasons in selected.values())


def test_full_answer_can_reuse_only_matching_live_pass_report(tmp_path):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        '{"cases":[{"case_id":"case-1","question":"Điều 1 quy định gì?"}]}',
        encoding="utf-8",
    )
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    report = tmp_path / "retrieval.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": "retrieval-dataset-v2",
                "status": "PASS",
                "live_retrieval": True,
                "dataset_sha256": digest,
                "dataset_version": "dataset-v1",
                "retrieval_version": "retrieval-v1",
                "ranking_strategy": "rrf_v2",
                "learned_reranker_enabled": False,
                "request_count": 1,
                "completed_count": 1,
                "external_error_count": 0,
                "cache_hits": 0,
                "cache_misses": 1,
                "cases": [
                    {
                        "case_id": "case-1",
                        "classification": "FOUND_AND_RETRIEVED",
                        "top10": [_row()],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    rows, stats = _load_verified_retrieval_report(
        report,
        dataset_path=dataset,
        cases=[{"case_id": "case-1"}],
        dataset_version="dataset-v1",
        retrieval_version="retrieval-v1",
        ranking_strategy="rrf_v2",
        learned_reranker_enabled=False,
    )

    assert rows[0]["selected_sources"] == rows[0]["top10"]
    assert stats == {"hits": 0, "misses": 1, "errors": 0}


def test_dataset_uses_reviewed_case_domain_when_issue_planner_domain_is_unknown(
    monkeypatch,
    tmp_path,
):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        '{"questions":[{"case_id":"ht-unknown","question":"Đăng ký khai sinh",'
        '"domain":"ho_tich"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "api.legal_section_grounding.plan_legal_issues",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                issue_id="issue-1",
                query_text="Đăng ký khai sinh",
                domain="unknown",
                intent="procedure",
            )
        ],
    )
    monkeypatch.setattr(
        "api.legal_section_grounding.retrieval_domain_slug",
        lambda *_args, **_kwargs: None,
    )

    cases = _load_dataset(dataset, "2026-07-26")

    assert cases[0]["domain"] == "ho_tich_chung_thuc"
    assert cases[0]["issues"][0]["domain"] == "ho_tich_chung_thuc"


def test_dataset_preserves_reviewed_case_domain_for_generic_administrative_issue(
    monkeypatch,
    tmp_path,
):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        '{"questions":[{"case_id":"certified-copy","question":"Chứng thực bản sao",'
        '"domain":"ho_tich"}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "api.legal_section_grounding.plan_legal_issues",
        lambda *_args, **_kwargs: [
            SimpleNamespace(
                issue_id="issue-1",
                query_text="Chứng thực bản sao",
                domain="administrative",
                intent="documents",
            )
        ],
    )

    cases = _load_dataset(dataset, "2026-07-26")

    assert cases[0]["issues"][0]["domain"] == "ho_tich_chung_thuc"


def test_dataset_records_declared_outside_scope_case_as_verified_data_gap(
    tmp_path,
):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        '{"questions":[{"case_id":"outside","question":"Đăng ký sáng chế",'
        '"domain":"tinh_huong","expected_scope":"outside"}]}',
        encoding="utf-8",
    )

    cases = _load_dataset(dataset, "2026-07-26")

    assert cases[0]["expected_sources"] == [
        {
            "outcome": "VERIFIED_DATA_GAP",
            "reason_code": "outside_commune_scope",
        }
    ]


def test_dataset_loader_projects_golden_v2_role_question_and_source(tmp_path):
    dataset = tmp_path / "golden.json"
    dataset.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "cases": [
                    {
                        "case_id": "golden-1",
                        "domain": "Cư trú/an ninh",
                        "legal_as_of": "2026-08-11",
                        "questions": {
                            "citizen": "Điều 16 116/2026/TT-BCA quy định gì?",
                            "officer": None,
                        },
                        "expected_sources": [
                            {"law_number": "116/2026/TT-BCA", "article": "16"}
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cases = _load_dataset(dataset, "2026-08-11")

    assert cases[0]["question"] == "Điều 16 116/2026/TT-BCA quy định gì?"
    assert cases[0]["domain"] == "cu_tru_an_ninh"
    assert cases[0]["expected_sources"][0]["outcome"] == "AVAILABLE_CORRECTLY_TIERED"
    assert cases[0]["expected_sources"][0]["provision"] == "16"


def test_classification_matches_golden_source_by_law_and_article_without_document_id():
    result = classify_case(
        expected_sources=[{
            "outcome": "AVAILABLE_CORRECTLY_TIERED",
            "law_number": "60/2014/QH13",
            "article": "13",
        }],
        rows=[_row()],
        requested_domain="ho_tich_chung_thuc",
    )

    assert result["classification"] == "FOUND_AND_RETRIEVED"
    assert result["expected_sources"][0]["rank"] == 1
    assert result["expected_sources"][0]["provision"] == "13"


def test_classification_matches_any_article_in_compact_golden_provision_list():
    result = classify_case(
        expected_sources=[{
            "outcome": "AVAILABLE_CORRECTLY_TIERED",
            "law_number": "60/2014/QH13",
            "article": "32,33,34",
        }],
        rows=[_row(article_number="33")],
        requested_domain="ho_tich_chung_thuc",
    )

    assert result["classification"] == "FOUND_AND_RETRIEVED"
    assert result["expected_sources"][0]["in_top10"] is True
    assert result["expected_sources"][0]["provision"] == "32,33,34"


def test_classification_keeps_top10_for_each_issue_in_multi_issue_question():
    rows = [
        _row(
            document_id=100 + index,
            law_number="OTHER/2026/QH15",
            article_number=str(index),
            _issue_id="issue-1",
            _issue_rank=index,
        )
        for index in range(1, 11)
    ]
    rows.append(
        _row(
            document_id=999,
            law_number="116/2026/TT-BCA",
            article_number="16",
            domain="cu_tru_an_ninh",
            _issue_id="issue-2",
            _issue_rank=1,
        )
    )

    result = classify_case(
        expected_sources=[{
            "law_number": "116/2026/TT-BCA",
            "article": "16",
            "outcome": "AVAILABLE_CORRECTLY_TIERED",
        }],
        rows=rows,
        requested_domain="cu_tru_an_ninh",
    )

    assert result["classification"] == "FOUND_AND_RETRIEVED"
    assert result["expected_sources"][0]["in_top5"] is True
    assert result["expected_sources"][0]["in_top10"] is True


def test_classification_records_rank_source_effectivity_domain_and_reason():
    expected = [
        {
            "outcome": "AVAILABLE_CORRECTLY_TIERED",
            "document_id": 10,
            "provision": "13",
            "law_number": "60/2014/QH13",
        }
    ]
    result = classify_case(
        expected_sources=expected,
        rows=[_row(), _row(rank=2, document_id=11, domain="dat_dai_xay_dung")],
        requested_domain="ho_tich_chung_thuc",
    )
    assert result["classification"] == "FOUND_AND_RETRIEVED"
    assert result["reason_code"] == "expected_source_ranked_top10"
    assert result["expected_sources"][0]["rank"] == 1
    assert result["selected_sources"][0]["source_url"].startswith("https://")
    assert result["selected_sources"][0]["effective_status"] == "active"
    assert result["wrong_field_count"] == 1
    assert result["expired_selection_count"] == 0


def test_domain_family_does_not_flag_civil_status_source_as_wrong_field():
    result = classify_case(
        expected_sources=[],
        rows=[_row(domain="tu_phap_ho_tich")],
        requested_domain="ho_tich_chung_thuc",
    )
    assert result["wrong_field_count"] == 0
    assert result["selected_sources"][0]["in_requested_domain"] is True


def test_domain_family_does_not_flag_labour_source_in_reviewed_social_scope():
    result = classify_case(
        expected_sources=[],
        rows=[_row(domain="an_sinh_y_te_giao_duc")],
        requested_domain="lao_dong",
    )
    assert result["wrong_field_count"] == 0
    assert result["selected_sources"][0]["in_requested_domain"] is True


def test_reviewed_urban_child_scope_is_not_wrong_field_but_residence_is():
    result = classify_case(
        expected_sources=[],
        rows=[
            _row(domain="xay_dung_do_thi"),
            _row(document_id=11, domain="cu_tru_an_ninh"),
        ],
        requested_domain="trat_tu_do_thi",
    )

    assert result["selected_sources"][0]["in_requested_domain"] is True
    assert result["selected_sources"][1]["in_requested_domain"] is False
    assert result["wrong_field_count"] == 1


def test_classification_distinguishes_not_retrieved_and_verified_gap():
    not_retrieved = classify_case(
        expected_sources=[
            {
                "outcome": "AVAILABLE_CORRECTLY_TIERED",
                "document_id": 99,
                "provision": "5",
            }
        ],
        rows=[_row()],
        requested_domain="ho_tich_chung_thuc",
    )
    gap = classify_case(
        expected_sources=[
            {
                "outcome": "VERIFIED_DATA_GAP",
                "expected_law_number": "999/2026/QH15",
                "reason_code": "official_source_absent",
            }
        ],
        rows=[],
        requested_domain="ho_tich_chung_thuc",
    )
    assert not_retrieved["classification"] == "FOUND_NOT_RETRIEVED"
    assert not_retrieved["reason_code"] == "source_exists_but_not_in_top10"
    assert gap["classification"] == "VERIFIED_DATA_GAP"
    assert gap["reason_code"] == "official_source_absent"
    assert gap["uncertain"] is False


def test_sampling_is_limited_to_declared_reasons_and_deterministic():
    rows = [
        {"case_id": "a", "domain": "land", "classification": "FOUND_AND_RETRIEVED", "uncertain": False},
        {"case_id": "b", "domain": "land", "classification": "FOUND_NOT_RETRIEVED", "uncertain": True},
        {"case_id": "c", "domain": "civil", "classification": "FOUND_AND_RETRIEVED", "uncertain": False, "is_regression": True},
        {"case_id": "d", "domain": "civil", "classification": "FOUND_AND_RETRIEVED", "uncertain": False},
    ]
    selected = select_model_sample_ids(rows, sample_percent=1, seed=7)
    assert selected["b"] == {"uncertain"}
    assert selected["c"] == {"regression", "representative"}
    assert selected["a"] == {"representative"}
    assert selected["d"] == set()


def test_privacy_report_rejects_question_answer_and_content_fields():
    safe = {
        "schema_version": "retrieval-dataset-v2",
        "dataset": "fixture-free.jsonl",
        "dataset_sha256": "abc",
        "dataset_version": "dataset-v1",
        "retrieval_version": "retrieval-v1",
        "ranking_strategy": "rrf_v2",
        "learned_reranker_enabled": False,
        "generation_version": "generation-v1",
        "run_id": "run-1",
        "legal_as_of": "2026-07-23",
        "retrieval_url": "http://127.0.0.1:8765",
        "retrieval_only": True,
        "live_retrieval": True,
        "fixture_fallback_count": 0,
        "external_error_count": 0,
        "external_status": "READY",
        "status": "PASS",
        "thresholds": {},
        "gates": {},
        "request_count": 2,
        "completed_count": 2,
        "expected_source_observation_count": 2,
        "classification_counts": {"FOUND_AND_RETRIEVED": 2},
        "recall_at_10": 1.0,
        "direct_source_top5": 1.0,
        "wrong_field_count": 0,
        "expired_selection_count": 0,
        "coverage": 1.0,
        "latency_ms": {"p50": 10, "p95": 20},
        "model_request_count": 0,
        "model_external_error_count": 0,
        "model_fallback_count": 0,
        "model_latency_ms": {"p50": 0, "p95": 0},
        "fallback_rate": 0.0,
        "case_pass_rate": 0.0,
        "available_facet_coverage": 0.0,
        "grounded_claim_rate": 0.0,
        "citation_validity_rate": 0.0,
        "form_gate_rate": 0.0,
        "broken_internal_marker_count": 0,
        "cost_estimate_usd": 0.0,
        "cache_hits": 1,
        "cache_misses": 1,
        "sample_counts": {},
        "selected_model_case_count": 0,
        "reason_code_counts": {},
        "cases": [],
    }
    assert validate_privacy_safe_report(safe)[0] is True
    unsafe = {**safe, "question": "private legal question"}
    assert validate_privacy_safe_report(unsafe)[0] is False


def test_expected_source_loader_ignores_unverified_contract_placeholders(tmp_path):
    manifest = tmp_path / "sources.jsonl"
    manifest.write_text(
        "\n".join(
            [
                '{"case_id":"a","outcome":"AVAILABLE_CORRECTLY_TIERED","document_id":10}',
                '{"case_id":"a","outcome":"EXPECTED_CONTRACT_GAP"}',
                '{"case_id":"b","outcome":"NOT_A_DOCUMENT_EXPECTATION"}',
                '{"case_id":"c","outcome":"VERIFIED_DATA_GAP","reason_code":"verified_absent"}',
            ]
        ),
        encoding="utf-8",
    )
    loaded = _load_expected_sources(manifest)
    assert [row["outcome"] for row in loaded["a"]] == ["AVAILABLE_CORRECTLY_TIERED"]
    assert "b" not in loaded
    assert loaded["c"][0]["outcome"] == "VERIFIED_DATA_GAP"


def test_evaluator_enqueues_only_declared_missing_sources_not_retrieval_misses(tmp_path):
    cases = [
        {
            "case_id": "missing-form",
            "legal_as_of": "2026-07-27",
            "source_gap": {
                "gap_type": "MISSING_FORM_SOURCE",
                "source_pages": ["https://dichvucong.gov.vn/procedure"],
                "procedure_id": "birth-registration",
            },
        },
        {
            "case_id": "retrieval-regression",
            "legal_as_of": "2026-07-27",
            "source_gap": {
                "gap_type": "FOUND_NOT_RETRIEVED",
                "source_pages": ["https://vbpl.vn/source"],
            },
        },
    ]
    results = [
        {"case_id": "missing-form", "classification": "VERIFIED_DATA_GAP"},
        {"case_id": "retrieval-regression", "classification": "FOUND_NOT_RETRIEVED"},
    ]

    counts = enqueue_detected_source_gaps(
        cases,
        results,
        store_path=tmp_path / "jobs.json",
    )

    assert counts == {"created": 1, "skipped": 1}


def test_report_fails_closed_when_quality_threshold_is_not_met():
    case = {
        "case_id": "a",
        "domain": "ho_tich_chung_thuc",
        "legal_as_of": "2026-07-23",
    }
    result = {
        **case,
        "classification": "FOUND_NOT_RETRIEVED",
        "reason_code": "source_exists_but_not_in_top10",
        "uncertain": True,
        "timing_ms": 40,
        "cache_hit": False,
        "wrong_field_count": 0,
        "expired_selection_count": 0,
        "selected_sources": [_row()],
        "top5": [_row()],
        "top10": [_row()],
        "expected_sources": [
            {
                "classification": "FOUND_NOT_RETRIEVED",
                "provision": None,
                "in_top5": False,
                "in_top10": False,
            }
        ],
    }
    report = build_report(
        dataset="dataset.jsonl",
        legal_as_of="2026-07-23",
        retrieval_url="http://127.0.0.1:8765",
        cases=[case],
        results=[result],
        cache_stats={"hits": 0, "misses": 1},
        sample_reasons={"a": {"uncertain"}},
    )
    assert report["status"] == "FAIL"
    assert report["gates"]["recall_at_10"] is False
    assert report["fixture_fallback_count"] == 0


def test_report_top5_gate_accepts_article_level_only_dataset():
    case = {
        "case_id": "article-only",
        "domain": "ho_tich_chung_thuc",
        "legal_as_of": "2026-08-11",
    }
    source = {
        "classification": "FOUND_AND_RETRIEVED",
        "law_number": "60/2014/QH13",
        "provision": "18",
        "in_top5": True,
        "in_top10": True,
    }
    report = build_report(
        dataset="article-only.json",
        legal_as_of="2026-08-11",
        retrieval_url="http://127.0.0.1:8765",
        cases=[case],
        results=[{
            **case,
            "classification": "FOUND_AND_RETRIEVED",
            "reason_code": "expected_source_ranked_top10",
            "uncertain": False,
            "timing_ms": 20,
            "cache_hit": False,
            "wrong_field_count": 0,
            "expired_selection_count": 0,
            "selected_sources": [_row(article_number="18")],
            "top5": [_row(article_number="18")],
            "top10": [_row(article_number="18")],
            "expected_sources": [source],
        }],
        cache_stats={"hits": 0, "misses": 1, "errors": 0},
        sample_reasons={"article-only": set()},
        thresholds={
            "recall_at_10_min": 0.99,
            "direct_source_top5_min": 0.99,
        },
    )

    assert report["direct_source_top5"] == 1.0
    assert report["gates"]["direct_source_top5"] is True
    assert report["status"] == "PASS"


def test_report_marks_live_transport_failure_as_blocked_external():
    report = build_report(
        dataset="dataset.jsonl",
        legal_as_of="2026-07-23",
        retrieval_url="http://127.0.0.1:8765",
        cases=[],
        results=[],
        cache_stats={"hits": 0, "misses": 0, "errors": 1},
        sample_reasons={},
    )
    assert report["status"] == "BLOCKED_EXTERNAL"
    assert report["external_status"] == "BLOCKED_EXTERNAL"
    assert report["gates"]["live_retrieval_without_fixture"] is False


def test_report_blocks_when_release_dataset_is_smaller_than_required():
    report = build_report(
        dataset="dataset.jsonl",
        legal_as_of="2026-07-23",
        retrieval_url="http://127.0.0.1:8765",
        cases=[{"case_id": "only-case"}],
        results=[],
        cache_stats={"hits": 0, "misses": 1, "errors": 0},
        sample_reasons={},
        thresholds={"minimum_case_count": 1000},
    )

    assert report["status"] == "FAIL"
    assert report["gates"]["minimum_dataset_size"] is False


def test_report_marks_mass_model_provider_failure_as_blocked_external():
    report = build_report(
        dataset="dataset.jsonl",
        legal_as_of="2026-07-23",
        retrieval_url="http://127.0.0.1:8765",
        cases=[],
        results=[],
        cache_stats={"hits": 0, "misses": 1, "errors": 0},
        sample_reasons={"a": {"representative"}},
        model_stats={
            "request_count": 1,
            "external_error_count": 1,
            "fallback_count": 1,
            "fallback_rate": 1.0,
        },
    )

    assert report["status"] == "BLOCKED_EXTERNAL"
    assert report["model_external_error_count"] == 1
    assert report["case_pass_rate"] == 0.0


def test_report_counts_verified_data_gap_as_covered():
    retrieved = {
        "case_id": "retrieved",
        "domain": "ho_tich_chung_thuc",
        "legal_as_of": "2026-07-23",
        "classification": "FOUND_AND_RETRIEVED",
        "reason_code": "live_result_without_expected_source_contract",
        "uncertain": False,
        "timing_ms": 40,
        "cache_hit": False,
        "wrong_field_count": 0,
        "expired_selection_count": 0,
        "selected_sources": [_row()],
        "top5": [_row()],
        "top10": [_row()],
        "expected_sources": [],
    }
    verified_gap = {
        "case_id": "verified-gap",
        "domain": "ho_tich_chung_thuc",
        "legal_as_of": "2026-07-23",
        "classification": "VERIFIED_DATA_GAP",
        "reason_code": "verified_absent",
        "uncertain": False,
        "timing_ms": 20,
        "cache_hit": False,
        "wrong_field_count": 0,
        "expired_selection_count": 0,
        "selected_sources": [],
        "top5": [],
        "top10": [],
        "expected_sources": [],
    }
    report = build_report(
        dataset="dataset.jsonl",
        legal_as_of="2026-07-23",
        retrieval_url="http://127.0.0.1:8765",
        cases=[retrieved, verified_gap],
        results=[retrieved, verified_gap],
        cache_stats={"hits": 0, "misses": 2},
        sample_reasons={},
    )
    assert report["coverage"] == 1.0


@pytest.mark.asyncio
async def test_retrieval_cache_reuses_normalized_question_and_date(monkeypatch, tmp_path):
    calls = 0

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"issues": [{"issue_id": "issue-1", "results": [_row()]}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(
        "scripts.evaluate_retrieval_dataset.httpx.AsyncClient",
        lambda *args, **kwargs: Client(),
    )
    cache = tmp_path / "retrieval-cache.json"
    first = [{
        "case_id": "first",
        "question": "Đăng ký  khai sinh?",
        "legal_as_of": "2026-07-23",
        "domain": "ho_tich_chung_thuc",
        "intent": "documents",
        "expected_sources": [],
    }]
    second = [{**first[0], "case_id": "second", "question": "  ĐĂNG KÝ KHAI SINH?  "}]

    first_result, first_stats = await evaluate_retrieval(
        cases=first,
        expected_by_case={},
        retrieval_url="http://retrieval.invalid",
        cache_path=cache,
    )
    second_result, second_stats = await evaluate_retrieval(
        cases=second,
        expected_by_case={},
        retrieval_url="http://retrieval.invalid",
        cache_path=cache,
    )

    assert calls == 1
    assert first_stats == {"hits": 0, "misses": 1, "errors": 0}
    assert second_stats == {"hits": 1, "misses": 0, "errors": 0}
    assert first_result[0]["classification"] == "FOUND_AND_RETRIEVED"
    assert second_result[0]["cache_hit"] is True


@pytest.mark.asyncio
async def test_retrieval_latency_excludes_client_semaphore_queue(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"issues": [{"issue_id": "issue-1", "results": [_row()]}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            await asyncio.sleep(0.02)
            return Response()

    monkeypatch.setattr(
        "scripts.evaluate_retrieval_dataset.httpx.AsyncClient",
        lambda *args, **kwargs: Client(),
    )
    cases = [
        {
            "case_id": f"case-{index}",
            "question": f"question {index}",
            "legal_as_of": "2026-07-27",
            "domain": "ho_tich_chung_thuc",
            "intent": "documents",
            "expected_sources": [],
        }
        for index in range(3)
    ]

    wall_started = time.perf_counter()
    results, _ = await evaluate_retrieval(
        cases=cases,
        expected_by_case={},
        retrieval_url="http://retrieval.local",
        concurrency=1,
    )
    wall_ms = (time.perf_counter() - wall_started) * 1000

    # Each case performs primary + one support call. On Windows a 20 ms
    # asyncio sleep commonly rounds to roughly 30 ms, so an absolute 55 ms
    # threshold is not portable. Queue leakage would make the three measured
    # values grow toward the complete serial wall time. The wall-time relation
    # tests the intended invariant without depending on timer granularity.
    timings = [float(row["timing_ms"]) for row in results]
    assert max(timings) < 90
    assert wall_ms > max(timings) * 2


def test_dataset_benchmark_uses_v2_explicit_only_issue_planning(tmp_path):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "questions": [
                    {
                        "case_id": "one-issue",
                        "question": (
                            "Tôi chưa nêu thủ tục, cơ quan và ngày nhận quyết định; "
                            "cần bổ sung gì?"
                        ),
                        "domain": "dat_dai_xay_dung",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cases = _load_dataset(dataset, "2026-08-11")

    assert len(cases[0]["issues"]) == 1
    assert cases[0]["issues"][0]["query"] == cases[0]["question"]


def test_dataset_loader_accepts_plain_regression_cases_object(tmp_path):
    dataset = tmp_path / "regression.json"
    dataset.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "web-001",
                        "question": "Đăng ký kết hôn cần giấy tờ gì?",
                        "domain": "Hộ tịch/chứng thực",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cases = _load_dataset(dataset, "2026-08-11")

    assert cases[0]["case_id"] == "web-001"
    assert cases[0]["question"] == "Đăng ký kết hôn cần giấy tờ gì?"
