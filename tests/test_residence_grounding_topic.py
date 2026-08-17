from api.legal_section_grounding import (
    LegalIssue,
    evaluate_evidence_eligibility,
    plan_legal_issues,
)
from api.legal_structured_answer import ensure_required_facet_issues


def _source(content: str) -> dict:
    return {
        "source_id": "source-1",
        "request_id": "request-1",
        "issue_id": "issue-1",
        "domain": "cu_tru_an_ninh",
        "effective_status": "active",
        "official": True,
        "scope": "central",
        "source_url": "https://vanban.chinhphu.vn/example",
        "law_number": "58/2026/NĐ-CP",
        "article_number": "21",
        "document_title": (
            "Nghị định sửa đổi quy định về cư trú và căn cước"
        ),
        "content": content,
    }


def test_residence_topic_is_carried_into_every_split_facet():
    question = (
        "Gia đình tôi thuê nhà, có đăng ký thường trú hay tạm trú được không? "
        "Hãy nêu hồ sơ, nơi nộp, thời hạn, lệ phí và biểu mẫu."
    )

    issues = plan_legal_issues(question, max_issues=6)

    assert len(issues) > 1
    assert all("thuong tru" in issue.subject for issue in issues)
    assert all("tam tru" in issue.subject for issue in issues)
    assert all("thuong tru" in issue.query_text for issue in issues)


def test_residence_fact_fragments_do_not_consume_requested_facet_slots():
    question = (
        "Ngày 30/07/2026, vợ chồng tôi thuê căn hộ, hợp đồng có chữ ký "
        "chủ nhà nhưng chưa rõ diện tích. Có đăng ký thường trú hay tạm trú "
        "được không? Hãy nêu hồ sơ, nơi nộp, trình tự, thời hạn, lệ phí và "
        "biểu mẫu, căn cứ pháp lý."
    )

    issues = plan_legal_issues(question, max_issues=6)

    assert {issue.intent for issue in issues} >= {
        "condition",
        "authority",
        "procedure",
        "deadline",
        "fee",
        "rule",
    }
    assert all("hợp đồng có chữ ký" in issue.query_text for issue in issues)

    completed = ensure_required_facet_issues(
        question=question,
        issues=issues,
        required_sections=["conclusion", "conditions_or_rights"],
        max_issues=6,
    )
    rule_issue = next(issue for issue in completed if issue.intent == "rule")
    assert question in rule_issue.query_text


def test_residence_issue_rejects_same_domain_citizen_id_fee():
    issue = LegalIssue(
        issue_id="issue-1",
        request_id="request-1",
        query_text="lệ phí",
        subject="đăng ký thường trú tạm trú",
        domain="cu_tru_an_ninh",
        intent="fee",
    )

    rejected = evaluate_evidence_eligibility(
        _source(
            "Công dân thực hiện thanh toán lệ phí cập nhật thông tin "
            "trên thẻ căn cước."
        ),
        issue,
    )
    accepted = evaluate_evidence_eligibility(
        _source("Lệ phí đăng ký tạm trú được thực hiện theo quy định."),
        issue,
    )

    assert rejected.status == "excluded"
    assert rejected.reason == "wrong_procedure_topic"
    assert accepted.status == "accepted"


def test_identity_card_issue_rejects_residence_law_and_residence_only_content():
    issue = LegalIssue(
        issue_id="issue-2",
        request_id="request-1",
        query_text="Thủ tục cấp đổi thẻ căn cước và hồ sơ cần nộp",
        subject="cấp đổi thẻ căn cước",
        domain="cu_tru_an_ninh",
        intent="documents",
    )

    residence_law_source = {
        "source_id": "source-residence-law",
        "request_id": "request-1",
        "issue_id": "issue-2",
        "domain": "cu_tru_an_ninh",
        "effective_status": "active",
        "official": True,
        "scope": "central",
        "source_url": "https://vbpl.vn/example",
        "law_number": "68/2020/QH14",
        "article_number": "20",
        "document_title": "Luật Cư trú",
        "content": "Điều kiện đăng ký thường trú tại chỗ ở hợp pháp do thuê, mượn, ở nhờ.",
    }

    residence_only_source = {
        "source_id": "source-residence-only",
        "request_id": "request-1",
        "issue_id": "issue-2",
        "domain": "cu_tru_an_ninh",
        "effective_status": "active",
        "official": True,
        "scope": "central",
        "source_url": "https://vbpl.vn/example2",
        "law_number": "62/2021/TT-BCA",
        "article_number": "5",
        "document_title": "Thông tư quy định về cư trú",
        "content": "Thành phần hồ sơ đăng ký tạm trú bao gồm tờ khai thay đổi thông tin cư trú.",
    }

    identity_source = {
        "source_id": "source-identity",
        "request_id": "request-1",
        "issue_id": "issue-2",
        "domain": "cu_tru_an_ninh",
        "effective_status": "active",
        "official": True,
        "scope": "central",
        "source_url": "https://vbpl.vn/example3",
        "law_number": "26/2023/QH15",
        "article_number": "23",
        "document_title": "Luật Căn cước",
        "content": "Thành phần hồ sơ cấp đổi thẻ căn cước bao gồm giấy tờ xác nhận thông tin và thẻ căn cước cũ.",
    }

    decision_residence_law = evaluate_evidence_eligibility(residence_law_source, issue)
    decision_residence_only = evaluate_evidence_eligibility(residence_only_source, issue)
    decision_identity = evaluate_evidence_eligibility(identity_source, issue)

    assert decision_residence_law.status == "excluded"
    assert decision_residence_law.reason == "wrong_procedure_topic"
    assert decision_residence_only.status == "excluded"
    assert decision_residence_only.reason == "wrong_procedure_topic"
    assert decision_identity.status == "accepted"


def test_general_query_without_residence_intent_rejects_residence_law():
    issue = LegalIssue(
        issue_id="issue-3",
        request_id="request-1",
        query_text="Thủ tục xác nhận tình trạng hôn nhân cần những giấy tờ gì",
        subject="xác nhận tình trạng hôn nhân",
        domain="civil_status",
        intent="documents",
    )

    residence_source = {
        "source_id": "source-residence",
        "request_id": "request-1",
        "issue_id": "issue-3",
        "domain": "cu_tru_an_ninh",
        "effective_status": "active",
        "official": True,
        "scope": "central",
        "source_url": "https://vbpl.vn/example",
        "law_number": "68/2020/QH14",
        "article_number": "10",
        "document_title": "Luật Cư trú",
        "content": "Quyền và nghĩa vụ của công dân về cư trú.",
    }

    decision = evaluate_evidence_eligibility(residence_source, issue)
    assert decision.status == "excluded"

