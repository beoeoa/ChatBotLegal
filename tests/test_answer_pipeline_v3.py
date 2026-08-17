import asyncio

from api.legal_domains import canonical_domain_decision, canonicalize_legal_domain
from api.legal_answer_presentation import project_legal_answer_presentation
from api.legal_answer_quality import apply_claim_validation
from api.models import AskRequest, AskResponse
from api.routers.search import _answer_delivery_projection
from api.unified_chat_service import run_legal_answer_pipeline_v3
from scripts.legal_search_server import _domain_matches, _domain_values, _lexical_query_text


def test_residence_alias_is_canonical_across_api_and_retrieval():
    decision = canonical_domain_decision("cu_tru")

    assert decision.canonical_domain == "cu_tru_an_ninh"
    assert decision.mapping_reason == "legacy_alias"
    assert canonicalize_legal_domain("an_ninh") == "cu_tru_an_ninh"
    assert "cu_tru_an_ninh" in _domain_values("cu_tru")
    assert _domain_matches("cu_tru", "cu_tru_an_ninh") is True


def test_long_natural_question_keeps_bounded_lexical_business_terms():
    query = (
        "Nhà em mới chuyển trọ sang phường khác, đăng ký tạm trú cần những "
        "giấy tờ gì và chủ nhà phải xác nhận thế nào?"
    )

    lexical_query = _lexical_query_text(query)

    assert lexical_query
    assert "dang ky" in lexical_query
    assert "tam tru" in lexical_query
    assert len(lexical_query) <= 160


def test_public_answer_status_contract_is_backward_compatible():
    response = AskResponse(
        answer="Đây là quy định khung đã được xác minh.",
        question="Tôi cần đăng ký tạm trú thế nào?",
        answer_status="broad_grounded",
        fallback_tier="full_corpus",
        canonical_domain="cu_tru_an_ninh",
        evidence_count=3,
        coverage_warning="Thiếu căn cứ để xác định biểu mẫu trong trường hợp cụ thể.",
    )

    assert response.answer_status == "broad_grounded"
    assert response.fallback_tier == "full_corpus"
    assert response.canonical_domain == "cu_tru_an_ninh"
    assert response.evidence_count == 3
    assert response.grounding_status == "unknown"


def test_delivery_projection_keeps_partial_claims_and_labels_full_corpus():
    broad = _answer_delivery_projection(
        grounding_status="fully_grounded",
        answer_completeness={"status": "complete"},
        evidence_count=2,
        clarifying_questions=[],
        provider_error_code=None,
        source_gap=[],
        section_trace={"retrieval_decision": {"fallback_tier": "full_corpus"}},
        verified_form_lookup=False,
    )
    partial = _answer_delivery_projection(
        grounding_status="fully_grounded",
        answer_completeness={"status": "incomplete"},
        evidence_count=1,
        clarifying_questions=[],
        provider_error_code=None,
        source_gap=["fee"],
        section_trace={"retrieval_decision": {"fallback_tier": "domain"}},
        verified_form_lookup=False,
    )

    assert broad["answer_status"] == "broad_grounded"
    assert broad["fallback_tier"] == "full_corpus"
    assert partial["answer_status"] == "partial_grounded"
    assert partial["evidence_count"] == 1
    assert partial["coverage_warning"]


def test_per_claim_validation_keeps_supported_claim_and_makes_coverage_advisory():
    supported = "Ủy ban nhân dân cấp xã tiếp nhận hồ sơ đăng ký khai sinh."
    unsupported = "Lệ phí bắt buộc là 50.000 đồng."
    answer = f"{supported} {unsupported}"
    decisions = [
        {
            "claim_type": "authority",
            "status": "verified",
            "original": supported,
            "evidence_ids": ["legal:16"],
        },
        {
            "claim_type": "fee",
            "status": "rejected",
            "original": unsupported,
            "evidence_ids": [],
        },
    ]

    validated = apply_claim_validation(answer, decisions)
    delivery = _answer_delivery_projection(
        grounding_status="partially_grounded",
        answer_completeness={"status": "incomplete"},
        evidence_count=1,
        clarifying_questions=[],
        provider_error_code=None,
        source_gap=["fee"],
        section_trace={"retrieval_decision": {"fallback_tier": "domain"}},
        verified_form_lookup=False,
    )
    presentation = project_legal_answer_presentation(
        {
            "answer": validated,
            "question": "Nộp ở đâu và lệ phí bao nhiêu?",
            "citations": [
                {
                    "law_number": "60/2014/QH13",
                    "article_number": "16",
                    "source_url": "https://vbpl.vn/example",
                }
            ],
            **delivery,
        }
    )

    assert supported in validated
    assert unsupported not in validated
    assert delivery["answer_status"] == "partial_grounded"
    assert presentation.sections.short_answer == validated
    assert presentation.sections.legal_bases
    assert delivery["coverage_warning"] in presentation.sections.caveats


def test_v3_service_executes_once_then_finalizes_one_public_contract():
    calls = []

    async def executor(ask_request, request, **kwargs):
        calls.append((ask_request.question, request, kwargs))
        return AskResponse(
            answer="Nội dung đã qua claim validation.",
            question=ask_request.question,
            grounding_status="fully_grounded",
            answer_status="grounded",
            evidence_count=1,
            citations=[
                {
                    "law_number": "60/2014/QH13",
                    "article_number": "16",
                    "source_url": "https://vbpl.vn/example",
                }
            ],
            answer_sections=[
                {
                    "issue_id": "article-16",
                    "title": "Quy định tại Điều 16",
                    "status": "sufficiently_evidenced",
                    "answer": "Nội dung đã qua claim validation.",
                    "citations": [
                        {
                            "law_number": "60/2014/QH13",
                            "document_title": "Luật hộ tịch",
                            "article_number": "16",
                            "effective_status": "active",
                            "source_url": "https://vbpl.vn/example",
                        }
                    ],
                }
            ],
            answer_route="exact_article",
        )

    request = object()
    result = asyncio.run(
        run_legal_answer_pipeline_v3(
            ask_request=AskRequest(question="Điều 16 quy định gì?"),
            request=request,
            executor=executor,
        )
    )

    assert len(calls) == 1
    assert calls[0][0] == "Điều 16 quy định gì?"
    assert calls[0][1] is request
    assert result.response_mode == "answer"
    assert result.presentation_version == "legal-answer-v1"
    assert result.sections is not None
    assert result.sections.short_answer == result.answer
    assert result.sections.legal_bases == result.citations
