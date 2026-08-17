#!/usr/bin/env python3
"""Build the complete, non-activatable Feature 017 release candidate.

The builder merges the 31 already released commune identities, the 60 newly
attested identities, and 40 explicit owner-deferred exclusions.  Deferred
records are decisions, but never verified gaps and never router eligible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_governance_models import canonical_sha256  # noqa: E402
from api.form_governance_release_validator import (  # noqa: E402
    validate_release_manifest,
)


DEFAULT_BASELINE = ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-30.json"
DEFAULT_CATALOG = ROOT / "notebook_data" / "forms" / "canonical_forms_catalog_v1.json"
DEFAULT_DIR = ROOT / "data" / "source_cache" / "feature017_approved_sources_20260811"
DEFAULT_CANDIDATE = DEFAULT_DIR / "scoped-legal-enrichment-candidate.json"
DEFAULT_ATTESTATION = DEFAULT_DIR / "scoped-attestation.json"
DEFAULT_SUPPLEMENT_SCOPE = DEFAULT_DIR / "supplement-scope-decision.json"
DEFAULT_PACKAGE_SCOPE = DEFAULT_DIR / "instrument-package-scope-decision.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "feature017-full-release-candidate-20260812"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _item_sha256(item: Mapping[str, Any]) -> str:
    return canonical_sha256(dict(item))


def _released_asset(
    identity: Mapping[str, Any],
    form: Mapping[str, Any],
) -> dict[str, Any]:
    source_url = str(
        form.get("official_source_page") or form.get("official_download_url") or ""
    )
    original_provenance = form.get("provenance") or {}
    return {
        "form_id": str(form.get("form_id") or ""),
        "identity_id": str(identity.get("identity_id") or ""),
        "form_code": identity.get("form_code") or form.get("form_code"),
        "canonical_name": form.get("canonical_name"),
        "issuing_instrument": identity.get("issuing_instrument"),
        "asset_kind": "file",
        "source_url": source_url,
        "source_page_url": form.get("official_source_page"),
        "source_checksum": str(form.get("sha256") or "").casefold(),
        "audiences": [str(form.get("audience") or "citizen")],
        "effective_from": form.get("effective_from"),
        "effective_to": form.get("effective_to"),
        "coverage_status": "released",
        "provenance": {
            "source": "canonical_forms_catalog_v1",
            "review_status": form.get("review_status"),
            "runtime_eligible_before_feature017": form.get("runtime_eligible") is True,
            "canonical_artifact": {
                "asset_kind": "file",
                "staging_path": form.get("local_path"),
                "sha256": form.get("sha256"),
                "source_page_url": form.get("official_source_page"),
                "source_download_url": form.get("official_download_url"),
                "source_package_sha256": original_provenance.get(
                    "source_package_sha256"
                ),
            },
        },
    }


def _new_asset(item: Mapping[str, Any]) -> dict[str, Any]:
    effectivity = item.get("effectivity_evidence") or {}
    return {
        "form_id": str(item.get("form_id") or ""),
        "identity_id": str(item.get("identity_id") or ""),
        "form_code": item.get("form_code"),
        "canonical_name": item.get("canonical_name"),
        "issuing_instrument": item.get("issuing_instrument"),
        "asset_kind": item.get("asset_kind"),
        "source_url": item.get("source_url"),
        "source_checksum": item.get("source_checksum"),
        "audiences": ["citizen"],
        "effective_from": effectivity.get("effective_from"),
        "effective_to": effectivity.get("effective_to"),
        "coverage_status": "released",
        "provenance": {
            "source": "feature017_scoped_attestation",
            "canonical_artifact": item.get("canonical_artifact"),
            "effectivity_evidence": effectivity,
        },
    }


def build_full_release(
    *,
    baseline: Mapping[str, Any],
    catalog: Mapping[str, Any],
    scoped_candidate: Mapping[str, Any],
    attestation: Mapping[str, Any],
    supplement_scope: Mapping[str, Any],
    package_scope: Mapping[str, Any],
    source_snapshot_sha256: str,
) -> dict[str, Any]:
    if attestation.get("attestation_status") != "attested":
        raise ValueError("FEATURE017_FULL_RELEASE_ATTESTATION_REQUIRED")
    if attestation.get("candidate_manifest_sha256") != scoped_candidate.get(
        "manifest_sha256"
    ):
        raise ValueError("FEATURE017_FULL_RELEASE_ATTESTATION_MISMATCH")

    procedures_source = [
        item
        for item in baseline.get("procedures") or []
        if isinstance(item, Mapping)
        and str(item.get("executing_level") or "").casefold() == "commune"
    ]
    procedure_ids = {str(item.get("procedure_id") or "") for item in procedures_source}
    identities = [
        item
        for item in baseline.get("form_identities") or []
        if isinstance(item, Mapping)
        and procedure_ids.intersection(
            str(value) for value in item.get("procedure_ids") or []
        )
    ]
    identity_index = {
        str(item.get("identity_id") or ""): item for item in identities
    }
    form_index = {
        str(item.get("form_id") or ""): item
        for item in catalog.get("forms") or []
        if isinstance(item, Mapping)
    }
    new_asset_by_identity = {
        str(item.get("identity_id") or ""): item
        for item in scoped_candidate.get("assets") or []
        if isinstance(item, Mapping)
    }
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
    deferred_by_identity = {
        str(item.get("identity_id") or ""): item for item in deferred_records
    }
    if len(identities) != 131 or len(deferred_by_identity) != 40:
        raise ValueError("FEATURE017_FULL_RELEASE_SCOPE_COUNT_INVALID")

    assets: list[dict[str, Any]] = []
    form_id_by_identity: dict[str, str] = {}
    for identity_id, identity in sorted(identity_index.items()):
        if identity_id in deferred_by_identity:
            continue
        if identity_id in new_asset_by_identity:
            asset = _new_asset(new_asset_by_identity[identity_id])
        else:
            catalog_ids = list(identity.get("approved_catalog_form_ids") or [])
            if len(catalog_ids) != 1 or str(catalog_ids[0]) not in form_index:
                raise ValueError("FEATURE017_RELEASED_ASSET_MAPPING_INVALID")
            form = form_index[str(catalog_ids[0])]
            if (
                form.get("approved") is not True
                or form.get("runtime_eligible") is not True
                or str(form.get("review_status") or "") != "approved"
            ):
                raise ValueError("FEATURE017_RELEASED_ASSET_NOT_APPROVED")
            asset = _released_asset(identity, form)
        form_id_by_identity[identity_id] = str(asset["form_id"])
        assets.append(asset)

    if len(assets) != 91 or len(new_asset_by_identity) != 60:
        raise ValueError("FEATURE017_FULL_RELEASE_ASSET_COUNT_INVALID")

    decision_fingerprint = str(attestation.get("attestation_fingerprint") or "")
    decided_at = str(attestation.get("attested_at") or "")
    decided_by = str(attestation.get("attested_by") or "")

    def exclusion(
        *, target_type: str, target_id: str, reason_code: str
    ) -> dict[str, Any]:
        return {
            "target_type": target_type,
            "target_id": target_id,
            "reason_code": reason_code,
            "decision_fingerprint": decision_fingerprint,
            "decided_by": decided_by,
            "decided_at": decided_at,
            "public_eligible": False,
            "router_eligible": False,
        }

    exclusions: list[dict[str, Any]] = []
    for identity_id, record in sorted(deferred_by_identity.items()):
        exclusions.append(
            exclusion(
                target_type="identity",
                target_id=identity_id,
                reason_code=str(record.get("reason_code") or ""),
            )
        )

    procedures: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    binding_decisions = 0
    for source in sorted(
        procedures_source, key=lambda item: str(item.get("procedure_id") or "")
    ):
        procedure_id = str(source.get("procedure_id") or "")
        required_ids = [
            str(value) for value in source.get("required_form_identity_ids") or []
        ]
        deferred_ids = [
            identity_id for identity_id in required_ids if identity_id in deferred_by_identity
        ]
        if not required_ids:
            coverage_status = "verified_gap"
        elif deferred_ids:
            coverage_status = "owner_deferred"
        else:
            coverage_status = "released"
        procedure = {
            "procedure_id": procedure_id,
            "procedure_code": procedure_id,
            "name": source.get("procedure_name"),
            "domain": source.get("domain"),
            "authority": "cấp xã",
            "official_source_url": source.get("official_source_page"),
            "official_source_checksum": _item_sha256(source),
            "coverage_status": coverage_status,
        }
        procedures.append(procedure)
        aliases.extend(
            [
                {
                    "procedure_id": procedure_id,
                    "alias": procedure_id,
                    "alias_kind": "exact",
                },
                {
                    "procedure_id": procedure_id,
                    "alias": str(source.get("procedure_name") or procedure_id),
                    "alias_kind": "natural",
                },
            ]
        )
        if coverage_status == "verified_gap":
            gaps.append(
                {
                    "target_type": "procedure",
                    "target_id": procedure_id,
                    "reason_code": "NO_OFFICIAL_FORM_LISTED",
                    "evidence_source_url": source.get("official_source_page"),
                    "evidence_sha256": _item_sha256(source),
                    "legal_as_of": scoped_candidate.get("legal_as_of"),
                }
            )
        if coverage_status == "owner_deferred":
            reason_code = str(
                deferred_by_identity[sorted(deferred_ids)[0]].get("reason_code")
                or ""
            )
            exclusions.append(
                exclusion(
                    target_type="procedure",
                    target_id=procedure_id,
                    reason_code=reason_code,
                )
            )
        for identity_id in required_ids:
            binding_id = f"binding-{identity_id}-{procedure_id}"
            binding_decisions += 1
            if coverage_status != "released" or identity_id in deferred_by_identity:
                deferred_record = deferred_by_identity.get(identity_id)
                reason_code = str(
                    (deferred_record or deferred_by_identity[sorted(deferred_ids)[0]]).get(
                        "reason_code"
                    )
                    or ""
                )
                exclusions.append(
                    exclusion(
                        target_type="binding",
                        target_id=binding_id,
                        reason_code=reason_code,
                    )
                )
                continue
            bindings.append(
                {
                    "binding_id": binding_id,
                    "procedure_id": procedure_id,
                    "form_id": form_id_by_identity[identity_id],
                    "identity_id": identity_id,
                    "requirement": "required",
                    "condition": None,
                    "audience": "citizen",
                    "coverage_status": "released",
                }
            )

    if len(procedures) != 191 or binding_decisions != 229:
        raise ValueError("FEATURE017_FULL_RELEASE_BASELINE_COUNT_INVALID")
    identity_exclusions = sum(
        item["target_type"] == "identity" for item in exclusions
    )
    binding_exclusions = sum(
        item["target_type"] == "binding" for item in exclusions
    )
    manifest = {
        "schema_version": "form-release-v1",
        "release_id": "forms-2026-08-11-feature017-attested",
        "version": 1,
        "legal_as_of": str(scoped_candidate.get("legal_as_of") or ""),
        "source_snapshot_sha256": source_snapshot_sha256,
        "previous_release_id": None,
        "procedures": procedures,
        "assets": sorted(assets, key=lambda item: item["form_id"]),
        "bindings": sorted(
            bindings, key=lambda item: (item["procedure_id"], item["form_id"])
        ),
        "aliases": sorted(
            aliases, key=lambda item: (item["procedure_id"], item["alias_kind"])
        ),
        "gaps": sorted(gaps, key=lambda item: item["target_id"]),
        "exclusions": sorted(
            exclusions,
            key=lambda item: (item["target_type"], item["target_id"]),
        ),
        "coverage": {
            "procedure_total": 191,
            "procedure_decided": 191,
            "identity_total": 131,
            "identity_decided": len(assets) + identity_exclusions,
            "binding_total": 229,
            "binding_decided": len(bindings) + binding_exclusions,
            "complete": True,
        },
        "build": {
            "pipeline_version": "feature017-v1-owner-deferred",
            "attestation_id": attestation.get("attestation_id"),
            "attestation_fingerprint": decision_fingerprint,
            "candidate_manifest_sha256": scoped_candidate.get("manifest_sha256"),
            "owner_deferred_identity_count": 40,
            "activation_allowed": False,
        },
    }
    report = validate_release_manifest(
        manifest,
        expected_manifest_sha256=canonical_sha256(manifest),
    )
    if not report["passed"]:
        raise ValueError("FEATURE017_FULL_RELEASE_GATE_FAILED:" + ",".join(report["errors"]))
    return {
        "manifest": manifest,
        "manifest_sha256": canonical_sha256(manifest),
        "gate_report": report,
        "status": "validated_candidate",
        "activation_allowed": False,
        "runtime_catalog_mutated": False,
        "active_pointer_changed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--attestation", type=Path, default=DEFAULT_ATTESTATION)
    parser.add_argument("--supplement-scope", type=Path, default=DEFAULT_SUPPLEMENT_SCOPE)
    parser.add_argument("--package-scope", type=Path, default=DEFAULT_PACKAGE_SCOPE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    result = build_full_release(
        baseline=_read(args.baseline),
        catalog=_read(args.catalog),
        scoped_candidate=_read(args.candidate),
        attestation=_read(args.attestation),
        supplement_scope=_read(args.supplement_scope),
        package_scope=_read(args.package_scope),
        source_snapshot_sha256=_file_sha256(args.baseline),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write(args.output_dir / "form-release-v1.json", result["manifest"])
    _write(args.output_dir / "gate-report.json", result["gate_report"])
    _write(args.output_dir / "release-candidate.json", result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "manifest_sha256": result["manifest_sha256"],
                "coverage": result["manifest"]["coverage"],
                "assets": len(result["manifest"]["assets"]),
                "bindings": len(result["manifest"]["bindings"]),
                "gaps": len(result["manifest"]["gaps"]),
                "exclusions": len(result["manifest"]["exclusions"]),
                "activation_allowed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
