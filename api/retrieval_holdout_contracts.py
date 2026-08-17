"""Fail-closed custody contracts for the sealed Retrieval Golden holdout.

The public repository may contain only a checksum envelope for the 500-case
production holdout. Questions, expected sources, issue labels and per-case
results remain in an independently controlled bundle outside the workspace.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping

from api.retrieval_release_contracts import DOMAINS


HOLDOUT_CASE_COUNT = 500
HOLDOUT_DOMAIN_COUNT = 100
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "answer",
        "case_id",
        "case_ids",
        "cases",
        "dataset_errors",
        "expected_answer",
        "expected_refusal",
        "hard_negative_sources",
        "issue_groups",
        "misses",
        "positive_source_groups",
        "question",
        "questions",
        "records",
        "required_source_group_ids",
    }
)
METRIC_KEYS = (
    "case_count",
    "answer_required_count",
    "expected_refusal_count",
    "recall_at_10",
    "mrr_at_10",
    "top5_rate",
    "correct_refusal_rate",
    "candidate_recall_at_50",
    "issue_count",
    "issue_recall_at_10",
    "all_required_sources_coverage",
    "multi_issue_case_count",
    "multi_issue_all_required_sources_coverage",
    "exact_law_article_case_count",
    "exact_law_article_lookup_recall_at_50",
    "exact_law_article_final_recall_at_10",
    "outside_manifest_count",
    "invalid_temporal_count",
    "errors",
)
DOMAIN_METRIC_KEYS = (
    "case_count",
    "answer_required_count",
    "expected_refusal_count",
    "recall_at_10",
    "mrr_at_10",
    "correct_refusal_rate",
)


def _forbidden_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in FORBIDDEN_PUBLIC_KEYS:
                found.add(str(key))
            found.update(_forbidden_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(_forbidden_keys(child))
    return found


def public_artifact_forbidden_keys(value: Any) -> set[str]:
    """Return any per-case or answer-bearing keys found recursively."""

    return _forbidden_keys(value)


def validate_public_holdout_envelope(
    envelope: Mapping[str, Any], *, require_unused: bool = False
) -> list[str]:
    """Validate public evidence without ever accepting hidden case content."""

    errors = [f"forbidden_public_key:{key}" for key in sorted(_forbidden_keys(envelope))]
    if envelope.get("schema_version") != "production-holdout-envelope-v1":
        errors.append("holdout_envelope_schema_version_mismatch")
    if envelope.get("split") != "production-holdout":
        errors.append("holdout_split_mismatch")
    if int(envelope.get("case_count") or 0) != HOLDOUT_CASE_COUNT:
        errors.append(f"holdout_case_count:{envelope.get('case_count')}!={HOLDOUT_CASE_COUNT}")
    counts = envelope.get("domain_counts") or {}
    if set(counts) != set(DOMAINS):
        errors.append("holdout_domain_set_mismatch")
    for domain in DOMAINS:
        if int(counts.get(domain) or 0) != HOLDOUT_DOMAIN_COUNT:
            errors.append(f"holdout_domain_count:{domain}:{counts.get(domain)}!={HOLDOUT_DOMAIN_COUNT}")
    if sum(int(value or 0) for value in counts.values()) != HOLDOUT_CASE_COUNT:
        errors.append("holdout_domain_partition_mismatch")
    if int(envelope.get("reviewer_approval_count") or 0) != HOLDOUT_CASE_COUNT:
        errors.append("holdout_reviewer_approval_incomplete")
    for field in (
        "content_sha256",
        "case_ids_sha256",
        "source_snapshot_sha256",
        "manifest_sha256",
        "quota_policy_sha256",
        "quota_attestation_sha256",
        "cross_split_leakage_audit_sha256",
        "official_source_approval_manifest_sha256",
    ):
        if not SHA256_RE.fullmatch(str(envelope.get(field) or "")):
            errors.append(f"invalid_sha256:{field}")
    custody_ref = str(envelope.get("custody_ref") or "")
    if not custody_ref or "/" in custody_ref or "\\" in custody_ref:
        errors.append("custody_ref_must_be_opaque")
    if not str(envelope.get("custodian_id") or "").strip():
        errors.append("custodian_id_missing")
    if not str(envelope.get("sealed_at") or "").strip():
        errors.append("sealed_at_missing")
    consumed_at = envelope.get("consumed_at")
    consumed_candidate = envelope.get("consumed_candidate_sha256")
    if bool(consumed_at) != bool(consumed_candidate):
        errors.append("holdout_consumption_evidence_incomplete")
    if consumed_candidate and not SHA256_RE.fullmatch(str(consumed_candidate)):
        errors.append("invalid_sha256:consumed_candidate_sha256")
    if require_unused and (
        envelope.get("eligible_for_single_run") is not True
        or consumed_at is not None
        or consumed_candidate is not None
    ):
        errors.append("holdout_already_consumed")
    return sorted(set(errors))


def _aggregate_metrics(summary: Mapping[str, Any]) -> dict[str, Any]:
    metrics = {key: summary.get(key) for key in METRIC_KEYS if key in summary}
    latency = summary.get("latency_ms")
    if isinstance(latency, Mapping):
        metrics["latency_ms"] = {
            key: latency.get(key)
            for key in ("p50", "p95", "max")
            if key in latency
        }
    domains = summary.get("per_domain")
    if isinstance(domains, Mapping):
        metrics["per_domain"] = {
            str(domain): {
                key: values.get(key)
                for key in DOMAIN_METRIC_KEYS
                if isinstance(values, Mapping) and key in values
            }
            for domain, values in domains.items()
            if str(domain) in DOMAINS and isinstance(values, Mapping)
        }
    return metrics


def aggregate_holdout_receipt(
    *,
    envelope: Mapping[str, Any],
    candidate_sha256: str,
    summary: Mapping[str, Any],
    gates: Mapping[str, bool],
) -> dict[str, Any]:
    """Create the only artifact allowed to leave the holdout custodian.

    A run consumes the holdout version whether it passes or fails. A failed
    candidate therefore requires a newly authored and sealed holdout version.
    """

    errors = validate_public_holdout_envelope(envelope, require_unused=True)
    if errors:
        raise ValueError("invalid_holdout_envelope:" + ";".join(errors))
    if not SHA256_RE.fullmatch(str(candidate_sha256 or "")):
        raise ValueError("invalid_candidate_sha256")
    gate_values = {str(key): bool(value) for key, value in gates.items()}
    status = "PASS" if gate_values and all(gate_values.values()) else "FAIL"
    receipt = {
        "schema_version": "production-holdout-aggregate-receipt-v1",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset_version": envelope["dataset_version"],
        "holdout_content_sha256": envelope["content_sha256"],
        "holdout_case_ids_sha256": envelope["case_ids_sha256"],
        "source_snapshot_sha256": envelope["source_snapshot_sha256"],
        "manifest_sha256": envelope["manifest_sha256"],
        "quota_policy_sha256": envelope["quota_policy_sha256"],
        "quota_attestation_sha256": envelope["quota_attestation_sha256"],
        "cross_split_leakage_audit_sha256": envelope["cross_split_leakage_audit_sha256"],
        "official_source_approval_manifest_sha256": envelope["official_source_approval_manifest_sha256"],
        "candidate_sha256": candidate_sha256,
        "case_count": HOLDOUT_CASE_COUNT,
        "metrics": _aggregate_metrics(summary),
        "gates": gate_values,
        "holdout_reusable": False,
        "next_holdout_version_required": status != "PASS",
        "per_case_output_included": False,
        "active_pointer_changed": False,
        "activation_performed": False,
        "database_mutated": False,
        "vector_collections_mutated": False,
    }
    leaked = _forbidden_keys(receipt)
    if leaked:  # pragma: no cover - invariant guarding future edits
        raise RuntimeError("aggregate_receipt_leak:" + ",".join(sorted(leaked)))
    return receipt
