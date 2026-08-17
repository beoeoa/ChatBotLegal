from __future__ import annotations

import copy

import pytest

from scripts.approve_feature017_golden_v3 import (
    approve_dataset,
    approved_checksum,
)
from scripts.build_feature017_golden_v3 import DOMAINS, build_cases


def _release():
    procedures = []
    assets = []
    bindings = []
    for index, domain in enumerate(DOMAINS):
        procedure_id = f"p{index}"
        form_id = f"f{index}"
        procedures.append(
            {
                "procedure_id": procedure_id,
                "name": f"Thủ tục {index}",
                "domain": domain,
                "coverage_status": "released",
                "official_source_url": f"https://dichvucong.gov.vn/p{index}",
            }
        )
        assets.append(
            {
                "form_id": form_id,
                "canonical_name": f"Mẫu {index}",
                "asset_kind": "file",
                "source_url": f"https://vbpl.vn/f{index}.pdf",
                "source_checksum": str(index) * 64,
                "coverage_status": "released",
                "audiences": ["citizen"],
            }
        )
        bindings.append(
            {
                "binding_id": f"b{index}",
                "procedure_id": procedure_id,
                "form_id": form_id,
                "requirement": "required",
                "audience": "citizen",
                "coverage_status": "released",
            }
        )
    return {
        "schema_version": "form-release-v1",
        "release_id": "r1",
        "version": 1,
        "legal_as_of": "2026-08-11",
        "source_snapshot_sha256": "a" * 64,
        "previous_release_id": None,
        "procedures": procedures,
        "assets": assets,
        "bindings": bindings,
        "aliases": [],
        "coverage": {
            "procedure_total": 5,
            "procedure_decided": 5,
            "identity_total": 5,
            "identity_decided": 5,
            "binding_total": 5,
            "binding_decided": 5,
            "complete": True,
        },
        "build": {"pipeline_version": "test"},
    }


def _proposed():
    release = _release()
    return release, {
        "schema_version": "feature017-golden-v3-dataset-v1",
        "review_status": "proposed",
        "approved_checksum": None,
        "cases": build_cases(release),
    }


def test_approval_freezes_all_cases_without_mutating_ground_truth() -> None:
    release, proposed = _proposed()
    before = copy.deepcopy(proposed)

    approved = approve_dataset(
        proposed,
        release,
        approved_by="workspace_user",
        approved_at="2026-08-12T00:00:00+00:00",
        approval_statement="approve all",
    )

    assert proposed == before
    assert approved["review_status"] == "approved"
    assert approved["approved_checksum"] == approved_checksum(approved)
    assert len(approved["cases"]) == 1000
    assert all(item["review_status"] == "approved" for item in approved["cases"])
    for original, frozen in zip(proposed["cases"], approved["cases"], strict=True):
        assert {k: v for k, v in original.items() if k != "review_status"} == {
            k: v for k, v in frozen.items() if k != "review_status"
        }


def test_approval_checksum_is_independent_of_approval_timestamp() -> None:
    release, proposed = _proposed()
    first = approve_dataset(
        proposed,
        release,
        approved_by="workspace_user",
        approved_at="2026-08-12T00:00:00+00:00",
        approval_statement="approve all",
    )
    second = approve_dataset(
        proposed,
        release,
        approved_by="workspace_user",
        approved_at="2026-08-12T01:00:00+00:00",
        approval_statement="approve all",
    )
    assert first["approved_checksum"] == second["approved_checksum"]


def test_approval_rejects_release_manifest_mismatch() -> None:
    release, proposed = _proposed()
    tampered = copy.deepcopy(release)
    tampered["version"] = 2
    with pytest.raises(ValueError, match="FEATURE017_GOLDEN_RELEASE_MISMATCH"):
        approve_dataset(
            proposed,
            tampered,
            approved_by="workspace_user",
            approved_at="2026-08-12T00:00:00+00:00",
            approval_statement="approve all",
        )
