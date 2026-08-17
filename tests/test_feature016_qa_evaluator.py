from __future__ import annotations

from api.legal_answer_trust_evaluator import evaluate_qa_run


def _golden() -> dict:
    return {
        "case_id": "case-001",
        "questions": {"citizen": "Đăng ký kết hôn cần gì?"},
        "expected_sources": [
            {"law_number": "60/2014/QH13", "article": "18"}
        ],
        "forbidden_sources": [
            {"law_number": "88/2001/NĐ-CP", "article": None}
        ],
        "required_claims": [
            {"claim_id": "documents", "text": "Tờ khai", "order": 1, "critical": True},
            {"claim_id": "presence", "text": "cùng có mặt", "order": 2, "critical": True},
        ],
        "expected_answer_mode": "grounded_answer",
        "expected_refusal": False,
    }


def _run() -> dict:
    return {
        "answer": "Nộp Tờ khai và hai bên phải cùng có mặt.",
        "answer_mode": "grounded_answer",
        "grounding_status": "fully_grounded",
        "citations": [
            {
                "law_number": "60/2014/QH13",
                "article_number": "18",
                "effective_status": "effective",
                "verification_level": "content_quote",
            }
        ],
        "claim_validation": [
            {"claim_id": "documents", "status": "verified"},
            {"claim_id": "presence", "status": "verified"},
        ],
    }


def test_correct_sources_claims_and_order_pass():
    result = evaluate_qa_run(_golden(), _run())
    assert result["passed"] is True
    assert all(result["rubrics"].values())


def test_has_sources_cannot_make_wrong_source_pass():
    run = _run()
    run["has_sources"] = True
    run["citations"] = [
        {
            "law_number": "99/2099/TT-ABC",
            "article_number": "18",
            "effective_status": "effective",
            "verification_level": "content_quote",
        }
    ]
    result = evaluate_qa_run(_golden(), run)
    assert result["passed"] is False
    assert result["rubrics"]["correct_source"] is False


def test_expired_or_metadata_only_evidence_cannot_support_current_claim():
    run = _run()
    run["citations"][0]["effective_status"] = "expired_or_repealed"
    run["citations"][0]["verification_level"] = "metadata_only"
    result = evaluate_qa_run(_golden(), run)
    assert result["passed"] is False
    assert result["rubrics"]["current_validity"] is False
    assert result["rubrics"]["no_fabrication"] is False


def test_missing_critical_claim_or_wrong_order_fails():
    run = _run()
    run["answer"] = "Hai bên cùng có mặt; sau đó nộp Tờ khai."
    run["claim_validation"] = [{"claim_id": "presence", "status": "verified"}]
    result = evaluate_qa_run(_golden(), run)
    assert result["passed"] is False
    assert result["rubrics"]["critical_claim_coverage"] is False
    assert result["rubrics"]["claim_order"] is False


def test_expected_source_view_refusal_passes_without_legal_claim():
    golden = _golden()
    golden["expected_sources"] = []
    golden["required_claims"] = []
    golden["expected_answer_mode"] = "source_view_only"
    golden["expected_refusal"] = True
    run = {
        "answer": "Chưa đủ căn cứ để kết luận. Bạn có thể xem nguồn tham khảo.",
        "answer_mode": "source_view_only",
        "grounding_status": "insufficient_evidence",
        "citations": [],
        "claim_validation": [],
    }
    assert evaluate_qa_run(golden, run)["passed"] is True
