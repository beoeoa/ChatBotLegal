from __future__ import annotations

import json
from pathlib import Path

from scripts.validate_legal_golden_v2 import validate_dataset


SCHEMA = (
    Path(__file__).parents[1]
    / "specs"
    / "016-legal-answer-trust-hardening"
    / "contracts"
    / "golden-case-v2.schema.json"
)


def _case(case_id: str = "case-001") -> dict:
    return {
        "case_id": case_id,
        "schema_version": "2.0",
        "domain": "Hộ tịch/chứng thực",
        "procedure_family": "marriage_registration",
        "legal_as_of": "2026-08-10",
        "questions": {"citizen": "Đăng ký kết hôn cần gì?", "officer": None},
        "expected_sources": [
            {
                "law_number": "60/2014/QH13",
                "article": "18",
                "clause": None,
                "point": None,
                "reason": "Nguồn bắt buộc",
                "proof": {
                    "quote": "Hai bên nam, nữ cùng có mặt khi đăng ký kết hôn.",
                    "page_number": 4,
                    "char_start": 10,
                    "char_end": 62,
                    "source_sha256": "a" * 64,
                },
            }
        ],
        "forbidden_sources": [],
        "required_claims": [
            {
                "claim_id": "presence",
                "facet": "condition",
                "text": "Hai bên cùng có mặt",
                "order": 1,
                "critical": True,
            }
        ],
        "expected_answer_mode": "grounded_answer",
        "expected_refusal": False,
        "risk_tags": ["procedure_confusion"],
        "review_status": "approved",
    }


def test_valid_approved_dataset_passes():
    payload = {"schema_version": "2.0", "cases": [_case()]}
    assert validate_dataset(payload, SCHEMA) == []


def test_validator_rejects_duplicate_question_and_bad_claim_order():
    first = _case("case-001")
    second = _case("case-002")
    second["required_claims"].append(
        {
            "claim_id": "documents",
            "facet": "documents",
            "text": "Tờ khai",
            "order": 3,
            "critical": True,
        }
    )
    errors = validate_dataset(
        {"schema_version": "2.0", "cases": [first, second]}, SCHEMA
    )
    assert any("duplicate question" in error for error in errors)
    assert any("contiguous" in error for error in errors)


def test_approved_case_needs_source_or_explicit_refusal():
    case = _case()
    case["expected_sources"] = []
    errors = validate_dataset({"schema_version": "2.0", "cases": [case]}, SCHEMA)
    assert any("expected source or explicit refusal" in error for error in errors)


def test_proof_offsets_must_be_coherent():
    case = _case()
    case["expected_sources"][0]["proof"]["char_end"] = 5
    errors = validate_dataset({"schema_version": "2.0", "cases": [case]}, SCHEMA)
    assert any("char_end" in error for error in errors)
