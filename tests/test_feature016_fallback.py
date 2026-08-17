from __future__ import annotations

from api.legal_provider_fallback import (
    SOURCE_VIEW_ONLY,
    VERIFIED_SOURCE_CONDENSED,
    choose_verified_fallback_mode,
)
from api.legal_section_grounding import LegalIssue
from api.legal_structured_answer import safe_extractive_fallback


def _evidence() -> dict:
    return {
        "evidence-1": {
            "source_id": "source-1",
            "request_id": "request-1",
            "issue_id": "issue-1",
            "domain": "cu_tru_an_ninh",
            "content": "Hồ sơ gồm tờ khai thay đổi thông tin cư trú.",
            "document_title": "Luật mẫu",
            "law_number": "68/2020/QH14",
            "article_number": "21",
            "effective_status": "active",
            "official": True,
            "scope": "central",
            "source_url": "https://vbpl.vn/official",
        }
    }


def _verified_trace() -> dict:
    return {
        "quality_gate": {"pass": True},
        "coverage_ratio": 1.0,
        "claim_grounding_ratio": 1.0,
        "accepted_claim_count": 1,
        "rejected_claim_count": 0,
        "displayed_legal_claim_count": 1,
        "displayed_claims_with_valid_evidence": 1,
        "issues": [
            {
                "issue_id": "issue-1",
                "accepted_claim_count": 1,
                "rejected_claim_count": 0,
            }
        ],
    }


def test_superficial_quality_numbers_without_claim_bindings_are_source_only():
    mode = choose_verified_fallback_mode(
        question="Hồ sơ gồm gì?",
        evidence_by_id=_evidence(),
        quality_trace={
            "quality_gate": {"pass": True},
            "coverage_ratio": 1.0,
            "claim_grounding_ratio": 1.0,
        },
    )
    assert mode == SOURCE_VIEW_ONLY


def test_condensed_mode_requires_every_displayed_claim_to_be_bound():
    assert (
        choose_verified_fallback_mode(
            question="Hồ sơ gồm gì?",
            evidence_by_id=_evidence(),
            quality_trace=_verified_trace(),
        )
        == VERIFIED_SOURCE_CONDENSED
    )
    trace = _verified_trace()
    trace["displayed_claims_with_valid_evidence"] = 0
    assert (
        choose_verified_fallback_mode(
            question="Hồ sơ gồm gì?",
            evidence_by_id=_evidence(),
            quality_trace=trace,
        )
        == SOURCE_VIEW_ONLY
    )


def test_source_view_only_never_turns_first_source_excerpt_into_legal_answer():
    issue = LegalIssue(
        request_id="request-1",
        issue_id="issue-1",
        title="Hồ sơ",
        query_text="Hồ sơ đăng ký thường trú gồm gì?",
        intent="documents",
        domain="cu_tru_an_ninh",
    )
    sections, aggregate = safe_extractive_fallback(
        request_id="request-1",
        issues=[issue],
        evidence_by_id=_evidence(),
    )
    assert sections[0].status == "insufficiently_evidenced"
    assert sections[0].answer is None
    assert sections[0].citations == []
    assert "chưa xác minh đủ từng ý" in (sections[0].limitation or "").casefold()
    assert "tờ khai thay đổi" not in aggregate["answer"].casefold()
    assert aggregate["grounding_status"] == "insufficient_evidence"
