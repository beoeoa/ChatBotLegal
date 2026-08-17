from __future__ import annotations

import itertools
import json
from copy import deepcopy
from pathlib import Path

import pytest

from api.legal_hierarchy import (
    POLICY_VERSION,
    classify_legal_authority,
    hierarchy_summary,
    public_authority_projection,
    rank_legal_evidence,
)

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "legal_hierarchy_cases.json"
)


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text(encoding="utf-8")))
def test_classification_matrix_uses_type_issuer_and_number(case):
    classification = classify_legal_authority(case)

    assert classification.level == case["expected_level"]
    assert classification.rank == case["expected_rank"]
    assert classification.policy_version == POLICY_VERSION


def test_content_and_title_cannot_invent_authority():
    classification = classify_legal_authority(
        {
            "document_type": "Tài liệu",
            "issuing_agency": "",
            "document_title": "Luật rất quan trọng của Quốc hội",
            "content": "Nghị định và quyết định của Chính phủ",
        }
    )

    assert classification.level == "authority_unverified"
    assert classification.confidence == "unverified"


def test_hard_authority_order_does_not_mutate_relevance_scores():
    candidates = [
        {
            "chunk_id": 1,
            "law_number": "01/2026/QĐ-UBND",
            "document_type": "Quyết định",
            "issuing_agency": "UBND thành phố Hải Phòng",
            "scope": "haiphong",
            "score": 9.9,
        },
        {
            "chunk_id": 2,
            "law_number": "64/2025/QH15",
            "document_type": "Luật",
            "issuing_agency": "Quốc hội",
            "scope": "central",
            "score": 0.1,
        },
        {
            "chunk_id": 3,
            "law_number": "78/2025/NĐ-CP",
            "document_type": "Nghị định",
            "issuing_agency": "Chính phủ",
            "scope": "central",
            "score": 0.2,
        },
    ]
    original = deepcopy(candidates)

    ranked = rank_legal_evidence(candidates, scope_filter="haiphong")

    assert [item["chunk_id"] for item in ranked] == [2, 3, 1]
    assert [item["score"] for item in ranked] == [0.1, 0.2, 9.9]
    assert candidates == original


def test_unverified_candidate_never_outranks_verified_authority_by_score():
    ranked = rank_legal_evidence(
        [
            {
                "chunk_id": 1,
                "document_type": "Tài liệu",
                "score": 100.0,
                "content": "Luật Quốc hội",
            },
            {
                "chunk_id": 2,
                "document_type": "Thông tư",
                "issuing_agency": "Bộ Tư pháp",
                "score": 0.01,
            },
        ]
    )

    assert [item["chunk_id"] for item in ranked] == [2, 1]
    assert ranked[1]["authority_reason_code"] == "AUTHORITY_METADATA_INSUFFICIENT"


def test_same_issuer_and_rank_prefers_later_issued_document():
    ranked = rank_legal_evidence(
        [
            {
                "chunk_id": 1,
                "document_type": "Quyết định",
                "issuing_agency": "UBND thành phố Hải Phòng",
                "law_number": "01/2024/QĐ-UBND",
                "issued_date": "2024-01-01",
                "score": 0.99,
            },
            {
                "chunk_id": 2,
                "document_type": "Quyết định",
                "issuing_agency": "Ủy ban nhân dân TP. Hải Phòng",
                "law_number": "02/2025/QĐ-UBND",
                "issued_date": "2025-01-01",
                "score": 0.10,
            },
        ]
    )

    assert [item["chunk_id"] for item in ranked] == [2, 1]


def test_different_issuers_at_same_tier_use_relevance_not_recency():
    ranked = rank_legal_evidence(
        [
            {
                "chunk_id": 1,
                "document_type": "Thông tư",
                "issuing_agency": "Bộ Tư pháp",
                "issued_date": "2024-01-01",
                "score": 0.9,
            },
            {
                "chunk_id": 2,
                "document_type": "Thông tư",
                "issuing_agency": "Bộ Tài chính",
                "issued_date": "2026-01-01",
                "score": 0.2,
            },
        ]
    )

    assert [item["chunk_id"] for item in ranked] == [1, 2]


def test_order_is_stable_across_input_permutations():
    candidates = [
        {
            "chunk_id": index,
            "document_type": doc_type,
            "issuing_agency": issuer,
            "score": score,
        }
        for index, doc_type, issuer, score in (
            (1, "Nghị định", "Chính phủ", 0.2),
            (2, "Luật", "Quốc hội", 0.1),
            (3, "Thông tư", "Bộ Tư pháp", 0.9),
            (4, "Tài liệu", "", 1.0),
        )
    ]
    expected = [2, 1, 3, 4]

    for permutation in itertools.permutations(candidates):
        assert [
            item["chunk_id"] for item in rank_legal_evidence(list(permutation))
        ] == expected


def test_same_issuer_date_constraint_is_stable_with_another_issuer():
    candidates = [
        {
            "chunk_id": 1,
            "document_type": "Thông tư",
            "issuing_agency": "Bộ X",
            "issued_date": "2026-01-01",
            "scope": "central",
            "score": 0.1,
        },
        {
            "chunk_id": 2,
            "document_type": "Thông tư",
            "issuing_agency": "Bộ X",
            "issued_date": "2025-01-01",
            "scope": "haiphong",
            "score": 0.9,
        },
        {
            "chunk_id": 3,
            "document_type": "Thông tư",
            "issuing_agency": "Bộ Y",
            "issued_date": "2024-01-01",
            "scope": "haiphong",
            "score": 0.8,
        },
    ]

    outputs = {
        tuple(
            item["chunk_id"]
            for item in rank_legal_evidence(
                list(permutation), scope_filter="haiphong"
            )
        )
        for permutation in itertools.permutations(candidates)
    }

    assert outputs == {(3, 1, 2)}


def test_summary_and_public_projection_separate_internal_details():
    ranked = rank_legal_evidence(
        [
            {
                "chunk_id": 1,
                "document_type": "Luật",
                "issuing_agency": "Quốc hội",
                "score": 0.5,
            },
            {
                "chunk_id": 2,
                "document_type": "Tài liệu",
                "score": 0.9,
                "legal_precedence_exception": True,
            },
        ]
    )

    summary = hierarchy_summary(ranked)
    public = public_authority_projection(ranked[0])

    assert summary == {
        "policy_version": POLICY_VERSION,
        "ordering": "authority>same_issuer_later>scope_fit>relevance>stable_key",
        "verified_count": 1,
        "unverified_count": 1,
        "review_required_count": 1,
    }
    assert set(public) == {"level", "label", "policy_version"}
    assert "authority_rank" not in public
    assert "authority_reason_code" not in public
