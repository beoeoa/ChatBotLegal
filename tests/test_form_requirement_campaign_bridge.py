from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _write(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest() -> dict:
    return {
        "legal_as_of": "2026-07-29",
        "summary": {
            "scoped_procedure_count": 3,
            "required_form_identity_planning_count": 3,
            "release_status_counts": {
                "APPROVED_RUNTIME": 1,
                "PENDING_LEGAL_REVIEW": 2,
            },
        },
        "form_identities": [
            {
                "identity_id": "identity-approved",
                "identity_status": "CONFIRMED_CODE_AND_INSTRUMENT",
                "release_status": "APPROVED_RUNTIME",
                "form_code": "M01",
                "issuing_instrument": "01/2026/TT-TEST",
                "canonical_titles": ["Mẫu đã duyệt"],
                "domains": ["test-domain"],
                "procedure_ids": ["procedure-1"],
                "official_source_pages": ["https://vbpl.vn/test-1"],
                "distribution_variants": ["paper_or_file"],
                "approved_catalog_form_ids": ["form-approved"],
            },
            {
                "identity_id": "identity-multi",
                "identity_status": "CONFIRMED_CODE_AND_INSTRUMENT",
                "release_status": "PENDING_LEGAL_REVIEW",
                "form_code": "M02",
                "issuing_instrument": "02/2026/TT-TEST",
                "canonical_titles": ["Mẫu dùng chung"],
                "domains": ["test-domain"],
                "procedure_ids": ["procedure-1", "procedure-2"],
                "official_source_pages": ["https://vbpl.vn/test-2"],
                "distribution_variants": ["paper_or_file"],
                "approved_catalog_form_ids": [],
            },
            {
                "identity_id": "identity-eform",
                "identity_status": "OFFICIAL_EFORM_IDENTITY",
                "release_status": "PENDING_LEGAL_REVIEW",
                "form_code": None,
                "issuing_instrument": None,
                "canonical_titles": ["Biểu mẫu điện tử"],
                "domains": ["test-domain"],
                "procedure_ids": ["procedure-3"],
                "official_source_pages": [
                    "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/procedure-3"
                ],
                "distribution_variants": ["interactive_eform"],
                "approved_catalog_form_ids": [],
            },
        ],
    }


def test_bridge_reconciles_catalog_and_preserves_multi_bindings(tmp_path: Path) -> None:
    from scripts.bridge_form_requirements_to_campaign import bridge_manifest

    manifest_path = tmp_path / "manifest.json"
    source_path = tmp_path / "source.json"
    catalog_path = tmp_path / "catalog.json"
    attestations_path = tmp_path / "attestations.json"
    manifest_sha = _write(manifest_path, _manifest())
    source_sha = _write(source_path, {"procedures": [1, 2, 3]})
    _write(
        catalog_path,
        {
            "forms": [
                {
                    "form_id": "form-approved",
                    "approved": True,
                    "runtime_eligible": True,
                    "procedure_ids": ["procedure-1"],
                }
            ]
        },
    )
    _write(attestations_path, {"attestations": []})

    result = bridge_manifest(
        manifest_path=manifest_path,
        source_snapshot_path=source_path,
        catalog_path=catalog_path,
        attestations_path=attestations_path,
        legal_as_of="2026-07-29",
        expected_manifest_sha256=manifest_sha,
        expected_source_snapshot_sha256=source_sha,
    )

    assert result["summary"] == {
        "target_identities": 3,
        "approved_identities": 1,
        "pending_identities": 2,
        "pending_procedure_bindings": 3,
        "paper_or_file_pending": 1,
        "interactive_eform_pending": 1,
    }
    paper_rows = result["campaign_inventory"]["verified_data_gaps"]
    assert {row["procedure_id"] for row in paper_rows} == {
        "procedure-1",
        "procedure-2",
    }
    assert {row["requirement_identity_id"] for row in paper_rows} == {
        "identity-multi"
    }
    assert result["campaign_inventory"]["eform_requirements"][0][
        "requirement_identity_id"
    ] == "identity-eform"
    assert all(row["approved"] is False for row in paper_rows)
    assert all(row["runtime_eligible"] is False for row in paper_rows)


def test_bridge_refuses_manifest_or_source_checksum_drift(tmp_path: Path) -> None:
    from scripts.bridge_form_requirements_to_campaign import bridge_manifest

    manifest_path = tmp_path / "manifest.json"
    source_path = tmp_path / "source.json"
    catalog_path = tmp_path / "catalog.json"
    attestations_path = tmp_path / "attestations.json"
    _write(manifest_path, _manifest())
    _write(source_path, {"procedures": []})
    _write(catalog_path, {"forms": []})
    _write(attestations_path, {"attestations": []})

    with pytest.raises(ValueError, match="MANIFEST_CHECKSUM_DRIFT"):
        bridge_manifest(
            manifest_path=manifest_path,
            source_snapshot_path=source_path,
            catalog_path=catalog_path,
            attestations_path=attestations_path,
            legal_as_of="2026-07-29",
            expected_manifest_sha256="0" * 64,
            expected_source_snapshot_sha256=hashlib.sha256(
                source_path.read_bytes()
            ).hexdigest(),
        )


def test_bridge_reconciles_a_newly_approved_manifest_snapshot(
    tmp_path: Path,
) -> None:
    from scripts.bridge_form_requirements_to_campaign import bridge_manifest

    manifest = _manifest()
    manifest["summary"]["release_status_counts"] = {
        "APPROVED_RUNTIME": 2,
        "PENDING_LEGAL_REVIEW": 1,
    }
    manifest["form_identities"][1]["release_status"] = "APPROVED_RUNTIME"
    manifest["form_identities"][1]["approved_catalog_form_ids"] = [
        "form-newly-approved"
    ]

    manifest_path = tmp_path / "manifest.json"
    source_path = tmp_path / "source.json"
    catalog_path = tmp_path / "catalog.json"
    attestations_path = tmp_path / "attestations.json"
    manifest_sha = _write(manifest_path, manifest)
    source_sha = _write(source_path, {"procedures": [1, 2, 3]})
    _write(
        catalog_path,
        {
            "forms": [
                {
                    "form_id": "form-approved",
                    "approved": True,
                    "runtime_eligible": True,
                },
                {
                    "form_id": "form-newly-approved",
                    "approved": True,
                    "runtime_eligible": True,
                },
            ]
        },
    )
    _write(attestations_path, {"attestations": [{"attestation_id": "a-1"}]})

    result = bridge_manifest(
        manifest_path=manifest_path,
        source_snapshot_path=source_path,
        catalog_path=catalog_path,
        attestations_path=attestations_path,
        legal_as_of="2026-07-29",
        expected_manifest_sha256=manifest_sha,
        expected_source_snapshot_sha256=source_sha,
    )

    assert result["summary"]["approved_identities"] == 2
    assert result["summary"]["pending_identities"] == 1
    assert result["summary"]["target_identities"] == 3
