from __future__ import annotations

from copy import deepcopy

import pytest

from api.form_governance_models import canonical_sha256
from scripts.rehearse_feature017_full_release import validate_candidate


def _candidate() -> dict:
    manifest = {
        "schema_version": "form-release-v1",
        "release_id": "release-test",
        "version": 1,
        "legal_as_of": "2026-08-11",
        "source_snapshot_sha256": "a" * 64,
        "previous_release_id": None,
        "procedures": [
            {
                "procedure_id": "p1",
                "name": "Thủ tục thử",
                "domain": "cu_tru_an_ninh",
                "official_source_url": "https://dichvucong.gov.vn/p1",
                "coverage_status": "released",
            }
        ],
        "assets": [
            {
                "form_id": "f1",
                "canonical_name": "Mẫu thử",
                "asset_kind": "file",
                "source_url": "https://vbpl.vn/f1.pdf",
                "source_checksum": "b" * 64,
                "audiences": ["citizen"],
                "coverage_status": "released",
            }
        ],
        "bindings": [
            {
                "binding_id": "b1",
                "procedure_id": "p1",
                "form_id": "f1",
                "requirement": "required",
                "audience": "citizen",
                "coverage_status": "released",
            }
        ],
        "aliases": [
            {"procedure_id": "p1", "alias": "làm thủ tục thử", "alias_kind": "natural"}
        ],
        "gaps": [],
        "exclusions": [],
        "coverage": {
            "procedure_total": 1,
            "procedure_decided": 1,
            "identity_total": 1,
            "identity_decided": 1,
            "binding_total": 1,
            "binding_decided": 1,
            "complete": True,
        },
        "build": {"pipeline_version": "test"},
    }
    return {
        "manifest": manifest,
        "manifest_sha256": canonical_sha256(manifest),
        "activation_allowed": False,
    }


def test_candidate_validation_preserves_non_activation() -> None:
    result = validate_candidate(_candidate())
    assert result["gate_report"]["passed"] is True


def test_candidate_validation_rejects_tampering_and_activation() -> None:
    tampered = _candidate()
    tampered["manifest"]["release_id"] = "tampered"
    with pytest.raises(ValueError, match="MANIFEST_HASH_MISMATCH"):
        validate_candidate(tampered)

    activatable = deepcopy(_candidate())
    activatable["activation_allowed"] = True
    with pytest.raises(ValueError, match="ACTIVATION_MUST_BE_FALSE"):
        validate_candidate(activatable)
