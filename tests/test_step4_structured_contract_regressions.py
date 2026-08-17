import pytest

from api.legal_claim_validation import validate_structured_claims
from api.legal_section_grounding import LegalIssue
from api.legal_structured_answer import (
    StructuredClaim,
    StructuredIssueAnswer,
    StructuredLegalAnswer,
    build_issue_coverage_matrix,
    parse_structured_answer,
    render_structured_answer,
    safe_extractive_fallback,
)


def _issue(*, intent="deadline", expected_form=None):
    return LegalIssue(
        request_id="request-1",
        issue_id="issue-1",
        title="Thời hạn",
        query_text="đăng ký kết hôn thời hạn bao lâu",
        intent=intent,
        domain="civil_status",
        split_confidence="high",
        expected_form=expected_form,
    )


def _evidence(**overrides):
    return {
        "evidence-1": {
            "chunk_id": 1,
            "request_id": "request-1",
            "issue_id": "issue-1",
            "content": "Trong 05 ngày làm việc, lệ phí là 20.000 đồng.",
            "document_title": "Nguồn chính thức",
            "law_number": "123/2015/NĐ-CP",
            "article_number": "18",
            "effective_status": "active",
            "official": True,
            "scope": "central",
            "domain": "civil_status",
            "source_url": "https://example.gov.vn/123",
            **overrides,
        }
    }


def _claim(**overrides):
    return {
        "issue_id": "issue-1",
        "claim_type": "deadline",
        "claim_text": "Thời hạn là 05 ngày làm việc.",
        "evidence_id": "evidence-1",
        "support_quote": "Trong 05 ngày làm việc",
        **overrides,
    }


def _reasons(result):
    return [item.reason for item in result.rejected]


def test_quote_not_in_evidence_is_rejected():
    result = validate_structured_claims(
        request_id="request-1",
        claims=[_claim(support_quote="Trong 07 ngày làm việc")],
        evidence_by_id=_evidence(),
    )
    assert _reasons(result) == ["support_quote_not_in_evidence"]


@pytest.mark.parametrize(
    "claim_overrides,evidence_overrides",
    [
        ({"evidence_id": "missing"}, {}),
        ({"issue_id": "other-issue"}, {}),
    ],
)
def test_evidence_bound_to_request_and_issue(claim_overrides, evidence_overrides):
    result = validate_structured_claims(
        request_id="request-1",
        claims=[_claim(**claim_overrides)],
        evidence_by_id=_evidence(**evidence_overrides),
    )
    assert _reasons(result) == (
        ["evidence_not_in_request"]
        if claim_overrides.get("evidence_id") == "missing"
        else ["request_or_issue_mismatch"]
    )


def test_wrong_article_or_clause_is_rejected():
    result = validate_structured_claims(
        request_id="request-1",
        claims=[_claim(claim_text="Theo khoản 2 Điều 99, thời hạn là 05 ngày làm việc.")],
        evidence_by_id=_evidence(),
    )
    assert _reasons(result) == ["legal_reference_not_supported"]


@pytest.mark.parametrize(
    "claim_type,claim_text,support_quote",
    [
        ("fee", "Lệ phí là 50.000 đồng.", "lệ phí là 20.000 đồng"),
        ("deadline", "Thời hạn là 07 ngày làm việc.", "Trong 05 ngày làm việc"),
        ("fee", "Lệ phí là 20.000 đồng.", "Lệ phí được thu theo quy định."),
    ],
)
def test_amount_deadline_and_fee_must_be_in_quote(claim_type, claim_text, support_quote):
    result = validate_structured_claims(
        request_id="request-1",
        claims=[_claim(claim_type=claim_type, claim_text=claim_text, support_quote=support_quote)],
        evidence_by_id=_evidence(content=support_quote),
    )
    assert _reasons(result) == ["claim_value_not_supported"]


def test_form_with_wrong_procedure_id_is_not_rendered():
    issue = _issue(
        intent="form",
        expected_form={"procedure_id": "birth-registration", "form_code": "01"},
    )
    evidence = _evidence(
        content="Sử dụng Mẫu 01/ĐK.",
        procedure_id="land-change",
        form_code="09/ĐK",
    )
    output = StructuredLegalAnswer(
        issues=[
            StructuredIssueAnswer(
                issue_id="issue-1",
                claims=[
                    StructuredClaim(
                        claim_type="form",
                        claim_text="Sử dụng Mẫu 01/ĐK.",
                        evidence_id="evidence-1",
                        support_quote="Sử dụng Mẫu 01/ĐK.",
                    )
                ],
            )
        ]
    )
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-1": ["form"]},
    )
    sections, _aggregate, trace = render_structured_answer(
        request_id="request-1",
        issues=[issue],
        output=output,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )
    assert sections[0].status == "insufficiently_evidenced"
    assert trace["issues"][0]["rejection_reasons"] == ["form_procedure_mismatch"]
    assert trace["displayed_legal_claim_count"] == 0


def test_invalid_model_json_is_fail_closed():
    with pytest.raises(ValueError, match="model_output_is_not_valid_structured_json"):
        parse_structured_answer("{not-json")


def test_model_timeout_fallback_is_source_view_without_new_claims():
    sections, aggregate = safe_extractive_fallback(
        request_id="request-1",
        issues=[_issue()],
        evidence_by_id=_evidence(
            content="Trong 05 ngày làm việc [legal:chunk-1].",
        ),
    )
    assert sections[0].status == "insufficiently_evidenced"
    assert sections[0].answer is None
    assert sections[0].citations == []
    assert "chưa xác minh đủ từng ý" in (sections[0].limitation or "").casefold()
    assert "05 ngày" not in aggregate["answer"]
    assert aggregate["grounding_status"] == "insufficient_evidence"


def test_internal_citation_marker_is_rejected_and_never_rendered():
    issue = _issue()
    evidence = _evidence(
        content="Trong 05 ngày làm việc [legal:chunk-1].",
    )
    output = StructuredLegalAnswer(
        issues=[
            StructuredIssueAnswer(
                issue_id="issue-1",
                claims=[
                    StructuredClaim(
                        claim_type="deadline",
                        claim_text="Thời hạn là 05 ngày làm việc [legal:chunk-1].",
                        evidence_id="evidence-1",
                        support_quote="Trong 05 ngày làm việc [legal:chunk-1].",
                    )
                ],
                guidance="Không hiển thị [chunk_id:1].",
            )
        ]
    )
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-1": ["deadline"]},
    )
    sections, _aggregate, trace = render_structured_answer(
        request_id="request-1",
        issues=[issue],
        output=output,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )
    rendered = " ".join(
        [sections[0].answer or "", sections[0].guidance or "", sections[0].clarifying_question or ""]
    )
    assert "[legal:" not in rendered.casefold()
    assert "[chunk_id" not in rendered.casefold()
    assert trace["issues"][0]["rejection_reasons"] == ["internal_citation_marker"]
