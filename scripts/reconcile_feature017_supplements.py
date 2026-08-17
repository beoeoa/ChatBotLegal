#!/usr/bin/env python3
"""Reconcile Feature 017 supplement rows against approved catalog assets.

This command only builds a review proposal.  It never changes the canonical
catalog, legal attestations, release manifests, or the active runtime pointer.
In particular, an approved interactive e-form is evidence of an online route;
it is not treated as a replacement for a paper form required by an offline
route.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.three_tier_form_inventory import fold  # noqa: E402


DEFAULT_QUEUE = (
    ROOT
    / "data"
    / "source_cache"
    / "feature017_approved_sources_20260811"
    / "supplement-queue.json"
)
DEFAULT_CATALOG = ROOT / "notebook_data" / "forms" / "canonical_forms_catalog_v1.json"
DEFAULT_BINDINGS = (
    ROOT / "notebook_data" / "forms" / "procedure_form_bindings_v1.json"
)
DEFAULT_OUTPUT = DEFAULT_QUEUE.with_name("supplement-reconciliation.json")
DEFAULT_SCOPE_DECISION = DEFAULT_QUEUE.with_name("supplement-scope-decision.json")


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
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_code(value: Any) -> str:
    return "".join(character for character in fold(str(value or "")) if character.isalnum())


def _approved_runtime_form(form: Mapping[str, Any]) -> bool:
    return (
        form.get("approved") is True
        and str(form.get("review_status") or "") == "approved"
        and form.get("runtime_eligible") is not False
    )


def _is_eform(form: Mapping[str, Any]) -> bool:
    haystack = " ".join(
        (
            str(form.get("form_type") or ""),
            str(form.get("canonical_name") or ""),
            str(form.get("official_download_url") or ""),
        )
    )
    normalized = fold(haystack)
    return any(
        marker in normalized
        for marker in ("dien tu tuong tac", "interactive", "eform", "e-form")
    )


def _attachment_evidence(record: Mapping[str, Any]) -> list[dict[str, str]]:
    evidence: dict[str, dict[str, str]] = {}
    for component in record.get("official_components") or []:
        if not isinstance(component, Mapping):
            continue
        for attachment in component.get("attachments") or []:
            if not isinstance(attachment, Mapping):
                continue
            attachment_id = str(attachment.get("attachment_id") or "").strip()
            if not attachment_id:
                continue
            evidence.setdefault(
                attachment_id,
                {
                    "attachment_id": attachment_id,
                    "file_name": str(attachment.get("file_name") or "").strip(),
                    "official_endpoint": str(
                        attachment.get("official_endpoint") or ""
                    ).strip(),
                },
            )
    return sorted(evidence.values(), key=lambda item: item["attachment_id"])


def _same_procedure_forms(
    record: Mapping[str, Any],
    forms: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    procedures = {
        str(value).strip()
        for value in record.get("procedure_ids") or []
        if str(value or "").strip()
    }
    return sorted(
        (
            form
            for form in forms
            if _approved_runtime_form(form)
            and procedures.intersection(
                str(value).strip()
                for value in form.get("procedure_ids") or []
                if str(value or "").strip()
            )
        ),
        key=lambda item: str(item.get("form_id") or ""),
    )


def _exact_reusable_assets(
    record: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    expected_code = _normalized_code(record.get("current_form_code"))
    normalized_name = fold(str(record.get("canonical_name") or ""))
    if not expected_code and "ct01" in normalized_name.replace(" ", ""):
        expected_code = "ct01"
    if not expected_code:
        return []
    return [
        form
        for form in candidates
        if _normalized_code(form.get("form_code")) == expected_code
        and not _is_eform(form)
    ]


def _looks_obsolete(record: Mapping[str, Any]) -> bool:
    normalized = fold(str(record.get("canonical_name") or ""))
    markers = (
        "so ho khau",
        "phieu bao thay doi ho khau nhan khau",
    )
    return any(marker in normalized for marker in markers)


def build_supplement_reconciliation(
    queue: Mapping[str, Any],
    catalog: Mapping[str, Any],
    bindings: Mapping[str, Any],
) -> dict[str, Any]:
    """Return deterministic, non-authoritative review proposals."""

    forms = [
        item
        for item in catalog.get("forms") or []
        if isinstance(item, Mapping)
    ]
    known_binding_pairs = {
        (str(item.get("procedure_id") or ""), str(item.get("form_id") or ""))
        for item in bindings.get("bindings") or []
        if isinstance(item, Mapping)
    }
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for queued in queue.get("records") or []:
        if not isinstance(queued, Mapping):
            continue
        identity_id = str(queued.get("identity_id") or "").strip()
        if not identity_id or identity_id in seen:
            raise ValueError("FEATURE017_SUPPLEMENT_IDENTITY_INVALID")
        seen.add(identity_id)
        attachments = _attachment_evidence(queued)
        same_procedure = _same_procedure_forms(queued, forms)
        reusable = _exact_reusable_assets(queued, same_procedure)
        eforms = [item for item in same_procedure if _is_eform(item)]

        if reusable:
            status = "REUSE_APPROVED_ASSET"
            next_action = "review_existing_asset_binding_and_conditions"
            reason = "EXACT_FORM_CODE_AND_PROCEDURE_MATCH"
        elif attachments:
            status = "OFFICIAL_ATTACHMENT_ENRICHMENT"
            next_action = "download_checksum_and_review_attachment_identity"
            reason = "OFFICIAL_DVC_ATTACHMENT_AVAILABLE"
        elif _looks_obsolete(queued):
            status = "OBSOLETE_OR_SUPERSEDED_REVIEW"
            next_action = "verify_current_replacement_or_verified_gap"
            reason = "LEGACY_HOUSEHOLD_REGISTRATION_TERMINOLOGY"
        elif eforms:
            status = "ONLINE_ALTERNATIVE_EXISTS"
            next_action = "retain_eform_and_verify_offline_form_requirement"
            reason = "APPROVED_EFORM_IS_NOT_AN_OFFLINE_FORM_REPLACEMENT"
        else:
            status = "PACKAGE_SOURCE_REQUIRED"
            next_action = "obtain_exact_issuing_instrument_asset_or_verified_gap"
            reason = "NO_EXACT_APPROVED_ASSET_OR_OFFICIAL_ATTACHMENT"

        def public_form(item: Mapping[str, Any]) -> dict[str, Any]:
            form_id = str(item.get("form_id") or "")
            procedures = sorted(
                str(value) for value in item.get("procedure_ids") or [] if value
            )
            return {
                "form_id": form_id,
                "form_code": item.get("form_code"),
                "canonical_name": item.get("canonical_name"),
                "asset_kind": "eform" if _is_eform(item) else "file",
                "procedure_ids": procedures,
                "source_url": item.get("official_source_page"),
                "download_url": item.get("official_download_url"),
                "sha256": item.get("sha256"),
                "binding_present": any(
                    (procedure_id, form_id) in known_binding_pairs
                    for procedure_id in queued.get("procedure_ids") or []
                ),
            }

        records.append(
            {
                "identity_id": identity_id,
                "canonical_name": queued.get("canonical_name"),
                "domains": list(queued.get("domains") or []),
                "procedure_ids": list(queued.get("procedure_ids") or []),
                "review_proposal": status,
                "reason_code": reason,
                "required_next_action": next_action,
                "official_attachments": attachments,
                "reusable_assets": [public_form(item) for item in reusable],
                "approved_online_alternatives": [
                    public_form(item) for item in eforms
                ],
                "paper_asset_auto_satisfied": bool(reusable),
                "eform_treated_as_paper_replacement": False,
                "automated_source_approval": False,
                "automated_verified_gap": False,
                "automated_attestation": False,
                "automated_release": False,
            }
        )

    records.sort(key=lambda item: item["identity_id"])
    counts = Counter(item["review_proposal"] for item in records)
    payload = {
        "schema_version": "feature017-supplement-reconciliation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": str(queue.get("legal_as_of") or ""),
        "candidate_only": True,
        "record_count": len(records),
        "status_counts": dict(sorted(counts.items())),
        "runtime_catalog_mutated": False,
        "attestation_created": False,
        "release_created": False,
        "records": records,
    }
    payload["manifest_sha256"] = _canonical_hash(
        {key: value for key, value in payload.items() if key != "generated_at"}
    )
    return payload


def build_owner_deferred_manifest(
    queue: Mapping[str, Any],
    *,
    decision_note: str,
) -> dict[str, Any]:
    """Record a scope decision without mislabelling it as a legal data gap."""

    records = []
    for queued in queue.get("records") or []:
        if not isinstance(queued, Mapping):
            continue
        records.append(
            {
                "identity_id": str(queued.get("identity_id") or ""),
                "canonical_name": queued.get("canonical_name"),
                "procedure_ids": list(queued.get("procedure_ids") or []),
                "scope_status": "OWNER_DEFERRED_MISSING_EVIDENCE",
                "coverage_decision": "deferred",
                "public_eligible": False,
                "router_eligible": False,
                "golden_positive_case_eligible": False,
                "verified_gap": False,
                "reason_code": "USER_EXCLUDED_SUPPLEMENT_FROM_CURRENT_RELEASE",
                "decision_note": decision_note,
                "data_retained_for_future_review": True,
                "automated_source_approval": False,
                "automated_attestation": False,
                "automated_release": False,
            }
        )
    records.sort(key=lambda item: item["identity_id"])
    payload = {
        "schema_version": "feature017-supplement-scope-decision-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": str(queue.get("legal_as_of") or ""),
        "record_count": len(records),
        "scope_status": "OWNER_DEFERRED_MISSING_EVIDENCE",
        "public_eligible_count": 0,
        "runtime_catalog_mutated": False,
        "records": records,
    }
    payload["manifest_sha256"] = _canonical_hash(
        {key: value for key, value in payload.items() if key != "generated_at"}
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--bindings", type=Path, default=DEFAULT_BINDINGS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--defer-all", action="store_true")
    parser.add_argument(
        "--scope-decision-output",
        type=Path,
        default=DEFAULT_SCOPE_DECISION,
    )
    args = parser.parse_args()
    result = build_supplement_reconciliation(
        _read(args.queue),
        _read(args.catalog),
        _read(args.bindings),
    )
    _write_json(args.output, result)
    if args.defer_all:
        deferred = build_owner_deferred_manifest(
            _read(args.queue),
            decision_note=(
                "Người sở hữu dữ liệu yêu cầu bỏ qua 27 hồ sơ thiếu ngày 2026-08-11."
            ),
        )
        _write_json(args.scope_decision_output, deferred)
    print(
        json.dumps(
            {
                "record_count": result["record_count"],
                "status_counts": result["status_counts"],
                "runtime_catalog_mutated": False,
                "owner_deferred_count": (
                    deferred["record_count"] if args.defer_all else 0
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
