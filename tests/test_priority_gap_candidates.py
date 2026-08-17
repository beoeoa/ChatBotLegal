import hashlib

import pytest

from api.priority_gap_candidates import (
    PriorityGapCandidateError,
    build_priority_gap_candidates,
    merge_priority_gap_candidates,
)


def _asset(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.7\n" + b"x" * 1024)
    return {
        "path": path,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_page_url": "https://haiphong.gov.vn/source",
        "source_download_url": f"https://cdn.haiphong.gov.vn/{name}",
        "source_package_sha256": "a" * 64,
        "source_pages_zero_based": [1],
    }


def test_four_priority_groups_are_pending_and_never_auto_approved(tmp_path):
    assets = {
        "land": _asset(tmp_path, "land.pdf"),
        "foreign_birth": _asset(tmp_path, "birth.pdf"),
        "foreign_marriage": _asset(tmp_path, "marriage.pdf"),
        "construction": _asset(tmp_path, "construction.pdf"),
    }

    records = build_priority_gap_candidates(assets, project_root=tmp_path)

    assert len(records) == 4
    assert all(record["review_status"] == "candidate_pending_review" for record in records)
    assert all(record["legal_review_status"] == "candidate_pending_review" for record in records)
    assert all(record["is_approved"] is False for record in records)
    by_group = {record["priority_group"]: record for record in records}
    assert by_group["foreign_birth"]["official_procedure_code"] == "2.000528"
    assert by_group["foreign_marriage"]["procedure_id"] == "dang_ky_ket_hon_nuoc_ngoai"
    assert by_group["foreign_marriage"]["official_procedure_code"] == "2.000806"
    assert by_group["construction"]["official_procedure_code"] == "1.009122"


def test_legacy_09_dk_is_quarantined_from_runtime_in_favor_of_reviewed_current_code(tmp_path):
    assets = {
        "land": _asset(tmp_path, "land.pdf"),
        "foreign_birth": _asset(tmp_path, "birth.pdf"),
        "foreign_marriage": _asset(tmp_path, "marriage.pdf"),
        "construction": _asset(tmp_path, "construction.pdf"),
    }
    land = {
        item["priority_group"]: item
        for item in build_priority_gap_candidates(assets, project_root=tmp_path)
    }["land_registration_change"]

    assert land["requested_legacy_form_code"] == "09/ĐK"
    assert land["form_code"] == "11/ĐK"
    assert land["legacy_form_status"] == "superseded_candidate_quarantined"
    assert land["runtime_eligible"] is False


def test_merge_is_idempotent_but_never_overwrites_a_reviewed_record():
    candidate = {
        "id": "priority-gap-land-registration-change",
        "review_status": "candidate_pending_review",
        "is_approved": False,
    }
    payload = {"records": []}
    assert len(merge_priority_gap_candidates(payload, [candidate])["records"]) == 1
    assert len(
        merge_priority_gap_candidates(
            merge_priority_gap_candidates(payload, [candidate]),
            [candidate],
        )["records"]
    ) == 1

    with pytest.raises(PriorityGapCandidateError, match="REVIEWED_CANDIDATE_IMMUTABLE"):
        merge_priority_gap_candidates(
            {
                "records": [
                    {
                        **candidate,
                        "review_status": "approved",
                        "is_approved": True,
                    }
                ]
            },
            [candidate],
        )
