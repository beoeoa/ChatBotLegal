from api.legal_replacement_candidates import project_replacement_candidates


def _observation(**overrides):
    value = {
        "law_number": "12/2020/NĐ-CP",
        "normalized_status": "replaced",
        "affecting_document_number": "34/2026/NĐ-CP",
        "source_url": "https://vbpl.vn/van-ban/34-2026-ND-CP",
        "source_kind": "vbpl",
        "identity_status": "exact",
        "evidence_status": "sufficient",
        "observed_at": "2026-08-09T02:00:00+00:00",
    }
    value.update(overrides)
    return value


def test_explicit_official_replacement_is_ranked_but_never_auto_approved():
    projection = project_replacement_candidates([_observation()])

    assert projection["status"] == "candidates_found"
    assert projection["requires_admin_review"] is True
    assert projection["candidates"] == [
        {
            "law_number": "34/2026/NĐ-CP",
            "confidence": "verified",
            "evidence_level": "explicit_official_relationship",
            "relation_status": "pending_admin_review",
            "source_url": "https://vbpl.vn/van-ban/34-2026-ND-CP",
            "source_kind": "vbpl",
            "observed_at": "2026-08-09T02:00:00+00:00",
            "basis": "official_affecting_document_number",
        }
    ]


def test_metadata_without_explicit_relation_is_not_promoted_to_legal_replacement():
    projection = project_replacement_candidates(
        [_observation(affecting_document_number=None)]
    )

    assert projection["status"] == "no_explicit_candidate"
    assert projection["candidates"] == []
    assert projection["reason_codes"] == ["replacement_relation_not_observed"]


def test_conflicting_or_unofficial_evidence_is_fail_closed():
    projection = project_replacement_candidates(
        [
            _observation(evidence_status="conflicting"),
            _observation(source_url="https://example.com/not-official"),
        ]
    )

    assert projection["status"] == "no_explicit_candidate"
    assert projection["candidates"] == []
    assert set(projection["reason_codes"]) == {
        "replacement_evidence_conflicting",
        "replacement_source_not_official",
    }

