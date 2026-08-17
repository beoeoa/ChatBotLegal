from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from api.legal_answer_presentation import (
    PRESENTATION_VERSION,
    LegalAnswerPresentationV1,
    project_legal_answer_presentation,
)
from api.models import AskResponse
from api.legal_structured_answer import project_backend_owned_answer_artifacts
from api.unified_chat_service import finalize_legal_answer_response


def _citation(**overrides):
    item = {
        "law_number": "60/2014/QH13",
        "document_title": "Luật hộ tịch",
        "article_number": "16",
        "source_url": "https://vbpl.vn/example",
        "effective_status": "active",
    }
    item.update(overrides)
    return item


def _form(**overrides):
    item = {
        "procedure_id": "dang_ky_khai_sinh",
        "form_id": "to-khai-khai-sinh",
        "name": "Tờ khai đăng ký khai sinh",
        "download_url": "/api/procedures/forms/to-khai-khai-sinh",
        "review_status": "released",
        "source_checksum": "a" * 64,
    }
    item.update(overrides)
    return item


def test_legacy_projection_keeps_backend_owned_forms_and_citations():
    citation = _citation()
    form = _form()
    payload = {
        "answer": (
            "Người dân nộp hồ sơ tại cơ quan có thẩm quyền. "
            "LLM text must not create https://invented.example/form or Mẫu 99."
        ),
        "question": "Đăng ký khai sinh cần mẫu nào?",
        "answer_status": "grounded",
        "evidence_count": 1,
        "legal_as_of": date(2026, 8, 13),
        "citations": [citation],
        "recommended_forms": [form],
        "procedure_detail": {
            "id": "dang_ky_khai_sinh",
            "name": "Đăng ký khai sinh",
            "department": "Ủy ban nhân dân cấp xã",
            "steps": ["Nộp hồ sơ", "Nhận kết quả"],
            "documents_required": ["Tờ khai đăng ký khai sinh"],
            "duration": "Theo nguồn đã xác minh",
            "fee": "Theo nguồn đã xác minh",
        },
    }

    presentation = project_legal_answer_presentation(payload)
    rendered = presentation.model_dump(mode="json", exclude_none=True)

    assert presentation.presentation_version == PRESENTATION_VERSION
    assert presentation.answer_route == "procedure_form"
    assert rendered["sections"]["recommended_forms"] == [form]
    assert rendered["sections"]["legal_bases"] == [citation]
    assert "invented.example" not in str(rendered["sections"]["recommended_forms"])
    assert presentation.sections.procedure["id"] == "dang_ky_khai_sinh"


def test_projection_has_one_stable_section_order():
    presentation = project_legal_answer_presentation(
        {
            "answer": "Phần trả lời ngắn đã được kiểm định.",
            "question": "Tôi cần làm gì và chuẩn bị giấy tờ nào?",
            "answer_status": "partial_grounded",
            "evidence_count": 1,
            "coverage_warning": "Chưa đủ căn cứ để xác định lệ phí.",
            "citations": [_citation()],
            "answer_sections": [
                {
                    "issue_id": "documents",
                    "title": "Hồ sơ",
                    "status": "sufficiently_evidenced",
                    "answer": "Chuẩn bị tờ khai đã phát hành.",
                    "citations": [_citation()],
                    "facet": "documents",
                    "claim_types": ["documents"],
                },
                {
                    "issue_id": "fee",
                    "title": "Lệ phí",
                    "status": "insufficiently_evidenced",
                    "limitation": "Chưa có nguồn hiện hành đủ để xác minh lệ phí.",
                    "citations": [],
                    "facet": "fee",
                    "claim_types": ["fee"],
                },
            ],
        }
    )

    section_keys = list(presentation.sections.model_dump().keys())
    assert section_keys == [
        "short_answer",
        "actions",
        "dossier",
        "procedure",
        "recommended_forms",
        "legal_bases",
        "caveats",
        "clarifying_questions",
    ]
    assert presentation.sections.dossier == ["Chuẩn bị tờ khai đã phát hành."]
    assert "Chưa đủ căn cứ để xác định lệ phí." in presentation.sections.caveats
    assert "Chưa có nguồn hiện hành đủ để xác minh lệ phí." in presentation.sections.caveats


def test_coverage_warning_does_not_remove_supported_answer():
    presentation = project_legal_answer_presentation(
        {
            "answer": "Khoản đã có căn cứ vẫn được trả lời.",
            "question": "Câu hỏi nhiều ý",
            "answer_status": "partial_grounded",
            "evidence_count": 1,
            "coverage_warning": "Một phần câu hỏi còn thiếu nguồn.",
            "citations": [_citation()],
        }
    )

    assert presentation.sections.short_answer == "Khoản đã có căn cứ vẫn được trả lời."
    assert presentation.sections.legal_bases == [_citation()]
    assert presentation.sections.caveats == ["Một phần câu hỏi còn thiếu nguồn."]


def test_verified_label_requires_evidence():
    no_evidence = project_legal_answer_presentation(
        {
            "answer": "Cần làm rõ thêm.",
            "question": "Tôi phải dùng mẫu nào?",
            "answer_status": "clarifying",
            "evidence_count": 0,
            "clarifying_questions": ["Bạn đang thực hiện thủ tục nào?"],
        }
    )
    with_evidence = project_legal_answer_presentation(
        {
            "answer": "Nội dung có căn cứ.",
            "question": "Điều 16 quy định gì?",
            "answer_status": "grounded",
            "evidence_count": 1,
            "citations": [_citation()],
        }
    )

    assert no_evidence.verification_label is None
    assert with_evidence.verification_label == "Đã xác minh từ nguồn pháp lý"


def test_historical_route_requires_date_and_adds_plain_label():
    presentation = project_legal_answer_presentation(
        {
            "answer": "Quy định áp dụng tại thời điểm được hỏi.",
            "question": "Quy định vào năm 2020 là gì?",
            "answer_status": "grounded",
            "answer_route": "historical",
            "legal_as_of": "2020-06-01",
            "evidence_count": 1,
            "citations": [_citation(effective_status="expired")],
        }
    )

    assert presentation.answer_route == "historical"
    assert presentation.historical_label == "Thông tin lịch sử — áp dụng tại ngày 01/06/2020"
    assert presentation.historical_label in presentation.sections.caveats

    with pytest.raises(ValidationError):
        LegalAnswerPresentationV1(
            answer_status="grounded",
            answer_route="historical",
            evidence_count=1,
            sections={"short_answer": "Nội dung lịch sử"},
        )


def test_trace_release_fields_are_allow_listed_and_legacy_defaults_remain_safe():
    presentation = project_legal_answer_presentation(
        {
            "answer": "Nội dung cũ vẫn hiển thị.",
            "question": "Quy định chung là gì?",
            "rag_trace": {
                "answer_route": "general_legal",
                "pipeline_version": "answer-v3",
                "data_release_id": "release-018",
                "index_fingerprint": "f" * 64,
                "validity_snapshot": "validity-20260813",
                "secret": "must-not-be-projected",
            },
        }
    )

    rendered = presentation.model_dump(mode="json", exclude_none=True)
    assert presentation.answer_status == "source_gap"
    assert presentation.answer_route == "general_legal"
    assert presentation.pipeline_version == "answer-v3"
    assert presentation.data_release_id == "release-018"
    assert presentation.index_fingerprint == "f" * 64
    assert presentation.validity_snapshot == "validity-20260813"
    assert "secret" not in str(rendered)


def test_ask_response_contract_exposes_projection_without_removing_legacy_fields():
    response = AskResponse(
        answer="Nội dung hiện hành có căn cứ.",
        question="Điều 16 quy định gì?",
        answer_status="grounded",
        evidence_count=1,
        citations=[_citation()],
        answer_route="exact_article",
        pipeline_version="answer-v3",
        data_release_id="release-018",
        validity_snapshot="v" * 64,
    )

    projected = finalize_legal_answer_response(response)
    payload = projected.model_dump(mode="json", exclude_none=True)

    assert payload["answer"] == response.answer
    assert payload["citations"] == response.citations
    assert payload["presentation_version"] == PRESENTATION_VERSION
    assert payload["answer_route"] == "exact_article"
    assert payload["pipeline_version"] == "answer-v3"
    assert payload["data_release_id"] == "release-018"
    assert payload["validity_snapshot"] == "v" * 64
    assert payload["sections"]["short_answer"] == response.answer
    assert payload["sections"]["legal_bases"] == response.citations


def test_provider_cannot_add_or_replace_backend_owned_form_and_citation_identity():
    citation = _citation()
    form = _form()
    artifacts = project_backend_owned_answer_artifacts(
        citations=[citation],
        recommended_forms=[form],
        provider_output={
            "citations": [
                {
                    "law_number": "BỊA/2026/AI",
                    "source_url": "https://invented.example/law",
                }
            ],
            "recommended_forms": [
                {
                    "form_id": "mau-bia",
                    "download_url": "https://invented.example/form",
                }
            ],
        },
    )

    assert artifacts == {
        "citations": [citation],
        "recommended_forms": [form],
    }
    assert "invented.example" not in str(artifacts)


def test_projection_deduplicates_near_identical_items_across_sections():
    presentation = project_legal_answer_presentation(
        {
            "answer": "Trả lời ngắn gọn.",
            "question": "Câu hỏi lặp",
            "procedure_detail": {
                "steps": [
                    "1. Nộp hồ sơ tại bộ phận một cửa",
                    "• Nộp hồ sơ tại bộ phận một cửa",
                ],
                "documents_required": [
                    "- Tờ khai theo mẫu",
                    "Tờ khai theo mẫu",
                ],
            },
            "source_gap": [
                "Chưa có thông tin lệ phí",
                "Chưa có thông tin lệ phí",
            ],
            "clarifying_questions": [
                "Bạn đang cư trú tại xã nào?",
                "Bạn đang cư trú tại xã nào?",
            ],
        }
    )

    assert len(presentation.sections.actions) == 1
    assert presentation.sections.actions[0] == "1. Nộp hồ sơ tại bộ phận một cửa"
    assert len(presentation.sections.dossier) == 1
    assert presentation.sections.dossier[0] == "- Tờ khai theo mẫu"
    assert len(presentation.sections.caveats) == 1
    assert presentation.sections.caveats[0] == "Chưa có thông tin lệ phí"
    assert len(presentation.sections.clarifying_questions) == 1
    assert presentation.sections.clarifying_questions[0] == "Bạn đang cư trú tại xã nào?"

