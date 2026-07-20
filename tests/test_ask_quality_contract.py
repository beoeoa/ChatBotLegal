from datetime import date

from api.models import AskRequest, AskResponse


def test_ask_request_accepts_legal_dates_and_idempotency_key():
    request = AskRequest(
        question="Thủ tục này áp dụng tại thời điểm nào?",
        role="citizen",
        strategy_model="model-1",
        answer_model="model-1",
        final_answer_model="model-1",
        event_date="2020-01-02",
        legal_as_of="2021-03-04",
        idempotency_key="request-12345",
    )
    assert request.event_date == date(2020, 1, 2)
    assert request.legal_as_of == date(2021, 3, 4)


def test_rag_trace_is_opt_in_and_defaults_to_false():
    request = AskRequest(question="Câu hỏi", role="citizen")
    assert request.show_rag_trace is False


def test_ask_response_exposes_quality_contract():
    response = AskResponse(
        answer="Nội dung đã kiểm tra.",
        question="Câu hỏi",
        legal_as_of=date(2026, 7, 14),
        evidence_coverage={"authority": {"status": "verified", "evidence_ids": ["42"]}},
        claim_validation=[{"claim_type": "authority", "status": "verified"}],
        authority_status="verified",
        clarifying_questions=[],
        quality_flags=[],
        answer_score_preview=9.2,
    )
    payload = response.model_dump(mode="json")
    assert payload["legal_as_of"] == "2026-07-14"
    assert payload["evidence_coverage"]["authority"]["status"] == "verified"
    assert payload["answer_score_preview"] == 9.2
