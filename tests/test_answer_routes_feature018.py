from __future__ import annotations

from datetime import date

from api.legal_answer_router import route_legal_answer
from api.models import AskResponse
from api.unified_chat_service import finalize_legal_answer_response


def _finalize(question: str, **updates) -> AskResponse:
    payload = {
        "answer": "Nội dung đã được kiểm định từ nguồn hiện có.",
        "question": question,
        "answer_status": "grounded",
        "evidence_count": 1,
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
    payload.update(updates)
    return finalize_legal_answer_response(AskResponse(**payload))


def test_exact_article_route_is_stable_in_public_presentation():
    question = "Điều 16 Luật hộ tịch số 60/2014/QH13 quy định gì?"

    routed = route_legal_answer(question)
    response = _finalize(question)

    assert routed.answer_route == "exact_article"
    assert routed.exact_law_number == "60/2014/QH13"
    assert routed.exact_article_number == "16"
    assert response.answer_route == "exact_article"
    assert response.sections and response.sections.legal_bases


def test_procedure_form_route_never_creates_forms_from_question_text():
    question = "Thủ tục đăng ký khai sinh cần Mẫu 99 nào?"

    routed = route_legal_answer(question)
    response = _finalize(question, recommended_forms=[])

    assert routed.answer_route == "procedure_form"
    assert response.answer_route == "procedure_form"
    assert response.sections is not None
    assert response.sections.recommended_forms == []


def test_general_legal_route_is_default_for_non_identity_question():
    question = "Ủy ban nhân dân cấp xã có những trách nhiệm pháp lý chung nào?"

    routed = route_legal_answer(question)
    response = _finalize(question)

    assert routed.answer_route == "general_legal"
    assert response.answer_route == "general_legal"
    assert response.historical_label is None


def test_historical_route_requires_applicability_date_and_label():
    question = "Vào năm 2020 quy định đăng ký khai sinh được áp dụng thế nào?"

    routed = route_legal_answer(question)
    response = _finalize(
        question,
        legal_as_of=date(2020, 6, 1),
        citations=[
            {
                "law_number": "60/2014/QH13",
                "document_title": "Luật hộ tịch",
                "article_number": "16",
                "effective_status": "expired",
                "source_url": "https://vbpl.vn/example",
            }
        ],
    )

    assert routed.answer_route == "historical"
    assert response.answer_route == "historical"
    assert response.historical_label == "Thông tin lịch sử — áp dụng tại ngày 01/06/2020"
    assert response.sections is not None
    assert response.historical_label in response.sections.caveats


def test_historical_wording_without_legal_as_of_fails_closed_to_general_route():
    response = _finalize("Năm 2020 quy định cũ như thế nào?")

    assert response.answer_route == "general_legal"
    assert response.historical_label is None
