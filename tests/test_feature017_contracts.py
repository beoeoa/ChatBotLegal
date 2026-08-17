from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from api.form_governance_models import FormReleaseManifest, FormSyncStatus


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "specs" / "017-procedure-form-governance" / "contracts"


def test_golden_v3_and_release_contracts_are_strict_json_schemas():
    golden = json.loads((CONTRACTS / "golden-case-v3.schema.json").read_text(encoding="utf-8"))
    release = json.loads((CONTRACTS / "form-release-manifest.schema.json").read_text(encoding="utf-8"))
    assert golden["properties"]["schema_version"]["const"] == "3.0"
    assert golden["additionalProperties"] is False
    assert set(golden["properties"]["expected_answer_mode"]["enum"]) == {
        "grounded_answer",
        "verified_source_condensed",
        "source_view_only",
    }
    assert release["properties"]["schema_version"]["const"] == "form-release-v1"
    assert release["additionalProperties"] is False


def test_api_contract_keeps_source_approval_separate_from_release():
    text = (CONTRACTS / "form-catalog-api.md").read_text(encoding="utf-8")
    assert "approve-source" in text
    assert "attest" in text
    assert "activate" in text
    assert "never runtime eligible" in text


def test_release_manifest_and_sync_status_have_typed_domain_contracts():
    manifest = FormReleaseManifest(
        release_id="r1",
        version=1,
        legal_as_of=date(2026, 8, 11),
        source_snapshot_sha256="a" * 64,
        procedures=[],
        assets=[],
        bindings=[],
        aliases=[],
        coverage={"complete": False},
        build={"pipeline_version": "feature017-v1"},
    )
    assert manifest.schema_version == "form-release-v1"
    assert {item.value for item in FormSyncStatus} == {"pending", "projected", "failed"}
