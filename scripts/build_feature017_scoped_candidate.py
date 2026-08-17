#!/usr/bin/env python3
"""Build the isolated Feature 017 candidate after explicit scope deferrals.

The output is deliberately not a ``form-release-v1`` manifest.  It is a legal
enrichment work package: source-approved records may enter it, while owner-
deferred records remain fail-closed.  No attestation, release, active pointer,
catalog, or database is changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = (
    ROOT / "data" / "source_cache" / "feature017_approved_sources_20260811"
)
DEFAULT_COLLECTION = DEFAULT_DIR / "collection-result.json"
DEFAULT_PLAN = DEFAULT_DIR / "collection-plan.json"
DEFAULT_SUPPLEMENTS = DEFAULT_DIR / "supplement-scope-decision.json"
DEFAULT_PACKAGES = DEFAULT_DIR / "instrument-package-review-queue.json"
DEFAULT_BASELINE = (
    ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-30.json"
)
DEFAULT_PACKAGE_DECISION = DEFAULT_DIR / "instrument-package-scope-decision.json"
DEFAULT_CANDIDATE = DEFAULT_DIR / "scoped-legal-enrichment-candidate.json"
DEFAULT_ATTESTATION_PREVIEW = DEFAULT_DIR / "scoped-attestation-preview.json"
DEFAULT_ATTESTATION = DEFAULT_DIR / "scoped-attestation.json"
DEFAULT_RELEASE_DELTA = DEFAULT_DIR / "scoped-release-delta.json"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def build_package_deferred_manifest(
    queue: Mapping[str, Any],
    *,
    decision_note: str,
) -> dict[str, Any]:
    records = []
    for item in queue.get("records") or []:
        if not isinstance(item, Mapping):
            continue
        records.append(
            {
                "identity_id": str(item.get("identity_id") or ""),
                "canonical_name": item.get("canonical_name"),
                "procedure_ids": list(item.get("procedure_ids") or []),
                "issuing_instrument": item.get("issuing_instrument"),
                "package_reason_code": item.get("reason_code"),
                "scope_status": "OWNER_DEFERRED_PACKAGE_REVIEW",
                "coverage_decision": "deferred",
                "public_eligible": False,
                "router_eligible": False,
                "golden_positive_case_eligible": False,
                "verified_gap": False,
                "reason_code": "USER_EXCLUDED_PACKAGE_REVIEW_FROM_CURRENT_RELEASE",
                "decision_note": decision_note,
                "data_retained_for_future_review": True,
                "automated_attestation": False,
                "automated_release": False,
            }
        )
    records.sort(key=lambda item: item["identity_id"])
    payload = {
        "schema_version": "feature017-package-scope-decision-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": str(queue.get("legal_as_of") or ""),
        "record_count": len(records),
        "scope_status": "OWNER_DEFERRED_PACKAGE_REVIEW",
        "public_eligible_count": 0,
        "runtime_catalog_mutated": False,
        "records": records,
    }
    payload["manifest_sha256"] = _canonical_hash(
        {key: value for key, value in payload.items() if key != "generated_at"}
    )
    return payload


def _artifact_integrity(artifact: Mapping[str, Any]) -> tuple[bool, str | None]:
    staging_path = str(artifact.get("staging_path") or "").strip()
    expected = str(artifact.get("sha256") or "").strip().casefold()
    if not staging_path:
        return False, "STAGING_PATH_REQUIRED"
    path = ROOT / staging_path
    if not path.is_file():
        return False, "STAGING_ARTIFACT_MISSING"
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if not expected or actual != expected:
        return False, "STAGING_ARTIFACT_CHECKSUM_MISMATCH"
    return True, None


def build_scoped_candidate(
    collection: Mapping[str, Any],
    collection_plan: Mapping[str, Any],
    supplement_scope: Mapping[str, Any],
    package_scope: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    component_semantics = {
        (
            str(item.get("identity_id") or ""),
            str(component.get("procedure_id") or ""),
        ): component
        for item in collection_plan.get("records") or []
        if isinstance(item, Mapping)
        for component in item.get("components") or []
        if isinstance(component, Mapping)
    }
    procedure_index = {
        str(item.get("procedure_id") or ""): item
        for item in baseline.get("procedures") or []
        if isinstance(item, Mapping)
        and str(item.get("executing_level") or "").casefold() == "commune"
    }
    required_identities_by_procedure = {
        procedure_id: {
            str(value)
            for value in item.get("required_form_identity_ids") or []
            if value
        }
        for procedure_id, item in procedure_index.items()
    }
    assets: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    procedures: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, str]] = []
    for item in collection.get("records") or []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "") != "READY_FOR_LEGAL_ENRICHMENT":
            continue
        identity_id = str(item.get("identity_id") or "")
        canonical = item.get("canonical_artifact") or {}
        asset_kind = str(canonical.get("asset_kind") or item.get("asset_kind") or "")
        source_checksum = str(item.get("source_checksum") or "").casefold()
        source_url = str(
            canonical.get("referer_url")
            or canonical.get("official_url")
            or item.get("official_source_url")
            or ""
        )
        integrity_ok = True
        integrity_reason = None
        if asset_kind == "file":
            integrity_ok, integrity_reason = _artifact_integrity(canonical)
        elif asset_kind != "eform":
            integrity_ok, integrity_reason = False, "ASSET_KIND_INVALID"
        package_effectivity = item.get("package_effectivity") or {}
        if package_effectivity:
            effectivity_evidence = {
                "evidence_basis": "official_instrument_package_status",
                "eligible": package_effectivity.get("eligible") is True,
                "effective_from": package_effectivity.get("effective_from"),
                "effective_to": package_effectivity.get("effective_to"),
                "status": package_effectivity.get("status"),
                "reason_code": package_effectivity.get("reason_code"),
                "legal_as_of": collection.get("legal_as_of"),
            }
        else:
            effectivity_evidence = {
                "evidence_basis": (
                    "official_current_dvc_eform"
                    if asset_kind == "eform"
                    else "official_current_dvc_attachment"
                ),
                "eligible": True,
                "effective_from": None,
                "effective_to": None,
                "status": "current_official_procedure_snapshot",
                "reason_code": "CURRENT_DVC_PROFILE_EVIDENCE",
                "legal_as_of": collection.get("legal_as_of"),
            }
        procedure_ids = sorted(
            str(value) for value in item.get("procedure_ids") or [] if value
        )
        missing_procedures = [
            procedure_id
            for procedure_id in procedure_ids
            if procedure_id not in procedure_index
        ]
        checks = {
            "source_approved": item.get("decision") == "source_approved",
            "source_checksum_present": len(source_checksum) == 64,
            "official_source_present": source_url.startswith("https://"),
            "artifact_integrity": integrity_ok,
            "single_domain": len(item.get("domains") or []) == 1,
            "procedure_metadata_complete": not missing_procedures,
            "binding_semantics_reviewed": False,
            "legal_effectivity_evidence_present": (
                effectivity_evidence.get("eligible") is True
            ),
            "legal_effectivity_attested": False,
        }
        hard_source_checks = all(
            checks[key]
            for key in (
                "source_approved",
                "source_checksum_present",
                "official_source_present",
                "artifact_integrity",
                "single_domain",
                "procedure_metadata_complete",
            )
        )
        if not hard_source_checks:
            rejected.append(
                {
                    "identity_id": identity_id,
                    "reason_code": integrity_reason
                    or "LEGAL_ENRICHMENT_SOURCE_PREREQUISITE_FAILED",
                }
            )
            continue
        form_id = f"form-feature017-{identity_id}"
        assets.append(
            {
                "form_id": form_id,
                "identity_id": identity_id,
                "canonical_name": item.get("canonical_name"),
                "form_code": item.get("form_code"),
                "issuing_instrument": item.get("issuing_instrument"),
                "domain": (item.get("domains") or [None])[0],
                "asset_kind": asset_kind,
                "source_url": source_url,
                "download_url": (
                    canonical.get("staging_path") if asset_kind == "file" else None
                ),
                "source_checksum": source_checksum,
                "canonical_artifact": dict(canonical),
                "effectivity_evidence": effectivity_evidence,
                "procedure_ids": procedure_ids,
                "legal_enrichment_checks": checks,
                "workflow_status": "legal_enrichment",
                "attestation_ready": False,
                "attestation_preview_ready": False,
                "runtime_eligible": False,
            }
        )
        for procedure_id in procedure_ids:
            procedure = procedure_index[procedure_id]
            component = component_semantics.get((identity_id, procedure_id), {})
            requirement = (
                "required"
                if identity_id
                in required_identities_by_procedure.get(procedure_id, set())
                else "conditional_review_required"
            )
            procedures[procedure_id] = {
                "procedure_id": procedure_id,
                "name": procedure.get("procedure_name"),
                "domain": procedure.get("domain"),
                "authority": "cấp xã",
                "official_source_url": procedure.get("official_source_page"),
                "legal_as_of": collection.get("legal_as_of"),
                "coverage_status": "candidate_pending_attestation",
            }
            bindings.append(
                {
                    "binding_id": f"binding-{identity_id}-{procedure_id}",
                    "procedure_id": procedure_id,
                    "form_id": form_id,
                    "requirement": requirement,
                    "condition": None,
                    "audience": "citizen",
                    "binding_review_status": (
                        "feature006_required_identity_manifest"
                        if requirement == "required"
                        else "condition_text_required"
                    ),
                    "binding_semantics_evidence": (
                        "required_form_identity_ids"
                        if requirement == "required"
                        else None
                    ),
                    "official_component_id": component.get("component_id"),
                    "official_component_required": component.get("required"),
                    "original_quantity": component.get("original_quantity"),
                    "copy_quantity": component.get("copy_quantity"),
                    "attestation_ready": requirement == "required",
                    "runtime_eligible": False,
                }
            )

    deferred_records = [
        *(
            item
            for item in supplement_scope.get("records") or []
            if isinstance(item, Mapping)
        ),
        *(
            item
            for item in package_scope.get("records") or []
            if isinstance(item, Mapping)
        ),
    ]
    deferred_ids = {str(item.get("identity_id") or "") for item in deferred_records}
    asset_ids = {str(item["identity_id"]) for item in assets}
    if asset_ids.intersection(deferred_ids):
        raise ValueError("FEATURE017_SCOPED_CANDIDATE_DEFERRED_LEAK")
    binding_review_required = sum(
        item["requirement"] == "conditional_review_required" for item in bindings
    )
    binding_ids_by_form: dict[str, list[dict[str, Any]]] = {}
    for binding in bindings:
        binding_ids_by_form.setdefault(str(binding["form_id"]), []).append(binding)
    for asset in assets:
        related_bindings = binding_ids_by_form.get(str(asset["form_id"]), [])
        preview_ready = (
            asset["legal_enrichment_checks"]["legal_effectivity_evidence_present"]
            and bool(related_bindings)
            and all(
                binding["requirement"] == "required"
                for binding in related_bindings
            )
        )
        asset["attestation_preview_ready"] = preview_ready
    source_identity_ids = {
        str(item.get("identity_id") or "")
        for item in collection.get("records") or []
        if isinstance(item, Mapping) and str(item.get("identity_id") or "")
    }
    rejected_ids = {
        str(item.get("identity_id") or "") for item in rejected
    }
    decision_ids = asset_ids | deferred_ids | rejected_ids
    status_counts = Counter(
        str(item.get("scope_status") or "UNKNOWN") for item in deferred_records
    )
    payload = {
        "schema_version": "feature017-legal-enrichment-candidate-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": str(collection.get("legal_as_of") or ""),
        "candidate_id": "feature017-scoped-20260811",
        "candidate_only": True,
        "source_approved_identity_count": len(assets),
        "deferred_identity_count": len(deferred_ids),
        "scope_decision_count": len(decision_ids),
        "expected_scope_decision_count": len(source_identity_ids),
        "scope_complete": decision_ids == source_identity_ids,
        "deferred_status_counts": dict(sorted(status_counts.items())),
        "procedure_count": len(procedures),
        "binding_count": len(bindings),
        "assets": sorted(assets, key=lambda item: item["form_id"]),
        "procedures": sorted(
            procedures.values(), key=lambda item: item["procedure_id"]
        ),
        "bindings": sorted(
            bindings,
            key=lambda item: (item["procedure_id"], item["form_id"]),
        ),
        "deferred": sorted(
            deferred_records, key=lambda item: str(item.get("identity_id") or "")
        ),
        "rejected_source_records": rejected,
        "binding_semantics_counts": dict(
            sorted(Counter(item["requirement"] for item in bindings).items())
        ),
        "binding_review_required_count": sum(
            item["requirement"] == "conditional_review_required"
            for item in bindings
        ),
        "attestation_preview_ready_count": sum(
            item["attestation_preview_ready"] for item in assets
        ),
        "attestation_ready_count": 0,
        "release_manifest_created": False,
        "runtime_catalog_mutated": False,
        "active_pointer_changed": False,
    }
    payload["manifest_sha256"] = _canonical_hash(
        {key: value for key, value in payload.items() if key != "generated_at"}
    )
    return payload


def build_attestation_preview(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Seal a tamper-evident preview without creating an attestation."""

    assets = list(candidate.get("assets") or [])
    bindings = list(candidate.get("bindings") or [])
    procedures = list(candidate.get("procedures") or [])
    if not candidate.get("scope_complete"):
        raise ValueError("FEATURE017_ATTESTATION_SCOPE_INCOMPLETE")
    if int(candidate.get("source_approved_identity_count") or 0) != len(assets):
        raise ValueError("FEATURE017_ATTESTATION_ASSET_COUNT_MISMATCH")
    if any(not item.get("attestation_preview_ready") for item in assets):
        raise ValueError("FEATURE017_ATTESTATION_PREVIEW_NOT_READY")
    if any(item.get("runtime_eligible") is not False for item in assets):
        raise ValueError("FEATURE017_ATTESTATION_RUNTIME_LEAK")
    if any(
        item.get("requirement") != "required"
        or item.get("attestation_ready") is not True
        for item in bindings
    ):
        raise ValueError("FEATURE017_ATTESTATION_BINDING_NOT_READY")
    sealed_payload = {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_manifest_sha256": candidate.get("manifest_sha256"),
        "legal_as_of": candidate.get("legal_as_of"),
        "assets": assets,
        "procedures": procedures,
        "bindings": bindings,
        "deferred_identity_ids": sorted(
            str(item.get("identity_id") or "")
            for item in candidate.get("deferred") or []
        ),
    }
    fingerprint = _canonical_hash(sealed_payload)
    return {
        "schema_version": "feature017-batch-attestation-preview-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_id": candidate.get("candidate_id"),
        "candidate_manifest_sha256": candidate.get("manifest_sha256"),
        "legal_as_of": candidate.get("legal_as_of"),
        "asset_count": len(assets),
        "procedure_count": len(procedures),
        "binding_count": len(bindings),
        "deferred_identity_count": len(candidate.get("deferred") or []),
        "attestation_fingerprint": fingerprint,
        "attestation_status": "awaiting_explicit_fingerprint_confirmation",
        "attested_by": None,
        "attested_at": None,
        "release_manifest_created": False,
        "runtime_catalog_mutated": False,
        "active_pointer_changed": False,
        "sealed_payload": sealed_payload,
    }


def confirm_scoped_attestation(
    candidate: Mapping[str, Any],
    preview: Mapping[str, Any],
    *,
    confirmed_fingerprint: str,
    attested_by: str,
) -> dict[str, Any]:
    """Create an isolated attestation only after exact fingerprint consent."""

    expected_preview = build_attestation_preview(candidate)
    expected = str(expected_preview["attestation_fingerprint"])
    stored = str(preview.get("attestation_fingerprint") or "")
    if preview.get("attestation_status") != (
        "awaiting_explicit_fingerprint_confirmation"
    ):
        raise ValueError("FEATURE017_ATTESTATION_PREVIEW_STATUS_INVALID")
    if stored != expected:
        raise ValueError("FEATURE017_ATTESTATION_PREVIEW_TAMPERED")
    if str(confirmed_fingerprint or "").casefold() != expected.casefold():
        raise ValueError("FEATURE017_ATTESTATION_FINGERPRINT_MISMATCH")
    actor = str(attested_by or "").strip()
    if not actor:
        raise ValueError("FEATURE017_ATTESTATION_ACTOR_REQUIRED")
    return {
        "schema_version": "feature017-batch-attestation-v1",
        "attestation_id": f"attestation-{expected[:24]}",
        "candidate_id": candidate.get("candidate_id"),
        "candidate_manifest_sha256": candidate.get("manifest_sha256"),
        "attestation_fingerprint": expected,
        "attestation_status": "attested",
        "attested_by": actor,
        "attested_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": candidate.get("legal_as_of"),
        "asset_count": len(candidate.get("assets") or []),
        "procedure_count": len(candidate.get("procedures") or []),
        "binding_count": len(candidate.get("bindings") or []),
        "deferred_identity_count": len(candidate.get("deferred") or []),
        "release_manifest_created": False,
        "runtime_catalog_mutated": False,
        "active_pointer_changed": False,
        "sealed_payload": expected_preview["sealed_payload"],
    }


def build_release_delta(
    candidate: Mapping[str, Any],
    attestation: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a non-activatable delta for later merge with an active release."""

    if attestation.get("attestation_status") != "attested":
        raise ValueError("FEATURE017_RELEASE_DELTA_ATTESTATION_REQUIRED")
    if attestation.get("candidate_manifest_sha256") != candidate.get(
        "manifest_sha256"
    ):
        raise ValueError("FEATURE017_RELEASE_DELTA_CANDIDATE_MISMATCH")
    preview = build_attestation_preview(candidate)
    if attestation.get("attestation_fingerprint") != preview.get(
        "attestation_fingerprint"
    ):
        raise ValueError("FEATURE017_RELEASE_DELTA_ATTESTATION_TAMPERED")
    deferred_ids = {
        str(item.get("identity_id") or "")
        for item in candidate.get("deferred") or []
    }
    assets = [dict(item) for item in candidate.get("assets") or []]
    if any(str(item.get("identity_id") or "") in deferred_ids for item in assets):
        raise ValueError("FEATURE017_RELEASE_DELTA_DEFERRED_LEAK")
    payload = {
        "schema_version": "feature017-release-delta-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "delta_id": f"feature017-delta-{str(attestation.get('attestation_id') or '')[-24:]}",
        "legal_as_of": candidate.get("legal_as_of"),
        "attestation_id": attestation.get("attestation_id"),
        "attestation_fingerprint": attestation.get("attestation_fingerprint"),
        "assets": assets,
        "procedures": list(candidate.get("procedures") or []),
        "bindings": list(candidate.get("bindings") or []),
        "deferred_identity_ids": sorted(deferred_ids),
        "previous_active_release_required": True,
        "full_release_gate_required": True,
        "activation_allowed": False,
        "runtime_catalog_mutated": False,
        "active_pointer_changed": False,
    }
    payload["manifest_sha256"] = _canonical_hash(
        {key: value for key, value in payload.items() if key != "generated_at"}
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", type=Path, default=DEFAULT_COLLECTION)
    parser.add_argument("--collection-plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--supplement-scope", type=Path, default=DEFAULT_SUPPLEMENTS)
    parser.add_argument("--package-queue", type=Path, default=DEFAULT_PACKAGES)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--package-decision", type=Path, default=DEFAULT_PACKAGE_DECISION)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument(
        "--attestation-preview",
        type=Path,
        default=DEFAULT_ATTESTATION_PREVIEW,
    )
    parser.add_argument("--confirm-attestation-fingerprint")
    parser.add_argument("--attested-by")
    parser.add_argument("--attestation-output", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument("--build-release-delta", action="store_true")
    parser.add_argument("--release-delta-output", type=Path, default=DEFAULT_RELEASE_DELTA)
    args = parser.parse_args()

    package_scope = build_package_deferred_manifest(
        _read(args.package_queue),
        decision_note=(
            "Người sở hữu dữ liệu yêu cầu bỏ qua 13 gói văn bản ngày 2026-08-11."
        ),
    )
    _write_json(args.package_decision, package_scope)
    candidate = build_scoped_candidate(
        _read(args.collection),
        _read(args.collection_plan),
        _read(args.supplement_scope),
        package_scope,
        _read(args.baseline),
    )
    _write_json(args.candidate, candidate)
    preview = build_attestation_preview(candidate)
    _write_json(args.attestation_preview, preview)
    if args.confirm_attestation_fingerprint or args.attested_by:
        if not args.confirm_attestation_fingerprint or not args.attested_by:
            raise ValueError("FEATURE017_ATTESTATION_CONFIRMATION_INCOMPLETE")
        attestation = confirm_scoped_attestation(
            candidate,
            preview,
            confirmed_fingerprint=args.confirm_attestation_fingerprint,
            attested_by=args.attested_by,
        )
        _write_json(args.attestation_output, attestation)
        if args.build_release_delta:
            _write_json(
                args.release_delta_output,
                build_release_delta(candidate, attestation),
            )
    elif args.build_release_delta:
        raise ValueError("FEATURE017_RELEASE_DELTA_ATTESTATION_REQUIRED")
    print(
        json.dumps(
            {
                "source_approved": candidate["source_approved_identity_count"],
                "deferred": candidate["deferred_identity_count"],
                "scope_complete": candidate["scope_complete"],
                "procedures": candidate["procedure_count"],
                "bindings": candidate["binding_count"],
                "attestation_ready": candidate["attestation_ready_count"],
                "attestation_preview_fingerprint": preview[
                    "attestation_fingerprint"
                ],
                "attestation_created": bool(
                    args.confirm_attestation_fingerprint and args.attested_by
                ),
                "runtime_catalog_mutated": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
