import asyncio
import json
import time

import pytest

from api.legal_section_grounding import (
    LegalIssue,
    plan_legal_issues,
    validate_answer_section,
)
from api.legal_structured_answer import (
    AsyncModelCache,
    build_extractive_structured_answer,
    build_issue_coverage_matrix,
    build_compact_evidence_context,
    build_structured_answer_prompt,
    derive_required_facets_by_issue,
    enforce_explicit_facet_claims,
    ensure_required_facet_issues,
    evaluate_structured_quality_gate,
    parse_structured_answer,
    render_structured_answer,
    safe_extractive_fallback,
    await_with_hard_deadline,
    invoke_blocking_model_with_deadline,
    is_hard_legal_request,
    model_invocation_budget_seconds,
    remaining_generation_budget_seconds,
    structured_model_options,
    structured_model_invocation_options,
    structured_context_max_chars,
    structured_generation_timeout_seconds,
    structured_total_timeout_seconds,
    structured_retrieval_timeout_seconds,
    optimized_profile_enabled,
    supplement_rule_source_diversity,
)


def test_required_facet_issues_fill_splitter_gaps_without_model_planning():
    planned = [
        LegalIssue(
            issue_id="issue-1",
            title="Hồ sơ",
            query_text="Cần hồ sơ gì?",
            intent="documents",
            domain="civil_status",
            split_confidence="high",
        )
    ]

    issues = ensure_required_facet_issues(
        question="Nộp ở đâu, hồ sơ, thời hạn và lệ phí?",
        issues=planned,
        required_sections=[
            "submission_place",
            "documents",
            "processing_time",
            "fee",
            "official_forms",
        ],
    )

    assert [item.intent for item in issues] == [
        "authority",
        "documents",
        "deadline",
        "fee",
        "form",
    ]
    assert [item.issue_id for item in issues] == [
        "issue-1",
        "issue-2",
        "issue-3",
        "issue-4",
        "issue-5",
    ]
    assert issues[1].query_text == "Cần hồ sơ gì?"


def test_rule_only_question_keeps_one_retrieval_issue():
    question = "Điều 18 của văn bản 60/2014/QH13 quy định nội dung gì?"
    planned = plan_legal_issues(question, max_issues=8)

    issues = ensure_required_facet_issues(
        question=question,
        issues=planned,
        required_sections=["applicable_rule", "legal_basis_links"],
        max_issues=8,
    )

    assert len(issues) == 1
    assert issues[0].intent == "rule"
    assert "60/2014/QH13" in issues[0].query_text


def test_exact_article_packet_bypasses_per_hit_excerpt_cap() -> None:
    issue = LegalIssue(
        request_id="request-exact-article",
        issue_id="issue-1",
        title="Nội dung Điều 73",
        query_text="Điều 73 của văn bản 60/2014/QH13 quy định gì?",
        text="Điều 73 của văn bản 60/2014/QH13 quy định gì?",
        intent="rule",
        domain="dat_dai_xay_dung",
        split_confidence="high",
    )
    full_article = "\n".join(
        f"{index}. Nội dung khoản {index} có điều kiện và hệ quả pháp lý riêng."
        for index in range(1, 35)
    )
    rows = [
        {
            "request_id": issue.request_id,
            "issue_id": issue.issue_id,
            "chunk_id": 700 + index,
            "article_id": 73,
            "document_id": 60,
            "law_number": "60/2014/QH13",
            "article_number": "73",
            "content": f"{index}. Nội dung khoản {index} có điều kiện và hệ quả pháp lý riêng.",
            "exact_article_packet_ref": "document:60:article:73",
            "exact_article_packet_status": "complete",
            "exact_article_loaded_chunk_count": 34,
            "exact_article_expected_chunk_count": 34,
            "exact_article_assembled_content": full_article if index == 1 else None,
            "parent_context": full_article if index == 1 else None,
            "parent_context_primary": index == 1,
            "parent_context_truncated": False,
        }
        for index in range(1, 35)
    ]

    context, evidence = build_compact_evidence_context(
        issues=[issue],
        evidence_rows=rows,
        max_chars=8000,
    )

    assert full_article in context
    assert len(evidence) == 1
    assert next(iter(evidence.values()))["exact_article_context_complete"] is True


def test_explicit_facet_claims_replace_model_selected_legacy_claim() -> None:
    issue = LegalIssue(
        request_id="request-explicit",
        issue_id="issue-authority",
        title="Nơi nộp đăng ký khai sinh",
        query_text="Nộp hồ sơ đăng ký khai sinh ở đâu?",
        text="Nộp hồ sơ đăng ký khai sinh ở đâu?",
        intent="authority",
        domain="ho_tich_chung_thuc",
        split_confidence="high",
    )
    evidence = {
        "evidence-reviewed": {
            "request_id": "request-explicit",
            "issue_id": "issue-authority",
            "content": "Ủy ban nhân dân cấp xã thực hiện đăng ký khai sinh.",
            "clean_content": "Ủy ban nhân dân cấp xã thực hiện đăng ký khai sinh.",
            "supported_facets": ["authority"],
            "law_number": "60/2014/QH13",
            "article_number": "16",
        },
        "evidence-legacy": {
            "request_id": "request-explicit",
            "issue_id": "issue-authority",
            "content": "Người đi đăng ký khai sinh nộp tờ khai và Giấy chứng sinh.",
            "law_number": "60/2014/QH13",
            "article_number": "16",
        },
    }
    coverage = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-authority": ["authority"]},
    )
    generated = parse_structured_answer(
        json.dumps(
            {
                "issues": [
                    {
                        "issue_id": "issue-authority",
                        "claims": [
                            {
                                "claim_text": "Người đi đăng ký khai sinh nộp tờ khai.",
                                "claim_type": "authority",
                                "evidence_id": "evidence-legacy",
                                "support_quote": "Người đi đăng ký khai sinh nộp tờ khai",
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
    )

    enforced = enforce_explicit_facet_claims(
        issues=[issue],
        output=generated,
        evidence_by_id=evidence,
        coverage_matrix=coverage,
    )

    assert [claim.evidence_id for claim in enforced.issues[0].claims] == [
        "evidence-reviewed"
    ]
    assert "Ủy ban nhân dân cấp xã" in enforced.issues[0].claims[0].claim_text


def test_required_facet_issues_drop_unrequested_location_derived_authority():
    planned = [
        LegalIssue(
            issue_id="issue-1",
            title="Hồ sơ",
            query_text="Cần giấy tờ gì để đăng ký khai sinh?",
            intent="documents",
            domain="civil_status",
            split_confidence="high",
        ),
        LegalIssue(
            issue_id="issue-2",
            title="Thẩm quyền",
            query_text="Đăng ký khai sinh tại UBND phường",
            intent="authority",
            domain="civil_status",
            split_confidence="high",
        ),
    ]

    issues = ensure_required_facet_issues(
        question=(
            "Tôi cần chuẩn bị giấy tờ gì để đăng ký khai sinh cho con "
            "tại UBND phường?"
        ),
        issues=planned,
        required_sections=["conclusion", "documents"],
        max_issues=8,
    )

    assert [issue.intent for issue in issues] == ["documents"]


def test_first_instance_complaint_authority_query_uses_focused_synonym():
    planned = [
        LegalIssue(
            issue_id="issue-1",
            title="Thẩm quyền",
            query_text="Ai giải quyết?",
            intent="authority",
            domain="khieu_nai_to_cao_xu_phat",
            split_confidence="high",
            subject="khiếu nại lần đầu",
        )
    ]

    issues = ensure_required_facet_issues(
        question=(
            "Ai có thẩm quyền giải quyết khiếu nại lần đầu đối với "
            "quyết định hành chính?"
        ),
        issues=planned,
        required_sections=["conclusion", "submission_place"],
    )

    assert all(
        "người đã ra quyết định hành chính" in issue.query_text
        for issue in issues
    )


def test_timeout_fallback_does_not_promote_source_excerpt_without_claim_binding():
    sections, aggregate = safe_extractive_fallback(
        request_id="request-1",
        issues=[_issue()],
        evidence_by_id={"evidence-1": _source()},
    )

    assert sections[0].status == "insufficiently_evidenced"
    assert sections[0].citations == []
    assert sections[0].answer is None
    assert aggregate["grounding_status"] == "insufficient_evidence"


def test_broad_meritorious_support_fallback_asks_for_benefit_subtype():
    issue = LegalIssue(
        request_id="request-meritorious",
        issue_id="issue-1",
        title="Nội dung cần làm rõ",
        query_text="Người có công xin trợ cấp cần hồ sơ gì?",
        intent="documents",
        domain="an_sinh_y_te_giao_duc",
        split_confidence="low",
    )

    sections, aggregate = safe_extractive_fallback(
        request_id="request-meritorious",
        issues=[issue],
        evidence_by_id={},
    )

    assert sections[0].status == "insufficiently_evidenced"
    assert "loại trợ cấp" in (sections[0].clarifying_question or "").casefold()
    assert "nội dung cần làm rõ" in (sections[0].limitation or "").casefold()
    assert "đã tìm thấy nguồn nhưng chưa thể tổng hợp" not in aggregate["answer"].casefold()
    assert aggregate["grounding_status"] == "insufficient_evidence"


def test_extractive_fallback_skips_relevant_sentence_with_mismatched_legal_reference():
    issue = LegalIssue(
        request_id="request-1",
        issue_id="issue-1",
        title="Hồ sơ",
        query_text="Hồ sơ đăng ký khai sinh gồm những gì?",
        intent="documents",
        domain="civil_status",
        split_confidence="high",
    )
    evidence = {
        "evidence-1": {
            **_source(),
            "article_number": "4",
            "content": (
                "Hồ sơ được xác định theo Khoản 1 Điều 16 của Luật Hộ tịch. "
                "Hồ sơ gồm Tờ khai đăng ký khai sinh và Giấy chứng sinh."
            ),
        }
    }
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-1": ["documents"]},
    )

    output = build_extractive_structured_answer(
        issues=[issue],
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )
    sections, _aggregate, trace = render_structured_answer(
        request_id="request-1",
        issues=[issue],
        output=output,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    assert sections[0].status == "sufficiently_evidenced"
    assert trace["quality_gate"]["pass"] is True
    assert "Tờ khai đăng ký khai sinh" in (sections[0].answer or "")


def test_exact_packet_citation_uses_the_same_capsule_as_claim_validation():
    issue = LegalIssue(
        request_id="request-exact-proof",
        issue_id="issue-1",
        title="Nội dung điều",
        query_text="Điều 18a của Luật 88/2025/QH15 quy định gì?",
        intent="rule",
        domain="administrative",
        split_confidence="low",
    )
    quote = "Việc thu thập dữ liệu phải bảo đảm an toàn."
    evidence = {
        "evidence-1": {
            **_source(),
            "request_id": "request-exact-proof",
            "issue_id": "issue-1",
            "law_number": "88/2025/QH15",
            "article_number": "18a",
            "content": "",
            "evidence_capsule": f"Điều 18a\n{quote}",
            "supported_facets": ["rule"],
        }
    }
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-1": ["rule"]},
    )
    output = parse_structured_answer(
        json.dumps(
            {
                "issues": [
                    {
                        "issue_id": "issue-1",
                        "claims": [
                            {
                                "claim_text": quote,
                                "claim_type": "rule",
                                "evidence_id": "evidence-1",
                                "support_quote": quote,
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
    )

    sections, _aggregate, _trace = render_structured_answer(
        request_id="request-exact-proof",
        issues=[issue],
        output=output,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    assert sections[0].citations[0].verification_status == "verified"
    assert sections[0].citations[0].verification_level == "content_quote"
    assert sections[0].citations[0].proof is not None


def test_extractive_fallback_prefers_subject_matched_sentence_over_generic_facet_marker():
    issue = LegalIssue(
        request_id="request-birth",
        issue_id="issue-documents",
        title="Hồ sơ",
        query_text="Đăng ký khai sinh cho con cần hồ sơ và giấy tờ gì?",
        intent="documents",
        domain="civil_status",
        split_confidence="high",
    )
    evidence = {
        "evidence-birth": {
            **_source(),
            "request_id": "request-birth",
            "issue_id": "issue-documents",
            "article_number": "16",
            "content": (
                "Ngay trong ngày tiếp nhận yêu cầu, Phòng Tư pháp kiểm tra hồ sơ. "
                "Người đi đăng ký khai sinh nộp tờ khai theo mẫu quy định và "
                "giấy chứng sinh cho cơ quan đăng ký hộ tịch."
            ),
        }
    }
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-documents": ["documents"]},
    )

    output = build_extractive_structured_answer(
        issues=[issue],
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    claim = output.issues[0].claims[0]
    assert "đăng ký khai sinh" in claim.claim_text.casefold()
    assert "Phòng Tư pháp kiểm tra hồ sơ" not in claim.claim_text


def test_extractive_fallback_rejects_mid_sentence_and_cutoff_fragments():
    issue = LegalIssue(
        request_id="request-fragment",
        issue_id="issue-condition",
        title="Điều kiện",
        query_text="Điều kiện cấp Giấy chứng nhận lần đầu",
        intent="condition",
        domain="land",
        split_confidence="high",
    )
    evidence = {
        "fragment": {
            **_source(),
            "request_id": issue.request_id,
            "issue_id": issue.issue_id,
            "domain": "land",
            "content": (
                "dụng và được xem xét cấp Giấy chứng nhận quyền sử dụng đất.\n"
                "Trường hợp người đang sử dụng đất đáp ứng các điều kiện theo quy định"
            ),
        }
    }
    matrix = {
        issue.issue_id: [{
            "facet": "condition",
            "evidence_available": True,
            "evidence_ids": ["fragment"],
        }]
    }

    output = build_extractive_structured_answer(
        issues=[issue],
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    assert output.issues[0].claims == []


def test_extractive_fallback_never_exposes_capsule_presentation_labels():
    issue = LegalIssue(
        request_id="request-residence",
        issue_id="issue-authority",
        title="Nơi nộp",
        query_text="Đăng ký tạm trú nộp tại cơ quan nào?",
        intent="authority",
        domain="administrative",
        split_confidence="high",
    )
    evidence = {
        "evidence-residence": {
            **_source(),
            "request_id": "request-residence",
            "issue_id": "issue-authority",
            "domain": "administrative",
            "document_title": "Luật Cư trú",
            "law_number": "68/2020/QH14",
            "article_number": "27",
            "content": (
                "Người đăng ký tạm trú nộp hồ sơ đến Công an cấp xã nơi "
                "mình dự kiến tạm trú."
            ),
            "clean_matched_child_content": (
                "Người đăng ký tạm trú nộp hồ sơ đến Công an cấp xã nơi "
                "mình dự kiến tạm trú."
            ),
            "clean_parent_context": "1. Người đăng ký tạm trú nộp hồ sơ đến Công an cấp xã.",
            "evidence_capsule": (
                "Cấu trúc: Điều 27 > Khoản 1\n\n"
                "Người đăng ký tạm trú nộp hồ sơ đến Công an cấp xã nơi mình dự kiến tạm trú.\n\n"
                "Ngữ cảnh chi phối:\n1. Người đăng ký tạm trú nộp hồ sơ đến Công an cấp xã."
            ),
        }
    }
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-authority": ["authority"]},
    )

    output = build_extractive_structured_answer(
        issues=[issue], evidence_by_id=evidence, coverage_matrix=matrix
    )

    assert output.issues[0].claims
    assert all(
        "ngữ cảnh chi phối" not in claim.claim_text.casefold()
        and "cấu trúc:" not in claim.claim_text.casefold()
        for claim in output.issues[0].claims
    )


def test_extractive_authority_prefers_named_decision_maker_over_generic_agency():
    issue = LegalIssue(
        request_id="request-marriage",
        issue_id="issue-authority",
        title="Thẩm quyền",
        query_text="Đăng ký kết hôn có yếu tố nước ngoài do cơ quan nào giải quyết?",
        intent="authority",
        domain="civil_status",
        split_confidence="high",
    )
    evidence = {
        "evidence-marriage": {
            **_source(),
            "request_id": "request-marriage",
            "issue_id": "issue-authority",
            "article_number": "38",
            "content": (
                "Hai bên nộp tờ khai cho cơ quan đăng ký hộ tịch. "
                "Phòng Tư pháp báo cáo Chủ tịch Ủy ban nhân dân cấp huyện giải quyết."
            ),
        }
    }
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-authority": ["authority"]},
    )

    output = build_extractive_structured_answer(
        issues=[issue],
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    assert "Ủy ban nhân dân cấp huyện" in output.issues[0].claims[0].claim_text


def test_extractive_fallback_preserves_distinct_direct_legal_provisions():
    issue = LegalIssue(
        request_id="request-comparison",
        issue_id="issue-rule",
        title="Căn cứ",
        query_text="Phân biệt thẩm quyền theo Điều 13 và Điều 35 Luật Hộ tịch",
        intent="rule",
        domain="civil_status",
        split_confidence="high",
    )
    evidence = {
        "article-13": {
            **_source(),
            "request_id": issue.request_id,
            "issue_id": issue.issue_id,
            "article_number": "13",
            "content": (
                "Ủy ban nhân dân cấp xã có thẩm quyền đăng ký hộ tịch "
                "theo quy định."
            ),
        },
        "article-35": {
            **_source(),
            "request_id": issue.request_id,
            "issue_id": issue.issue_id,
            "article_number": "35",
            "content": (
                "Ủy ban nhân dân cấp huyện đăng ký khai sinh có yếu tố "
                "nước ngoài."
            ),
        },
    }
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={issue.issue_id: ["rule"]},
    )

    output = build_extractive_structured_answer(
        issues=[issue],
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    assert {claim.evidence_id for claim in output.issues[0].claims} == {
        "article-13",
        "article-35",
    }


def test_successful_model_output_is_supplemented_with_validated_rule_source_diversity():
    issue = LegalIssue(
        request_id="request-building",
        issue_id="issue-rule",
        title="Căn cứ pháp lý",
        query_text="Cấp giấy phép xây dựng nhà ở riêng lẻ áp dụng căn cứ nào?",
        intent="rule",
        domain="dat_dai_xay_dung",
        split_confidence="high",
    )
    evidence = {
        "building-law": {
            **_source(),
            "request_id": issue.request_id,
            "issue_id": issue.issue_id,
            "document_id": 34116,
            "law_number": "50/2014/QH13",
            "article_number": "95",
            "content": (
                "Hồ sơ đề nghị cấp giấy phép xây dựng mới đối với nhà ở riêng lẻ "
                "gồm đơn đề nghị và bản vẽ thiết kế."
            ),
        },
        "building-decree": {
            **_source(),
            "request_id": issue.request_id,
            "issue_id": issue.issue_id,
            "document_id": 119956,
            "law_number": "175/2024/NĐ-CP",
            "article_number": "51",
            "content": (
                "Thủ tục cấp giấy phép xây dựng nhà ở riêng lẻ được thực hiện "
                "theo quy định về hồ sơ và trình tự giải quyết."
            ),
        },
    }
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={issue.issue_id: ["rule"]},
    )
    model_output = parse_structured_answer(
        """
        {
          "issues": [{
            "issue_id": "issue-rule",
            "claims": [{
              "claim_text": "Hồ sơ đề nghị cấp giấy phép xây dựng mới đối với nhà ở riêng lẻ gồm đơn đề nghị và bản vẽ thiết kế.",
              "claim_type": "rule",
              "evidence_id": "building-law",
              "support_quote": "Hồ sơ đề nghị cấp giấy phép xây dựng mới đối với nhà ở riêng lẻ gồm đơn đề nghị và bản vẽ thiết kế."
            }],
            "guidance": null,
            "clarifying_question": null
          }]
        }
        """
    )

    supplemented = supplement_rule_source_diversity(
        request_id=issue.request_id,
        issues=[issue],
        output=model_output,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )
    sections, aggregate, trace = render_structured_answer(
        request_id=issue.request_id,
        issues=[issue],
        output=supplemented,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    assert {claim.evidence_id for claim in supplemented.issues[0].claims} == {
        "building-law",
        "building-decree",
    }
    assert {
        citation.law_number for citation in sections[0].citations
    } == {"50/2014/QH13", "175/2024/NĐ-CP"}
    assert trace["claim_grounding_ratio"] == 1.0
    assert aggregate["grounding_status"] == "fully_grounded"


def test_short_rule_answer_is_completed_with_distinct_verified_source_details():
    issue = LegalIssue(
        request_id="request-detailed-rule",
        issue_id="issue-rule-detail",
        title="Kết luận và căn cứ",
        query_text="Hãy giải thích chi tiết từng quy tắc của Điều 18.",
        intent="rule",
        domain="ho_tich_chung_thuc",
        split_confidence="high",
    )
    content = (
        "Hai bên nam, nữ nộp tờ khai đăng ký kết hôn cho cơ quan đăng ký hộ tịch. "
        "Hai bên nam, nữ cùng có mặt khi đăng ký kết hôn. "
        "Công chức tư pháp, hộ tịch ghi việc kết hôn vào Sổ hộ tịch sau khi đủ điều kiện."
    )
    evidence = {
        "marriage-law": {
            **_source(),
            "request_id": issue.request_id,
            "issue_id": issue.issue_id,
            "document_id": 37879,
            "law_number": "60/2014/QH13",
            "article_number": "18",
            "content": content,
        }
    }
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={issue.issue_id: ["rule"]},
    )
    model_output = parse_structured_answer(
        """{
          "issues": [{
            "issue_id": "issue-rule-detail",
            "claims": [{
              "claim_text": "Hai bên nam, nữ nộp tờ khai đăng ký kết hôn cho cơ quan đăng ký hộ tịch.",
              "claim_type": "rule",
              "evidence_id": "marriage-law",
              "support_quote": "Hai bên nam, nữ nộp tờ khai đăng ký kết hôn cho cơ quan đăng ký hộ tịch."
            }]
          }]
        }"""
    )

    supplemented = supplement_rule_source_diversity(
        request_id=issue.request_id,
        issues=[issue],
        output=model_output,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )
    sections, aggregate, _trace = render_structured_answer(
        request_id=issue.request_id,
        issues=[issue],
        output=supplemented,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    rule_claims = [
        claim for claim in supplemented.issues[0].claims
        if claim.claim_type == "rule"
    ]
    assert len(rule_claims) == 3
    assert len({claim.claim_text for claim in rule_claims}) == 3
    assert sections[0].answer.count("Kết luận") == 1
    assert sections[0].answer.count("\n- ") == 3
    assert aggregate["grounding_status"] == "fully_grounded"


def test_structured_generation_timeout_uses_normal_and_hard_sla_budgets():
    assert structured_generation_timeout_seconds({}) == 60.0
    assert structured_generation_timeout_seconds({}, hard_question=True) == 120.0
    assert structured_generation_timeout_seconds(
        {"LEGAL_STRUCTURED_GENERATION_TIMEOUT_SECONDS": "12"}
    ) == 12.0
    assert structured_generation_timeout_seconds(
        {"LEGAL_STRUCTURED_GENERATION_TIMEOUT_SECONDS": "12"},
        hard_question=True,
    ) == 12.0
    assert structured_generation_timeout_seconds(
        {"LEGAL_STRUCTURED_GENERATION_TIMEOUT_SECONDS": "900"}
    ) == 120.0
    assert structured_generation_timeout_seconds(
        {"LEGAL_STRUCTURED_HARD_GENERATION_TIMEOUT_SECONDS": "900"},
        hard_question=True,
    ) == 240.0


def test_structured_total_timeout_allows_complete_answer_before_fallback():
    assert structured_total_timeout_seconds({}) == 90.0
    assert structured_total_timeout_seconds({}, hard_question=True) == 180.0
    assert structured_total_timeout_seconds(
        {"LEGAL_STRUCTURED_TOTAL_TIMEOUT_SECONDS": "45"}
    ) == 45.0
    assert structured_total_timeout_seconds(
        {"LEGAL_STRUCTURED_HARD_TOTAL_TIMEOUT_SECONDS": "900"},
        hard_question=True,
    ) == 300.0


def test_optimized_profile_is_explicit_role_scoped_and_bounded():
    disabled = {}
    assert optimized_profile_enabled("citizen", disabled) is False
    assert structured_total_timeout_seconds(disabled, role="citizen") == 90.0
    enabled = {
        "LEGAL_ANSWER_OPTIMIZED_PROFILE_ENABLED": "true",
        "LEGAL_ANSWER_OPTIMIZED_PROFILE_ROLES": "citizen,officer",
    }
    assert optimized_profile_enabled("citizen", enabled) is True
    assert optimized_profile_enabled("admin", enabled) is False
    assert structured_retrieval_timeout_seconds("core", role="citizen", environ=enabled) == 4.0
    assert structured_retrieval_timeout_seconds("expanded", hard_question=True, role="officer", environ=enabled) == 3.0
    assert structured_generation_timeout_seconds(enabled, role="citizen") == 12.0
    assert structured_generation_timeout_seconds(enabled, hard_question=True, role="officer") == 14.0
    assert structured_total_timeout_seconds(enabled, role="citizen") == 20.0
    assert structured_total_timeout_seconds(enabled, hard_question=True, role="officer") == 24.0
    assert structured_context_max_chars(enabled, role="citizen") == 6000
    assert structured_context_max_chars(enabled, hard_question=True, role="officer") == 12000
    benchmark = {**enabled, "LEGAL_ANSWER_OPTIMIZED_PROFILE_CONTEXT_BENCHMARK": "true"}
    assert structured_context_max_chars(benchmark, role="citizen") == 4500
    assert structured_context_max_chars(benchmark, hard_question=True, role="officer") == 9000
    assert structured_model_options(12.0, enabled, role="citizen")["max_tokens"] == 1536
    assert structured_model_options(14.0, enabled, role="officer", hard_question=True)["max_tokens"] == 2048


def test_hard_question_classification_is_deterministic():
    issues = [
        LegalIssue(
            issue_id=f"issue-{index}",
            title="Facet",
            query_text="Nội dung pháp lý đầy đủ",
            intent="documents",
            domain="civil_status",
            split_confidence="high",
        )
        for index in range(1, 4)
    ]
    assert is_hard_legal_request(
        issues=issues,
        required_facets_by_issue={"issue-1": ["documents"]},
    )
    assert is_hard_legal_request(
        issues=issues[:2],
        required_facets_by_issue={
            "issue-1": ["condition", "authority", "documents"],
            "issue-2": ["deadline", "form"],
        },
    )
    cross_domain = [
        issues[0],
        LegalIssue(
            issue_id="issue-2",
            title="Facet",
            query_text="Nội dung pháp lý đầy đủ",
            intent="documents",
            domain="land",
            split_confidence="high",
        ),
    ]
    assert is_hard_legal_request(
        issues=cross_domain,
        required_facets_by_issue={"issue-1": ["documents"]},
    )
    assert not is_hard_legal_request(
        issues=issues[:2],
        required_facets_by_issue={
            "issue-1": ["documents"],
            "issue-2": ["deadline"],
        },
    )


def test_generation_budget_includes_model_provisioning_time():
    assert remaining_generation_budget_seconds(100.0, 24.0, now=110.0) == 14.0
    assert remaining_generation_budget_seconds(100.0, 24.0, now=130.0) == 0.0


def test_model_invocation_budget_keeps_wall_clock_below_24_second_gate():
    assert model_invocation_budget_seconds(24.0) == 23.75
    assert model_invocation_budget_seconds(1.0) == 1.0


def test_structured_model_options_force_json_without_creative_sampling():
    assert structured_model_options(24.0) == {
        "max_tokens": 2048,
        "timeout": 24.0,
        "temperature": 0.0,
        "streaming": False,
        "structured": {"type": "json_object"},
    }


def test_structured_model_options_allows_bounded_latency_probe_override():
    options = structured_model_options(
        24.0,
        {"LEGAL_STRUCTURED_MAX_OUTPUT_TOKENS": "128"},
    )
    assert options["max_tokens"] == 512
    assert structured_model_options(
        24.0,
        {"LEGAL_STRUCTURED_MAX_OUTPUT_TOKENS": "9999"},
    )["max_tokens"] == 4096
    assert structured_model_options(
        24.0,
        {"LEGAL_STRUCTURED_MAX_OUTPUT_TOKENS": "not-a-number"},
    )["max_tokens"] == 2048
    assert "structured" not in structured_model_options(
        24.0,
        {"LEGAL_STRUCTURED_PROVIDER_JSON_MODE": "false"},
    )


def test_structured_context_max_chars_is_bounded_for_latency_probes():
    assert structured_context_max_chars({}) == 6000
    assert structured_context_max_chars(
        {"LEGAL_STRUCTURED_CONTEXT_MAX_CHARS": "2000"}
    ) == 2000
    assert structured_context_max_chars(
        {"LEGAL_STRUCTURED_CONTEXT_MAX_CHARS": "9999"}
    ) == 6000
    assert structured_context_max_chars(
        {"LEGAL_STRUCTURED_CONTEXT_MAX_CHARS": "10"}
    ) == 1200


@pytest.mark.asyncio
async def test_hard_deadline_does_not_wait_for_slow_task_cancellation():
    async def slow_to_cancel():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)

    started = time.perf_counter()
    with pytest.raises(asyncio.TimeoutError):
        await await_with_hard_deadline(slow_to_cancel(), timeout=0.01)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    await asyncio.sleep(0.25)


@pytest.mark.asyncio
async def test_blocking_model_cannot_block_the_event_loop_deadline():
    class BlockingModel:
        def invoke(self, prompt):
            del prompt
            time.sleep(0.5)
            return "late"

    started = time.perf_counter()
    with pytest.raises(asyncio.TimeoutError):
        await invoke_blocking_model_with_deadline(
            BlockingModel(), "prompt", timeout=0.01
        )
    elapsed = time.perf_counter() - started

    # Keep scheduler headroom for a loaded full-suite run while still proving
    # that the 500 ms blocking invocation does not hold the event loop.
    assert elapsed < 0.3
    await asyncio.sleep(0.55)


@pytest.mark.asyncio
async def test_deepseek_v4_structured_invocation_disables_hidden_reasoning():
    calls = []

    class DeepSeekV4Model:
        model_name = "deepseek-v4-pro"

        def invoke(self, prompt, **kwargs):
            calls.append((prompt, kwargs))
            return "done"

    result = await invoke_blocking_model_with_deadline(
        DeepSeekV4Model(), "prompt", timeout=1.0
    )

    assert result == "done"
    assert calls == [
        (
            "prompt",
            {"extra_body": {"thinking": {"type": "disabled"}}},
        )
    ]
    assert structured_model_invocation_options(
        type("OtherModel", (), {"model_name": "gemini-2.5-flash"})()
    ) == {}


@pytest.mark.asyncio
async def test_structured_model_cache_reuses_the_provisioned_adapter():
    cache = AsyncModelCache()
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return object()

    first = await cache.get(("model-1", 1024, 24.0), factory)
    second = await cache.get(("model-1", 1024, 24.0), factory)

    assert first is second
    assert calls == 1
    assert structured_generation_timeout_seconds(
        {"LEGAL_STRUCTURED_GENERATION_TIMEOUT_SECONDS": "invalid"}
    ) == 60.0
    assert structured_generation_timeout_seconds(
        {"LEGAL_STRUCTURED_GENERATION_TIMEOUT_SECONDS": "invalid"},
        hard_question=True,
    ) == 120.0


def _issue():
    return LegalIssue(
        request_id="request-1",
        issue_id="issue-1",
        title="Thời hạn",
        query_text="đăng ký kết hôn thời hạn bao lâu",
        intent="deadline",
        domain="civil_status",
        split_confidence="high",
    )


def _source():
    return {
        "chunk_id": 123,
        "request_id": "request-1",
        "issue_id": "issue-1",
        "content": "Trong ngày tiếp nhận hồ sơ, công chức kiểm tra và giải quyết.",
        "document_title": "Nghị định hộ tịch",
        "law_number": "123/2015/NĐ-CP",
        "article_number": "18",
        "effective_status": "active",
        "official": True,
        "scope": "central",
        "domain": "civil_status",
        "source_url": "https://example.gov.vn/123",
    }


def test_compact_context_assigns_internal_evidence_ids_and_respects_budget():
    context, evidence = build_compact_evidence_context(
        issues=[_issue()], evidence_rows=[_source()], max_chars=9000
    )

    assert len(context) <= 9000
    assert "evidence-1" in context
    assert "123" not in evidence  # chunk id is never the evidence key
    assert evidence["evidence-1"]["issue_id"] == "issue-1"


def test_compact_context_removes_breadcrumbs_and_duplicate_source_rows():
    duplicate_rows = [
        {
            **_source(),
            "chunk_id": 501,
            "document_title": "Trang chủ > Hộ tịch > Luật Hộ tịch",
        },
        {
            **_source(),
            "chunk_id": 501,
            "document_title": "Trang chủ > Hộ tịch > Luật Hộ tịch",
            "content": _source()["content"] + " Nội dung lặp.",
        },
    ]

    context, evidence = build_compact_evidence_context(
        issues=[_issue()],
        evidence_rows=duplicate_rows,
        max_chars=4000,
    )

    assert len(evidence) == 1
    assert "Trang chủ" not in context
    assert "Hộ tịch >" not in context
    assert "Luật Hộ tịch" in context


def test_compact_context_round_robins_sources_across_all_issues():
    issues = [
        _issue(),
        LegalIssue(
            request_id="request-1",
            issue_id="issue-2",
            title="Lệ phí",
            query_text="đăng ký kết hôn lệ phí",
            intent="fee",
            domain="civil_status",
            split_confidence="high",
        ),
    ]
    first_issue_rows = [
        {**_source(), "chunk_id": 200 + index, "content": f"Nguồn hồ sơ {index}. " * 80}
        for index in range(5)
    ]
    fee_source = {
        **_source(),
        "chunk_id": 999,
        "issue_id": "issue-2",
        "content": "Nguồn lệ phí chính thức.",
    }

    _context, evidence = build_compact_evidence_context(
        issues=issues,
        evidence_rows=[*first_issue_rows, fee_source],
        max_chars=1800,
    )

    assert {row["issue_id"] for row in evidence.values()} == {"issue-1", "issue-2"}


def test_compact_context_keeps_same_provision_for_each_issue_that_needs_it():
    authority_issue = LegalIssue(
        request_id="request-1",
        issue_id="issue-2",
        title="Thẩm quyền",
        query_text="Đăng ký khai sinh nộp ở đâu?",
        intent="authority",
        domain="civil_status",
        split_confidence="high",
    )
    shared = {
        **_source(),
        "article_number": "13",
        "content": (
            "Ủy ban nhân dân cấp xã nơi cư trú của người cha hoặc người mẹ "
            "thực hiện đăng ký khai sinh."
        ),
    }

    _context, evidence = build_compact_evidence_context(
        issues=[_issue(), authority_issue],
        evidence_rows=[
            {**shared, "issue_id": "issue-1"},
            {**shared, "issue_id": "issue-2"},
        ],
        max_chars=1800,
    )

    assert [row["issue_id"] for row in evidence.values()] == ["issue-1", "issue-2"]


def test_compact_context_reserves_first_source_for_six_requested_issues():
    issues = [
        LegalIssue(
            request_id="request-many",
            issue_id=f"issue-{index}",
            title=f"Mục {index}",
            query_text="Đăng ký khai sinh",
            intent="rule",
            domain="civil_status",
            split_confidence="high",
        )
        for index in range(1, 7)
    ]
    rows = [
        {
            **_source(),
            "request_id": "request-many",
            "issue_id": issue.issue_id,
            "chunk_id": 300 + index,
            "article_number": str(index),
            "content": f"Nguồn trực tiếp cho {issue.issue_id}. " + ("x" * 850),
        }
        for index, issue in enumerate(issues, start=1)
    ]

    context, evidence = build_compact_evidence_context(
        issues=issues,
        evidence_rows=rows,
        max_chars=4000,
    )

    assert len(context) <= 4000
    assert {row["issue_id"] for row in evidence.values()} == {
        issue.issue_id for issue in issues
    }


def test_compact_context_keeps_both_explicit_comparison_provisions():
    issues = [
        LegalIssue(
            request_id="request-compare",
            issue_id=f"issue-{index}",
            title=f"Facet {index}",
            query_text="Điều 37 và Điều 38 Luật Hộ tịch",
            intent="rule",
            domain="civil_status",
            split_confidence="high",
        )
        for index in range(1, 5)
    ]
    rows = []
    for issue in issues:
        for article in ("38", "37"):
            rows.append(
                {
                    **_source(),
                    "request_id": "request-compare",
                    "issue_id": issue.issue_id,
                    "chunk_id": f"{issue.issue_id}-{article}",
                    "law_number": "60/2014/QH13",
                    "article_number": article,
                    "content": f"Điều {article}. " + ("nội dung chính thức " * 40),
                }
            )

    context, evidence = build_compact_evidence_context(
        issues=issues,
        evidence_rows=rows,
        max_chars=4000,
    )

    assert len(context) <= 4000
    assert {
        (row["issue_id"], row["article_number"])
        for row in evidence.values()
    } == {
        (issue.issue_id, article)
        for issue in issues
        for article in ("37", "38")
    }


def test_compact_context_keeps_bounded_long_exact_packet_for_later_issue():
    issues = [
        _issue(),
        LegalIssue(
            request_id="request-1",
            issue_id="issue-2",
            title="Điều 1 văn bản sửa đổi",
            query_text="Điều 1 văn bản sửa đổi",
            intent="rule",
            domain="land",
            split_confidence="high",
        ),
    ]
    rows = [
        {**_source(), "issue_id": "issue-1", "chunk_id": "short", "content": "Nguồn ngắn."},
        {
            **_source(),
            "issue_id": "issue-2",
            "chunk_id": "long",
            "law_number": "62/2020/QH14",
            "article_number": "1",
            "exact_article_packet_ref": "document:1:article:1",
            "exact_article_packet_status": "complete",
            "exact_article_serving_mode": "bounded_long_article_window",
            "exact_article_assembled_content": "Nội dung Điều 1. " + ("x" * 15000),
        },
    ]

    context, evidence = build_compact_evidence_context(
        issues=issues,
        evidence_rows=rows,
        max_chars=1800,
    )

    assert len(context) <= 1800
    assert {row["issue_id"] for row in evidence.values()} == {"issue-1", "issue-2"}
    long_row = next(row for row in evidence.values() if row["issue_id"] == "issue-2")
    assert long_row["exact_article_context_truncated"] is True


def test_parse_structured_answer_rejects_markdown_or_unknown_evidence_shape():
    parsed = parse_structured_answer(
        '{"issues":[{"issue_id":"issue-1","claims":[],"guidance":null,'
        '"clarifying_question":null}]}'
    )
    assert parsed.issues[0].issue_id == "issue-1"

    with pytest.raises(ValueError):
        parse_structured_answer("not json")

    with pytest.raises(ValueError):
        parse_structured_answer(
            '{"issues":[{"issue_id":"issue-1","claims":[],"unexpected":true}]}'
        )


def test_extractive_branch_keeps_clarifying_question_even_with_supported_claim():
    issue = LegalIssue(
        request_id="request-1",
        issue_id="issue-1",
        title="Khong hop tac hay tranh chap",
        query_text="Nguoi thua ke khong hop tac; phan biet tranh chap",
        text="Phan biet tu choi ky voi tranh chap chinh thuc",
        intent="rule",
        domain="land",
        split_confidence="high",
    )
    evidence = {
        "evidence-1": {
            "request_id": "request-1",
            "issue_id": "issue-1",
            "content": (
                "Sau 30 ngay ma khong co don tranh chap thi co quan dang ky "
                "tiep tuc xu ly ho so."
            ),
            "document_title": "Nghi dinh thu nghiem",
            "law_number": "01/2026/ND-CP",
            "effective_status": "active",
            "scope": "central",
            "source_url": "https://example.gov.vn/source",
        }
    }
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-1": ["rule"]},
    )

    output = build_extractive_structured_answer(
        issues=[issue],
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    assert output.issues[0].claims
    assert "tranh chấp" in (
        output.issues[0].clarifying_question or ""
    ).casefold()
    sections, _aggregate, _trace = render_structured_answer(
        request_id="request-1",
        issues=[issue],
        output=output,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )
    assert "tranh chấp" in (
        sections[0].clarifying_question or ""
    ).casefold()


def test_render_deduplicates_same_proposition_across_claim_types():
    issue = LegalIssue(
        request_id="request-birth",
        issue_id="issue-documents",
        title="Hồ sơ",
        query_text="Hồ sơ đăng ký khai sinh gồm những gì?",
        intent="documents",
        domain="civil_status",
        split_confidence="high",
    )
    proposition = (
        "Người đi đăng ký khai sinh nộp tờ khai theo mẫu quy định và "
        "giấy chứng sinh cho cơ quan đăng ký hộ tịch."
    )
    evidence = {
        "evidence-birth": {
            **_source(),
            "request_id": "request-birth",
            "issue_id": "issue-documents",
            "article_number": "16",
            "content": proposition,
        }
    }
    output = parse_structured_answer(
        json.dumps(
            {
                "issues": [
                    {
                        "issue_id": "issue-documents",
                        "claims": [
                            {
                                "claim_text": proposition,
                                "claim_type": "documents",
                                "evidence_id": "evidence-birth",
                                "support_quote": proposition,
                            },
                            {
                                "claim_text": proposition,
                                "claim_type": "rule",
                                "evidence_id": "evidence-birth",
                                "support_quote": proposition,
                            },
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-documents": ["documents", "rule"]},
    )

    sections, _aggregate, _trace = render_structured_answer(
        request_id="request-birth",
        issues=[issue],
        output=output,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    assert sections[0].answer.count(proposition) == 1


def test_combined_documents_submission_issue_requires_both_facets():
    issue = LegalIssue(
        request_id="request-1",
        issue_id="issue-1",
        title="Chung cu, ho so va noi nop",
        query_text="Ho so cap Giay chung nhan nop o co quan tiep nhan nao",
        text="Xac dinh giay to va noi nop ho so",
        intent="documents",
        domain="land",
        split_confidence="high",
    )

    facets = derive_required_facets_by_issue(
        issues=[issue],
        required_sections=[],
    )

    assert facets["issue-1"] == ["documents", "authority"]


def test_explicit_multi_facet_issues_do_not_inherit_documents_from_full_question():
    full_question = (
        "Tôi cần hồ sơ gì, nộp ở đâu, thời hạn bao lâu và điều kiện thế nào?"
    )
    issues = [
        LegalIssue(
            request_id="request-1",
            issue_id="issue-documents",
            title="Hồ sơ",
            query_text=f"Hồ sơ cần nộp. Bối cảnh: {full_question}",
            text="Xác định thành phần hồ sơ",
            intent="documents",
            domain="civil_status",
            split_confidence="high",
        ),
        LegalIssue(
            request_id="request-1",
            issue_id="issue-authority",
            title="Nơi nộp",
            query_text=f"Cơ quan tiếp nhận. Bối cảnh: {full_question}",
            text="Xác định cơ quan tiếp nhận",
            intent="authority",
            domain="civil_status",
            split_confidence="high",
        ),
        LegalIssue(
            request_id="request-1",
            issue_id="issue-deadline",
            title="Thời hạn",
            query_text=f"Thời hạn giải quyết. Bối cảnh: {full_question}",
            text="Xác định thời hạn giải quyết",
            intent="deadline",
            domain="civil_status",
            split_confidence="high",
        ),
    ]

    facets = derive_required_facets_by_issue(
        issues=issues,
        required_sections=["documents", "submission_place", "processing_time"],
    )

    assert facets == {
        "issue-documents": ["documents"],
        "issue-authority": ["authority"],
        "issue-deadline": ["deadline"],
    }


def test_multi_facet_cards_do_not_inherit_auxiliary_conclusion_rule():
    issues = [
        LegalIssue(
            request_id="request-1",
            issue_id="issue-condition",
            title="Điều kiện",
            query_text="Điều kiện đăng ký tạm trú",
            intent="condition",
            domain="cu_tru_an_ninh",
            split_confidence="high",
        ),
        LegalIssue(
            request_id="request-1",
            issue_id="issue-documents",
            title="Hồ sơ",
            query_text="Hồ sơ đăng ký tạm trú",
            intent="documents",
            domain="cu_tru_an_ninh",
            split_confidence="high",
        ),
    ]

    facets = derive_required_facets_by_issue(
        issues=issues,
        required_sections=["conclusion", "conditions_or_rights", "documents"],
    )

    assert facets == {
        "issue-condition": ["condition"],
        "issue-documents": ["documents"],
    }


def test_required_facet_queries_are_focused_when_procedure_subject_is_known():
    full_question = (
        "Xin Giấy xác nhận tình trạng hôn nhân: cần điều kiện, hồ sơ, "
        "nộp ở đâu và thời hạn bao lâu?"
    )
    planned = [
        LegalIssue(
            issue_id="issue-1",
            title="Hồ sơ",
            query_text=full_question,
            intent="documents",
            domain="civil_status",
            split_confidence="high",
            subject="tình trạng hôn nhân",
            location="Hải Phòng",
        )
    ]

    issues = ensure_required_facet_issues(
        question=full_question,
        issues=planned,
        required_sections=["documents", "submission_place", "processing_time"],
        max_issues=8,
    )

    by_intent = {issue.intent: issue.query_text.casefold() for issue in issues}
    assert "tình trạng hôn nhân" in by_intent["documents"]
    assert "hồ sơ giấy tờ" in by_intent["documents"]
    assert "thời hạn bao lâu" not in by_intent["documents"]
    # A processing-time clause is normally anchored by receipt of a complete
    # dossier; this phrase is legal deadline context, not cross-facet leakage.
    assert "kể từ ngày nhận đủ hồ sơ hợp lệ" in by_intent["deadline"]
    assert "hồ sơ giấy tờ" not in by_intent["deadline"]
    assert "thời hạn giải quyết" in by_intent["deadline"]


def test_form_focused_query_does_not_turn_plain_adjective_into_form_code():
    planned = [
        LegalIssue(
            issue_id="issue-1",
            title="Biểu mẫu",
            query_text="Cần biểu mẫu chính thức nào?",
            intent="form",
            domain="civil_status",
            split_confidence="high",
            subject="Giấy xác nhận tình trạng hôn nhân",
        )
    ]

    issues = ensure_required_facet_issues(
        question="Cần biểu mẫu chính thức nào?",
        issues=planned,
        required_sections=["official_forms"],
        max_issues=8,
    )

    assert "mẫu ch" not in issues[0].query_text.casefold()


def test_fee_facet_does_not_treat_capital_contribution_as_currency():
    issue = LegalIssue(
        request_id="request-1",
        issue_id="issue-1",
        title="Nghia vu tai chinh",
        query_text="Le phi cap Giay chung nhan",
        intent="fee",
        domain="land",
        split_confidence="high",
    )
    evidence = {
        "evidence-1": {
            "request_id": "request-1",
            "issue_id": "issue-1",
            "content": "Nha nuoc dong gop von vao doanh nghiep.",
        }
    }

    matrix = build_issue_coverage_matrix(
        issues=[issue],
        evidence_by_id=evidence,
        required_facets_by_issue={"issue-1": ["fee"]},
    )

    assert matrix["issue-1"][0]["evidence_available"] is False


def test_render_drops_only_unsupported_claim_and_keeps_supported_claim():
    output = parse_structured_answer(
        """{
          "issues": [{
            "issue_id": "issue-1",
            "claims": [
              {
                "claim_text": "Hồ sơ được giải quyết trong ngày tiếp nhận.",
                "claim_type": "deadline",
                "evidence_id": "evidence-1",
                "support_quote": "Trong ngày tiếp nhận hồ sơ"
              },
              {
                "claim_text": "Lệ phí là 50.000 đồng.",
                "claim_type": "fee",
                "evidence_id": "evidence-1",
                "support_quote": "Trong ngày tiếp nhận hồ sơ"
              }
            ],
            "guidance": null,
            "clarifying_question": null
          }]
        }"""
    )

    sections, aggregate, trace = render_structured_answer(
        request_id="request-1",
        issues=[_issue()],
        output=output,
        evidence_by_id={"evidence-1": _source()},
    )

    assert sections[0].status == "sufficiently_evidenced"
    assert "trong ngày tiếp nhận" in sections[0].answer.casefold()
    assert "50.000" not in sections[0].answer
    assert sections[0].facet == "deadline"
    assert sections[0].priority == "normal"
    assert sections[0].claim_types == ["deadline"]
    assert aggregate["grounding_status"] == "fully_grounded"
    assert trace["rejected_claim_count"] == 1


def test_render_requires_a_claim_for_the_requested_facet():
    output = parse_structured_answer(
        """{
          "issues": [{
            "issue_id": "issue-1",
            "claims": [{
              "claim_text": "Cơ quan tiếp nhận kiểm tra hồ sơ.",
              "claim_type": "authority",
              "evidence_id": "evidence-1",
              "support_quote": "công chức kiểm tra"
            }],
            "guidance": null,
            "clarifying_question": null
          }]
        }"""
    )

    sections, aggregate, trace = render_structured_answer(
        request_id="request-1",
        issues=[_issue()],
        output=output,
        evidence_by_id={"evidence-1": _source()},
        role="citizen",
    )

    assert sections[0].status == "insufficiently_evidenced"
    assert aggregate["grounding_status"] == "insufficient_evidence"
    assert trace["issues"][0]["requested_facets"] == ["deadline"]
    assert trace["issues"][0]["missing_facets"] == ["deadline"]
    assert trace["coverage_ratio"] == 0.0


def test_role_renderer_makes_verified_claims_actionable_without_changing_evidence():
    output = parse_structured_answer(
        """{
          "issues": [{
            "issue_id": "issue-1",
            "claims": [{
              "claim_text": "Hồ sơ được giải quyết trong ngày tiếp nhận.",
              "claim_type": "deadline",
              "evidence_id": "evidence-1",
              "support_quote": "Trong ngày tiếp nhận hồ sơ"
            }],
            "guidance": null,
            "clarifying_question": null
          }]
        }"""
    )

    citizen_sections, _, _ = render_structured_answer(
        request_id="request-1",
        issues=[_issue()],
        output=output,
        evidence_by_id={"evidence-1": _source()},
        role="citizen",
    )
    officer_sections, _, _ = render_structured_answer(
        request_id="request-1",
        issues=[_issue()],
        output=output,
        evidence_by_id={"evidence-1": _source()},
        role="officer",
    )

    assert citizen_sections[0].answer.startswith("- **Mốc thời gian/thời hạn:**")
    assert officer_sections[0].answer.startswith("- **Thời hạn/mốc thời gian nghiệp vụ:**")
    assert "trong ngày tiếp nhận" in citizen_sections[0].answer.casefold()
    assert "trong ngày tiếp nhận" in officer_sections[0].answer.casefold()


def test_role_renderer_groups_same_type_claims_under_one_label():
    issue = LegalIssue(
        request_id="request-documents",
        issue_id="issue-documents",
        title="Hồ sơ, giấy tờ",
        query_text="Hồ sơ đăng ký gồm những gì?",
        intent="documents",
        domain="civil_status",
        split_confidence="high",
    )
    source = {
        **_source(),
        "request_id": issue.request_id,
        "issue_id": issue.issue_id,
        "supported_facets": ["documents"],
        "content": "Hồ sơ gồm Tờ khai đăng ký. Hồ sơ có Giấy chứng sinh.",
    }
    output = parse_structured_answer(
        """{
          "issues": [{
            "issue_id": "issue-documents",
            "claims": [
              {
                "claim_text": "Hồ sơ gồm Tờ khai đăng ký.",
                "claim_type": "documents",
                "evidence_id": "evidence-1",
                "support_quote": "Hồ sơ gồm Tờ khai đăng ký."
              },
              {
                "claim_text": "Hồ sơ có Giấy chứng sinh.",
                "claim_type": "documents",
                "evidence_id": "evidence-1",
                "support_quote": "Hồ sơ có Giấy chứng sinh."
              }
            ],
            "guidance": null,
            "clarifying_question": null
          }]
        }"""
    )

    sections, _, _ = render_structured_answer(
        request_id=issue.request_id,
        issues=[issue],
        output=output,
        evidence_by_id={"evidence-1": source},
        role="citizen",
    )

    assert sections[0].answer.startswith("**Hồ sơ cần chuẩn bị**\n")
    assert sections[0].answer.count("Hồ sơ cần chuẩn bị") == 1
    assert sections[0].answer.count("\n- ") == 2


def test_structured_prompt_supports_action_exception_warning_and_role_contracts():
    prompt = build_structured_answer_prompt(
        question="Tôi cần làm gì tiếp theo?",
        role="citizen",
        context="ISSUES\n- issue-1\nEVIDENCE\n",
    )

    assert "next_action" in prompt
    assert "exception" in prompt
    assert "warning" in prompt
    assert "việc cần làm tiếp theo" in prompt.casefold()


def test_coverage_matrix_tracks_every_available_requested_facet():
    documents_issue = LegalIssue(
        request_id="request-1", issue_id="issue-1", title="Hồ sơ",
        query_text="Cần hồ sơ gì?", intent="documents",
        domain="civil_status", split_confidence="high",
    )
    fee_issue = LegalIssue(
        request_id="request-1", issue_id="issue-2", title="Lệ phí",
        query_text="Lệ phí bao nhiêu?", intent="fee",
        domain="civil_status", split_confidence="high",
    )
    evidence = {
        "evidence-1": {
            **_source(), "issue_id": "issue-1",
            "content": "Hồ sơ gồm tờ khai đăng ký và bản sao giấy tờ tùy thân.",
        },
        "evidence-2": {
            **_source(), "issue_id": "issue-2",
            "content": "Lệ phí đăng ký là 20.000 đồng.",
        },
    }
    required = derive_required_facets_by_issue(
        issues=[documents_issue, fee_issue],
        required_sections=["documents", "fee"],
    )
    matrix = build_issue_coverage_matrix(
        issues=[documents_issue, fee_issue],
        evidence_by_id=evidence,
        required_facets_by_issue=required,
    )
    output = parse_structured_answer(
        """{"issues":[
          {"issue_id":"issue-1","claims":[{
            "claim_text":"Hồ sơ gồm tờ khai đăng ký.",
            "claim_type":"documents","evidence_id":"evidence-1",
            "support_quote":"Hồ sơ gồm tờ khai đăng ký"
          }]},
          {"issue_id":"issue-2","claims":[]}
        ]}"""
    )

    _sections, _aggregate, trace = render_structured_answer(
        request_id="request-1", issues=[documents_issue, fee_issue],
        output=output, evidence_by_id=evidence, coverage_matrix=matrix,
    )

    assert trace["coverage_ratio"] == 0.5
    assert trace["claim_grounding_ratio"] == 1.0
    assert trace["displayed_legal_claim_count"] == 1
    assert trace["displayed_claims_with_valid_evidence"] == 1
    assert trace["issues"][0]["coverage_matrix"][0]["status"] == "covered"
    assert trace["issues"][1]["coverage_matrix"][0]["status"] == "missing"
    assert trace["quality_gate"]["pass"] is True
    assert trace["quality_gate"]["coverage_warning"] is True


def test_coverage_preserves_requires_user_fact_even_with_direct_evidence():
    issue = _issue()
    evidence = {"evidence-1": _source()}
    output = parse_structured_answer(
        """{"issues":[{"issue_id":"issue-1","claims":[{
          "claim_text":"Trong ngày tiếp nhận hồ sơ",
          "claim_type":"deadline","evidence_id":"evidence-1",
          "support_quote":"Trong ngày tiếp nhận hồ sơ"
        }]}]}"""
    )
    matrix = {
        "issue-1": [{
            "facet": "deadline", "required": True,
            "evidence_available": True, "evidence_ids": ["evidence-1"],
            "requires_user_fact": True,
            "missing_fact_ids": ["fact-example"],
        }]
    }

    _sections, _aggregate, trace = render_structured_answer(
        request_id="request-1",
        issues=[issue],
        output=output,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    row = trace["issues"][0]["coverage_matrix"][0]
    assert row["coverage_status"] == "requires_user_fact"
    assert row["missing_fact_ids"] == ["fact-example"]
    assert trace["coverage_ratio"] == 0.0


def test_clarifying_question_is_preserved_with_supported_conditional_claim():
    section = validate_answer_section(
        request_id="request-1",
        issue_id="issue-1",
        title="Dieu kien",
        sources=[_source()],
        answer="Ket luan co dieu kien.",
        clarifying_question="Giay to co du chu ky cua hai ben khong?",
    )

    assert section.status == "sufficiently_evidenced"
    assert section.clarifying_question == "Giay to co du chu ky cua hai ben khong?"


def test_public_citations_only_cover_claims_that_are_actually_displayed():
    issue = _issue()
    first = _source()
    second = {
        **_source(),
        "law_number": "02/2026/ND-CP",
        "source_url": "https://official.example/second",
        "content": "Trong ngay tiep nhan ho so thu hai",
    }
    output = parse_structured_answer(
        """{"issues":[{"issue_id":"issue-1","claims":[
          {"claim_text":"Trong ngày tiếp nhận hồ sơ","claim_type":"deadline",
           "evidence_id":"evidence-1","support_quote":"Trong ngày tiếp nhận hồ sơ"},
          {"claim_text":"Trong ngay tiep nhan ho so thu hai","claim_type":"deadline",
           "evidence_id":"evidence-2","support_quote":"Trong ngay tiep nhan ho so thu hai"}
        ]}]}"""
    )

    sections, _aggregate, _trace = render_structured_answer(
        request_id="request-1",
        issues=[issue],
        output=output,
        evidence_by_id={"evidence-1": first, "evidence-2": second},
    )

    assert len(sections[0].citations) == 1
    assert sections[0].citations[0].source_url == first["source_url"]


def test_required_facet_binding_prefers_exact_issue_over_rule_compatibility():
    rule_issue = LegalIssue(
        request_id="request-1",
        issue_id="issue-1",
        title="Căn cứ",
        query_text="Quy định đăng ký khai sinh",
        intent="rule",
        domain="civil_status",
        split_confidence="high",
    )
    documents_issue = LegalIssue(
        request_id="request-1",
        issue_id="issue-2",
        title="Hồ sơ",
        query_text="Hồ sơ đăng ký khai sinh gồm những gì?",
        intent="documents",
        domain="civil_status",
        split_confidence="high",
    )

    required = derive_required_facets_by_issue(
        issues=[rule_issue, documents_issue],
        required_sections=["conclusion", "documents"],
    )

    assert required == {
        "issue-1": ["rule"],
        "issue-2": ["documents"],
    }


def test_coverage_excludes_requested_facet_when_store_has_no_supporting_source():
    form_issue = LegalIssue(
        request_id="request-1", issue_id="issue-1", title="Biểu mẫu",
        query_text="Tải biểu mẫu nào?", intent="form",
        domain="civil_status", split_confidence="high",
    )
    evidence = {"evidence-1": _source()}
    matrix = build_issue_coverage_matrix(
        issues=[form_issue], evidence_by_id=evidence,
        required_facets_by_issue={"issue-1": ["form"]},
    )
    output = parse_structured_answer(
        '{"issues":[{"issue_id":"issue-1","claims":[]}]}'
    )

    _sections, _aggregate, trace = render_structured_answer(
        request_id="request-1", issues=[form_issue], output=output,
        evidence_by_id=evidence, coverage_matrix=matrix,
    )

    # Missing evidence remains in the denominator; unavailable facets cannot
    # make coverage appear complete.
    assert trace["coverage_ratio"] == 0.0
    assert trace["available_required_facet_count"] == 0
    assert trace["issues"][0]["coverage_matrix"][0]["status"] == "not_available"
    assert trace["quality_gate"]["pass"] is False


def test_step3_quality_gate_preserves_grounded_claims_when_coverage_is_partial():
    assert evaluate_structured_quality_gate({
        "displayed_legal_claim_count": 10,
        "displayed_claims_with_valid_evidence": 10,
        "coverage_ratio": 0.9,
    })["pass"] is True
    assert evaluate_structured_quality_gate({
        "displayed_legal_claim_count": 10,
        "displayed_claims_with_valid_evidence": 9,
        "coverage_ratio": 1.0,
    })["pass"] is False
    assert evaluate_structured_quality_gate({
        "displayed_legal_claim_count": 10,
        "displayed_claims_with_valid_evidence": 10,
        "coverage_ratio": 0.8999,
    })["pass"] is True
    partial = evaluate_structured_quality_gate({
        "displayed_legal_claim_count": 1,
        "displayed_claims_with_valid_evidence": 1,
        "coverage_ratio": 0.5,
    })
    assert partial["pass"] is True
    assert partial["coverage_warning"] is True
    assert partial["coverage_at_least_90_percent"] is False
    assert evaluate_structured_quality_gate({
        "displayed_legal_claim_count": 0,
        "displayed_claims_with_valid_evidence": 0,
        "coverage_ratio": 1.0,
    })["pass"] is False


def test_exact_quantitative_condition_uses_two_direct_threshold_claims():
    issue = LegalIssue(
        request_id="request-threshold",
        issue_id="issue-condition",
        title="Điều kiện áp dụng",
        query_text=(
            "Theo Nghị quyết 22/2025/NQ-HĐND, diện tích tối thiểu tại phường "
            "và xã là bao nhiêu; tỷ lệ đất ở tối đa là bao nhiêu?"
        ),
        text="Ngưỡng diện tích và tỷ lệ đất ở",
        intent="condition",
        domain="dat_dai_xay_dung",
        split_confidence="high",
    )
    common = {
        **_source(),
        "request_id": issue.request_id,
        "issue_id": issue.issue_id,
        "law_number": "22/2025/NQ-HĐND",
        "article_number": "3",
        "document_title": "Nghị quyết 22/2025/NQ-HĐND",
        "scope": "haiphong",
        "domain": "dat_dai_xay_dung",
        "source_url": "https://vbpl.vn/van-ban/chi-tiet/nghi-quyet--183161",
    }
    evidence = {
        "evidence-ratio": {
            **common,
            "chunk_id": 751330,
            "clause_number": "1",
            "content": (
                "Trong đó, tỷ lệ đất ở trong phạm vi khu đất thu hồi không "
                "vượt quá 15% tổng diện tích đất thu hồi."
            ),
        },
        "evidence-area": {
            **common,
            "chunk_id": 751331,
            "clause_number": "2",
            "content": (
                "Khu đất có diện tích từ 1000m2 trở lên tại các phường, từ "
                "3000m2 trở lên tại các xã, đặc khu, nhưng không đủ diện tích "
                "tối thiểu để hình thành khu đô thị, khu dân cư nông thôn."
            ),
        },
    }
    matrix = {
        issue.issue_id: [{
            "facet": "condition",
            "required": True,
            "evidence_available": True,
            "evidence_ids": ["evidence-ratio", "evidence-area"],
        }]
    }
    generated = parse_structured_answer(
        '{"issues":[{"issue_id":"issue-condition","claims":[]}]}'
    )

    enforced = enforce_explicit_facet_claims(
        issues=[issue],
        output=generated,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )
    sections, _aggregate, trace = render_structured_answer(
        request_id=issue.request_id,
        issues=[issue],
        output=enforced,
        evidence_by_id=evidence,
        coverage_matrix=matrix,
    )

    answer = sections[0].answer or ""
    assert "1000m2" in answer
    assert "3000m2" in answer
    assert "15%" in answer
    assert trace["quality_gate"]["pass"] is True
