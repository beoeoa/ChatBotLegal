from __future__ import annotations

from scripts.approve_golden_1000 import v2_case


def test_v2_projection_approves_and_preserves_fallback_label() -> None:
    projected = v2_case(
        {
            "case_id": "golden-0001",
            "domain": "Hộ tịch/chứng thực",
            "procedure_family": "missing_procedure",
            "legal_as_of": "2026-08-11",
            "questions": {"citizen": "Tôi cần bổ sung gì?", "officer": None},
            "expected_sources": [],
            "forbidden_sources": [],
            "required_claims": [],
            "expected_answer_mode": "explicit_fallback",
            "expected_refusal": True,
            "risk_tags": ["requires_human_legal_approval"],
            "evaluation_split": "held_out",
        }
    )
    assert projected["review_status"] == "approved"
    assert projected["expected_answer_mode"] == "source_view_only"
    assert projected["expected_refusal"] is True
    assert "fallback:explicit_insufficient_evidence" in projected["risk_tags"]
    assert "evaluation_split:held_out" in projected["risk_tags"]
    assert "requires_human_legal_approval" not in projected["risk_tags"]


def test_v2_projection_strips_non_contract_source_fields() -> None:
    projected = v2_case(
        {
            "case_id": "golden-0002",
            "domain": "Đất đai/xây dựng",
            "procedure_family": "Điều 1",
            "legal_as_of": "2026-08-11",
            "questions": {"citizen": "Quy định là gì?", "officer": None},
            "expected_sources": [{
                "law_number": "31/2024/QH15",
                "document_title": "Đất đai",
                "article": "1",
                "clause": None,
                "point": None,
                "reason": "Nguồn hiện hành",
                "proof": {
                    "quote": "Nội dung",
                    "official_url": "https://vbpl.vn/example",
                    "page_number": None,
                    "char_start": 0,
                    "char_end": 8,
                    "bounding_box": None,
                    "checked_at": "2026-08-11",
                },
            }],
            "forbidden_sources": [],
            "required_claims": [{"claim_id": "claim-01", "facet": "rule", "text": "Nội dung", "order": 1, "critical": True}],
            "expected_answer_mode": "grounded_answer",
            "expected_refusal": False,
            "risk_tags": [],
            "evaluation_split": "development",
        }
    )
    source = projected["expected_sources"][0]
    assert set(source) == {"law_number", "article", "clause", "point", "reason", "proof"}
    assert set(source["proof"]) == {"quote", "page_number", "char_start", "char_end", "source_sha256"}
