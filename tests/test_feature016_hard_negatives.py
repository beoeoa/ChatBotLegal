from __future__ import annotations

import json

from scripts.build_feature016_hard_negatives import build_hard_negatives


def test_hard_negative_builder_uses_only_approved_explicit_forbidden_sources():
    golden = {
        "schema_version": "2.0",
        "cases": [
            {
                "case_id": "case-1",
                "review_status": "approved",
                "domain": "Cư trú",
                "legal_as_of": "2026-08-10",
                "questions": {"citizen": "Điều kiện đăng ký thường trú?"},
                "expected_sources": [
                    {"law_number": "68/2020/QH14", "article": "20"}
                ],
                "forbidden_sources": [
                    {"law_number": "68/2020/QH14", "article": "24"}
                ],
            },
            {
                "case_id": "case-2",
                "review_status": "proposed",
                "domain": "Cư trú",
                "questions": {"citizen": "Ca chưa duyệt"},
                "expected_sources": [{"law_number": "X", "article": "1"}],
                "forbidden_sources": [{"law_number": "Y", "article": "2"}],
            },
        ],
    }

    first = build_hard_negatives(golden)
    second = build_hard_negatives(json.loads(json.dumps(golden)))

    assert first == second
    assert first["summary"] == {
        "approved_case_count": 1,
        "example_count": 1,
        "positive_source_count": 1,
        "hard_negative_source_count": 1,
    }
    assert first["examples"][0]["negative_origin"] == "explicit_forbidden_source"
    assert first["examples"][0]["hard_negatives"][0]["article"] == "24"

