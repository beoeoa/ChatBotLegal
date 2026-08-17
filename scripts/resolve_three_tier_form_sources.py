#!/usr/bin/env python3
"""Resolve three-tier form occurrences to exact official VBPL attachments.

The command is deterministic and fail-closed.  It can add technically eligible
records to the existing candidate queue, but never approves or runtime-serves
them.  Legal review remains an authenticated Admin action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import queue
import re
import shutil
import sys
import unicodedata
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_source_resolution import (
    build_pending_candidate,
    classify_effectivity,
    classify_form_effectivity,
    extract_appendix_heading_identifier,
    extract_appendix_identifier,
    extract_issuing_instruments,
    extract_strict_form_codes,
    group_occurrences,
    locate_pdf_form_page_range,
    merge_pending_records,
    normalize_appendix_identifier,
    normalize_document_number,
    quarantine_stale_pending_records,
    select_exact_document,
    select_standalone_form_file,
)
from api.official_source_adapters import (
    extract_exact_instrument_links,
    load_curated_government_document,
    ordered_fallback_urls,
)
from api.official_http import build_verified_ssl_context
from api.official_source_diagnostics import (
    classify_official_source_failure,
    require_official_component_response,
)
from api.source_gap_jobs import _basic_malware_scan
from scripts.crawl_canonical_forms import is_allowed_official_url, validate_download

VBPL_ACTION_URL = "https://vbpl.vn/van-ban/trung-uong"
VBPL_SEARCH_ACTION = "c529d164f28418e5898a834422629e64c6816af1"
VBPL_FILE_LIST_ACTION = "7b29f485c8e43b71a94bfc11b54459d7e27293e6"
VBPL_FILE_API = (
    "https://vbpl-bientap-gateway.moj.gov.vn/api/qtdc/public/doc/minio/"
    "buckets/vbpl"
)
USER_AGENT = "ChatBotLegal-ThreeTierFormResolver/1.0"
DVC_ATTACHMENT_API_URL = (
    "https://dichvucong.gov.vn/api/v1/submitting/preview-attachment"
)
DVC_PUBLISHER = "Cổng Dịch vụ công quốc gia"
DEFAULT_INVENTORY = (
    ROOT / "notebook_data" / "forms" / "three_tier_form_inventory_v1.json"
)
DEFAULT_GROUPS = (
    ROOT / "notebook_data" / "forms" / "three_tier_form_groups_v1.json"
)
DEFAULT_RESOLUTION = (
    ROOT / "notebook_data" / "forms" / "three_tier_form_source_resolution_v1.json"
)
DEFAULT_SHORTLIST = (
    ROOT / "notebook_data" / "forms" / "three_tier_form_review_shortlist_v2.json"
)
DEFAULT_QUEUE = (
    ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json"
)
DEFAULT_CACHE_DIR = ROOT / "data" / "source_cache" / "vbpl_form_documents"
DEFAULT_OCR_CACHE_DIR = DEFAULT_CACHE_DIR / "ocr"
DEFAULT_DOWNLOAD_DIR = (
    ROOT / "data" / "uploads" / "forms" / "three_tier_official_candidates"
)
DEFAULT_EFFECTIVITY_EVIDENCE = (
    ROOT
    / "notebook_data"
    / "forms"
    / "official_form_effectivity_evidence_v1.json"
)
DEFAULT_REPORT = (
    ROOT / "reports" / "feature005" / "three-tier-form-resolution-latest.json"
)

# A durable checkpoint may contain a technical failure caused by a bounded
# worker deadline or a transient official-source outage. Those records are not
# terminal legal/data decisions: a resumed campaign must try the same group
# again instead of permanently counting it as processed.
RETRYABLE_GROUP_REASON_CODES = frozenset(
    {
        "OFFICIAL_ARTIFACT_PROCESSING_TIMEOUT",
        "OFFICIAL_ARTIFACT_PROCESSING_FAILED",
        "OFFICIAL_FORM_DOWNLOAD_FAILED",
        "OFFICIAL_SOURCE_DNS_FAILURE",
        "OFFICIAL_SOURCE_TLS_FAILURE",
        "OFFICIAL_SOURCE_CONNECT_TIMEOUT",
        "OFFICIAL_SOURCE_READ_TIMEOUT",
        "OFFICIAL_SOURCE_RATE_LIMITED",
        "OFFICIAL_SOURCE_SERVER_ERROR",
    }
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


_PROVISION_TOKEN_RE = re.compile(
    r"[1-9]\d*(?:\.(?:[1-9]\d*|[a-zđ]))*",
    flags=re.IGNORECASE,
)


def _provision_tokens(values: Any) -> tuple[set[str], bool]:
    if not isinstance(values, list) or not values:
        return set(), False
    tokens = {
        str(value).strip().casefold()
        for value in values
        if _PROVISION_TOKEN_RE.fullmatch(str(value).strip())
    }
    return tokens, len(tokens) == len(values)


def _provision_scopes_overlap(left: set[str], right: set[str]) -> bool:
    for left_value in left:
        left_parts = left_value.split(".")
        for right_value in right:
            right_parts = right_value.split(".")
            shared = min(len(left_parts), len(right_parts))
            if left_parts[:shared] == right_parts[:shared]:
                return True
    return False


def _form_code_tokens(values: Any) -> tuple[set[str], bool]:
    if not isinstance(values, list) or not values:
        return set(), False
    tokens: list[str] = []
    for value in values:
        codes = extract_strict_form_codes(f"Mẫu {value}")
        if len(codes) != 1:
            return set(), False
        tokens.append(codes[0])
    unique = set(tokens)
    return unique, len(unique) == len(tokens)


def _validate_current_dvc_attachment_rule(
    rule: Mapping[str, Any],
    *,
    target_code: str,
    instrument: str,
    legal_as_of: date,
) -> str | None:
    """Validate a fixed DVC attachment contract, never an arbitrary request."""

    appendix = normalize_appendix_identifier(rule.get("appendix_identifier"))
    history_url = str(rule.get("history_source_url") or "").strip()
    current_status_url = str(
        rule.get("current_status_source_url") or ""
    ).strip()
    decision_number = str(
        rule.get("publication_decision_number") or ""
    ).strip()
    if (
        not appendix
        or not history_url
        or not current_status_url
        or not decision_number
    ):
        return "FORM_EFFECTIVITY_DVC_BINDING_INCOMPLETE"
    if not is_allowed_official_url(history_url) or not is_allowed_official_url(
        current_status_url
    ):
        return "FORM_EFFECTIVITY_DVC_BINDING_SOURCE_NOT_OFFICIAL"

    try:
        effective_from = date.fromisoformat(
            str(rule.get("effective_from") or "")[:10]
        )
        effective_to = date.fromisoformat(
            str(rule.get("effective_to") or "")[:10]
        )
    except ValueError:
        return "FORM_EFFECTIVITY_DVC_CURRENT_WINDOW_INVALID"
    if effective_from > legal_as_of or effective_to < legal_as_of:
        return "FORM_EFFECTIVITY_DVC_CURRENT_WINDOW_INVALID"

    bindings = rule.get("procedure_bindings")
    if not isinstance(bindings, list) or not bindings:
        return "FORM_EFFECTIVITY_DVC_BINDING_INCOMPLETE"
    procedure_ids: set[str] = set()
    normalized_instrument = normalize_document_number(instrument)
    for binding in bindings:
        if not isinstance(binding, Mapping):
            return "FORM_EFFECTIVITY_DVC_BINDING_INCOMPLETE"
        procedure_id = str(binding.get("procedure_id") or "").strip()
        binding_url = str(binding.get("official_source_url") or "").strip()
        binding_text = str(binding.get("binding_text") or "").strip()
        if (
            not re.fullmatch(r"\d+\.\d{6}", procedure_id)
            or procedure_id in procedure_ids
            or not is_allowed_official_url(binding_url)
            or urlparse(binding_url).hostname != "dichvucong.gov.vn"
            or not binding_text
        ):
            return "FORM_EFFECTIVITY_DVC_BINDING_INCOMPLETE"
        if (
            extract_strict_form_codes(binding_text) != [target_code]
            or extract_appendix_identifier(binding_text) != appendix
            or normalized_instrument
            not in {
                normalize_document_number(item)
                for item in extract_issuing_instruments(binding_text)
            }
        ):
            return "FORM_EFFECTIVITY_DVC_BINDING_IDENTITY_MISMATCH"
        procedure_ids.add(procedure_id)

    attachment = rule.get("canonical_artifact_attachment")
    if not isinstance(attachment, Mapping):
        return "FORM_EFFECTIVITY_DVC_ATTACHMENT_INTEGRITY_INCOMPLETE"
    api_url = str(attachment.get("api_url") or "").strip()
    attachment_id = str(attachment.get("attachment_id") or "").strip()
    file_name = str(attachment.get("file_name") or "").strip()
    checksum = str(attachment.get("sha256") or "").strip().casefold()
    referer_url = str(attachment.get("referer_url") or "").strip()
    source_page_url = str(attachment.get("source_page_url") or "").strip()
    try:
        size_bytes = int(attachment.get("size_bytes") or 0)
    except (TypeError, ValueError):
        size_bytes = 0
    if api_url != DVC_ATTACHMENT_API_URL:
        return "FORM_EFFECTIVITY_DVC_ATTACHMENT_ENDPOINT_INVALID"
    try:
        canonical_attachment_id = str(UUID(attachment_id))
    except (ValueError, AttributeError):
        canonical_attachment_id = ""
    if (
        canonical_attachment_id != attachment_id.casefold()
        or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        or size_bytes <= 0
        or size_bytes > 25 * 1024 * 1024
        or not file_name
        or Path(file_name).name != file_name
        or "/" in file_name
        or "\\" in file_name
        or not file_name.casefold().endswith((".pdf", ".doc", ".docx"))
    ):
        return "FORM_EFFECTIVITY_DVC_ATTACHMENT_INTEGRITY_INCOMPLETE"
    if (
        not is_allowed_official_url(referer_url)
        or not is_allowed_official_url(source_page_url)
        or urlparse(referer_url).hostname != "dichvucong.gov.vn"
        or urlparse(source_page_url).hostname != "dichvucong.gov.vn"
    ):
        return "FORM_EFFECTIVITY_DVC_ATTACHMENT_SOURCE_NOT_OFFICIAL"
    return None


def _validated_form_effectivity_rules(
    payload: Mapping[str, Any],
    *,
    legal_as_of: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Validate curated exact-form evidence without turning it into approval."""

    as_of = date.fromisoformat(legal_as_of)
    accepted: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    for raw in payload.get("form_effectivity") or []:
        if not isinstance(raw, Mapping):
            continue
        rule = dict(raw)
        evidence_id = str(rule.get("evidence_id") or "").strip()

        def reject(reason_code: str) -> None:
            rejections.append(
                {
                    "evidence_id": evidence_id or "UNIDENTIFIED_EVIDENCE",
                    "reason_code": reason_code,
                }
            )

        instrument = str(rule.get("issuing_instrument") or "").strip()
        target_code = extract_strict_form_codes(
            f"Mẫu {rule.get('target_form_code') or ''}"
        )
        source_url = str(rule.get("official_source_url") or "").strip()
        history_url = str(rule.get("history_source_url") or "").strip()
        try:
            verified_as_of = date.fromisoformat(
                str(rule.get("verified_as_of") or "")[:10]
            )
        except ValueError:
            reject("FORM_EFFECTIVITY_VERIFIED_DATE_INVALID")
            continue
        if not evidence_id or not instrument or len(target_code) != 1:
            reject("FORM_EFFECTIVITY_IDENTITY_INCOMPLETE")
            continue
        if not is_allowed_official_url(source_url) or (
            history_url and not is_allowed_official_url(history_url)
        ):
            reject("FORM_EFFECTIVITY_SOURCE_NOT_OFFICIAL")
            continue
        if verified_as_of > as_of:
            reject("FORM_EFFECTIVITY_EVIDENCE_FROM_FUTURE")
            continue
        status = str(rule.get("status") or "").strip().casefold()
        if status not in {"active", "current", "expired", "replaced", "superseded"}:
            reject("FORM_EFFECTIVITY_STATUS_INVALID")
            continue
        if status in {"active", "current"}:
            evidence_basis = str(rule.get("evidence_basis") or "").strip()
            if evidence_basis == "official_current_dvc_attachment":
                reason_code = _validate_current_dvc_attachment_rule(
                    rule,
                    target_code=target_code[0],
                    instrument=instrument,
                    legal_as_of=as_of,
                )
                if reason_code:
                    reject(reason_code)
                    continue
            elif evidence_basis == "official_current_form_attachment":
                attachment_source_url = str(
                    rule.get("official_attachment_source_url") or ""
                ).strip()
                current_status_url = str(
                    rule.get("current_status_source_url") or ""
                ).strip()
                form_file_name = str(
                    rule.get("official_form_file_name") or ""
                ).strip()
                form_download_url = str(
                    rule.get("official_form_download_url") or ""
                ).strip()
                if (
                    not attachment_source_url
                    or not current_status_url
                    or not form_file_name
                    or not form_download_url
                ):
                    reject("FORM_EFFECTIVITY_ATTACHMENT_INCOMPLETE")
                    continue
                if not is_allowed_official_url(current_status_url):
                    reject("FORM_EFFECTIVITY_CURRENT_STATUS_SOURCE_NOT_OFFICIAL")
                    continue
                if not is_allowed_official_url(
                    attachment_source_url
                ) or not is_allowed_official_url(form_download_url):
                    reject("FORM_EFFECTIVITY_ATTACHMENT_SOURCE_NOT_OFFICIAL")
                    continue
                if extract_strict_form_codes(form_file_name) != [target_code[0]]:
                    reject("FORM_EFFECTIVITY_ATTACHMENT_CODE_MISMATCH")
                    continue
            elif evidence_basis == "explicit_partial_provision_scope":
                form_scope_url = str(
                    rule.get("form_scope_source_url") or ""
                ).strip()
                package_file_name = str(
                    rule.get("official_form_package_file_name") or ""
                ).strip()
                package_download_url = str(
                    rule.get("official_form_package_download_url") or ""
                ).strip()
                package_sha256 = str(
                    rule.get("official_form_package_sha256") or ""
                ).strip().casefold()
                try:
                    package_size = int(
                        rule.get("official_form_package_size") or 0
                    )
                except (TypeError, ValueError):
                    package_size = 0
                target_provision_values = rule.get("target_provisions") or []
                target_provisions, target_provisions_valid = _provision_tokens(
                    target_provision_values
                )
                if not history_url:
                    reject("FORM_EFFECTIVITY_PROVISION_HISTORY_MISSING")
                    continue
                if (
                    not package_file_name.casefold().endswith(
                        (".pdf", ".doc", ".docx")
                    )
                    or not re.fullmatch(r"[0-9a-f]{64}", package_sha256)
                    or package_size <= 0
                    or not package_download_url
                ):
                    reject("FORM_EFFECTIVITY_PACKAGE_INTEGRITY_INCOMPLETE")
                    continue
                if not is_allowed_official_url(package_download_url):
                    reject("FORM_EFFECTIVITY_PACKAGE_SOURCE_NOT_OFFICIAL")
                    continue
                if not is_allowed_official_url(form_scope_url):
                    reject("FORM_EFFECTIVITY_PROVISION_SOURCE_NOT_OFFICIAL")
                    continue
                if (
                    not target_provisions
                    or not target_provisions_valid
                ):
                    reject("FORM_EFFECTIVITY_PROVISION_SCOPE_UNSAFE")
                    continue
                edges = [
                    dict(item)
                    for item in rule.get("relation_edges") or []
                    if isinstance(item, Mapping)
                ]
                if not edges:
                    reject("FORM_EFFECTIVITY_RELATION_EDGE_MISSING")
                    continue
                unsafe_edge = False
                for edge in edges:
                    edge_url = str(edge.get("official_source_url") or "").strip()
                    relation = str(edge.get("relation") or "").strip()
                    scope_field = (
                        "confirmed_provisions"
                        if relation == "confirms_target_form_binding"
                        else "affected_provisions"
                    )
                    edge_scope, edge_scope_valid = _provision_tokens(
                        edge.get(scope_field) or []
                    )
                    try:
                        edge_effective_from = date.fromisoformat(
                            str(edge.get("effective_from") or "")[:10]
                        )
                    except ValueError:
                        unsafe_edge = True
                        break
                    if (
                        relation
                        not in {
                            "amends_other_provisions_only",
                            "confirms_target_form_binding",
                            "expires_other_provisions_only",
                            "repeals_other_provisions_only",
                        }
                        or not str(
                            edge.get("modifying_document_number") or ""
                        ).strip()
                        or not edge_scope
                        or not edge_scope_valid
                        or edge_effective_from > as_of
                        or not is_allowed_official_url(edge_url)
                    ):
                        unsafe_edge = True
                        break
                    overlaps = _provision_scopes_overlap(
                        target_provisions,
                        edge_scope,
                    )
                    if relation == "confirms_target_form_binding":
                        if not overlaps:
                            unsafe_edge = True
                            break
                    elif overlaps:
                        unsafe_edge = True
                        break
                if unsafe_edge:
                    reject("FORM_EFFECTIVITY_PROVISION_SCOPE_UNSAFE")
                    continue
            elif evidence_basis == "explicit_suspended_repeal_scope":
                form_scope_url = str(
                    rule.get("form_scope_source_url") or ""
                ).strip()
                package_file_name = str(
                    rule.get("official_form_package_file_name") or ""
                ).strip()
                package_download_url = str(
                    rule.get("official_form_package_download_url") or ""
                ).strip()
                package_sha256 = str(
                    rule.get("official_form_package_sha256") or ""
                ).strip().casefold()
                try:
                    package_size = int(
                        rule.get("official_form_package_size") or 0
                    )
                except (TypeError, ValueError):
                    package_size = 0
                target_provisions, target_provisions_valid = _provision_tokens(
                    rule.get("target_provisions") or []
                )
                if not history_url:
                    reject("FORM_EFFECTIVITY_PROVISION_HISTORY_MISSING")
                    continue
                if (
                    not package_file_name.casefold().endswith(
                        (".pdf", ".doc", ".docx")
                    )
                    or not re.fullmatch(r"[0-9a-f]{64}", package_sha256)
                    or package_size <= 0
                    or not package_download_url
                ):
                    reject("FORM_EFFECTIVITY_PACKAGE_INTEGRITY_INCOMPLETE")
                    continue
                if not is_allowed_official_url(package_download_url):
                    reject("FORM_EFFECTIVITY_PACKAGE_SOURCE_NOT_OFFICIAL")
                    continue
                if not is_allowed_official_url(form_scope_url):
                    reject("FORM_EFFECTIVITY_PROVISION_SOURCE_NOT_OFFICIAL")
                    continue
                if not target_provisions or not target_provisions_valid:
                    reject(
                        "FORM_EFFECTIVITY_SUSPENDED_REPEAL_SCOPE_UNSAFE"
                    )
                    continue
                edges = [
                    dict(item)
                    for item in rule.get("relation_edges") or []
                    if isinstance(item, Mapping)
                ]
                repeal_edges = [
                    edge
                    for edge in edges
                    if edge.get("relation") == "repeals_target_provisions"
                ]
                suspension_edges = [
                    edge
                    for edge in edges
                    if edge.get("relation")
                    == "suspends_repealing_instrument"
                ]
                unsafe_edge = (
                    not repeal_edges
                    or not suspension_edges
                    or len(repeal_edges) + len(suspension_edges) != len(edges)
                )
                repealing_documents: set[str] = set()
                if not unsafe_edge:
                    for edge in repeal_edges:
                        edge_url = str(
                            edge.get("official_source_url") or ""
                        ).strip()
                        repealing_document = normalize_document_number(
                            edge.get("modifying_document_number")
                        )
                        affected, affected_valid = _provision_tokens(
                            edge.get("affected_provisions") or []
                        )
                        try:
                            edge_effective_from = date.fromisoformat(
                                str(edge.get("effective_from") or "")[:10]
                            )
                        except ValueError:
                            unsafe_edge = True
                            break
                        if (
                            not repealing_document
                            or not affected
                            or not affected_valid
                            or not _provision_scopes_overlap(
                                target_provisions,
                                affected,
                            )
                            or edge_effective_from > as_of
                            or not is_allowed_official_url(edge_url)
                        ):
                            unsafe_edge = True
                            break
                        repealing_documents.add(repealing_document)
                suspended_documents: set[str] = set()
                if not unsafe_edge:
                    for edge in suspension_edges:
                        edge_url = str(
                            edge.get("official_source_url") or ""
                        ).strip()
                        suspending_document = normalize_document_number(
                            edge.get("modifying_document_number")
                        )
                        suspended_document = normalize_document_number(
                            edge.get("suspended_document_number")
                        )
                        try:
                            edge_effective_from = date.fromisoformat(
                                str(edge.get("effective_from") or "")[:10]
                            )
                        except ValueError:
                            unsafe_edge = True
                            break
                        if (
                            not suspending_document
                            or not suspended_document
                            or str(
                                edge.get("suspension_status") or ""
                            ).strip().casefold()
                            != "active"
                            or edge_effective_from > as_of
                            or not is_allowed_official_url(edge_url)
                        ):
                            unsafe_edge = True
                            break
                        suspended_documents.add(suspended_document)
                if (
                    unsafe_edge
                    or repealing_documents != suspended_documents
                ):
                    reject(
                        "FORM_EFFECTIVITY_SUSPENDED_REPEAL_SCOPE_UNSAFE"
                    )
                    continue
            elif evidence_basis == "explicit_form_code_scope":
                form_scope_url = str(
                    rule.get("form_scope_source_url") or ""
                ).strip()
                package_file_name = str(
                    rule.get("official_form_package_file_name") or ""
                ).strip()
                package_download_url = str(
                    rule.get("official_form_package_download_url") or ""
                ).strip()
                package_sha256 = str(
                    rule.get("official_form_package_sha256") or ""
                ).strip().casefold()
                try:
                    package_size = int(
                        rule.get("official_form_package_size") or 0
                    )
                except (TypeError, ValueError):
                    package_size = 0
                if not history_url:
                    reject("FORM_EFFECTIVITY_FORM_HISTORY_MISSING")
                    continue
                if (
                    not package_file_name.casefold().endswith(
                        (".pdf", ".doc", ".docx")
                    )
                    or not re.fullmatch(r"[0-9a-f]{64}", package_sha256)
                    or package_size <= 0
                    or not package_download_url
                ):
                    reject("FORM_EFFECTIVITY_PACKAGE_INTEGRITY_INCOMPLETE")
                    continue
                if not is_allowed_official_url(package_download_url):
                    reject("FORM_EFFECTIVITY_PACKAGE_SOURCE_NOT_OFFICIAL")
                    continue
                if not is_allowed_official_url(form_scope_url):
                    reject("FORM_EFFECTIVITY_FORM_SOURCE_NOT_OFFICIAL")
                    continue
                edges = [
                    dict(item)
                    for item in rule.get("relation_edges") or []
                    if isinstance(item, Mapping)
                ]
                if not edges:
                    reject("FORM_EFFECTIVITY_RELATION_EDGE_MISSING")
                    continue
                target = target_code[0]
                unsafe_edge = False
                for edge in edges:
                    edge_url = str(edge.get("official_source_url") or "").strip()
                    affected, affected_valid = _form_code_tokens(
                        edge.get("affected_form_codes") or []
                    )
                    try:
                        edge_effective_from = date.fromisoformat(
                            str(edge.get("effective_from") or "")[:10]
                        )
                    except ValueError:
                        unsafe_edge = True
                        break
                    if (
                        edge.get("relation")
                        not in {
                            "expires_other_forms_only",
                            "replaces_other_forms_only",
                        }
                        or not str(
                            edge.get("modifying_document_number") or ""
                        ).strip()
                        or not affected
                        or not affected_valid
                        or target in affected
                        or edge_effective_from > as_of
                        or not is_allowed_official_url(edge_url)
                    ):
                        unsafe_edge = True
                        break
                if unsafe_edge:
                    reject("FORM_EFFECTIVITY_FORM_SCOPE_UNSAFE")
                    continue
            elif evidence_basis != "explicit_replacement_scope":
                reject("FORM_EFFECTIVITY_BASIS_INSUFFICIENT")
                continue
            else:
                edges = [
                    dict(item)
                    for item in rule.get("relation_edges") or []
                    if isinstance(item, Mapping)
                ]
                if not edges:
                    reject("FORM_EFFECTIVITY_RELATION_EDGE_MISSING")
                    continue
                target = target_code[0]
                unsafe_edge = False
                for edge in edges:
                    edge_url = str(edge.get("official_source_url") or "").strip()
                    affected = {
                        code
                        for value in edge.get("affected_form_codes") or []
                        for code in extract_strict_form_codes(f"Mẫu {value}")
                    }
                    if (
                        edge.get("relation") != "replaces_other_forms_only"
                        or not str(
                            edge.get("modifying_document_number") or ""
                        ).strip()
                        or not affected
                        or target in affected
                        or not is_allowed_official_url(edge_url)
                    ):
                        unsafe_edge = True
                        break
                if unsafe_edge:
                    reject("FORM_EFFECTIVITY_REPLACEMENT_SCOPE_UNSAFE")
                    continue
        accepted.append(
            {
                **rule,
                "issuing_instrument": instrument,
                "target_form_code": target_code[0],
                "verified_as_of": verified_as_of.isoformat(),
            }
        )
    return accepted, rejections


def _attach_form_effectivity_evidence(
    document: Mapping[str, Any],
    rules: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Attach only evidence for the exact issuing instrument."""

    enriched = dict(document)
    expected = str(document.get("docNum") or "").replace(" ", "").upper()
    existing = [
        dict(item)
        for item in document.get("appendix_effectivity") or []
        if isinstance(item, Mapping)
    ]
    seen = {str(item.get("evidence_id") or "") for item in existing}
    for rule in rules:
        instrument = str(rule.get("issuing_instrument") or "")
        if instrument.replace(" ", "").upper() != expected:
            continue
        evidence_id = str(rule.get("evidence_id") or "")
        if evidence_id and evidence_id in seen:
            continue
        existing.append(dict(rule))
        seen.add(evidence_id)
    enriched["appendix_effectivity"] = existing
    return enriched


def _bind_dvc_attachment_rules(
    groups: list[dict[str, Any]],
    rules: list[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Bind one validated DVC artifact request to one exact identity group."""

    issues: list[dict[str, str]] = []
    dvc_rules = [
        item
        for item in rules
        if str(item.get("evidence_basis") or "")
        == "official_current_dvc_attachment"
    ]
    for group in groups:
        group_code = extract_strict_form_codes(
            f"Mẫu {group.get('form_code') or ''}"
        )
        group_instrument = normalize_document_number(
            group.get("issuing_instrument")
        )
        group_appendix = normalize_appendix_identifier(
            group.get("appendix_identifier")
        )
        matches = [
            item
            for item in dvc_rules
            if extract_strict_form_codes(
                f"Mẫu {item.get('target_form_code') or ''}"
            )
            == group_code
            and normalize_document_number(item.get("issuing_instrument"))
            == group_instrument
            and normalize_appendix_identifier(item.get("appendix_identifier"))
            == group_appendix
        ]
        if not matches:
            continue
        group["official_dvc_attachment_required"] = True
        group["official_dvc_attachment_requests"] = []
        if len(matches) != 1:
            reason_code = "OFFICIAL_DVC_ATTACHMENT_RULE_AMBIGUOUS"
            group["official_dvc_attachment_reason_code"] = reason_code
            issues.append(
                {
                    "group_id": str(group.get("group_id") or ""),
                    "reason_code": reason_code,
                }
            )
            continue
        rule = matches[0]
        bound_procedure_ids = {
            str(item.get("procedure_id") or "").strip()
            for item in rule.get("procedure_bindings") or []
            if isinstance(item, Mapping)
        }
        group_procedure_ids = {
            str(item).strip()
            for item in group.get("procedure_ids") or []
            if str(item).strip()
        }
        if not group_procedure_ids.issubset(bound_procedure_ids):
            reason_code = "OFFICIAL_DVC_PROCEDURE_BINDING_MISMATCH"
            group["official_dvc_attachment_reason_code"] = reason_code
            issues.append(
                {
                    "group_id": str(group.get("group_id") or ""),
                    "reason_code": reason_code,
                }
            )
            continue
        request = dict(rule.get("canonical_artifact_attachment") or {})
        request["evidence_id"] = str(rule.get("evidence_id") or "")
        request["publication_decision_number"] = str(
            rule.get("publication_decision_number") or ""
        )
        group["official_dvc_attachment_reason_code"] = None
        group["official_dvc_attachment_requests"] = [request]
    return issues


def _resume_processed_group_ids(payload: Mapping[str, Any]) -> set[str]:
    processed: set[str] = set()
    for item in payload.get("resolved_groups") or []:
        if isinstance(item, Mapping):
            group_id = str(item.get("group_id") or "").strip()
            if group_id:
                processed.add(group_id)
    for item in payload.get("verified_data_gaps") or []:
        if not isinstance(item, Mapping):
            continue
        reason_code = str(item.get("reason_code") or "").strip()
        if reason_code in RETRYABLE_GROUP_REASON_CODES:
            continue
        group_id = str(item.get("group_id") or "").strip()
        if group_id:
            processed.add(group_id)
    return processed


def _filter_prior_resolution_for_active_groups(
    payload: Mapping[str, Any],
    *,
    active_group_ids: set[str],
    active_instruments: set[str],
) -> dict[str, Any]:
    """Drop stale checkpoint rows after deterministic identity keys change.

    A resolver code change can split or merge groups. Reusing rows whose group
    IDs no longer exist would double-count gaps and keep obsolete candidates
    alive. Source attempts are similarly bounded to instruments in the current
    inventory.
    """

    filtered = dict(payload)
    filtered["pending_records"] = [
        dict(item)
        for item in payload.get("pending_records") or []
        if isinstance(item, Mapping)
        and str(
            item.get("three_tier_group_id") or item.get("group_id") or ""
        ).strip()
        in active_group_ids
    ]
    filtered["resolved_groups"] = [
        dict(item)
        for item in payload.get("resolved_groups") or []
        if isinstance(item, Mapping)
        and str(item.get("group_id") or "").strip() in active_group_ids
    ]
    filtered["verified_data_gaps"] = [
        dict(item)
        for item in payload.get("verified_data_gaps") or []
        if isinstance(item, Mapping)
        and str(item.get("group_id") or "").strip() in active_group_ids
        and str(item.get("reason_code") or "").strip()
        not in RETRYABLE_GROUP_REASON_CODES
    ]
    for key in ("source_attempts", "external_errors"):
        filtered[key] = [
            dict(item)
            for item in payload.get(key) or []
            if isinstance(item, Mapping)
            and str(item.get("instrument") or "").strip() in active_instruments
        ]
    return filtered


def _resumable_prior_resolution(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep checkpoint rows only for an interrupted resolver run.

    A complete prior probe is historical evidence, not an append target. Reusing
    its pending records on a fresh invocation duplicates candidate bindings and
    makes a clean rerun appear to have discovered more forms.
    """

    if (
        payload.get("run_id")
        and payload.get("group_progress_complete") is False
    ):
        return dict(payload)
    return {}


def _merge_source_attempts(
    previous: list[Mapping[str, Any]],
    current: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in [*previous, *current]:
        if not isinstance(item, Mapping):
            continue
        instrument = str(item.get("instrument") or "").strip()
        if instrument:
            merged[instrument] = dict(item)
    return sorted(merged.values(), key=lambda item: str(item.get("instrument") or ""))


def _slug(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", str(value or ""))
    ascii_text = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("Đ", "D").replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.casefold()).strip("-")


def _rsc_json(
    payload: bytes,
    predicate: Callable[[Any], bool],
) -> Any:
    text = payload.decode("utf-8")
    decoder = json.JSONDecoder()
    for pattern in (r"\d+:(\{\"total\":)", r"(?m)^\d+:(\[)"):
        for match in re.finditer(pattern, text):
            try:
                value, _end = decoder.raw_decode(text[match.start(1) :])
            except json.JSONDecodeError:
                continue
            if predicate(value):
                return value
    for match in re.finditer(r"(?m)^\d+:", text):
        try:
            value, _end = decoder.raw_decode(text[match.end() :])
        except json.JSONDecodeError:
            continue
        if predicate(value):
            return value
    raise ValueError("VBPL_RSC_PAYLOAD_MISSING")


def _headers(action: str) -> dict[str, str]:
    return {
        "Accept": "text/x-component",
        "Content-Type": "text/plain;charset=UTF-8",
        "Origin": "https://vbpl.vn",
        "User-Agent": USER_AGENT,
        "Next-Action": action,
    }


def _cache_path(cache_dir: Path, instrument: str) -> Path:
    digest = hashlib.sha256(instrument.encode("utf-8")).hexdigest()[:24]
    return cache_dir / f"{digest}.json"


def fetch_official_document(
    instrument: str,
    *,
    timeout: float,
    cache_dir: Path,
    refresh: bool,
    fallback_urls: list[str] | None = None,
    legal_as_of: str | None = None,
) -> dict[str, Any]:
    cache_path = _cache_path(cache_dir, instrument)
    # The modern VBPL endpoint does not expose every historic/current record.
    # A curated Government/Cong Bao record is usable only when it has an exact
    # document number and current-status evidence for this campaign date.  The
    # package still passes the ordinary download, extraction and review gates.
    curated = (
        load_curated_government_document(
            instrument,
            legal_as_of=legal_as_of,
        )
        if legal_as_of
        else None
    )
    if curated is not None:
        _write(cache_path, curated)
        return curated
    cached = _read(cache_path, None)
    if isinstance(cached, dict) and not refresh:
        return cached

    keyword = instrument
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        verify=build_verified_ssl_context(),
    ) as client:
        response = client.post(
            VBPL_ACTION_URL,
            headers=_headers(VBPL_SEARCH_ACTION),
            content=json.dumps(
                [{"keyword": keyword, "pageNumber": 0, "pageSize": 100}],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        response.raise_for_status()
        require_official_component_response(response)
        search = _rsc_json(
            response.content,
            lambda value: isinstance(value, dict) and "items" in value,
        )
        search_items = list(search.get("items") or [])
        document = select_exact_document(search_items, instrument)
        try:
            total = int(search.get("total") or len(search_items))
        except (TypeError, ValueError):
            total = len(search_items)
        page_number = 1
        # Exact-number queries can still return hundreds of relationship hits.
        # Search a bounded number of result pages and retain exact equality;
        # never fall back to title/fuzzy matching.
        while document is None and page_number * 100 < total and page_number < 5:
            page_response = client.post(
                VBPL_ACTION_URL,
                headers=_headers(VBPL_SEARCH_ACTION),
                content=json.dumps(
                    [
                        {
                            "keyword": keyword,
                            "pageNumber": page_number,
                            "pageSize": 100,
                        }
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            page_response.raise_for_status()
            require_official_component_response(page_response)
            page = _rsc_json(
                page_response.content,
                lambda value: isinstance(value, dict) and "items" in value,
            )
            search_items.extend(page.get("items") or [])
            document = select_exact_document(search_items, instrument)
            page_number += 1
        if document is None:
            fallback_attempts: list[dict[str, Any]] = []
            for fallback_url in ordered_fallback_urls(
                fallback_urls or [], instrument=instrument
            ):
                try:
                    fallback_response = client.get(fallback_url)
                    fallback_response.raise_for_status()
                    content_type = str(
                        fallback_response.headers.get("content-type") or ""
                    ).casefold()
                    if "text/html" in content_type or "application/xhtml" in content_type:
                        links = extract_exact_instrument_links(
                            fallback_response.content,
                            page_url=fallback_url,
                            instrument=instrument,
                        )
                        fallback_attempts.append(
                            {
                                "url": fallback_url,
                                "status": "page_checked",
                                "exact_links": len(links),
                            }
                        )
                        if links:
                            result = {
                                "instrument": instrument,
                                "status": "verified_gap",
                                "reason_code": "OFFICIAL_FALLBACK_EXACT_LINK_FOUND_REQUIRES_DOCUMENT_VALIDATION",
                                "checked_url": VBPL_ACTION_URL,
                                "fallback_attempts": fallback_attempts,
                            }
                            _write(cache_path, result)
                            return result
                    elif any(
                        marker in content_type
                        for marker in ("pdf", "msword", "officedocument")
                    ):
                        fallback_attempts.append(
                            {
                                "url": fallback_url,
                                "status": "file_located",
                                "exact_links": 0,
                            }
                        )
                        result = {
                            "instrument": instrument,
                            "status": "verified_gap",
                            "reason_code": "OFFICIAL_FALLBACK_FILE_LOCATED_REQUIRES_DOCUMENT_VALIDATION",
                            "checked_url": VBPL_ACTION_URL,
                            "fallback_attempts": fallback_attempts,
                        }
                        _write(cache_path, result)
                        return result
                    else:
                        fallback_attempts.append(
                            {
                                "url": fallback_url,
                                "status": "content_type_drift",
                                "exact_links": 0,
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    fallback_attempts.append(
                        {
                            "url": fallback_url,
                            "status": "failed",
                            "reason_code": classify_official_source_failure(exc),
                        }
                    )
            result = {
                "instrument": instrument,
                "status": "verified_gap",
                "reason_code": "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND",
                "checked_url": VBPL_ACTION_URL,
                "fallback_attempts": fallback_attempts,
            }
            _write(cache_path, result)
            return result

        document_id = str(document["id"])
        document["detailUrl"] = (
            f"https://vbpl.vn/van-ban/chi-tiet/"
            f"{_slug(document.get('title'))}--{document_id}"
        )
        file_response = client.post(
            VBPL_ACTION_URL,
            headers=_headers(VBPL_FILE_LIST_ACTION),
            content=json.dumps(
                [document_id, [1, 2, 4]],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        file_response.raise_for_status()
        require_official_component_response(file_response)
        files = _rsc_json(
            file_response.content,
            lambda value: isinstance(value, list)
            and all(isinstance(item, dict) for item in value),
        )
    result = {
        "instrument": instrument,
        "status": "found",
        "reason_code": "EXACT_OFFICIAL_DOCUMENT_FOUND",
        "document": document,
        "files": files,
        "checked_url": VBPL_ACTION_URL,
    }
    _write(cache_path, result)
    return result


def load_cached_official_document(
    instrument: str,
    *,
    cache_dir: Path,
) -> dict[str, Any]:
    cached = _read(_cache_path(cache_dir, instrument), None)
    if isinstance(cached, dict):
        return cached
    return {
        "instrument": instrument,
        "status": "verified_gap",
        "reason_code": "OFFICIAL_CACHE_MISS_OFFLINE",
        "checked_url": VBPL_ACTION_URL,
    }


def _official_download_url(document_id: str, filename: str) -> str:
    return f"{VBPL_FILE_API}/{quote(document_id, safe='')}/{quote(filename, safe='')}/download"


def _validated_content(
    client: httpx.Client,
    *,
    document_id: str,
    file_record: Mapping[str, Any],
    cache_dir: Path | None = None,
) -> tuple[bytes, str, str]:
    filename = str(file_record.get("fileName") or "").strip()
    if not filename:
        raise ValueError("OFFICIAL_ATTACHMENT_FILENAME_MISSING")
    url = _official_download_url(document_id, filename)
    if not is_allowed_official_url(url):
        raise ValueError("SOURCE_HOST_NOT_ALLOWED")
    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cached_payload = (
        cache_dir / f"{cache_key}.bin" if cache_dir is not None else None
    )
    cached_meta = (
        cache_dir / f"{cache_key}.json" if cache_dir is not None else None
    )
    if cached_payload is not None and cached_meta is not None:
        cached_bytes = cached_payload.read_bytes() if cached_payload.is_file() else None
        cached_info = _read(cached_meta, None)
        if (
            cached_bytes is not None
            and isinstance(cached_info, Mapping)
            and str(cached_info.get("sha256") or "")
            == hashlib.sha256(cached_bytes).hexdigest()
        ):
            return (
                cached_bytes,
                url,
                str(cached_info.get("file_format") or ""),
            )
    response = client.get(url)
    response.raise_for_status()
    if len(response.content) > 25 * 1024 * 1024:
        raise ValueError("FILE_TOO_LARGE")
    valid, reason, file_format = validate_download(
        response.content,
        response.headers.get("content-type"),
        (
            "https://vbpl-bientap-gateway.moj.gov.vn/"
            f"{quote(filename, safe='')}"
        ),
    )
    if not valid or not file_format:
        raise ValueError(reason)
    scan_ok, scan_reason = _basic_malware_scan(response.content)
    if not scan_ok:
        raise ValueError(scan_reason)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_dir / f"{cache_key}.bin.tmp"
        temporary.write_bytes(response.content)
        temporary.replace(cache_dir / f"{cache_key}.bin")
        _write(
            cache_dir / f"{cache_key}.json",
            {
                "url": url,
                "file_format": file_format,
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "size_bytes": len(response.content),
            },
        )
    return response.content, url, file_format


def _validated_direct_content(
    client: httpx.Client,
    *,
    url: str,
    cache_dir: Path | None = None,
) -> tuple[bytes, str, str]:
    """Download one explicitly supplied allowlisted official package URL."""

    requested_url = str(url or "").strip()
    if not is_allowed_official_url(requested_url):
        raise ValueError("SOURCE_HOST_NOT_ALLOWED")
    cache_key = hashlib.sha256(requested_url.encode("utf-8")).hexdigest()
    cached_payload = (
        cache_dir / f"{cache_key}.bin" if cache_dir is not None else None
    )
    cached_meta = (
        cache_dir / f"{cache_key}.json" if cache_dir is not None else None
    )
    if cached_payload is not None and cached_meta is not None:
        cached_bytes = (
            cached_payload.read_bytes() if cached_payload.is_file() else None
        )
        cached_info = _read(cached_meta, None)
        cached_final_url = str(
            (cached_info or {}).get("final_url")
            or (cached_info or {}).get("url")
            or ""
        )
        if (
            cached_bytes is not None
            and isinstance(cached_info, Mapping)
            and is_allowed_official_url(cached_final_url)
            and str(cached_info.get("sha256") or "")
            == hashlib.sha256(cached_bytes).hexdigest()
        ):
            return (
                cached_bytes,
                cached_final_url,
                str(cached_info.get("file_format") or ""),
            )

    response = client.get(requested_url)
    response.raise_for_status()
    final_url = str(response.url)
    if not is_allowed_official_url(final_url):
        raise ValueError("SOURCE_HOST_NOT_ALLOWED")
    if len(response.content) > 25 * 1024 * 1024:
        raise ValueError("FILE_TOO_LARGE")
    validation_url = final_url
    if not Path(urlparse(validation_url).path).suffix:
        for candidate_url in (final_url, requested_url):
            parsed_candidate = urlparse(candidate_url)
            query_names = [
                unquote(value)
                for value in parse_qs(parsed_candidate.query).get("file_name", [])
            ]
            filename = next(
                (
                    candidate
                    for candidate in [
                        *(unquote(segment) for segment in reversed(parsed_candidate.path.split("/"))),
                        *query_names,
                    ]
                    if Path(candidate).suffix
                ),
                "",
            )
            if filename:
                validation_url = (
                    f"{parsed_candidate.scheme}://{parsed_candidate.netloc}/"
                    f"{quote(filename, safe='')}"
                )
                break
    valid, reason, file_format = validate_download(
        response.content,
        response.headers.get("content-type"),
        validation_url,
    )
    if not valid or not file_format:
        raise ValueError(reason)
    scan_ok, scan_reason = _basic_malware_scan(response.content)
    if not scan_ok:
        raise ValueError(scan_reason)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_dir / f"{cache_key}.bin.tmp"
        temporary.write_bytes(response.content)
        temporary.replace(cache_dir / f"{cache_key}.bin")
        _write(
            cache_dir / f"{cache_key}.json",
            {
                "url": requested_url,
                "final_url": final_url,
                "file_format": file_format,
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "size_bytes": len(response.content),
            },
        )
    return response.content, final_url, file_format


def _validated_dvc_attachment_content(
    client: httpx.Client,
    *,
    request: Mapping[str, Any],
    cache_dir: Path | None = None,
) -> tuple[bytes, str, str]:
    """POST one checksum-bound fileId to the fixed public DVC endpoint."""

    api_url = str(request.get("api_url") or "").strip()
    attachment_id = str(request.get("attachment_id") or "").strip()
    file_name = str(request.get("file_name") or "").strip()
    expected_sha256 = str(request.get("sha256") or "").strip().casefold()
    referer_url = str(request.get("referer_url") or "").strip()
    source_page_url = str(request.get("source_page_url") or "").strip()
    try:
        expected_size = int(request.get("size_bytes") or 0)
    except (TypeError, ValueError):
        expected_size = 0
    try:
        canonical_attachment_id = str(UUID(attachment_id))
    except (ValueError, AttributeError):
        canonical_attachment_id = ""
    if api_url != DVC_ATTACHMENT_API_URL:
        raise ValueError("OFFICIAL_DVC_ATTACHMENT_ENDPOINT_INVALID")
    if canonical_attachment_id != attachment_id.casefold():
        raise ValueError("OFFICIAL_DVC_ATTACHMENT_ID_INVALID")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or expected_size <= 0
        or expected_size > 25 * 1024 * 1024
        or not file_name
        or Path(file_name).name != file_name
        or "/" in file_name
        or "\\" in file_name
        or not file_name.casefold().endswith((".pdf", ".doc", ".docx"))
    ):
        raise ValueError("OFFICIAL_DVC_ATTACHMENT_INTEGRITY_INCOMPLETE")
    if (
        not is_allowed_official_url(referer_url)
        or not is_allowed_official_url(source_page_url)
        or urlparse(referer_url).hostname != "dichvucong.gov.vn"
        or urlparse(source_page_url).hostname != "dichvucong.gov.vn"
    ):
        raise ValueError("OFFICIAL_DVC_ATTACHMENT_SOURCE_NOT_OFFICIAL")

    cache_material = "|".join(
        [api_url, attachment_id, expected_sha256, str(expected_size)]
    )
    cache_key = hashlib.sha256(cache_material.encode("utf-8")).hexdigest()
    cached_payload = (
        cache_dir / f"{cache_key}.bin" if cache_dir is not None else None
    )
    cached_meta = (
        cache_dir / f"{cache_key}.json" if cache_dir is not None else None
    )
    if cached_payload is not None and cached_meta is not None:
        try:
            cached_bytes = (
                cached_payload.read_bytes() if cached_payload.is_file() else None
            )
            cached_info = _read(cached_meta, None)
            cached_size = int(
                cached_info.get("size_bytes") or 0
            ) if isinstance(cached_info, Mapping) else 0
            cached_file_format = str(
                cached_info.get("file_format") or ""
            ).casefold() if isinstance(cached_info, Mapping) else ""
        except (OSError, TypeError, ValueError):
            # A cache is only an optimization. A truncated, malformed or stale
            # sidecar is never evidence and must not stop a fresh checksum-bound
            # retrieval from the fixed official endpoint.
            cached_bytes = None
            cached_info = None
            cached_size = 0
            cached_file_format = ""
        if (
            cached_bytes is not None
            and isinstance(cached_info, Mapping)
            and str(cached_info.get("api_url") or "") == api_url
            and str(cached_info.get("attachment_id") or "") == attachment_id
            and str(cached_info.get("sha256") or "") == expected_sha256
            and cached_size == expected_size
            and cached_file_format in {"pdf", "doc", "docx"}
            and len(cached_bytes) == expected_size
            and hashlib.sha256(cached_bytes).hexdigest() == expected_sha256
        ):
            return (
                cached_bytes,
                api_url,
                cached_file_format,
            )

    response = client.post(
        api_url,
        json={"fileId": attachment_id},
        headers={
            "Accept": "application/json;odata=verbose",
            "Content-Type": "application/json; charset=UTF-8",
            "Origin": "https://dichvucong.gov.vn",
            "Referer": referer_url,
        },
    )
    response.raise_for_status()
    final_url = str(response.url)
    if final_url != api_url:
        raise ValueError("OFFICIAL_DVC_ATTACHMENT_REDIRECT_REJECTED")
    if len(response.content) > 25 * 1024 * 1024:
        raise ValueError("FILE_TOO_LARGE")
    if len(response.content) != expected_size:
        raise ValueError("OFFICIAL_FORM_SIZE_DRIFT")
    actual_sha256 = hashlib.sha256(response.content).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("OFFICIAL_FORM_CHECKSUM_DRIFT")
    validation_url = (
        "https://dichvucong.gov.vn/"
        f"{quote(file_name, safe='')}"
    )
    valid, reason, file_format = validate_download(
        response.content,
        response.headers.get("content-type"),
        validation_url,
    )
    if not valid or not file_format:
        raise ValueError(reason)
    scan_ok, scan_reason = _basic_malware_scan(response.content)
    if not scan_ok:
        raise ValueError(scan_reason)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = cache_dir / f"{cache_key}.bin.tmp"
        temporary.write_bytes(response.content)
        temporary.replace(cache_dir / f"{cache_key}.bin")
        _write(
            cache_dir / f"{cache_key}.json",
            {
                "api_url": api_url,
                "attachment_id": attachment_id,
                "file_name": file_name,
                "file_format": file_format,
                "sha256": actual_sha256,
                "size_bytes": len(response.content),
                "source_page_url": source_page_url,
            },
        )
    return response.content, api_url, file_format


def _save_artifact(
    content: bytes,
    *,
    file_format: str,
    download_url: str,
    download_dir: Path,
    source_package_sha256: str | None = None,
    source_pages_zero_based: list[int] | None = None,
) -> dict[str, Any]:
    digest = hashlib.sha256(content).hexdigest()
    download_dir.mkdir(parents=True, exist_ok=True)
    path = download_dir / f"{digest[:24]}.{file_format}"
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("CANDIDATE_CHECKSUM_DRIFT")
    else:
        path.write_bytes(content)
    try:
        local_path = str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        # Test/adapter callers may use an isolated temporary directory. The
        # production resolver always writes beneath ROOT; do not serialize an
        # arbitrary absolute path into campaign artifacts.
        local_path = path.name
    result = {
        "file_name": path.name,
        "local_path": local_path,
        "download_url": download_url,
        "sha256": digest,
        "size_bytes": len(content),
        "file_format": file_format,
    }
    if source_package_sha256:
        result["source_package_sha256"] = source_package_sha256
    if source_pages_zero_based is not None:
        result["source_pages_zero_based"] = source_pages_zero_based
    return result


def _select_explicit_form_page_binding(
    group: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Select one checksum-pinned scanned-PDF range for an exact group.

    Curated page bindings are deliberately narrower than the generic package
    extractor: code, appendix and the whole procedure set must agree.  A
    malformed or ambiguous binding cannot fall through to a guessed OCR path.
    """

    raw_bindings = bundle.get("explicit_form_page_bindings")
    if raw_bindings is None:
        return None, None
    if not isinstance(raw_bindings, list):
        return None, "CURATED_OFFICIAL_PAGE_BINDING_INVALID"
    codes = extract_strict_form_codes(f"Mẫu {group.get('form_code') or ''}")
    appendix_identifier = normalize_appendix_identifier(
        group.get("appendix_identifier")
    )
    group_procedure_ids = {
        str(value).strip()
        for value in group.get("procedure_ids") or []
        if str(value).strip()
    }
    if len(codes) != 1 or not appendix_identifier or not group_procedure_ids:
        return None, None
    matches: list[dict[str, Any]] = []
    for raw in raw_bindings:
        if not isinstance(raw, Mapping):
            return None, "CURATED_OFFICIAL_PAGE_BINDING_INVALID"
        binding = dict(raw)
        binding_codes = extract_strict_form_codes(
            f"Mẫu {binding.get('form_code') or ''}"
        )
        binding_appendix = normalize_appendix_identifier(
            binding.get("appendix_identifier")
        )
        source_page_url = str(binding.get("source_page_url") or "").strip()
        download_url = str(binding.get("official_download_url") or "").strip()
        checksum = str(binding.get("source_package_sha256") or "").strip().casefold()
        try:
            size_bytes = int(binding.get("source_package_size_bytes") or 0)
        except (TypeError, ValueError):
            size_bytes = 0
        procedure_ids = binding.get("procedure_ids")
        pages = binding.get("source_pages_zero_based")
        if (
            len(binding_codes) != 1
            or not binding_appendix
            or not is_allowed_official_url(source_page_url)
            or not is_allowed_official_url(download_url)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or size_bytes <= 256
            or size_bytes > 25 * 1024 * 1024
            or not isinstance(procedure_ids, list)
            or not procedure_ids
            or any(
                not isinstance(procedure_id, str)
                or not re.fullmatch(r"\d+\.\d{6}", procedure_id)
                for procedure_id in procedure_ids
            )
            or procedure_ids != sorted(set(procedure_ids))
            or not isinstance(pages, list)
            or not pages
            or any(
                not isinstance(page, int) or isinstance(page, bool) or page < 0
                for page in pages
            )
            or pages != sorted(set(pages))
            or pages != list(range(pages[0], pages[-1] + 1))
        ):
            return None, "CURATED_OFFICIAL_PAGE_BINDING_INVALID"
        if (
            binding_codes[0] == codes[0]
            and binding_appendix == appendix_identifier
            and set(procedure_ids) == group_procedure_ids
        ):
            matches.append(binding)
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "CURATED_OFFICIAL_PAGE_BINDING_AMBIGUOUS"
    return None, None


def _extract_from_explicit_pdf_page_range(
    content: bytes,
    *,
    form_code: str,
    appendix_identifier: str,
    source_pages_zero_based: list[int],
    download_url: str,
    download_dir: Path,
) -> dict[str, Any] | None:
    """Copy a previously verified contiguous range from a source PDF.

    This is used only with a checksum-pinned, manually verified registry
    binding.  It does not inspect OCR text or infer a form boundary.
    """

    if (
        not source_pages_zero_based
        or any(
            not isinstance(page, int) or isinstance(page, bool) or page < 0
            for page in source_pages_zero_based
        )
        or source_pages_zero_based != sorted(set(source_pages_zero_based))
        or source_pages_zero_based
        != list(
            range(
                source_pages_zero_based[0],
                source_pages_zero_based[-1] + 1,
            )
        )
    ):
        return None
    try:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(BytesIO(content))
        if source_pages_zero_based[-1] >= len(reader.pages):
            return None
        writer = PdfWriter()
        for page_index in source_pages_zero_based:
            writer.add_page(reader.pages[page_index])
        output = BytesIO()
        writer.write(output)
    except Exception:  # pragma: no cover - parser boundary
        return None
    extracted = output.getvalue()
    valid, _reason, file_format = validate_download(
        extracted,
        "application/pdf",
        "https://vbpl-bientap-gateway.moj.gov.vn/extracted.pdf",
    )
    if not valid or file_format != "pdf":
        return None
    artifact = _save_artifact(
        extracted,
        file_format="pdf",
        download_url=download_url,
        download_dir=download_dir,
        source_package_sha256=hashlib.sha256(content).hexdigest(),
        source_pages_zero_based=list(source_pages_zero_based),
    )
    artifact["extraction"] = {
        "kind": "curated_official_package_page_range",
        "complete": True,
        "source_pages_zero_based": list(source_pages_zero_based),
        "form_code": form_code,
        "appendix_identifier": appendix_identifier,
    }
    return artifact


def _extract_from_pdf_package(
    content: bytes,
    *,
    form_code: str,
    appendix_identifier: str | None = None,
    download_url: str,
    download_dir: Path,
    allow_ocr: bool = True,
    ocr_cache_dir: Path = DEFAULT_OCR_CACHE_DIR,
) -> dict[str, Any] | None:
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return None
    reader = PdfReader(BytesIO(content))
    page_texts = [str(page.extract_text() or "") for page in reader.pages]
    page_range = locate_pdf_form_page_range(
        page_texts,
        form_code,
        appendix_identifier=appendix_identifier,
    )
    if (
        allow_ocr
        and page_range is None
        and not any(text.strip() for text in page_texts)
    ):
        try:
            from api.crawlers.ocr_extractor import extract_ocr_from_pdf_bytes

            ocr_cache_path = (
                ocr_cache_dir
                / f"{hashlib.sha256(content).hexdigest()}.json"
            )
            ocr = _read(ocr_cache_path, None)
            if not isinstance(ocr, Mapping) or ocr.get("complete") is not True:
                ocr = extract_ocr_from_pdf_bytes(
                    content,
                    dpi=150,
                    page_timeout_seconds=45,
                    collect_confidence=False,
                )
                if ocr.get("complete") is True:
                    _write(ocr_cache_path, ocr)
        except Exception:  # pragma: no cover - optional tool boundary
            ocr = {"complete": False, "text": ""}
        if ocr.get("complete") is True and str(ocr.get("text") or "").strip():
            by_page = {
                int(number): text
                for number, text in re.findall(
                    r"(?ms)^--- Trang (\d+) ---\s*(.*?)(?=^--- Trang \d+ ---|\Z)",
                    str(ocr["text"]),
                )
            }
            if len(by_page) == len(page_texts):
                page_texts = [
                    by_page.get(index + 1, "")
                    for index in range(len(page_texts))
                ]
                page_range = locate_pdf_form_page_range(
                    page_texts,
                    form_code,
                    appendix_identifier=appendix_identifier,
                )
    if page_range is None:
        return None
    start, end = page_range
    writer = PdfWriter()
    for index in range(start, end):
        writer.add_page(reader.pages[index])
    output = BytesIO()
    writer.write(output)
    extracted = output.getvalue()
    valid, _reason, file_format = validate_download(
        extracted,
        "application/pdf",
        "https://vbpl-bientap-gateway.moj.gov.vn/extracted.pdf",
    )
    if not valid or file_format != "pdf":
        return None
    return _save_artifact(
        extracted,
        file_format="pdf",
        download_url=download_url,
        download_dir=download_dir,
        source_package_sha256=hashlib.sha256(content).hexdigest(),
        source_pages_zero_based=list(range(start, end)),
    )


def _deterministic_docx_bytes(content: bytes) -> bytes:
    """Normalize ZIP timestamps and core metadata after structural DOCX slicing.

    ``python-docx`` writes a new package on every extraction and otherwise
    embeds the current time in both ZIP members and ``docProps/core.xml``. The
    visible form is identical but its checksum changes, which is unacceptable
    for review evidence. This canonicalization changes no document body,
    relationships or media and gives the same source bytes the same artifact
    checksum on repeat runs.
    """

    fixed_timestamp = b"1980-01-01T00:00:00Z"
    output = BytesIO()
    try:
        with zipfile.ZipFile(BytesIO(content), "r") as source, zipfile.ZipFile(
            output,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as destination:
            for member in sorted(source.infolist(), key=lambda item: item.filename):
                data = source.read(member)
                if member.filename == "docProps/core.xml":
                    for field in (b"created", b"modified"):
                        data = re.sub(
                            br"(<dcterms:" + field + br"[^>]*>).*?(</dcterms:"
                            + field
                            + br">)",
                            lambda match: match.group(1)
                            + fixed_timestamp
                            + match.group(2),
                            data,
                            flags=re.DOTALL,
                        )
                normalized = zipfile.ZipInfo(
                    member.filename,
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                normalized.compress_type = zipfile.ZIP_DEFLATED
                normalized.create_system = 3
                normalized.external_attr = 0o600 << 16
                destination.writestr(normalized, data)
    except (OSError, zipfile.BadZipFile):
        return content
    return output.getvalue()


def _extract_from_docx_package(
    content: bytes,
    *,
    form_code: str,
    appendix_identifier: str | None = None,
    download_url: str,
    download_dir: Path,
    source_package_sha256: str | None = None,
) -> dict[str, Any] | None:
    try:
        from docx import Document
    except ImportError:
        return None
    document = Document(BytesIO(content))
    body = document.element.body
    elements = [
        element
        for element in list(body)
        if not str(element.tag).endswith("}sectPr")
    ]
    texts = [
        "".join(
            str(node.text or "")
            for node in element.iter()
            if str(node.tag).endswith("}t")
        )
        for element in elements
    ]
    codes_by_element = [extract_strict_form_codes(text) for text in texts]
    requested_appendix = normalize_appendix_identifier(appendix_identifier)
    if appendix_identifier and not requested_appendix:
        return None
    current_appendix: str | None = None
    appendix_by_element: list[str | None] = []
    appendix_heading_indexes: set[int] = set()
    for index, text in enumerate(texts):
        heading = extract_appendix_heading_identifier(text)
        if heading:
            current_appendix = heading
            appendix_heading_indexes.add(index)
        appendix_by_element.append(current_appendix)
    if requested_appendix and requested_appendix not in appendix_by_element:
        return None

    def matches_requested_appendix(index: int) -> bool:
        if requested_appendix is None:
            return True
        if appendix_by_element[index] == requested_appendix:
            return True
        next_index = index + 1
        return (
            next_index < len(elements)
            and next_index in appendix_heading_indexes
            and appendix_by_element[next_index] == requested_appendix
        )

    matching_indexes = [
        index
        for index, codes in enumerate(codes_by_element)
        if codes == [form_code]
        and matches_requested_appendix(index)
    ]
    if not matching_indexes:
        return None
    code_pattern = re.escape(form_code).replace(r"\ ", r"\s+")
    header_pattern = re.compile(
        rf"^\s*Mẫu\s*(?:số\s*)?{code_pattern}\b",
        re.IGNORECASE,
    )
    header_starts = [
        index
        for index in matching_indexes
        if header_pattern.search(texts[index])
        and " theo Mẫu" not in texts[index]
        and len(" ".join(texts[index].split())) <= 180
    ]
    # A legal provision often cites "theo Mẫu số 02" before the appendix.
    # Starting extraction at that citation truncates the candidate or triggers
    # expensive OCR even though the official DOC contains a clean form header.
    # Require one unambiguous appendix header and fail closed otherwise.
    if len(header_starts) != 1:
        return None
    start = header_starts[0]
    leading_appendix_index = (
        start + 1
        if (
            requested_appendix
            and start + 1 in appendix_heading_indexes
            and appendix_by_element[start + 1] == requested_appendix
        )
        else None
    )
    end = min(len(elements), start + 80)
    for index in range(start + 1, end):
        if index in appendix_heading_indexes:
            if index == leading_appendix_index:
                continue
            end = index
            break
        if re.search(
            r"(?iu)\bmã\s+thủ\s+tục\s+hành\s+chính\s*:",
            texts[index],
        ):
            end = index
            break
        if (
            requested_appendix
            and appendix_by_element[index] != requested_appendix
        ):
            end = index
            break
        if codes_by_element[index] and form_code not in codes_by_element[index]:
            end = index
            break
    selected_text = " ".join(texts[start:end]).strip()
    if len(selected_text) < 100:
        return None
    for index, element in enumerate(elements):
        if index < start or index >= end:
            body.remove(element)
    output = BytesIO()
    document.save(output)
    extracted = _deterministic_docx_bytes(output.getvalue())
    valid, _reason, file_format = validate_download(
        extracted,
        (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        "https://vbpl-bientap-gateway.moj.gov.vn/extracted.docx",
    )
    if not valid or file_format != "docx":
        return None
    artifact = _save_artifact(
        extracted,
        file_format="docx",
        download_url=download_url,
        download_dir=download_dir,
        source_package_sha256=(
            source_package_sha256 or hashlib.sha256(content).hexdigest()
        ),
    )
    artifact["extraction"] = {
        "kind": "structural_docx_form_boundary",
        "complete": True,
        "element_range": [start, end],
        "form_code": form_code,
        "appendix_identifier": requested_appendix,
    }
    return artifact


def _extract_from_legacy_doc_package(
    content: bytes,
    *,
    filename: str,
    form_code: str,
    appendix_identifier: str | None = None,
    download_url: str,
    download_dir: Path,
    timeout: float = 45.0,
) -> tuple[dict[str, Any] | None, str]:
    from api.crawlers.office_converter import convert_legacy_doc_bytes

    converted = convert_legacy_doc_bytes(
        content,
        filename=filename,
        timeout_seconds=max(10, min(60, int(timeout))),
    )
    if converted.get("status") != "ok" or converted.get("complete") is not True:
        return None, str(
            converted.get("reason_code") or "LIBREOFFICE_CONVERSION_FAILED"
        )
    artifact = _extract_from_docx_package(
        bytes(converted["content"]),
        form_code=form_code,
        appendix_identifier=appendix_identifier,
        download_url=download_url,
        download_dir=download_dir,
        source_package_sha256=hashlib.sha256(content).hexdigest(),
    )
    if artifact is None:
        return None, "OFFICIAL_FORM_FILE_NOT_FOUND"
    artifact["source_file_format"] = "doc"
    artifact["conversion"] = {
        "converter": "libreoffice-headless",
        "complete": True,
        "converted_sha256": converted.get("sha256"),
    }
    return artifact, "EXTRACTED_FROM_LEGACY_OFFICIAL_DOC_PACKAGE"


def resolve_group_artifact(
    group: Mapping[str, Any],
    bundle: Mapping[str, Any],
    *,
    timeout: float,
    download_dir: Path,
) -> tuple[dict[str, Any] | None, str]:
    document = bundle.get("document")
    files = bundle.get("files")
    if not isinstance(document, Mapping) or not isinstance(files, list):
        return None, str(bundle.get("reason_code") or "OFFICIAL_DOCUMENT_NOT_FOUND")
    document_id = str(document.get("id") or "")
    standalone = select_standalone_form_file(files, str(group.get("form_code") or ""))
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        verify=build_verified_ssl_context(),
    ) as client:
        if group.get("official_dvc_attachment_required") is True:
            dvc_reason = str(
                group.get("official_dvc_attachment_reason_code") or ""
            ).strip()
            dvc_requests = [
                dict(item)
                for item in group.get("official_dvc_attachment_requests") or []
                if isinstance(item, Mapping)
            ]
            if dvc_reason:
                return None, dvc_reason
            if len(dvc_requests) != 1:
                return None, "OFFICIAL_DVC_ATTACHMENT_RULE_AMBIGUOUS"
            request = dvc_requests[0]
            try:
                content, retrieval_url, file_format = (
                    _validated_dvc_attachment_content(
                        client,
                        request=request,
                        cache_dir=DEFAULT_CACHE_DIR / "dvc-attachments",
                    )
                )
            except httpx.HTTPError:
                return None, "OFFICIAL_FORM_DOWNLOAD_FAILED"
            except ValueError as exc:
                return None, str(exc) or "OFFICIAL_FORM_DOWNLOAD_FAILED"

            source_page_url = str(
                request.get("source_page_url") or ""
            ).strip()
            source_package_sha256 = hashlib.sha256(content).hexdigest()
            if file_format == "pdf":
                artifact = _extract_from_pdf_package(
                    content,
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=source_page_url,
                    download_dir=download_dir,
                    allow_ocr=True,
                )
                artifact_reason = "OFFICIAL_FORM_FILE_NOT_FOUND"
            elif file_format == "docx":
                artifact = _extract_from_docx_package(
                    content,
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=source_page_url,
                    download_dir=download_dir,
                    source_package_sha256=source_package_sha256,
                )
                artifact_reason = "OFFICIAL_FORM_FILE_NOT_FOUND"
            elif file_format == "doc":
                artifact, artifact_reason = _extract_from_legacy_doc_package(
                    content,
                    filename=str(
                        request.get("file_name") or "official-form.doc"
                    ),
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=source_page_url,
                    download_dir=download_dir,
                    timeout=timeout,
                )
            else:
                artifact = None
                artifact_reason = "OFFICIAL_FORM_FILE_NOT_FOUND"
            if artifact is None:
                return None, artifact_reason
            artifact.update(
                {
                    "download_url": source_page_url,
                    "source_page_url": source_page_url,
                    "source_retrieval_url": retrieval_url,
                    "source_retrieval_method": "POST",
                    "source_attachment_id": str(
                        request.get("attachment_id") or ""
                    ),
                    "source_file_name": str(
                        request.get("file_name") or ""
                    ),
                    "source_package_sha256": source_package_sha256,
                    "source_package_size_bytes": len(content),
                    "publication_decision_number": str(
                        request.get("publication_decision_number") or ""
                    ),
                    "provenance_kind": "official_dvc_attachment",
                    "publisher": DVC_PUBLISHER,
                }
            )
            return artifact, "EXTRACTED_FROM_OFFICIAL_DVC_ATTACHMENT"

        binding, binding_reason = _select_explicit_form_page_binding(
            group,
            bundle,
        )
        if binding_reason:
            return None, binding_reason
        if binding is not None:
            try:
                content, retrieval_url, file_format = _validated_direct_content(
                    client,
                    url=str(binding["official_download_url"]),
                    cache_dir=DEFAULT_CACHE_DIR / "attachments",
                )
            except httpx.HTTPError:
                return None, "OFFICIAL_FORM_DOWNLOAD_FAILED"
            except ValueError as exc:
                return None, str(exc) or "OFFICIAL_FORM_DOWNLOAD_FAILED"
            source_package_sha256 = hashlib.sha256(content).hexdigest()
            if source_package_sha256 != str(
                binding["source_package_sha256"]
            ).casefold() or len(content) != int(
                binding["source_package_size_bytes"]
            ):
                return None, "CURATED_OFFICIAL_PACKAGE_CHECKSUM_DRIFT"
            if file_format != "pdf":
                return None, "CURATED_OFFICIAL_PACKAGE_FORMAT_INVALID"
            artifact = _extract_from_explicit_pdf_page_range(
                content,
                form_code=str(group.get("form_code") or ""),
                appendix_identifier=str(group.get("appendix_identifier") or ""),
                source_pages_zero_based=list(
                    binding["source_pages_zero_based"]
                ),
                download_url=retrieval_url,
                download_dir=download_dir,
            )
            if artifact is None:
                return None, "CURATED_OFFICIAL_PAGE_EXTRACTION_FAILED"
            artifact.update(
                {
                    "source_page_url": str(
                        binding["source_page_url"]
                    ).strip(),
                    "source_retrieval_url": retrieval_url,
                    "source_retrieval_method": "GET",
                    "source_file_name": (
                        Path(urlparse(retrieval_url).path).name
                        or "official-form.pdf"
                    ),
                    "source_package_sha256": source_package_sha256,
                    "source_package_size_bytes": len(content),
                    "provenance_kind": "curated_official_package_page_range",
                    "publisher": str(
                        document.get("publisher") or ""
                    ).strip()
                    or "Official government source",
                }
            )
            return artifact, "EXTRACTED_FROM_CURATED_OFFICIAL_PACKAGE_PAGE_RANGE"

        if standalone is not None:
            try:
                content, url, file_format = _validated_content(
                    client,
                    document_id=document_id,
                    file_record=standalone,
                    cache_dir=DEFAULT_CACHE_DIR / "attachments",
                )
                return (
                    _save_artifact(
                        content,
                        file_format=file_format,
                        download_url=url,
                        download_dir=download_dir,
                    ),
                    "STANDALONE_OFFICIAL_FORM_FILE",
                )
            except (httpx.HTTPError, ValueError):
                return None, "OFFICIAL_FORM_DOWNLOAD_FAILED"

        direct_extracted: list[dict[str, Any]] = []
        direct_package_sha256: set[str] = set()
        direct_failure_reasons: list[str] = []
        for direct_url in list(
            dict.fromkeys(group.get("official_download_urls") or [])
        )[:8]:
            try:
                content, url, file_format = _validated_direct_content(
                    client,
                    url=str(direct_url),
                    cache_dir=DEFAULT_CACHE_DIR / "attachments",
                )
            except (httpx.HTTPError, ValueError):
                continue
            source_package_sha256 = hashlib.sha256(content).hexdigest()
            if source_package_sha256 in direct_package_sha256:
                continue
            direct_package_sha256.add(source_package_sha256)
            if file_format == "pdf":
                artifact = _extract_from_pdf_package(
                    content,
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=url,
                    download_dir=download_dir,
                    allow_ocr=True,
                )
            elif file_format == "docx":
                artifact = _extract_from_docx_package(
                    content,
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=url,
                    download_dir=download_dir,
                )
            elif file_format == "doc":
                artifact, conversion_reason = _extract_from_legacy_doc_package(
                    content,
                    filename=(Path(urlparse(url).path).name or "form.doc"),
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=url,
                    download_dir=download_dir,
                    timeout=timeout,
                )
                if artifact is None:
                    direct_failure_reasons.append(conversion_reason)
            else:
                artifact = None
            if artifact is not None:
                # Direct Government/Cong Bao packages have no VBPL attachment
                # row.  Preserve the page, package checksum and publisher so
                # the legal reviewer sees the same evidence chain.
                artifact.update(
                    {
                        "source_page_url": str(
                            document.get("detailUrl") or url
                        ).strip()
                        or url,
                        "source_retrieval_url": url,
                        "source_retrieval_method": "GET",
                        "source_file_name": (
                            Path(urlparse(url).path).name or "official-form"
                        ),
                        "source_package_sha256": source_package_sha256,
                        "source_package_size_bytes": len(content),
                        "provenance_kind": str(
                            document.get("source_adapter")
                            or "official_direct_attachment"
                        ),
                        "publisher": str(
                            document.get("publisher") or ""
                        ).strip()
                        or "Official government source",
                    }
                )
                direct_extracted.append(artifact)
        direct_unique = {
            str(item.get("sha256") or ""): item
            for item in direct_extracted
            if str(item.get("sha256") or "")
        }
        if len(direct_unique) == 1:
            return (
                next(iter(direct_unique.values())),
                "EXTRACTED_FROM_DIRECT_OFFICIAL_PACKAGE",
            )
        if len(direct_unique) > 1:
            return None, "AMBIGUOUS_OFFICIAL_PDF_PACKAGE"

        package_files = [
            item
            for item in files
            if isinstance(item, Mapping)
            and str(item.get("fileName") or "").casefold().endswith(
                (".pdf", ".docx", ".doc")
            )
        ]
        # Prefer directly parseable packages before legacy DOC conversion.
        # A broken/interactive office converter must not block the rest of the
        # campaign; the DOC path remains a fallback when PDF/DOCX extraction
        # cannot identify the requested form.
        package_files.sort(
            key=lambda item: (
                0
                if str(item.get("fileName") or "").casefold().endswith(
                    (".pdf", ".docx")
                )
                else 1,
                str(item.get("fileName") or "").casefold(),
            )
        )
        extracted: list[dict[str, Any]] = []
        failure_reasons: list[str] = list(direct_failure_reasons)
        pdf_ocr_fallbacks: list[tuple[bytes, str]] = []
        processed_package_sha256: set[str] = set()
        for package in package_files[:8]:
            try:
                content, url, file_format = _validated_content(
                    client,
                    document_id=document_id,
                    file_record=package,
                    cache_dir=DEFAULT_CACHE_DIR / "attachments",
                )
            except (httpx.HTTPError, ValueError):
                continue
            # VBPL can expose the same official attachment through multiple
            # file records. Validate every record first, then extract identical
            # bytes only once; filenames, sizes and URLs are never sufficient
            # to collapse distinct evidence.
            source_package_sha256 = hashlib.sha256(content).hexdigest()
            if source_package_sha256 in processed_package_sha256:
                continue
            processed_package_sha256.add(source_package_sha256)
            if file_format == "pdf":
                artifact = _extract_from_pdf_package(
                    content,
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=url,
                    download_dir=download_dir,
                    # Do not OCR a complete scan before trying an accompanying
                    # machine-readable DOC/DOCX package. OCR remains the last
                    # local fallback and is never skipped when it is the only
                    # viable official artifact.
                    allow_ocr=False,
                )
                if artifact is None:
                    pdf_ocr_fallbacks.append((content, url))
            elif file_format == "docx":
                artifact = _extract_from_docx_package(
                    content,
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=url,
                    download_dir=download_dir,
                )
            elif file_format == "doc":
                artifact, conversion_reason = _extract_from_legacy_doc_package(
                    content,
                    filename=str(package.get("fileName") or "form.doc"),
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=url,
                    download_dir=download_dir,
                    timeout=timeout,
                )
                if artifact is None:
                    failure_reasons.append(conversion_reason)
            else:
                artifact = None
            if artifact is not None:
                extracted.append(artifact)
        if not extracted:
            for content, url in pdf_ocr_fallbacks:
                artifact = _extract_from_pdf_package(
                    content,
                    form_code=str(group.get("form_code") or ""),
                    appendix_identifier=(
                        str(group.get("appendix_identifier") or "") or None
                    ),
                    download_url=url,
                    download_dir=download_dir,
                    allow_ocr=True,
                )
                if artifact is not None:
                    extracted.append(artifact)
        unique = {item["sha256"]: item for item in extracted}
        if len(unique) == 1:
            artifact = next(iter(unique.values()))
            return artifact, "EXTRACTED_FROM_OFFICIAL_PACKAGE"
        if len(unique) > 1:
            return None, "AMBIGUOUS_OFFICIAL_PDF_PACKAGE"
        if failure_reasons and not extracted:
            return None, sorted(failure_reasons)[0]
    return None, "OFFICIAL_FORM_FILE_NOT_FOUND"


def _bound_group_for_procedure(
    group: Mapping[str, Any],
    procedure_id: str,
) -> dict[str, Any]:
    """Retain group-level jurisdiction when a binding has no narrower value."""

    procedure_meta = (group.get("procedure_metadata") or {}).get(
        procedure_id,
        {},
    )
    if not isinstance(procedure_meta, Mapping):
        procedure_meta = {}
    return {
        **group,
        "domain": procedure_meta.get("domain") or group.get("domain"),
        "source_tier": procedure_meta.get("source_tier")
        or group.get("source_tier"),
        "executing_level": procedure_meta.get("executing_level")
        or group.get("executing_level")
        or "commune",
    }


def _artifact_worker(
    output: Any,
    group: Mapping[str, Any],
    bundle: Mapping[str, Any],
    timeout: float,
    download_dir: Path,
) -> None:
    try:
        output.put(
            resolve_group_artifact(
                group,
                bundle,
                timeout=timeout,
                download_dir=download_dir,
            )
        )
    except Exception as exc:  # noqa: BLE001
        output.put((None, f"ARTIFACT_EXTRACTION_FAILED:{type(exc).__name__}"))


def resolve_group_artifact_bounded(
    group: Mapping[str, Any],
    bundle: Mapping[str, Any],
    *,
    timeout: float,
    download_dir: Path,
) -> tuple[dict[str, Any] | None, str]:
    """Isolate one heavy package extraction so later groups keep running."""

    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(
        target=_artifact_worker,
        args=(output, dict(group), dict(bundle), timeout, download_dir),
    )
    process.start()
    # OCR is isolated and cached per official package. The first complete scan
    # may take several minutes, but subsequent form codes reuse the same text.
    # Keep one finite group deadline while avoiding repeated 135-second kills
    # that could never produce a durable OCR checkpoint.
    process.join(timeout=max(30.0, timeout * 12))
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        output.close()
        return None, "OFFICIAL_ARTIFACT_PROCESSING_TIMEOUT"
    try:
        result = output.get_nowait()
    except queue.Empty:
        return None, "OFFICIAL_ARTIFACT_PROCESSING_FAILED"
    finally:
        output.close()
    if not isinstance(result, tuple) or len(result) != 2:
        return None, "OFFICIAL_ARTIFACT_PROCESSING_FAILED"
    artifact, reason = result
    return artifact, str(reason or "OFFICIAL_ARTIFACT_PROCESSING_FAILED")


def _occurrences(inventory: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = [
        *inventory.get("verified_data_gaps", []),
        *inventory.get("canonical_forms", []),
    ]
    return [
        dict(item)
        for item in rows
        if isinstance(item, Mapping)
        and item.get("component_kind") == "applicant_form"
        and item.get("prior_approval_status") is not True
    ]


def _backup(path: Path, run_id: str) -> Path | None:
    if not path.is_file():
        return None
    destination = (
        ROOT
        / "backups"
        / "form-candidate-queue"
        / run_id
        / path.name
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument(
        "--effectivity-evidence",
        type=Path,
        default=DEFAULT_EFFECTIVITY_EVIDENCE,
    )
    parser.add_argument("--legal-as-of", default="2026-07-27")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--commit-candidates", action="store_true")
    parser.add_argument("--max-groups", type=int)
    parser.add_argument("--group-id", action="append", default=[])
    parser.add_argument("--groups-output", type=Path, default=DEFAULT_GROUPS)
    parser.add_argument(
        "--resolution-output", type=Path, default=DEFAULT_RESOLUTION
    )
    parser.add_argument("--shortlist-output", type=Path, default=DEFAULT_SHORTLIST)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    inventory = _read(args.inventory, {})
    effectivity_payload = _read(
        args.effectivity_evidence,
        {"form_effectivity": []},
    )
    if not isinstance(effectivity_payload, Mapping):
        effectivity_payload = {"form_effectivity": []}
    effectivity_rules, effectivity_evidence_rejections = (
        _validated_form_effectivity_rules(
            effectivity_payload,
            legal_as_of=args.legal_as_of,
        )
    )
    occurrences = _occurrences(inventory)
    grouped = group_occurrences(occurrences)
    dvc_attachment_binding_issues = _bind_dvc_attachment_rules(
        grouped["groups"],
        effectivity_rules,
    )
    groups = grouped["groups"]
    active_group_ids = {
        str(item.get("group_id") or "").strip()
        for item in grouped["groups"]
        if str(item.get("group_id") or "").strip()
    }
    active_instruments = {
        str(item.get("issuing_instrument") or "").strip()
        for item in grouped["groups"]
        if str(item.get("issuing_instrument") or "").strip()
    }
    if args.group_id:
        selected_ids = set(args.group_id)
        groups = [item for item in groups if item["group_id"] in selected_ids]
    if args.max_groups is not None:
        groups = groups[: max(0, args.max_groups)]
    prior_resolution = _resumable_prior_resolution(
        _filter_prior_resolution_for_active_groups(
            _read(args.resolution_output, {}),
            active_group_ids=active_group_ids,
            active_instruments=active_instruments,
        )
    )
    resume_mode = bool(prior_resolution)
    run_id = (
        str(prior_resolution.get("run_id"))
        if resume_mode
        else datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    )
    processed_group_ids = (
        _resume_processed_group_ids(prior_resolution)
        if resume_mode
        else set()
    )
    groups = [
        item for item in groups if str(item.get("group_id") or "") not in processed_group_ids
    ]

    all_instruments = sorted(
        {item["issuing_instrument"] for item in grouped["groups"]}
    )
    instruments = sorted({item["issuing_instrument"] for item in groups})
    fallback_urls_by_instrument: dict[str, list[str]] = {}
    for group in groups:
        instrument = str(group.get("issuing_instrument") or "").strip()
        if not instrument:
            continue
        fallback_urls_by_instrument[instrument] = list(
            dict.fromkeys(
                [
                    *(group.get("official_source_urls") or []),
                    *(group.get("official_download_urls") or []),
                ]
            )
        )
    bundles: dict[str, dict[str, Any]] = {}
    external_errors: list[dict[str, str]] = [
        dict(item)
        for item in (prior_resolution.get("external_errors") or [])
        if isinstance(item, Mapping)
    ]
    source_attempts: list[dict[str, Any]] = [
        dict(item)
        for item in (prior_resolution.get("source_attempts") or [])
        if isinstance(item, Mapping)
    ]
    # Persist the identity grouping before network work starts so an
    # interrupted subprocess can still be resumed and attributed per source.
    _write(
        args.groups_output,
        {
            "schema_version": 1,
            "run_id": run_id,
            "legal_as_of": args.legal_as_of,
            "input_occurrence_count": len(occurrences),
            "group_count": len(grouped["groups"]),
            "groups": grouped["groups"],
            "unresolved": grouped["unresolved"],
            "dvc_attachment_binding_issues": dvc_attachment_binding_issues,
        },
    )
    _write(
        args.resolution_output,
        {
            "schema_version": 1,
            "run_id": run_id,
            "generated_at": _utcnow(),
            "legal_as_of": args.legal_as_of,
            "candidate_only": True,
            "auto_approved_count": 0,
            "effectivity_evidence_rule_count": len(effectivity_rules),
            "effectivity_evidence_rejections": effectivity_evidence_rejections,
            "dvc_attachment_binding_issues": dvc_attachment_binding_issues,
            "pending_records": list(prior_resolution.get("pending_records") or []),
            "resolved_groups": list(prior_resolution.get("resolved_groups") or []),
            "verified_data_gaps": list(prior_resolution.get("verified_data_gaps") or []),
            "source_attempts": source_attempts,
            "source_attempts_complete": False,
            "group_progress_complete": False,
            "processed_group_count": len(processed_group_ids),
        },
    )
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(
                (
                    load_cached_official_document
                    if args.offline
                    else fetch_official_document
                ),
                instrument,
                cache_dir=DEFAULT_CACHE_DIR,
                **(
                    {}
                    if args.offline
                    else {
                        "timeout": args.timeout,
                        "refresh": args.refresh,
                        "fallback_urls": fallback_urls_by_instrument.get(
                            instrument, []
                        ),
                        "legal_as_of": args.legal_as_of,
                    }
                ),
            ): instrument
            for instrument in instruments
        }
        for future in as_completed(futures):
            instrument = futures[future]
            try:
                bundles[instrument] = future.result()
                bundle = bundles[instrument]
                document = bundle.get("document")
                if isinstance(document, Mapping):
                    bundle["document"] = _attach_form_effectivity_evidence(
                        document,
                        effectivity_rules,
                    )
                source_attempts.append(
                    {
                        "instrument": instrument,
                        "source_domain": "vbpl.vn",
                        "status": str(bundle.get("status") or "unknown"),
                        "reason_code": str(
                            bundle.get("reason_code")
                            or "OFFICIAL_SOURCE_RESULT_UNCLASSIFIED"
                        ),
                        "checked_at": _utcnow(),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                reason_code = classify_official_source_failure(exc)
                bundles[instrument] = {
                    "instrument": instrument,
                    "status": "blocked_external",
                    "reason_code": reason_code,
                }
                external_errors.append(
                    {
                        "instrument": instrument,
                        "error_type": type(exc).__name__,
                        "reason_code": reason_code,
                    }
                )
                source_attempts.append(
                    {
                        "instrument": instrument,
                        "source_domain": "vbpl.vn",
                        "status": "blocked_external",
                        "reason_code": reason_code,
                        "checked_at": _utcnow(),
                    }
                )
            _write(
                args.resolution_output,
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "generated_at": _utcnow(),
                    "legal_as_of": args.legal_as_of,
                    "candidate_only": True,
                    "auto_approved_count": 0,
                    "effectivity_evidence_rule_count": len(effectivity_rules),
                    "effectivity_evidence_rejections": (
                        effectivity_evidence_rejections
                    ),
                    "dvc_attachment_binding_issues": (
                        dvc_attachment_binding_issues
                    ),
                    "pending_records": list(
                        prior_resolution.get("pending_records") or []
                    ),
                    "resolved_groups": list(
                        prior_resolution.get("resolved_groups") or []
                    ),
                    "verified_data_gaps": list(
                        prior_resolution.get("verified_data_gaps") or []
                    ),
                    "source_attempts": _merge_source_attempts(
                        [], source_attempts
                    ),
                    "source_attempts_complete": len(
                        _merge_source_attempts([], source_attempts)
                    )
                    == len(all_instruments),
                    "group_progress_complete": False,
                    "processed_group_count": len(processed_group_ids),
                },
            )

    pending_records: list[dict[str, Any]] = [
        dict(item)
        for item in (prior_resolution.get("pending_records") or [])
        if isinstance(item, Mapping)
    ]
    resolved_groups: list[dict[str, Any]] = [
        dict(item)
        for item in (prior_resolution.get("resolved_groups") or [])
        if isinstance(item, Mapping)
    ]
    gaps: list[dict[str, Any]] = [
        dict(item)
        for item in (prior_resolution.get("verified_data_gaps") or [])
        if isinstance(item, Mapping)
    ]
    existing_gap_ids = {
        str(item.get("occurrence_id") or item.get("candidate_id") or "")
        for item in gaps
    }
    for item in grouped["unresolved"]:
        candidate_id = str(item.get("candidate_id") or "")
        if candidate_id in existing_gap_ids:
            continue
        gaps.append(
            {
                "candidate_id": item.get("candidate_id"),
                "procedure_id": item.get("procedure_id"),
                "reason_code": item.get("reason_code"),
            }
        )
        existing_gap_ids.add(candidate_id)

    def group_resolution_priority(group: Mapping[str, Any]) -> tuple[int, str, str]:
        bundle = bundles.get(str(group.get("issuing_instrument") or ""), {})
        document = bundle.get("document")
        if not isinstance(document, Mapping):
            priority = 0
        elif classify_form_effectivity(
            document,
            legal_as_of=args.legal_as_of,
            appendix_identifier=group.get("appendix_identifier"),
            form_code=group.get("form_code"),
        ).get("eligible") is not True:
            priority = 1
        else:
            priority = 2
        return (
            priority,
            str(group.get("issuing_instrument") or ""),
            str(group.get("form_code") or ""),
        )

    groups.sort(key=group_resolution_priority)

    def persist_group_progress() -> None:
        _write(
            args.resolution_output,
            {
                "schema_version": 1,
                "run_id": run_id,
                "generated_at": _utcnow(),
                "legal_as_of": args.legal_as_of,
                "candidate_only": True,
                "auto_approved_count": 0,
                "effectivity_evidence_rule_count": len(effectivity_rules),
                "effectivity_evidence_rejections": (
                    effectivity_evidence_rejections
                ),
                "dvc_attachment_binding_issues": (
                    dvc_attachment_binding_issues
                ),
                "pending_records": pending_records,
                "resolved_groups": resolved_groups,
                "verified_data_gaps": gaps,
                "source_attempts": sorted(
                    source_attempts,
                    key=lambda item: str(item.get("instrument") or ""),
                ),
                "source_attempts_complete": len(source_attempts)
                == len(all_instruments),
                "external_errors": external_errors,
                "group_progress_complete": False,
                "processed_group_count": len(resolved_groups)
                + max(0, len(gaps) - len(grouped["unresolved"])),
            },
        )

    for group in groups:
        bundle = bundles[group["issuing_instrument"]]
        direct_download_urls = [
            str(value).strip()
            for value in bundle.get("direct_download_urls") or []
            if str(value).strip()
        ]
        if direct_download_urls:
            group["official_download_urls"] = list(
                dict.fromkeys(
                    [
                        *(group.get("official_download_urls") or []),
                        *direct_download_urls,
                    ]
                )
            )
        document = bundle.get("document")
        if not isinstance(document, Mapping):
            gaps.append(
                {
                    "group_id": group["group_id"],
                    "form_code": group["form_code"],
                    "issuing_instrument": group["issuing_instrument"],
                    "procedure_ids": group["procedure_ids"],
                    "reason_code": bundle.get("reason_code"),
                }
            )
            persist_group_progress()
            continue
        effectivity = classify_form_effectivity(
            document,
            legal_as_of=args.legal_as_of,
            appendix_identifier=group.get("appendix_identifier"),
            form_code=group.get("form_code"),
        )
        if effectivity["eligible"] is not True:
            gaps.append(
                {
                    "group_id": group["group_id"],
                    "form_code": group["form_code"],
                    "issuing_instrument": group["issuing_instrument"],
                    "procedure_ids": group["procedure_ids"],
                    "reason_code": effectivity["reason_code"],
                    "effectivity": effectivity,
                }
            )
            persist_group_progress()
            continue
        artifact, artifact_reason = resolve_group_artifact_bounded(
            group,
            bundle,
            timeout=args.timeout,
            download_dir=DEFAULT_DOWNLOAD_DIR,
        )
        if artifact is None:
            gaps.append(
                {
                    "group_id": group["group_id"],
                    "form_code": group["form_code"],
                    "issuing_instrument": group["issuing_instrument"],
                    "procedure_ids": group["procedure_ids"],
                    "reason_code": artifact_reason,
                }
            )
            persist_group_progress()
            continue
        group_records = []
        for procedure_id in group["procedure_ids"]:
            bound_group = _bound_group_for_procedure(group, procedure_id)
            group_records.append(
                build_pending_candidate(
                    group=bound_group,
                    document=document,
                    artifact=artifact,
                    legal_as_of=args.legal_as_of,
                    procedure_id=procedure_id,
                )
            )
        pending_records.extend(group_records)
        resolved_groups.append(
            {
                "group_id": group["group_id"],
                "form_code": group["form_code"],
                "issuing_instrument": group["issuing_instrument"],
                "procedure_ids": group["procedure_ids"],
                "artifact": artifact,
                "reason_code": artifact_reason,
            }
        )
        persist_group_progress()

    merge_result = {"action_counts": {"created": 0, "updated": 0, "unchanged": 0}}
    stale_quarantined_count = 0
    backup_path = None
    if args.commit_candidates and pending_records:
        queue = _read(DEFAULT_QUEUE, {"summary": {}, "records": []})
        queue_records = list(queue.get("records") or [])
        if args.max_groups is None and not args.group_id:
            quarantined = quarantine_stale_pending_records(
                queue_records,
                active_ids={str(item["id"]) for item in pending_records},
            )
            queue_records = quarantined["records"]
            stale_quarantined_count = quarantined["quarantined_count"]
        merge_result = merge_pending_records(
            queue_records,
            pending_records,
        )
        backup_path = _backup(DEFAULT_QUEUE, run_id)
        summary = dict(queue.get("summary") or {})
        total_unapproved_three_tier = sum(
            1
            for item in merge_result["records"]
            if str(item.get("id") or "").startswith("three-tier-")
            and item.get("approved") is False
            and item.get("is_quarantined") is not True
        )
        summary.update(
            {
                # A targeted resolver run must not erase older unapproved
                # candidates from the Admin summary.  The merged queue is the
                # only authoritative scope for this aggregate.
                "three_tier_pending_review": total_unapproved_three_tier,
                "three_tier_last_resolution_run_id": run_id,
                "three_tier_auto_approved": 0,
                "three_tier_stale_quarantined": stale_quarantined_count,
            }
        )
        _write(
            DEFAULT_QUEUE,
            {
                **queue,
                "summary": summary,
                "records": merge_result["records"],
            },
        )

    reason_counts = Counter(str(item.get("reason_code") or "UNKNOWN") for item in gaps)
    groups_payload = {
        "schema_version": 1,
        "run_id": run_id,
        "legal_as_of": args.legal_as_of,
        "input_occurrence_count": len(occurrences),
        "group_count": len(grouped["groups"]),
        "groups": grouped["groups"],
        "unresolved": grouped["unresolved"],
        "dvc_attachment_binding_issues": dvc_attachment_binding_issues,
    }
    processed_after_run = set(processed_group_ids)
    processed_after_run.update(
        str(item.get("group_id") or "")
        for item in resolved_groups
        if str(item.get("group_id") or "")
    )
    processed_after_run.update(
        str(item.get("group_id") or "")
        for item in gaps
        if str(item.get("group_id") or "")
    )
    all_group_ids = {
        str(item.get("group_id") or "")
        for item in grouped["groups"]
        if str(item.get("group_id") or "")
    }
    resolution = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": _utcnow(),
        "legal_as_of": args.legal_as_of,
        "candidate_only": True,
        "auto_approved_count": 0,
        "effectivity_evidence_rule_count": len(effectivity_rules),
        "effectivity_evidence_rejections": effectivity_evidence_rejections,
        "dvc_attachment_binding_issues": dvc_attachment_binding_issues,
        "resolved_groups": resolved_groups,
        "pending_records": pending_records,
        "verified_data_gaps": gaps,
        "source_attempts": _merge_source_attempts([], source_attempts),
        "source_attempts_complete": len(
            _merge_source_attempts([], source_attempts)
        )
        == len(all_instruments),
        "group_progress_complete": all_group_ids.issubset(processed_after_run),
        "processed_group_count": len(processed_after_run & all_group_ids),
        "external_errors": external_errors,
        "queue_commit": bool(args.commit_candidates),
        "queue_merge": merge_result["action_counts"],
        "stale_quarantined_count": stale_quarantined_count,
        "queue_backup": str(backup_path) if backup_path else None,
    }
    shortlist = {
        "schema_version": 1,
        "run_id": run_id,
        "legal_as_of": args.legal_as_of,
        "status": (
            "READY_FOR_HUMAN_ATTESTATION"
            if pending_records
            else "VERIFIED_DATA_GAP"
        ),
        "human_confirmation_required": True,
        "records": pending_records,
    }
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "legal_as_of": args.legal_as_of,
        "input_occurrence_count": len(occurrences),
        "unique_group_count": len(grouped["groups"]),
        "unresolved_identity_count": len(grouped["unresolved"]),
        "official_instrument_count": len(instruments),
        "resolved_group_count": len(resolved_groups),
        "candidate_binding_count": len(pending_records),
        "verified_data_gap_count": len(gaps),
        "gap_reason_counts": dict(sorted(reason_counts.items())),
        "blocked_external_count": len(external_errors),
        "queue_commit": bool(args.commit_candidates),
        "queue_merge": merge_result["action_counts"],
        "stale_quarantined_count": stale_quarantined_count,
        "auto_approved_count": 0,
        "effectivity_evidence_rule_count": len(effectivity_rules),
        "effectivity_evidence_rejection_count": len(
            effectivity_evidence_rejections
        ),
        "dvc_attachment_binding_issue_count": len(
            dvc_attachment_binding_issues
        ),
        "human_confirmation_required": True,
        "contains_question_text": False,
        "contains_answer_text": False,
        "contains_credentials": False,
    }
    _write(args.groups_output, groups_payload)
    _write(args.resolution_output, resolution)
    _write(args.shortlist_output, shortlist)
    _write(args.report_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
