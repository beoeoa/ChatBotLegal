from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from api.legal_answer_trust_log import (
    build_public_trust_projection,
    build_structured_qa_run,
)
from api.models import AskResponse


def test_structured_log_keeps_answer_only_and_omits_admin_trace():
    case = {
        "case_id": "web-001",
        "domain": "Hộ tịch/chứng thực",
        "legal_as_of": "2026-08-10",
        "questions": {"citizen": "Đăng ký kết hôn cần gì?"},
    }
    response = {
        "question": case["questions"]["citizen"],
        "answer": "Hai bên nộp tờ khai và cùng có mặt.",
        "answer_mode": "normal",
        "grounding_status": "fully_grounded",
        "citations": [],
        "claim_validation": [],
        "rag_trace": {"private_chunk_id": "secret"},
        "trace_id": "trace-1",
        "has_sources": True,
    }
    result = build_structured_qa_run(
        run_id="run-1",
        case=case,
        role="citizen",
        response=response,
        latency_stages={"end_to_end_ms": 123},
        versions={"app": "test"},
        evaluation={"passed": False, "reason_codes": ["failed_correct_source"]},
    )

    assert result["answer"] == response["answer"]
    assert result["answer_mode"] == "grounded_answer"
    assert result["evaluation"]["passed"] is False
    assert "rag_trace" not in result
    assert "has_sources" not in result
    assert "private_chunk_id" not in str(result)
    assert len(result["question_hash"]) == 64
    schema_path = (
        Path(__file__).parents[1]
        / "specs"
        / "016-legal-answer-trust-hardening"
        / "contracts"
        / "structured-qa-run.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert list(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(result)
    ) == []


def test_structured_log_rejects_accessibility_dom_as_answer():
    with pytest.raises(ValueError, match="DOM/accessibility"):
        build_structured_qa_run(
            run_id="run-1",
            case={
                "case_id": "web-001",
                "domain": "x",
                "legal_as_of": "2026-08-10",
                "questions": {"citizen": "Câu hỏi"},
            },
            role="citizen",
            response={
                "answer": 'region "Ask Response":\n- textbox "Enter your question"',
                "trace_id": "trace-1",
            },
            latency_stages={},
            versions={},
            evaluation={"passed": False},
        )


def test_public_trust_projection_is_additive_and_model_validates():
    fields = build_public_trust_projection(
        trace={
            "intent": {"domain": "ho_tich", "confidence": 0.95},
            "validity_decision": {"state": "effective"},
            "fallback_reason": None,
        },
        citations=[{"verification_level": "content_quote"}],
        answer_mode="normal",
        provider_label="local",
    )
    response = AskResponse(
        answer="Nội dung đã kiểm tra.",
        question="Câu hỏi",
        legal_as_of=date(2026, 8, 10),
        **fields,
    )
    payload = response.model_dump(mode="json")
    assert payload["generation_provenance"]["provider_label"] == "local"
    assert payload["citation_verification_summary"]["content_quote"] == 1
    assert payload["retrieval_decision_summary"]["strict_validity"] is True
    assert payload["intent"]["domain"] == "ho_tich"
    assert payload["validity_decision"]["state"] == "effective"
