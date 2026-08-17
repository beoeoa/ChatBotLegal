from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_feature018_contracts import (
    Feature018ContractError,
    validate,
)


ROOT = Path(__file__).resolve().parents[1]
FEATURE = ROOT / "specs" / "018-production-release-readiness"


def test_feature018_contracts_are_complete_and_strict():
    result = validate()
    assert result["status"] == "passed"
    assert result["feature"] == "018-production-release-readiness"
    assert result["contracts"] == [
        "capability-registry.schema.json",
        "release-readiness-api.md",
            "legal-retrieval-metrics-v2.schema.json",
            "legal-retrieval-source-gap-v1.schema.json",
            "production-holdout-aggregate-receipt-v1.schema.json",
            "production-holdout-envelope-v1.schema.json",
            "retrieval-eval-suite-v1.schema.json",
            "retrieval-chunk-manifest-v2.schema.json",
            "retrieval-serving-manifest-v3.schema.json",
            "retrieval-source-gap-review-v2.schema.json",
            "retrieval-metadata-attestation-v1.schema.json",
            "legal-serving-manifest-v3.schema.json",
    ]


def test_retrieval_quality_contracts_bind_denominators_and_non_mutation():
    metrics = json.loads(
        (FEATURE / "contracts" / "legal-retrieval-metrics-v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    gaps = json.loads(
        (FEATURE / "contracts" / "legal-retrieval-source-gap-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert {
        "answer_required_count",
        "expected_refusal_count",
        "correct_refusal_rate",
        "issue_recall_at_10",
        "all_required_sources_coverage",
    } <= set(metrics["required"])
    assert gaps["properties"]["candidate_collection_mutated"]["const"] is False
    assert gaps["properties"]["active_pointer_changed"]["const"] is False


def test_capability_contract_requires_release_ownership_fields():
    schema = json.loads(
        (FEATURE / "contracts" / "capability-registry.schema.json").read_text(
            encoding="utf-8"
        )
    )
    required = set(schema["properties"]["capabilities"]["items"]["required"])
    assert {
        "owner",
        "roles",
        "data_source",
        "audit",
        "retention",
        "tests",
        "rollback",
        "status",
    } <= required
    assert schema["additionalProperties"] is False


def test_validator_fails_when_required_artifact_is_missing(tmp_path: Path):
    feature = tmp_path / "feature"
    feature.mkdir()
    with pytest.raises(
        Feature018ContractError,
        match="FEATURE018_REQUIRED_ARTIFACT_MISSING",
    ):
        validate(feature)
