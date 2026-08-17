from __future__ import annotations

from scripts.evaluate_feature016_gate_b import evaluate_gate_b


def test_gate_b_separates_implementation_from_legal_approval():
    observed = {
        "case_id": "web-001",
        "question_hash": "a" * 64,
        "question": "Đăng ký thường trú cần hồ sơ gì?",
    }
    baseline = {"cases": [observed] * 100}
    # Stable unique IDs are required by the real builder; this unit fixture
    # focuses only on the approval boundary.
    baseline["cases"] = [
        {**observed, "case_id": f"web-{index:03d}"}
        for index in range(1, 101)
    ]
    golden = {
        "cases": [
            {
                "case_id": f"web-{index:03d}",
                "legal_as_of": "2026-08-10",
                "review_status": "proposed",
            }
            for index in range(1, 101)
        ]
    }
    result = evaluate_gate_b(baseline, golden)
    assert result["implementation_pass"] is True
    assert result["legal_acceptance_ready"] is False
    assert result["status"] == "implementation_pass_legal_review_pending"
