"""Validate Feature 018 design contracts without network or database access."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FEATURE = ROOT / "specs" / "018-production-release-readiness"
CONTRACTS = FEATURE / "contracts"


class Feature018ContractError(ValueError):
    """Raised when a release-readiness contract is incomplete or inconsistent."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Feature018ContractError(
            f"FEATURE018_CONTRACT_JSON_INVALID:{path.name}"
        ) from exc
    if not isinstance(payload, dict):
        raise Feature018ContractError(
            f"FEATURE018_CONTRACT_ROOT_INVALID:{path.name}"
        )
    return payload


def validate(feature_dir: Path = FEATURE) -> dict[str, object]:
    required_docs = (
        "spec.md",
        "plan.md",
        "research.md",
        "data-model.md",
        "quickstart.md",
        "tasks.md",
    )
    missing = [name for name in required_docs if not (feature_dir / name).is_file()]
    if missing:
        raise Feature018ContractError(
            "FEATURE018_REQUIRED_ARTIFACT_MISSING:" + ",".join(missing)
        )

    schema_path = feature_dir / "contracts" / "capability-registry.schema.json"
    schema = _load_json(schema_path)
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise Feature018ContractError("FEATURE018_SCHEMA_DRAFT_INVALID")
    if schema.get("additionalProperties") is not False:
        raise Feature018ContractError("FEATURE018_SCHEMA_NOT_STRICT")
    item_schema = (
        schema.get("properties", {})
        .get("capabilities", {})
        .get("items", {})
    )
    required_fields = set(item_schema.get("required") or [])
    expected_fields = {
        "capability_id",
        "kind",
        "path",
        "owner",
        "roles",
        "data_source",
        "sensitivity",
        "audit",
        "retention",
        "sla",
        "feature_flag",
        "tests",
        "rollback",
        "status",
    }
    if required_fields != expected_fields:
        raise Feature018ContractError("FEATURE018_CAPABILITY_FIELDS_INCOMPLETE")

    retrieval_contracts = (
        "legal-retrieval-metrics-v2.schema.json",
        "legal-retrieval-source-gap-v1.schema.json",
        "production-holdout-aggregate-receipt-v1.schema.json",
        "production-holdout-envelope-v1.schema.json",
        "retrieval-eval-suite-v1.schema.json",
        "retrieval-chunk-manifest-v2.schema.json",
        "retrieval-serving-manifest-v3.schema.json",
        "retrieval-source-gap-review-v2.schema.json",
        "retrieval-metadata-attestation-v1.schema.json",
    )
    for contract_name in retrieval_contracts:
        contract = _load_json(feature_dir / "contracts" / contract_name)
        if contract.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise Feature018ContractError(
                f"FEATURE018_RETRIEVAL_SCHEMA_DRAFT_INVALID:{contract_name}"
            )

    holdout_contract = _load_json(
        feature_dir / "contracts" / "production-holdout-envelope-v1.schema.json"
    )
    if holdout_contract.get("additionalProperties") is not False:
        raise Feature018ContractError("FEATURE018_HOLDOUT_ENVELOPE_NOT_STRICT")
    holdout_properties = holdout_contract.get("properties", {})
    if holdout_properties.get("case_count", {}).get("const") != 500:
        raise Feature018ContractError("FEATURE018_HOLDOUT_COUNT_INVALID")
    forbidden_holdout_fields = {
        "cases", "question", "expected_answer", "positive_source_groups",
        "issue_groups", "records", "misses",
    }
    if forbidden_holdout_fields & set(holdout_properties):
        raise Feature018ContractError("FEATURE018_HOLDOUT_ENVELOPE_LEAKS_CASE_CONTENT")

    chunk_contract = _load_json(
        feature_dir / "contracts" / "retrieval-chunk-manifest-v2.schema.json"
    )
    if chunk_contract.get("properties", {}).get("schema_version", {}).get("const") != (
        "legal-retrieval-chunk-manifest-v2"
    ):
        raise Feature018ContractError("FEATURE018_CHUNK_MANIFEST_SCHEMA_VERSION_INVALID")
    chunk_required = set(chunk_contract.get("required") or [])
    if not {
        "schema_version",
        "release_id",
        "source_snapshot_sha256",
        "manifest_sha256",
        "approved",
        "legal_review_attestation",
        "chunk_count",
        "vector_count",
        "chunks",
    }.issubset(chunk_required):
        raise Feature018ContractError("FEATURE018_CHUNK_MANIFEST_HEADER_INCOMPLETE")
    chunk_schema = chunk_contract.get("$defs", {}).get("chunk", {})
    chunk_required_fields = set(chunk_schema.get("required") or [])
    if not {
        "chunk_revision_id",
        "release_id",
        "document_id",
        "article_id",
        "structural_path",
        "content",
        "content_sha256",
        "source_content_sha256",
        "passage_sha256",
        "token_count",
        "eligible",
        "serving_state",
        "document_serving_state",
    }.issubset(chunk_required_fields):
        raise Feature018ContractError("FEATURE018_CHUNK_MANIFEST_CHUNK_FIELDS_INCOMPLETE")
    if chunk_schema.get("properties", {}).get("token_count", {}).get("maximum") != 512:
        raise Feature018ContractError("FEATURE018_CHUNK_MANIFEST_TOKEN_BUDGET_INVALID")

    serving_contract = _load_json(
        feature_dir / "contracts" / "retrieval-serving-manifest-v3.schema.json"
    )
    if serving_contract.get("properties", {}).get("schema_version", {}).get("const") != (
        "legal-serving-manifest-v3"
    ):
        raise Feature018ContractError("FEATURE018_SERVING_MANIFEST_SCHEMA_VERSION_INVALID")
    serving_required = set(serving_contract.get("required") or [])
    if not {
        "release_id",
        "source_snapshot_sha256",
        "chunk_manifest_sha256",
        "current_collection",
        "temporal_collection",
        "exact_lexical_index",
        "documents",
        "activation_performed",
        "active_pointer_changed",
    }.issubset(serving_required):
        raise Feature018ContractError("FEATURE018_SERVING_MANIFEST_FIELDS_INCOMPLETE")
    if serving_contract.get("properties", {}).get("activation_performed", {}).get("const") is not False:
        raise Feature018ContractError("FEATURE018_SERVING_MANIFEST_MUTATION_CONTRACT_INVALID")

    vector_contract_name = "legal-serving-manifest-v3.schema.json"
    vector_contract = _load_json(feature_dir / "contracts" / vector_contract_name)
    if vector_contract.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise Feature018ContractError("FEATURE018_VECTOR_SCHEMA_DRAFT_INVALID")
    if vector_contract.get("additionalProperties") is not False:
        raise Feature018ContractError("FEATURE018_VECTOR_SCHEMA_NOT_STRICT")
    vector_required = set(vector_contract.get("required") or [])
    required_vector_provenance = {
        "model_artifact_fingerprint",
        "tokenizer_fingerprint",
        "embedding_recipe_fingerprint",
        "passage_recipe_fingerprint",
        "splitter_fingerprint",
        "source_snapshot_fingerprint",
        "dependency_lock_fingerprint",
        "vector_content_fingerprint",
        "quality_policy_version",
    }
    if not required_vector_provenance.issubset(vector_required):
        raise Feature018ContractError("FEATURE018_VECTOR_PROVENANCE_INCOMPLETE")
    if "embedding_fingerprint" in vector_required:
        raise Feature018ContractError("FEATURE018_VECTOR_FINGERPRINT_AMBIGUOUS")

    api_contract = (
        feature_dir / "contracts" / "release-readiness-api.md"
    ).read_text(encoding="utf-8")
    contract_markers = (
        "presentation_version",
        "queue-status",
        "claim-next",
        "expiring_30",
        "index-manifests/active",
        "sensitive-view",
        "postgres_shadow",
    )
    missing_markers = [marker for marker in contract_markers if marker not in api_contract]
    if missing_markers:
        raise Feature018ContractError(
            "FEATURE018_API_CONTRACT_INCOMPLETE:" + ",".join(missing_markers)
        )

    spec_text = (feature_dir / "spec.md").read_text(encoding="utf-8")
    if "[NEEDS CLARIFICATION" in spec_text:
        raise Feature018ContractError("FEATURE018_SPEC_HAS_CLARIFICATION")

    return {
        "status": "passed",
        "feature": feature_dir.name,
        "artifacts": list(required_docs),
        "contracts": [
            "capability-registry.schema.json",
            "release-readiness-api.md",
            *retrieval_contracts,
            vector_contract_name,
        ],
        "capability_required_fields": sorted(required_fields),
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
