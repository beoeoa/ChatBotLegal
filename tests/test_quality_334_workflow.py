from scripts.audit_334_quality_results import audit as audit_results
from scripts.build_reviewed_legal_matrices import build
from scripts.mark_golden_needs_revalidation import mark


def test_matrices_use_only_approved_expert_records():
    payload = {"records": [
        {
            "case_id": "approved",
            "domain": "ho_tich_chung_thuc",
            "legal_as_of": "2026-07-14",
            "expert_name": "Chuyên gia A",
            "review_version": "1.0",
            "expert_review_status": "approved",
            "expected_authority": "UBND cấp xã",
            "expected_documents": ["60/2014/QH13"],
            "expected_articles": ["Điều 16"],
        },
        {"case_id": "pending", "expert_review_status": "pending"},
    ]}
    authority, procedure = build(payload)
    assert [row["case_id"] for row in authority["rows"]] == ["approved"]
    assert [row["case_id"] for row in procedure["rows"]] == ["approved"]


def test_changed_law_marks_only_approved_matching_cases():
    payload = {"records": [
        {"review_id": "a", "expert_review_status": "approved", "expected_documents": ["60/2014/QH13"]},
        {"review_id": "b", "expert_review_status": "pending", "expected_documents": ["60/2014/QH13"]},
        {"review_id": "c", "expert_review_status": "approved", "expected_documents": ["31/2024/QH15"]},
    ]}
    assert mark(payload, {"60/2014/QH13"}) == 1
    assert payload["records"][0]["expert_review_status"] == "needs_revalidation"
    assert payload["records"][1]["expert_review_status"] == "pending"
    assert payload["records"][2]["expert_review_status"] == "approved"


def test_release_gate_refuses_missing_live_and_pending_expert_data():
    report = audit_results({"results": []}, {"records": []}, {})
    assert report["pass"] is False
    assert report["live"]["completed"] == 0
    assert report["asset_health"]["pass"] is False
