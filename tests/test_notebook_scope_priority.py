from __future__ import annotations

from copy import deepcopy

from open_notebook.domain.notebook import _apply_scope_priority


def test_legacy_scope_priority_uses_hierarchy_without_multiplying_score():
    results = [
        {
            "id": "local",
            "chunk_id": 1,
            "document_type": "Quyết định",
            "issuing_agency": "UBND thành phố Hải Phòng",
            "scope": "haiphong",
            "score": 1.0,
        },
        {
            "id": "law",
            "chunk_id": 2,
            "document_type": "Luật",
            "issuing_agency": "Quốc hội",
            "scope": "central",
            "score": 0.1,
        },
    ]
    original = deepcopy(results)

    ranked = _apply_scope_priority(results, "haiphong")

    assert [item["id"] for item in ranked] == ["law", "local"]
    assert [item["score"] for item in ranked] == [0.1, 1.0]
    assert results == original


def test_legacy_boost_factor_is_accepted_only_for_signature_compatibility():
    results = [
        {
            "id": "local",
            "document_type": "Quyết định",
            "issuing_agency": "UBND thành phố Hải Phòng",
            "scope": "haiphong",
            "score": 1.0,
        }
    ]

    ranked = _apply_scope_priority(results, "haiphong", boost_factor=99.0)

    assert ranked[0]["score"] == 1.0
    assert ranked[0]["authority_level"] == "provincial_people_committee"


def test_legacy_search_applies_hierarchy_without_a_scope_filter():
    ranked = _apply_scope_priority(
        [
            {
                "id": "local",
                "document_type": "Quyết định",
                "issuing_agency": "UBND thành phố Hải Phòng",
                "score": 1.0,
            },
            {
                "id": "law",
                "document_type": "Luật",
                "issuing_agency": "Quốc hội",
                "score": 0.1,
            },
        ],
        None,
    )

    assert [item["id"] for item in ranked] == ["law", "local"]
