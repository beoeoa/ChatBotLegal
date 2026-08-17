#!/usr/bin/env python3
"""Collect official Feature 017 form candidates after human source review.

The command is intentionally candidate-only.  It reads the approved workbook
projection and the official DVC snapshot, downloads only fixed-endpoint DVC
attachments, and writes checksum-bound staging evidence.  It never changes the
runtime catalog, creates a legal attestation, or activates a release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
import zipfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote
from uuid import UUID
from xml.etree import ElementTree

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_source_resolution import (  # noqa: E402
    extract_appendix_identifier,
    extract_strict_form_codes,
    resolve_single_issuing_instrument,
)
from api.source_gap_jobs import _basic_malware_scan  # noqa: E402
from api.three_tier_form_inventory import classify_component_kind, fold  # noqa: E402
from scripts.build_form_requirement_manifest import (  # noqa: E402
    _identity_key,
    _title_signature,
)
from scripts.crawl_canonical_forms import validate_download  # noqa: E402
from scripts.discover_official_procedure_sources import (  # noqa: E402
    PORTAL_ORIGIN,
    collect_profile_components,
)


DVC_ATTACHMENT_API_URL = (
    "https://dichvucong.gov.vn/api/v1/submitting/preview-attachment"
)
# The official portal's WAF rejects non-browser user agents.  This collector
# still calls only the fixed official endpoint and first obtains the portal's
# own short-lived anti-bot cookie from the exact public procedure page.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)
DEFAULT_DECISIONS = (
    ROOT
    / "outputs"
    / "feature017-form-review-approved-100"
    / "feature017-approved-source-decisions.json"
)
DEFAULT_SNAPSHOT = (
    ROOT
    / "data"
    / "source_cache"
    / "form_requirements"
    / "dvc-form-requirements-2026-07-30.json"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "data" / "source_cache" / "feature017_approved_sources_20260811"
)
MAX_FILE_BYTES = 25 * 1024 * 1024
DETAIL_ENDPOINT = (
    "https://dichvucong.gov.vn/api/v1/configuring/"
    "formality/get-formality-by-citizen"
)


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


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _docx_semantic_sha256(path: Path) -> str | None:
    if path.suffix.casefold() != ".docx":
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return None
    text = " ".join(
        node.text or ""
        for node in root.iter()
        if str(node.tag).endswith("}t")
    )
    normalized = unicodedata.normalize(
        "NFC",
        " ".join(text.split()),
    ).casefold()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _is_interactive_eform(name: str) -> bool:
    normalized = fold(name)
    return any(
        marker in normalized
        for marker in (
            "mau ho tich dien tu tuong tac",
            "mau dien tu tuong tac",
            "bieu mau dien tu tuong tac",
        )
    )


def _catalog_index(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("code") or item.get("codeNotation") or item.get("id")): dict(item)
        for item in snapshot.get("catalog") or []
        if isinstance(item, Mapping)
    }


def _select_attachments_for_code(
    attachments: list[dict[str, str]],
    form_code: str | None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Narrow a multi-form component only when filenames prove the code.

    DVC sometimes puts Mẫu 01..05 in one component.  Returning every file for
    each identity creates a false ambiguity.  We filter only when at least one
    filename has an explicit ``mau<form_code>``/``form<form_code>`` marker;
    otherwise all attachments remain for human/legal disambiguation.
    """

    if not attachments or not form_code:
        return attachments, []
    normalized_code = re.sub(r"[^a-z0-9]", "", fold(form_code))
    normalized_code = re.sub(r"^0+(?=\d)", "", normalized_code)
    if not normalized_code:
        return attachments, []
    selected = []
    explicitly_coded_ids: set[str] = set()
    for attachment in attachments:
        stem = Path(str(attachment.get("file_name") or "")).stem
        normalized_stem = fold(stem)
        match = re.match(
            r"^(?:mau|mus|form|ms)(?:\s*so)?\s*0*"
            r"([0-9]+(?:[a-zđ])?)\b",
            normalized_stem,
        )
        if not match:
            continue
        attachment_code = re.sub(r"[^a-z0-9]", "", fold(match.group(1)))
        attachment_code = re.sub(r"^0+(?=\d)", "", attachment_code)
        explicitly_coded_ids.add(str(attachment.get("attachment_id") or ""))
        if attachment_code == normalized_code:
            selected.append(attachment)
    if not selected:
        if explicitly_coded_ids:
            return (
                [
                    item
                    for item in attachments
                    if str(item.get("attachment_id") or "")
                    not in explicitly_coded_ids
                ],
                sorted(explicitly_coded_ids),
            )
        return attachments, []
    selected_ids = {item.get("attachment_id") for item in selected}
    excluded = [
        str(item.get("attachment_id") or "")
        for item in attachments
        if item.get("attachment_id") not in selected_ids
    ]
    return selected, excluded


def _matching_components(
    decision: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    catalog = _catalog_index(snapshot)
    details = snapshot.get("details") or {}
    if not isinstance(details, Mapping):
        return []
    domains = [str(value) for value in decision.get("domains") or [] if value]
    if len(domains) != 1:
        return []
    matches: list[dict[str, Any]] = []
    for procedure_id in decision.get("procedure_ids") or []:
        procedure = catalog.get(str(procedure_id))
        if not procedure:
            continue
        detail = details.get(str(procedure.get("id") or ""))
        if not isinstance(detail, Mapping):
            continue
        legal_basis = [
            str(item.get("code") or "").strip()
            for item in detail.get("legalBasisesDetails") or []
            if isinstance(item, Mapping) and str(item.get("code") or "").strip()
        ]
        source_page = str(decision.get("official_source_url") or "").strip()
        if not source_page:
            source_page = (
                f"{PORTAL_ORIGIN}/thu-tuc-hanh-chinh/"
                f"{procedure.get('id')}"
            )
        for component in collect_profile_components(dict(detail)):
            if classify_component_kind(component) != "applicant_form":
                continue
            name = str(component.get("name") or "").strip()
            eform = _is_interactive_eform(name)
            instrument = resolve_single_issuing_instrument(
                {"form_name": name, "issuing_instruments": legal_basis}
            )
            codes = extract_strict_form_codes(name) or [None]
            for code in codes:
                identity_id = _identity_key(
                    domain=domains[0],
                    code=code,
                    instrument=instrument,
                    title_signature=_title_signature(name),
                    eform=eform,
                )
                if identity_id != str(decision.get("identity_id") or ""):
                    continue
                attachments = [
                    {
                        "attachment_id": str(item.get("id") or "").strip(),
                        "file_name": str(item.get("fileName") or "").strip(),
                        "bucket_name": str(item.get("bucketName") or "").strip(),
                        "file_path": str(item.get("filePath") or "").strip(),
                    }
                    for item in component.get("attachments") or []
                    if isinstance(item, Mapping)
                ]
                attachments, excluded_attachment_ids = _select_attachments_for_code(
                    attachments,
                    code,
                )
                matches.append(
                    {
                        "procedure_id": str(procedure_id),
                        "official_formality_id": str(procedure.get("id") or ""),
                        "source_page_url": source_page,
                        "component_id": str(
                            component.get("profileComponentId")
                            or component.get("id")
                            or ""
                        ),
                        "component_name": name,
                        "component_code": str(component.get("code") or ""),
                        "form_code": code,
                        "issuing_instrument": instrument,
                        "is_interactive_eform": eform,
                        "has_electronic_form": component.get("hasElectronicForm") is True,
                        "required": component.get("required") is True,
                        "original_quantity": component.get("originalQty"),
                        "copy_quantity": component.get("copyQty"),
                        "attachments": attachments,
                        "excluded_attachment_ids": excluded_attachment_ids,
                        "attachment_selection": (
                            "exact_form_code_filename"
                            if excluded_attachment_ids
                            else "all_official_component_attachments"
                        ),
                    }
                )
    matches.sort(
        key=lambda item: (
            item["procedure_id"],
            item["component_id"],
            item.get("form_code") or "",
        )
    )
    return matches


def build_collection_plan(
    decisions_payload: Mapping[str, Any],
    snapshot_payload: Mapping[str, Any],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for decision in decisions_payload.get("records") or []:
        if not isinstance(decision, Mapping):
            continue
        identity_id = str(decision.get("identity_id") or "").strip()
        if not identity_id or identity_id in seen:
            raise ValueError("FEATURE017_SOURCE_DECISION_IDENTITY_INVALID")
        seen.add(identity_id)
        matches = _matching_components(decision, snapshot_payload)
        decision_name = str(decision.get("decision") or "").strip()
        if decision_name != "source_approved":
            status = "NEEDS_SUPPLEMENT"
            asset_kind = None
        elif not matches:
            status = "SOURCE_COMPONENT_NOT_FOUND"
            asset_kind = None
        elif all(item["is_interactive_eform"] for item in matches):
            status = "READY_EFORM_METADATA"
            asset_kind = "eform"
        elif any(item["attachments"] for item in matches):
            status = "READY_ATTACHMENT_FETCH"
            asset_kind = "file"
        else:
            status = "OFFICIAL_INSTRUMENT_PACKAGE_REQUIRED"
            asset_kind = "file"
        metadata_evidence = {
            "identity_id": identity_id,
            "legal_as_of": str(snapshot_payload.get("legal_as_of") or ""),
            "source": str(snapshot_payload.get("source") or ""),
            "official_source_url": str(decision.get("official_source_url") or ""),
            "procedure_ids": sorted(
                str(value) for value in decision.get("procedure_ids") or []
            ),
            "components": matches,
        }
        records.append(
            {
                "identity_id": identity_id,
                "decision": decision_name,
                "status": status,
                "asset_kind": asset_kind,
                "form_code": decision.get("form_code"),
                "issuing_instrument": decision.get("issuing_instrument"),
                "canonical_name": decision.get("canonical_name"),
                "domains": list(decision.get("domains") or []),
                "procedure_ids": list(decision.get("procedure_ids") or []),
                "official_source_url": decision.get("official_source_url"),
                "metadata_snapshot_sha256": _canonical_json_sha256(
                    metadata_evidence
                ),
                "components": matches,
                "supplement_reason": decision.get("supplement_reason"),
            }
        )
    return {
        "schema_version": "feature017-source-collection-plan-v1",
        "candidate_only": True,
        "automated_approval": False,
        "attestation_created": False,
        "release_created": False,
        "legal_as_of": str(snapshot_payload.get("legal_as_of") or ""),
        "records": records,
    }


def build_supplement_queue(
    plan: Mapping[str, Any],
    snapshot_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Gather official evidence for supplements without inferring legal truth."""

    catalog = _catalog_index(snapshot_payload)
    details = snapshot_payload.get("details") or {}
    records: list[dict[str, Any]] = []
    for planned in plan.get("records") or []:
        if not isinstance(planned, Mapping):
            continue
        if str(planned.get("status") or "") != "NEEDS_SUPPLEMENT":
            continue
        legal_bases: dict[str, dict[str, str]] = {}
        official_procedures: list[dict[str, str]] = []
        for procedure_id in planned.get("procedure_ids") or []:
            procedure = catalog.get(str(procedure_id))
            if not procedure:
                continue
            formality_id = str(procedure.get("id") or "")
            official_procedures.append(
                {
                    "procedure_id": str(procedure_id),
                    "official_formality_id": formality_id,
                    "procedure_name": str(procedure.get("name") or "").strip(),
                    "source_page_url": (
                        f"{PORTAL_ORIGIN}/thu-tuc-hanh-chinh/{formality_id}"
                    ),
                }
            )
            detail = details.get(formality_id) if isinstance(details, Mapping) else None
            if not isinstance(detail, Mapping):
                continue
            for basis in detail.get("legalBasisesDetails") or []:
                if not isinstance(basis, Mapping):
                    continue
                code = str(basis.get("code") or "").strip()
                name = str(basis.get("name") or "").strip()
                if code:
                    legal_bases.setdefault(
                        fold(code),
                        {"document_number": code, "title": name},
                    )

        components: list[dict[str, Any]] = []
        attachment_ids: set[str] = set()
        for component in planned.get("components") or []:
            if not isinstance(component, Mapping):
                continue
            attachments = []
            for attachment in component.get("attachments") or []:
                if not isinstance(attachment, Mapping):
                    continue
                attachment_id = str(attachment.get("attachment_id") or "").strip()
                if not attachment_id or attachment_id in attachment_ids:
                    continue
                attachment_ids.add(attachment_id)
                attachments.append(
                    {
                        "attachment_id": attachment_id,
                        "file_name": str(attachment.get("file_name") or "").strip(),
                        "official_endpoint": DVC_ATTACHMENT_API_URL,
                    }
                )
            components.append(
                {
                    "procedure_id": str(component.get("procedure_id") or ""),
                    "component_id": str(component.get("component_id") or ""),
                    "component_name": str(component.get("component_name") or ""),
                    "observed_form_code": component.get("form_code"),
                    "observed_issuing_instrument": component.get(
                        "issuing_instrument"
                    ),
                    "is_interactive_eform": component.get(
                        "is_interactive_eform"
                    )
                    is True,
                    "attachments": attachments,
                }
            )

        missing_fields = []
        if not str(planned.get("form_code") or "").strip():
            missing_fields.append("form_code_or_descriptive_identity")
        if not str(planned.get("issuing_instrument") or "").strip():
            missing_fields.append("issuing_instrument")
        has_attachment = any(item["attachments"] for item in components)
        has_eform = any(item["is_interactive_eform"] for item in components)
        if not has_attachment and not has_eform:
            missing_fields.append("official_form_asset_or_verified_gap")
        records.append(
            {
                "identity_id": str(planned.get("identity_id") or ""),
                "workflow_status": "needs_supplement",
                "canonical_name": planned.get("canonical_name"),
                "domains": list(planned.get("domains") or []),
                "procedure_ids": list(planned.get("procedure_ids") or []),
                "current_form_code": planned.get("form_code"),
                "current_issuing_instrument": planned.get("issuing_instrument"),
                "supplement_reason": planned.get("supplement_reason"),
                "missing_fields": missing_fields,
                "official_procedures": official_procedures,
                "official_components": components,
                "official_legal_basis_candidates": sorted(
                    legal_bases.values(),
                    key=lambda item: fold(item["document_number"]),
                ),
                "evidence_summary": {
                    "official_attachment_count": len(attachment_ids),
                    "interactive_eform_observed": has_eform,
                    "legal_basis_candidate_count": len(legal_bases),
                },
                "allowed_next_action": "officer_resubmit_for_source_review",
                "automated_identity_selection": False,
                "automated_source_approval": False,
                "attestation_created": False,
                "release_created": False,
            }
        )
    records.sort(key=lambda item: item["identity_id"])
    payload = {
        "schema_version": "feature017-supplement-queue-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": str(snapshot_payload.get("legal_as_of") or ""),
        "candidate_only": True,
        "record_count": len(records),
        "records": records,
    }
    payload["manifest_sha256"] = _canonical_json_sha256(
        {key: value for key, value in payload.items() if key != "generated_at"}
    )
    return payload


DetailFetcher = Callable[[str, str], dict[str, Any]]


class OfficialDvcClient:
    """Browser-compatible client restricted to official DVC endpoints."""

    def __init__(self) -> None:
        self.client = httpx.Client(
            timeout=30,
            follow_redirects=False,
            http1=True,
            http2=False,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

    def __enter__(self) -> "OfficialDvcClient":
        warmup = self.client.get(
            PORTAL_ORIGIN,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        warmup.raise_for_status()
        if str(warmup.url).rstrip("/") != PORTAL_ORIGIN:
            raise ValueError("OFFICIAL_DVC_ORIGIN_REDIRECT_REJECTED")
        return self

    def __exit__(self, *args: object) -> None:
        self.client.close()

    def detail(self, formality_id: str, referer_url: str) -> dict[str, Any]:
        response = self.client.post(
            DETAIL_ENDPOINT,
            json={"id": formality_id},
            headers={
                "Accept": "application/json;odata=verbose",
                "Content-Type": "application/json; charset=UTF-8",
                "Origin": PORTAL_ORIGIN,
                "Referer": referer_url,
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        response.raise_for_status()
        if str(response.url) != DETAIL_ENDPOINT:
            raise ValueError("OFFICIAL_DVC_DETAIL_REDIRECT_REJECTED")
        payload = response.json()
        detail = payload.get("data") if isinstance(payload, Mapping) else None
        if payload.get("code") != "OK" or not isinstance(detail, Mapping):
            raise ValueError("OFFICIAL_DVC_DETAIL_INVALID")
        if str(detail.get("id") or "") != formality_id:
            raise ValueError("OFFICIAL_DVC_DETAIL_ID_MISMATCH")
        return dict(detail)


def refresh_official_snapshot(
    decisions_payload: Mapping[str, Any],
    base_snapshot: Mapping[str, Any],
    *,
    legal_as_of: str,
    detail_fetcher: DetailFetcher,
) -> dict[str, Any]:
    catalog = _catalog_index(base_snapshot)
    procedure_ids = sorted(
        {
            str(procedure_id)
            for decision in decisions_payload.get("records") or []
            if isinstance(decision, Mapping)
            for procedure_id in decision.get("procedure_ids") or []
        }
    )
    selected_catalog: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    for procedure_id in procedure_ids:
        procedure = catalog.get(procedure_id)
        if not procedure:
            errors.append(
                {
                    "procedure_id": procedure_id,
                    "reason_code": "OFFICIAL_PROCEDURE_NOT_IN_BASE_SNAPSHOT",
                }
            )
            continue
        selected_catalog.append(procedure)
        formality_id = str(procedure.get("id") or "")
        referer = f"{PORTAL_ORIGIN}/thu-tuc-hanh-chinh/{formality_id}"
        try:
            details[formality_id] = detail_fetcher(formality_id, referer)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            errors.append(
                {
                    "procedure_id": procedure_id,
                    "official_formality_id": formality_id,
                    "reason_code": str(exc) or type(exc).__name__,
                }
            )
    return {
        "schema_version": "feature017-official-source-snapshot-v1",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": date.fromisoformat(legal_as_of).isoformat(),
        "source": DETAIL_ENDPOINT,
        "candidate_only": True,
        "runtime_catalog_mutated": False,
        "scope": "feature017-approved-review-100",
        "procedure_count": len(procedure_ids),
        "detail_success_count": len(details),
        "detail_error_count": len(errors),
        "complete": not errors and len(details) == len(procedure_ids),
        "catalog": selected_catalog,
        "details": details,
        "detail_errors": errors,
    }


AttachmentFetcher = Callable[[str, str, str], tuple[bytes, str | None]]


def resolve_instrument_packages(
    collection: Mapping[str, Any],
    *,
    legal_as_of: str,
    output_dir: Path,
    refresh: bool,
) -> dict[str, Any]:
    """Resolve standalone forms from exact official instrument packages.

    This reuses the established VBPL/Công Báo package extractor and its
    effectivity gate.  Failures remain in the package-required state; no
    candidate is approved or attested here.
    """

    from api.form_source_resolution import classify_form_effectivity
    from scripts.resolve_three_tier_form_sources import (
        DEFAULT_CACHE_DIR,
        DEFAULT_EFFECTIVITY_EVIDENCE,
        _attach_form_effectivity_evidence,
        _read as read_resolution_json,
        _validated_form_effectivity_rules,
        build_pending_candidate,
        fetch_official_document,
        resolve_group_artifact_bounded,
    )

    effectivity_payload = read_resolution_json(
        DEFAULT_EFFECTIVITY_EVIDENCE,
        {"form_effectivity": []},
    )
    rules, rule_rejections = _validated_form_effectivity_rules(
        effectivity_payload if isinstance(effectivity_payload, Mapping) else {},
        legal_as_of=legal_as_of,
    )
    package_dir = output_dir / "instrument_artifacts"
    records: list[dict[str, Any]] = []
    bundle_cache: dict[str, dict[str, Any]] = {}
    for source_record in collection.get("records") or []:
        record = dict(source_record)
        if record.get("status") != "OFFICIAL_INSTRUMENT_PACKAGE_REQUIRED":
            records.append(record)
            continue
        instrument = str(record.get("issuing_instrument") or "").strip()
        form_code = str(record.get("form_code") or "").strip()
        if not instrument or not form_code:
            record["package_resolution_reason"] = "FORM_IDENTITY_INCOMPLETE"
            records.append(record)
            continue
        try:
            bundle = bundle_cache.get(instrument)
            if bundle is None:
                bundle = fetch_official_document(
                    instrument,
                    timeout=30,
                    cache_dir=DEFAULT_CACHE_DIR,
                    refresh=refresh,
                    fallback_urls=[str(record.get("official_source_url") or "")],
                    legal_as_of=legal_as_of,
                )
                document = bundle.get("document")
                if isinstance(document, Mapping):
                    bundle["document"] = _attach_form_effectivity_evidence(
                        document,
                        rules,
                    )
                bundle_cache[instrument] = bundle
            document = bundle.get("document")
            if not isinstance(document, Mapping):
                record["package_resolution_reason"] = str(
                    bundle.get("reason_code")
                    or "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND"
                )
                records.append(record)
                continue
            appendix = extract_appendix_identifier(record.get("canonical_name"))
            effectivity = classify_form_effectivity(
                document,
                legal_as_of=legal_as_of,
                appendix_identifier=appendix,
                form_code=form_code,
            )
            record["package_effectivity"] = effectivity
            if effectivity.get("eligible") is not True:
                record["package_resolution_reason"] = str(
                    effectivity.get("reason_code") or "FORM_EFFECTIVITY_UNVERIFIED"
                )
                records.append(record)
                continue
            group = {
                "group_id": str(record.get("identity_id") or ""),
                "form_code": form_code,
                "form_name": str(record.get("canonical_name") or ""),
                "issuing_instrument": instrument,
                "appendix_identifier": appendix,
                "procedure_ids": list(record.get("procedure_ids") or []),
                "domains": list(record.get("domains") or []),
                "domain": (record.get("domains") or ["unknown"])[0],
                "source_tier": "central",
            }
            artifact, reason = resolve_group_artifact_bounded(
                group,
                bundle,
                timeout=30,
                download_dir=package_dir,
            )
            if artifact is None:
                record["package_resolution_reason"] = reason
                records.append(record)
                continue
            procedure_ids = list(record.get("procedure_ids") or [])
            candidate = build_pending_candidate(
                group=group,
                document=document,
                artifact=artifact,
                legal_as_of=legal_as_of,
                procedure_id=str(procedure_ids[0] if procedure_ids else ""),
            )
            local_path = str(candidate.get("local_path") or "")
            path = ROOT / local_path
            expected_sha256 = str(candidate.get("sha256") or "")
            if (
                not path.is_file()
                or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
                or _sha256_path(path) != expected_sha256
            ):
                record["package_resolution_reason"] = "PACKAGE_ARTIFACT_CHECKSUM_INVALID"
                records.append(record)
                continue
            record["status"] = "READY_FOR_LEGAL_ENRICHMENT"
            record["source_checksum"] = expected_sha256
            record["package_resolution_reason"] = reason
            record["canonical_artifact"] = {
                "asset_kind": "file",
                "sha256": expected_sha256,
                "size_bytes": int(candidate.get("size_bytes") or path.stat().st_size),
                "staging_path": local_path,
                "source_page_url": candidate.get("source_page_url"),
                "source_download_url": candidate.get("source_download_url"),
                "source_package_sha256": candidate.get("source_package_sha256"),
                "extraction": candidate.get("extraction"),
            }
            record["package_candidate"] = candidate
        except (OSError, ValueError, httpx.HTTPError) as exc:
            record["package_resolution_reason"] = str(exc) or type(exc).__name__
        records.append(record)

    result = dict(collection)
    result["records"] = records
    result["status_counts"] = dict(
        sorted(Counter(str(item.get("status") or "UNKNOWN") for item in records).items())
    )
    result["effectivity_rule_rejection_count"] = len(rule_rejections)
    result["instrument_package_resolution_enabled"] = True
    result["automated_approval"] = False
    result["attestation_created"] = False
    result["release_created"] = False
    return result


def build_instrument_package_review_queue(
    collection: Mapping[str, Any],
) -> dict[str, Any]:
    """Project unresolved package results into deterministic Admin work."""

    next_action_by_reason = {
        "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND": (
            "locate_exact_official_document_and_verify_effectivity"
        ),
        "PARTIAL_EFFECTIVITY_REQUIRES_REVIEW": (
            "verify_exact_form_scope_in_amending_or_repealing_instrument"
        ),
        "ISSUING_INSTRUMENT_EXPIRED": (
            "verify_replacement_form_or_confirm_historical_verified_gap"
        ),
        "OFFICIAL_FORM_FILE_NOT_FOUND": (
            "reconcile_form_code_instrument_and_official_package"
        ),
        "AMBIGUOUS_OFFICIAL_PDF_PACKAGE": (
            "select_exact_form_page_range_and_checksum"
        ),
    }
    records = []
    for item in collection.get("records") or []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "") != "OFFICIAL_INSTRUMENT_PACKAGE_REQUIRED":
            continue
        reason_code = str(
            item.get("package_resolution_reason") or "PACKAGE_EVIDENCE_REQUIRED"
        )
        records.append(
            {
                "identity_id": str(item.get("identity_id") or ""),
                "form_code": item.get("form_code"),
                "issuing_instrument": item.get("issuing_instrument"),
                "canonical_name": item.get("canonical_name"),
                "domains": list(item.get("domains") or []),
                "procedure_ids": list(item.get("procedure_ids") or []),
                "official_source_url": item.get("official_source_url"),
                "reason_code": reason_code,
                "effectivity_evidence": item.get("package_effectivity"),
                "required_next_action": next_action_by_reason.get(
                    reason_code,
                    "manual_exact_source_review",
                ),
                "suggested_review_outcome": (
                    "replacement_or_verified_gap_review"
                    if reason_code == "ISSUING_INSTRUMENT_EXPIRED"
                    else "source_supplement_review"
                ),
                "automated_verified_gap": False,
                "automated_attestation": False,
                "automated_release": False,
            }
        )
    records.sort(key=lambda item: item["identity_id"])
    payload = {
        "schema_version": "feature017-instrument-package-review-queue-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": str(collection.get("legal_as_of") or ""),
        "candidate_only": True,
        "record_count": len(records),
        "reason_counts": dict(
            sorted(Counter(item["reason_code"] for item in records).items())
        ),
        "records": records,
    }
    payload["manifest_sha256"] = _canonical_json_sha256(
        {key: value for key, value in payload.items() if key != "generated_at"}
    )
    return payload


def fetch_dvc_attachment(
    attachment_id: str,
    file_name: str,
    referer_url: str,
) -> tuple[bytes, str | None]:
    try:
        canonical_id = str(UUID(attachment_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("OFFICIAL_DVC_ATTACHMENT_ID_INVALID") from exc
    if canonical_id != attachment_id.casefold():
        raise ValueError("OFFICIAL_DVC_ATTACHMENT_ID_INVALID")
    if not file_name or Path(file_name).name != file_name:
        raise ValueError("OFFICIAL_DVC_ATTACHMENT_NAME_INVALID")
    if not str(referer_url).startswith("https://dichvucong.gov.vn/"):
        raise ValueError("OFFICIAL_DVC_ATTACHMENT_REFERER_INVALID")
    with httpx.Client(
        timeout=30,
        follow_redirects=False,
        http1=True,
        http2=False,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    ) as client:
        warmup = client.get(
            referer_url,
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        warmup.raise_for_status()
        if str(warmup.url) != referer_url:
            raise ValueError("OFFICIAL_DVC_REFERER_REDIRECT_REJECTED")
        response = client.post(
            DVC_ATTACHMENT_API_URL,
            json={"fileId": attachment_id},
            headers={
                "Accept": "application/json;odata=verbose",
                "Content-Type": "application/json; charset=UTF-8",
                "Origin": PORTAL_ORIGIN,
                "Referer": referer_url,
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        response.raise_for_status()
        if str(response.url) != DVC_ATTACHMENT_API_URL:
            raise ValueError("OFFICIAL_DVC_ATTACHMENT_REDIRECT_REJECTED")
        if len(response.content) > MAX_FILE_BYTES:
            raise ValueError("FILE_TOO_LARGE")
        return response.content, response.headers.get("content-type")


def execute_collection_plan(
    plan: Mapping[str, Any],
    *,
    output_dir: Path,
    network: bool,
    fetcher: AttachmentFetcher = fetch_dvc_attachment,
) -> dict[str, Any]:
    artifact_dir = output_dir / "artifacts"
    cached_artifacts: dict[str, dict[str, Any]] = {}
    previous_result_path = output_dir / "collection-result.json"
    if previous_result_path.is_file():
        try:
            previous = _read(previous_result_path)
            for previous_record in previous.get("records") or []:
                for artifact in previous_record.get("artifacts") or []:
                    attachment_id = str(artifact.get("attachment_id") or "")
                    staging_path = str(artifact.get("staging_path") or "")
                    path = ROOT / staging_path
                    expected_sha256 = str(artifact.get("sha256") or "")
                    expected_size = int(artifact.get("size_bytes") or 0)
                    if (
                        attachment_id
                        and path.is_file()
                        and path.is_relative_to(artifact_dir)
                        and path.stat().st_size == expected_size
                        and _sha256_path(path) == expected_sha256
                    ):
                        cached_artifact = dict(artifact)
                        cached_artifact["semantic_sha256"] = (
                            cached_artifact.get("semantic_sha256")
                            or _docx_semantic_sha256(path)
                        )
                        cached_artifacts[attachment_id] = cached_artifact
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            cached_artifacts = {}
    records: list[dict[str, Any]] = []
    for planned in plan.get("records") or []:
        record = {key: value for key, value in dict(planned).items() if key != "components"}
        record["source_checksum"] = None
        record["artifacts"] = []
        status = str(record.get("status") or "")
        if status == "READY_EFORM_METADATA":
            record["source_checksum"] = record["metadata_snapshot_sha256"]
            record["status"] = "READY_FOR_LEGAL_ENRICHMENT"
            record["canonical_artifact"] = {
                "asset_kind": "eform",
                "official_url": record.get("official_source_url"),
                "sha256": record["source_checksum"],
            }
        elif status == "READY_ATTACHMENT_FETCH" and not network:
            record["status"] = "ATTACHMENT_FETCH_PENDING"
        elif status == "READY_ATTACHMENT_FETCH":
            unique_requests: dict[str, tuple[str, str]] = {}
            for component in planned.get("components") or []:
                referer = str(component.get("source_page_url") or "")
                for attachment in component.get("attachments") or []:
                    attachment_id = str(attachment.get("attachment_id") or "")
                    file_name = str(attachment.get("file_name") or "")
                    if attachment_id:
                        unique_requests[attachment_id] = (file_name, referer)
            errors: list[str] = []
            for attachment_id, (file_name, referer) in sorted(unique_requests.items()):
                cached = cached_artifacts.get(attachment_id)
                if cached is not None:
                    record["artifacts"].append(cached)
                    continue
                try:
                    content, content_type = fetcher(attachment_id, file_name, referer)
                    if len(content) > MAX_FILE_BYTES:
                        raise ValueError("FILE_TOO_LARGE")
                    validation_url = (
                        f"https://dichvucong.gov.vn/{quote(file_name, safe='')}"
                    )
                    valid, reason, file_format = validate_download(
                        content,
                        content_type,
                        validation_url,
                    )
                    if not valid or not file_format:
                        raise ValueError(reason)
                    scan_ok, scan_reason = _basic_malware_scan(content)
                    if not scan_ok:
                        raise ValueError(scan_reason)
                    digest = hashlib.sha256(content).hexdigest()
                    artifact_dir.mkdir(parents=True, exist_ok=True)
                    target = artifact_dir / f"{digest[:24]}.{file_format}"
                    if not target.exists():
                        temporary = target.with_suffix(target.suffix + ".tmp")
                        temporary.write_bytes(content)
                        temporary.replace(target)
                    record["artifacts"].append(
                        {
                            "attachment_id": attachment_id,
                            "file_name": file_name,
                            "file_format": file_format,
                            "sha256": digest,
                            "semantic_sha256": _docx_semantic_sha256(target),
                            "size_bytes": len(content),
                            "staging_path": str(target.relative_to(ROOT)).replace("\\", "/")
                            if target.is_relative_to(ROOT)
                            else None,
                            "official_endpoint": DVC_ATTACHMENT_API_URL,
                            "referer_url": referer,
                        }
                    )
                except (httpx.HTTPError, OSError, ValueError) as exc:
                    errors.append(str(exc) or type(exc).__name__)
            checksums = sorted(
                {str(item["sha256"]) for item in record["artifacts"]}
            )
            if errors:
                record["status"] = "ATTACHMENT_FETCH_FAILED"
                record["reason_codes"] = sorted(set(errors))
            elif not checksums:
                record["status"] = "ATTACHMENT_FETCH_FAILED"
                record["reason_codes"] = ["OFFICIAL_ATTACHMENT_NOT_AVAILABLE"]
            elif len(checksums) > 1:
                semantic_hashes = []
                for artifact in record["artifacts"]:
                    staging_path = str(artifact.get("staging_path") or "")
                    semantic_hashes.append(
                        str(artifact.get("semantic_sha256") or "")
                        or (
                            _docx_semantic_sha256(ROOT / staging_path)
                            if staging_path
                            else None
                        )
                    )
                distinct_semantic = {
                    value for value in semantic_hashes if value is not None
                }
                if (
                    len(distinct_semantic) == 1
                    and len(semantic_hashes) == len(record["artifacts"])
                    and all(semantic_hashes)
                ):
                    record["status"] = "READY_FOR_LEGAL_ENRICHMENT"
                    record["semantic_equivalence_sha256"] = next(
                        iter(distinct_semantic)
                    )
                    record["reason_codes"] = [
                        "OFFICIAL_BINARY_VARIANTS_SEMANTICALLY_EQUIVALENT"
                    ]
                    canonical = sorted(
                        record["artifacts"],
                        key=lambda item: (
                            str(item.get("attachment_id") or ""),
                            str(item.get("staging_path") or ""),
                        ),
                    )[0]
                    record["source_checksum"] = str(canonical["sha256"])
                    record["canonical_artifact"] = {
                        **canonical,
                        "asset_kind": "file",
                        "semantic_equivalence_sha256": record[
                            "semantic_equivalence_sha256"
                        ],
                        "binary_variant_sha256": checksums,
                        "provenance_attachment_ids": sorted(
                            str(item.get("attachment_id") or "")
                            for item in record["artifacts"]
                        ),
                    }
                else:
                    record["status"] = "AMBIGUOUS_OFFICIAL_ATTACHMENTS"
                    record["reason_codes"] = [
                        "MULTIPLE_DISTINCT_OFFICIAL_FILES"
                    ]
            else:
                record["status"] = "READY_FOR_LEGAL_ENRICHMENT"
                record["source_checksum"] = checksums[0]
                canonical = sorted(
                    record["artifacts"],
                    key=lambda item: (
                        str(item.get("attachment_id") or ""),
                        str(item.get("staging_path") or ""),
                    ),
                )[0]
                record["canonical_artifact"] = {
                    **canonical,
                    "asset_kind": "file",
                    "provenance_attachment_ids": sorted(
                        str(item.get("attachment_id") or "")
                        for item in record["artifacts"]
                    ),
                }
        records.append(record)

    counts = Counter(str(item.get("status") or "UNKNOWN") for item in records)
    result = {
        "schema_version": "feature017-source-collection-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": str(plan.get("legal_as_of") or ""),
        "candidate_only": True,
        "network_enabled": network,
        "automated_approval": False,
        "attestation_created": False,
        "release_created": False,
        "runtime_catalog_mutated": False,
        "record_count": len(records),
        "status_counts": dict(sorted(counts.items())),
        "records": records,
    }
    result["manifest_sha256"] = _canonical_json_sha256(
        {key: value for key, value in result.items() if key != "generated_at"}
    )
    return result


def collect_supplement_attachment_evidence(
    queue: Mapping[str, Any],
    *,
    output_dir: Path,
    network: bool,
    fetcher: AttachmentFetcher = fetch_dvc_attachment,
) -> dict[str, Any]:
    """Quarantine official supplement files without approving their identity."""

    artifact_dir = output_dir / "supplement_artifacts"
    procedure_pages: dict[str, str] = {}
    records: list[dict[str, Any]] = []
    for queued in queue.get("records") or []:
        if not isinstance(queued, Mapping):
            continue
        procedure_pages.clear()
        for procedure in queued.get("official_procedures") or []:
            if not isinstance(procedure, Mapping):
                continue
            procedure_pages[str(procedure.get("procedure_id") or "")] = str(
                procedure.get("source_page_url") or ""
            )
        requests: dict[str, tuple[str, str]] = {}
        for component in queued.get("official_components") or []:
            if not isinstance(component, Mapping):
                continue
            referer = procedure_pages.get(
                str(component.get("procedure_id") or ""),
                "",
            )
            for attachment in component.get("attachments") or []:
                if not isinstance(attachment, Mapping):
                    continue
                attachment_id = str(attachment.get("attachment_id") or "").strip()
                file_name = str(attachment.get("file_name") or "").strip()
                if attachment_id:
                    requests[attachment_id] = (file_name, referer)

        record = {
            "identity_id": str(queued.get("identity_id") or ""),
            "canonical_name": queued.get("canonical_name"),
            "procedure_ids": list(queued.get("procedure_ids") or []),
            "artifacts": [],
            "identity_confirmed": False,
            "source_approved": False,
            "attestation_created": False,
            "release_created": False,
        }
        if not requests:
            record["status"] = "NO_OFFICIAL_ATTACHMENT"
            records.append(record)
            continue
        if not network:
            record["status"] = "ATTACHMENT_FETCH_PENDING"
            record["attachment_ids"] = sorted(requests)
            records.append(record)
            continue

        errors: list[dict[str, str]] = []
        for attachment_id, (file_name, referer) in sorted(requests.items()):
            try:
                content, content_type = fetcher(attachment_id, file_name, referer)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError("FILE_TOO_LARGE")
                validation_url = (
                    f"https://dichvucong.gov.vn/{quote(file_name, safe='')}"
                )
                valid, reason, file_format = validate_download(
                    content,
                    content_type,
                    validation_url,
                )
                if not valid or not file_format:
                    raise ValueError(reason)
                scan_ok, scan_reason = _basic_malware_scan(content)
                if not scan_ok:
                    raise ValueError(scan_reason)
                digest = hashlib.sha256(content).hexdigest()
                artifact_dir.mkdir(parents=True, exist_ok=True)
                target = artifact_dir / f"{digest[:24]}.{file_format}"
                if not target.exists():
                    temporary = target.with_suffix(target.suffix + ".tmp")
                    temporary.write_bytes(content)
                    temporary.replace(target)
                record["artifacts"].append(
                    {
                        "attachment_id": attachment_id,
                        "file_name": file_name,
                        "file_format": file_format,
                        "sha256": digest,
                        "semantic_sha256": _docx_semantic_sha256(target),
                        "size_bytes": len(content),
                        "staging_path": str(target.relative_to(ROOT)).replace(
                            "\\", "/"
                        )
                        if target.is_relative_to(ROOT)
                        else None,
                        "official_endpoint": DVC_ATTACHMENT_API_URL,
                        "referer_url": referer,
                    }
                )
            except (httpx.HTTPError, OSError, ValueError) as exc:
                errors.append(
                    {
                        "attachment_id": attachment_id,
                        "reason": str(exc) or type(exc).__name__,
                    }
                )
        record["errors"] = errors
        if record["artifacts"] and errors:
            record["status"] = "EVIDENCE_PARTIAL"
        elif record["artifacts"]:
            record["status"] = "EVIDENCE_COLLECTED"
        else:
            record["status"] = "ATTACHMENT_FETCH_FAILED"
        records.append(record)

    records.sort(key=lambda item: item["identity_id"])
    counts = Counter(str(item["status"]) for item in records)
    result = {
        "schema_version": "feature017-supplement-evidence-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": str(queue.get("legal_as_of") or ""),
        "candidate_only": True,
        "network_enabled": network,
        "record_count": len(records),
        "status_counts": dict(sorted(counts.items())),
        "runtime_catalog_mutated": False,
        "automated_source_approval": False,
        "attestation_created": False,
        "release_created": False,
        "records": records,
    }
    result["manifest_sha256"] = _canonical_json_sha256(
        {key: value for key, value in result.items() if key != "generated_at"}
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--refresh-official", action="store_true")
    parser.add_argument("--resolve-instrument-packages", action="store_true")
    parser.add_argument("--collect-supplement-evidence", action="store_true")
    parser.add_argument("--legal-as-of", default=date.today().isoformat())
    args = parser.parse_args()

    decisions = _read(args.decisions)
    snapshot = _read(args.snapshot)
    snapshot_path = args.snapshot
    if args.refresh_official:
        with OfficialDvcClient() as official_client:
            snapshot = refresh_official_snapshot(
                decisions,
                snapshot,
                legal_as_of=args.legal_as_of,
                detail_fetcher=official_client.detail,
            )
        snapshot_path = args.output_dir / "official-snapshot-current.json"
        _write_json(snapshot_path, snapshot)
    plan = build_collection_plan(decisions, snapshot)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(args.output_dir / "collection-plan.json", plan)
    supplement_queue = build_supplement_queue(plan, snapshot)
    _write_json(args.output_dir / "supplement-queue.json", supplement_queue)
    if args.collect_supplement_evidence:
        supplement_evidence = collect_supplement_attachment_evidence(
            supplement_queue,
            output_dir=args.output_dir,
            network=args.network,
        )
        _write_json(
            args.output_dir / "supplement-evidence-result.json",
            supplement_evidence,
        )
    result = execute_collection_plan(
        plan,
        output_dir=args.output_dir,
        network=args.network,
    )
    if args.resolve_instrument_packages:
        result = resolve_instrument_packages(
            result,
            legal_as_of=date.fromisoformat(args.legal_as_of).isoformat(),
            output_dir=args.output_dir,
            refresh=args.refresh_official,
        )
    package_review_queue = build_instrument_package_review_queue(result)
    _write_json(
        args.output_dir / "instrument-package-review-queue.json",
        package_review_queue,
    )
    result["decision_input_sha256"] = _sha256_path(args.decisions)
    result["official_snapshot_sha256"] = _sha256_path(snapshot_path)
    result["requested_legal_as_of"] = date.fromisoformat(args.legal_as_of).isoformat()
    result["source_snapshot_legal_as_of"] = str(snapshot.get("legal_as_of") or "")
    result["manifest_sha256"] = _canonical_json_sha256(
        {
            key: value
            for key, value in result.items()
            if key not in {"generated_at", "manifest_sha256"}
        }
    )
    _write_json(args.output_dir / "collection-result.json", result)
    print(
        json.dumps(
            {
                "record_count": result["record_count"],
                "status_counts": result["status_counts"],
                "network_enabled": result["network_enabled"],
                "runtime_catalog_mutated": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
