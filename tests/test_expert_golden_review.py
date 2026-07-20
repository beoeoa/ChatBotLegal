from scripts.audit_expert_golden_review import audit
from scripts.prepare_expert_golden_review import build_records


def test_generator_creates_two_pending_roles_without_fake_approval():
    records = build_records({
        "questions": [{
            "id": "case-1",
            "domain": "ho_tich_chung_thuc",
            "question_citizen": "Câu hỏi của người dân?",
            "question_officer": "Câu hỏi của cán bộ?",
            "expected_citations": ["Nguồn máy đề xuất"],
        }]
    })
    assert [item["role"] for item in records] == ["citizen", "officer"]
    assert all(item["expert_review_status"] == "pending" for item in records)
    assert all(item["expert_name"] is None for item in records)
    assert all(item["expected_documents"] is None for item in records)
    assert records[0]["machine_proposal"]["expected_citations"] == ["Nguồn máy đề xuất"]


def test_audit_refuses_pending_or_incomplete_review_set():
    report = audit({"records": [{"review_id": "case-1:citizen", "expert_review_status": "pending"}]})
    assert report["pass"] is False
    assert report["status_counts"]["pending"] == 1
