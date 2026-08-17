from __future__ import annotations

import asyncio

import httpx

from api.legal_provider_fallback import (
    SOURCE_VIEW_ONLY,
    VERIFIED_SOURCE_CONDENSED,
    choose_verified_fallback_mode,
    classify_structured_provider_error,
)
from api.legal_section_grounding import LegalIssue
from api.legal_structured_answer import safe_extractive_fallback


def _quality_trace(*, passed: bool = True) -> dict:
    return {
        "quality_gate": {"pass": passed},
        "coverage_ratio": 1.0 if passed else 0.5,
        "claim_grounding_ratio": 1.0,
        "accepted_claim_count": 1 if passed else 0,
        "rejected_claim_count": 0,
        "displayed_legal_claim_count": 1 if passed else 0,
        "displayed_claims_with_valid_evidence": 1 if passed else 0,
        "issues": [
            {
                "accepted_claim_count": 1 if passed else 0,
                "rejected_claim_count": 0,
            }
        ],
    }


def test_provider_failures_keep_distinct_public_reason_codes() -> None:
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://provider.example/v1/chat"),
    )
    rate_limit = httpx.HTTPStatusError(
        "rate limited",
        request=response.request,
        response=response,
    )

    assert classify_structured_provider_error(rate_limit) == "provider_rate_limit"
    assert classify_structured_provider_error(asyncio.TimeoutError()) == "provider_timeout"
    assert (
        classify_structured_provider_error(
            ValueError("model_output_is_not_valid_structured_json")
        )
        == "invalid_output"
    )


def test_generic_verified_coverage_can_use_deterministic_condensed_mode() -> None:
    mode = choose_verified_fallback_mode(
        question="Thời hạn giải quyết là bao lâu?",
        evidence_by_id={
            "evidence-1": {
                "content": "Thời hạn giải quyết là 03 ngày làm việc.",
                "source_url": "https://example.gov.vn/source",
            }
        },
        quality_trace=_quality_trace(),
    )

    assert mode == VERIFIED_SOURCE_CONDENSED


def test_partial_facet_coverage_keeps_grounded_claims_in_condensed_mode() -> None:
    trace = _quality_trace()
    trace["coverage_ratio"] = 0.5
    trace["issues"] = [
        {"accepted_claim_count": 1, "rejected_claim_count": 0},
        {"accepted_claim_count": 0, "rejected_claim_count": 0},
    ]

    assert (
        choose_verified_fallback_mode(
            question="Hồ sơ và biểu mẫu gồm những gì?",
            evidence_by_id={"evidence-1": {"content": "Hồ sơ đã được xác minh."}},
            quality_trace=trace,
        )
        == VERIFIED_SOURCE_CONDENSED
    )


def test_rejected_claim_still_forces_source_view_mode() -> None:
    trace = _quality_trace()
    trace["issues"][0]["rejected_claim_count"] = 1

    assert (
        choose_verified_fallback_mode(
            question="Hồ sơ gồm những gì?",
            evidence_by_id={"evidence-1": {"content": "Hồ sơ đã được xác minh."}},
            quality_trace=trace,
        )
        == SOURCE_VIEW_ONLY
    )


def test_exact_article_requires_complete_parent_or_packet_before_condensing() -> None:
    common = {
        "question": "Điều 73 của văn bản 60/2014/QH13 quy định gì?",
        "quality_trace": _quality_trace(),
    }

    unsafe = choose_verified_fallback_mode(
        **common,
        evidence_by_id={
            "evidence-1": {
                "content": "Khoản 2...",
                "parent_context_truncated": True,
                "source_url": "https://vbpl.vn/source",
            }
        },
    )
    safe = choose_verified_fallback_mode(
        **common,
        evidence_by_id={
            "evidence-1": {
                "content": "Toàn bộ Điều 73...",
                "exact_article_packet_status": "complete",
                "source_url": "https://vbpl.vn/source",
            }
        },
    )

    assert unsafe == SOURCE_VIEW_ONLY
    assert safe == VERIFIED_SOURCE_CONDENSED


def test_failed_quality_gate_never_claims_a_safe_condensed_answer() -> None:
    assert (
        choose_verified_fallback_mode(
            question="Nội dung gì?",
            evidence_by_id={"evidence-1": {"content": "Một đoạn nguồn."}},
            quality_trace=_quality_trace(passed=False),
        )
        == SOURCE_VIEW_ONLY
    )


def test_source_view_fallback_never_presents_excerpt_as_verified_answer() -> None:
    issue = LegalIssue(
        request_id="request-source-view",
        issue_id="issue-1",
        title="Nội dung Điều 73",
        query_text="Điều 73 quy định gì?",
        intent="rule",
        domain="land",
        split_confidence="high",
    )
    sections, _aggregate = safe_extractive_fallback(
        request_id=issue.request_id,
        issues=[issue],
        evidence_by_id={
            "evidence-1": {
                "source_id": "source-1",
                "request_id": issue.request_id,
                "issue_id": issue.issue_id,
                "domain": "land",
                "content": "Khoản 1 của Điều 73.",
                "document_title": "Văn bản kiểm thử",
                "law_number": "60/2014/QH13",
                "article_number": "73",
                "effective_status": "active",
                "official": True,
                "scope": "central",
                "source_url": "https://vbpl.vn/van-ban-goc",
            }
        },
    )

    assert sections[0].answer is None
    assert sections[0].citations == []
    assert "thẻ nguồn" in str(sections[0].limitation)
