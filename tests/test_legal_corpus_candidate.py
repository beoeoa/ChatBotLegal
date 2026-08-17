from __future__ import annotations

from datetime import date

import pytest

from api.legal_corpus_candidate import (
    canonical_sha256,
    classify_document,
    prepare_documents,
    required_source_blockers,
    select_candidate,
)


AS_OF = date(2026, 8, 14)


def _row(document_id: int, **overrides):
    row = {
        "document_id": document_id,
        "title": f"Quy định thủ tục hồ sơ thời hạn mẫu số {document_id}",
        "law_number": f"{document_id}/2026/NĐ-CP",
        "document_type": "Nghị định",
        "issuing_agency": "Chính phủ",
        "source_url": f"https://vbpl.vn/?ItemID={document_id}",
        "status": "active",
        "effective_date": "2026-01-01",
        "expired_date": None,
        "scope_reason": "central_commune_specific",
        "domain": "ho_tich_chung_thuc",
        "chunk_count": 4,
        "nonempty_chunk_count": 4,
        "eligible_chunk_count": 4,
        "relationship_count": 2,
        "chunk_ids": [document_id * 10 + offset for offset in range(4)],
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    ("overrides", "status", "reason"),
    [
        ({"expired_date": "2026-08-14"}, "expired", "expired_at_legal_as_of"),
        ({"scope_reason": "out_of_commune_scope"}, "out_of_scope", "out_of_commune_scope"),
        ({"scope_reason": "other_province"}, "out_of_scope", "other_province"),
        ({"scope_reason": "known_superseded"}, "superseded", "known_superseded"),
        ({"effective_date": "2026-09-01"}, "pending_review", "not_yet_effective"),
        ({"nonempty_chunk_count": 0}, "empty_or_corrupt", "no_usable_chunk_content"),
        ({"source_url": ""}, "metadata_unverified", "required_metadata_missing"),
    ],
)
def test_hard_filter_is_fail_closed(overrides, status, reason):
    actual_status, actual_reason, eligible, _ = classify_document(
        _row(1, **overrides), legal_as_of=AS_OF
    )
    assert (actual_status, actual_reason) == (status, reason)
    assert eligible is False


def test_missing_effective_date_is_repairable_not_fabricated():
    status, reason, eligible, fields = classify_document(
        _row(1, effective_date=None), legal_as_of=AS_OF
    )
    assert status == "metadata_repairable"
    assert reason == "official_metadata_repair_required"
    assert eligible is False
    assert fields == ["effective_date"]


def test_duplicate_identity_keeps_required_best_record():
    rows = [
        _row(1, law_number="60/2014/QH13", document_type="Luật"),
        _row(
            2,
            law_number="60-2014-QH13",
            document_type="Luật",
            nonempty_chunk_count=2,
        ),
    ]
    prepared = prepare_documents(
        rows, legal_as_of=AS_OF, required_law_numbers=["60/2014/QH13"]
    )
    assert prepared[0]["candidate_eligible"] is True
    assert prepared[1]["status"] == "duplicate"
    assert prepared[1]["canonical_duplicate_document_id"] == 1


def test_same_number_different_document_type_is_not_a_duplicate():
    prepared = prepare_documents(
        [
            _row(1, law_number="51/2010/QH12", document_type="Luật"),
            _row(2, law_number="51/2010/QH12", document_type="Nghị quyết"),
        ],
        legal_as_of=AS_OF,
    )
    assert [row["status"] for row in prepared] == ["eligible", "eligible"]


def test_selection_preserves_required_foundation_and_coverage_cells():
    rows = [
        _row(1, law_number="60/2014/QH13", document_type="Luật"),
        _row(2, document_type="Thông tư", domain="dat_dai_xay_dung"),
        _row(3, document_type="Thông tư", domain="cu_tru_an_ninh"),
        _row(4, document_type="Thông tư", domain="khieu_nai_to_cao_xu_phat"),
        _row(5, document_type="Thông tư", domain="an_sinh_y_te_giao_duc"),
        _row(6, document_type="Thông tư", domain="hanh_chinh_cong"),
        _row(7, document_type="Thông tư", domain="ho_tich_chung_thuc"),
        _row(8, document_type="Thông tư", domain="ho_tich_chung_thuc"),
    ]
    prepared = prepare_documents(
        rows, legal_as_of=AS_OF, required_law_numbers=["60/2014/QH13"]
    )
    kept, excluded, audit = select_candidate(
        prepared,
        target_count=7,
        coverage_per_cell=1,
        max_domain_share=0.5,
    )
    kept_ids = {row["document_id"] for row in kept}
    assert 1 in kept_ids
    assert len(kept) == 7
    assert len(excluded) == 1
    assert set(audit["summary"]["selected_domain_counts"]) == {
        "an_sinh_y_te_giao_duc",
        "cu_tru_an_ninh",
        "dat_dai_xay_dung",
        "hanh_chinh_cong",
        "ho_tich_chung_thuc",
        "khieu_nai_to_cao_xu_phat",
    }


def test_required_source_blocker_is_explicit():
    prepared = prepare_documents([_row(1)], legal_as_of=AS_OF)
    blockers = required_source_blockers(prepared, ["99/2099/QH99"])
    assert blockers == [
        {
            "law_number": "99/2099/QH99",
            "normalized_law_number": "992099QH99",
            "reason_code": "required_source_missing_or_ineligible",
            "observed_documents": [],
        }
    ]


def test_candidate_selection_is_deterministic():
    prepared = prepare_documents(
        [_row(index, document_type="Thông tư") for index in range(1, 10)],
        legal_as_of=AS_OF,
    )
    first = select_candidate(prepared, target_count=5, coverage_per_cell=1)
    second = select_candidate(list(reversed(prepared)), target_count=5, coverage_per_cell=1)
    first_projection = [row["document_id"] for row in first[0]]
    second_projection = [row["document_id"] for row in second[0]]
    assert first_projection == second_projection
    assert canonical_sha256(first_projection) == canonical_sha256(second_projection)
