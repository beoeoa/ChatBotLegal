from __future__ import annotations

from datetime import date

from api.legal_problem_map import build_legal_intent
from api.legal_section_grounding import LegalIssue, evaluate_evidence_eligibility


def test_residence_registration_and_deletion_are_distinct_families():
    register = build_legal_intent(
        "Đăng ký thường trú tại nhà thuê cần hồ sơ gì?",
        legal_as_of=date(2026, 8, 10),
    )
    delete = build_legal_intent(
        "Xóa đăng ký thường trú trong trường hợp nào?",
        legal_as_of=date(2026, 8, 10),
    )
    assert register.procedure_family == "permanent_residence_registration"
    assert delete.procedure_family == "permanent_residence_deletion"
    assert register.requested_facets == ["documents"]
    assert register.state == "resolved"


def test_complaint_denunciation_and_enforcement_do_not_collapse():
    complaint = build_legal_intent("Thời hạn giải quyết khiếu nại là bao lâu?")
    denunciation = build_legal_intent("Thời hạn giải quyết tố cáo là bao lâu?")
    enforcement = build_legal_intent(
        "Thi hành quyết định xử phạt vi phạm hành chính thế nào?"
    )
    sanction = build_legal_intent("Thủ tục xử phạt vi phạm hành chính thế nào?")
    assert complaint.procedure_family == "complaint_resolution"
    assert denunciation.procedure_family == "denunciation_resolution"
    assert enforcement.procedure_family == "sanction_decision_enforcement"
    assert sanction.procedure_family == "administrative_sanction"


def test_mixed_procedure_question_requires_clarification():
    intent = build_legal_intent(
        "Tôi muốn đăng ký thường trú hay xóa đăng ký thường trú thì làm sao?"
    )
    assert intent.state == "ambiguous_needs_clarification"
    assert "multiple_procedure_families" in intent.ambiguity_reasons


def _source(*, procedure_family: str, content: str) -> dict:
    return {
        "source_id": "source-1",
        "request_id": "request-1",
        "issue_id": "issue-1",
        "domain": "cu_tru_an_ninh",
        "effective_status": "active",
        "official": True,
        "scope": "central",
        "source_url": "https://vbpl.vn/official",
        "law_number": "68/2020/QH14",
        "article_number": "24",
        "document_title": "Luật Cư trú",
        "procedure_family": procedure_family,
        "supported_facets": ["documents"],
        "content": content,
    }


def test_wrong_procedure_family_is_hard_excluded_before_generation():
    issue = LegalIssue(
        issue_id="issue-1",
        request_id="request-1",
        query_text="Hồ sơ đăng ký thường trú gồm gì?",
        subject="đăng ký thường trú",
        domain="cu_tru_an_ninh",
        intent="documents",
        procedure_family="permanent_residence_registration",
    )
    wrong = evaluate_evidence_eligibility(
        _source(
            procedure_family="permanent_residence_deletion",
            content="Hồ sơ xóa đăng ký thường trú gồm tài liệu chứng minh.",
        ),
        issue,
    )
    right = evaluate_evidence_eligibility(
        _source(
            procedure_family="permanent_residence_registration",
            content="Hồ sơ đăng ký thường trú gồm tờ khai thay đổi thông tin cư trú.",
        ),
        issue,
    )
    assert wrong.status == "excluded"
    assert wrong.reason == "wrong_procedure_family"
    assert right.status == "accepted"
