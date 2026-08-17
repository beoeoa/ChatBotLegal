from api.legal_claim_validation import validate_structured_claims


def _evidence():
    return {
        "evidence-1": {
            "request_id": "request-1",
            "issue_id": "issue-1",
            "content": "Thời hạn giải quyết là 03 ngày làm việc kể từ ngày nhận đủ hồ sơ.",
            "document_title": "Nghị định thử nghiệm",
            "law_number": "01/2026/NĐ-CP",
            "article_number": "12",
            "clause_number": "2",
            "effective_status": "active",
            "scope": "central",
            "source_url": "https://example.gov.vn/source",
        }
    }


def test_claim_requires_exact_issue_bound_evidence_and_verbatim_support_quote():
    claims = [{
        "issue_id": "issue-1",
        "claim_text": "Thời hạn giải quyết là 03 ngày làm việc.",
        "claim_type": "deadline",
        "evidence_id": "evidence-1",
        "support_quote": "Thời hạn giải quyết là 03 ngày làm việc",
    }]

    result = validate_structured_claims(
        request_id="request-1", claims=claims, evidence_by_id=_evidence()
    )

    assert len(result.accepted) == 1
    assert not result.rejected


def test_numeric_claim_is_rejected_when_number_is_not_in_support_quote():
    claims = [{
        "issue_id": "issue-1",
        "claim_text": "Thời hạn giải quyết là 05 ngày làm việc.",
        "claim_type": "deadline",
        "evidence_id": "evidence-1",
        "support_quote": "Thời hạn giải quyết là 03 ngày làm việc",
    }]

    result = validate_structured_claims(
        request_id="request-1", claims=claims, evidence_by_id=_evidence()
    )

    assert not result.accepted
    assert result.rejected[0].reason == "claim_value_not_supported"


def test_authority_claim_accepts_direct_assignment_with_official_text_artifact():
    quote = (
        "Trường hợp khu đất nhỏ hơn diện tích quy định thì giao Ủy ban nhân "
        "thành phố Hải Phòng xem xét, quyết định từng trường hợp cụ thể."
    )
    evidence = {
        "evidence-authority": {
            **_evidence()["evidence-1"],
            "issue_id": "issue-authority",
            "content": quote,
        }
    }
    claims = [{
        "issue_id": "issue-authority",
        "claim_text": quote,
        "claim_type": "authority",
        "evidence_id": "evidence-authority",
        "support_quote": quote,
    }]

    result = validate_structured_claims(
        request_id="request-1",
        claims=claims,
        evidence_by_id=evidence,
        issue_context="cơ quan nào xem xét khu đất nhỏ hơn ngưỡng",
        issue_intent="authority",
    )

    assert len(result.accepted) == 1
    assert not result.rejected


def test_claim_from_another_issue_is_rejected():
    claims = [{
        "issue_id": "issue-2",
        "claim_text": "Thời hạn giải quyết là 03 ngày làm việc.",
        "claim_type": "deadline",
        "evidence_id": "evidence-1",
        "support_quote": "Thời hạn giải quyết là 03 ngày làm việc",
    }]

    result = validate_structured_claims(
        request_id="request-1", claims=claims, evidence_by_id=_evidence()
    )

    assert not result.accepted
    assert result.rejected[0].reason == "request_or_issue_mismatch"


def test_claim_from_another_request_is_rejected():
    evidence = _evidence()
    evidence["evidence-1"]["request_id"] = "request-other"
    claims = [{
        "issue_id": "issue-1",
        "claim_text": "Thời hạn giải quyết là 03 ngày làm việc.",
        "claim_type": "deadline",
        "evidence_id": "evidence-1",
        "support_quote": "Thời hạn giải quyết là 03 ngày làm việc",
    }]
    result = validate_structured_claims(
        request_id="request-1", claims=claims, evidence_by_id=evidence
    )
    assert not result.accepted
    assert result.rejected[0].reason == "request_or_issue_mismatch"


def test_claim_is_rejected_when_support_quote_does_not_exist_verbatim():
    claims = [{
        "issue_id": "issue-1",
        "claim_text": "Thời hạn giải quyết là 03 ngày làm việc.",
        "claim_type": "deadline",
        "evidence_id": "evidence-1",
        "support_quote": "Thời hạn giải quyết là 03 ngày kể từ ngày tiếp nhận",
    }]
    result = validate_structured_claims(
        request_id="request-1", claims=claims, evidence_by_id=_evidence()
    )
    assert not result.accepted
    assert result.rejected[0].reason == "support_quote_not_in_evidence"


def test_claim_is_rejected_when_article_or_clause_conflicts_with_metadata():
    claims = [{
        "issue_id": "issue-1",
        "claim_text": "Theo Điều 12 khoản 3, thời hạn là 03 ngày làm việc.",
        "claim_type": "deadline",
        "evidence_id": "evidence-1",
        "support_quote": "Thời hạn giải quyết là 03 ngày làm việc",
    }]
    result = validate_structured_claims(
        request_id="request-1", claims=claims, evidence_by_id=_evidence()
    )
    assert not result.accepted
    assert result.rejected[0].reason == "legal_reference_not_supported"


def test_non_numeric_fee_claim_requires_fee_language_in_quote():
    evidence = _evidence()
    evidence["evidence-1"]["content"] += " Hồ sơ được tiếp nhận tại bộ phận một cửa."
    claims = [{
        "issue_id": "issue-1",
        "claim_text": "Thủ tục này được miễn lệ phí.",
        "claim_type": "fee",
        "evidence_id": "evidence-1",
        "support_quote": "Hồ sơ được tiếp nhận tại bộ phận một cửa",
    }]
    result = validate_structured_claims(
        request_id="request-1", claims=claims, evidence_by_id=evidence
    )
    assert not result.accepted
    assert result.rejected[0].reason == "claim_value_not_supported"


def test_claim_text_must_be_materially_supported_by_support_quote():
    evidence = _evidence()
    evidence["evidence-1"][
        "content"
    ] = "Cơ quan quản lý được khai thác dữ liệu về chỗ ở hợp pháp."
    claims = [{
        "issue_id": "issue-1",
        "claim_text": "Gia đình chưa đủ điều kiện đăng ký thường trú.",
        "claim_type": "condition",
        "evidence_id": "evidence-1",
        "support_quote": "Cơ quan quản lý được khai thác dữ liệu về chỗ ở hợp pháp",
    }]

    result = validate_structured_claims(
        request_id="request-1",
        claims=claims,
        evidence_by_id=evidence,
    )

    assert not result.accepted
    assert result.rejected[0].reason == "claim_text_not_supported_by_quote"


def test_exact_quote_is_rejected_when_subject_does_not_match_issue():
    evidence = _evidence()
    quote = (
        "To chuc trong nuoc dau tu xay dung nha o duoc cap "
        "Giay chung nhan quyen su dung dat."
    )
    evidence["evidence-1"]["content"] = quote
    claims = [{
        "issue_id": "issue-1",
        "claim_text": quote,
        "claim_type": "condition",
        "evidence_id": "evidence-1",
        "support_quote": quote,
    }]

    result = validate_structured_claims(
        request_id="request-1",
        claims=claims,
        evidence_by_id=evidence,
        issue_context=(
            "Ca nhan chua co so do xin cap Giay chung nhan "
            "quyen su dung dat lan dau"
        ),
    )

    assert not result.accepted
    assert result.rejected[0].reason == (
        "claim_issue_mismatch:subject_mismatch_organization"
    )


def test_exact_quote_is_rejected_when_transaction_cutoff_does_not_match():
    evidence = _evidence()
    quote = (
        "Giay to chuyen nhuong quyen su dung dat lap truoc ngay "
        "15 thang 10 nam 1993."
    )
    evidence["evidence-1"]["content"] = quote
    claims = [{
        "issue_id": "issue-1",
        "claim_text": quote,
        "claim_type": "condition",
        "evidence_id": "evidence-1",
        "support_quote": quote,
    }]

    result = validate_structured_claims(
        request_id="request-1",
        claims=claims,
        evidence_by_id=evidence,
        issue_context="Ca nhan mua dat bang giay viet tay nam 2009",
    )

    assert not result.accepted
    assert result.rejected[0].reason == (
        "claim_issue_mismatch:time_condition_mismatch"
    )


def test_document_claim_requires_document_list_language_in_quote():
    evidence = _evidence()
    quote = "Noi dung tren Giay chung nhan the hien theo Mau so 04/DK-GCN."
    evidence["evidence-1"]["content"] = quote
    claims = [{
        "issue_id": "issue-1",
        "claim_text": quote,
        "claim_type": "documents",
        "evidence_id": "evidence-1",
        "support_quote": quote,
    }]

    result = validate_structured_claims(
        request_id="request-1",
        claims=claims,
        evidence_by_id=evidence,
    )

    assert not result.accepted
    assert result.rejected[0].reason == "claim_type_not_supported_by_quote"


def test_generic_deadline_for_another_procedure_is_not_direct_support():
    evidence = _evidence()
    quote = "Thoi han thuc hien thu tuc nay khong qua 07 ngay lam viec."
    evidence["evidence-1"]["content"] = quote
    claims = [{
        "issue_id": "issue-1",
        "claim_text": quote,
        "claim_type": "deadline",
        "evidence_id": "evidence-1",
        "support_quote": quote,
    }]

    result = validate_structured_claims(
        request_id="request-1",
        claims=claims,
        evidence_by_id=evidence,
        issue_context=(
            "Thoi han giai quyet cap Giay chung nhan lan dau cho mua dat "
            "giay viet tay"
        ),
    )

    assert not result.accepted
    assert result.rejected[0].reason == (
        "claim_issue_mismatch:deadline_not_first_registration_specific"
    )
