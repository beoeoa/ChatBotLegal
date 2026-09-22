"""Deterministic procedure and form catalog resolution.

This module deliberately does not use an LLM and does not search form binaries.
Legal citations stay in the legal retrieval pipeline; this catalog is only for
procedure identity and reviewed form assets.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = ROOT / "notebook_data" / "forms"
PROCEDURES_PATH = FORMS_DIR / "canonical_procedures_v1.json"
FORMS_PATH = FORMS_DIR / "canonical_forms_catalog_v1.json"
BINDINGS_PATH = FORMS_DIR / "procedure_form_bindings_v1.json"
THREE_TIER_PROCEDURES_PATH = FORMS_DIR / "three_tier_procedure_catalog_v1.json"
# A form can be technically valid and have an approved catalog/binding record,
# but still not be one of the forms officially required for that procedure.
# The requirement manifest is the release-bound source for that final mapping.
DEFAULT_REQUIREMENT_MANIFEST_PATH = (
    ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-30.json"
)

OFFICIAL_HOST_SUFFIXES = (
    "dichvucong.gov.vn",
    "dichvucong.haiphong.gov.vn",
    "haiphong.gov.vn",
    "vbpl.vn",
    "moj.gov.vn",
    "bocongan.gov.vn",
    "mps.gov.vn",
    "moc.gov.vn",
    "monre.gov.vn",
    "moh.gov.vn",
    "molisa.gov.vn",
    "baohiemxahoi.gov.vn",
    "chinhphu.vn",
    # Official CDN used by the Government Gazette's own download routes.
    "cdnchinhphu.vn",
)

FORM_DATA_GAP_REASONS = {
    "FORM_PROCEDURE_ID_MISSING",
    "FORM_FILE_INVALID",
    "FORM_FILE_OR_DOWNLOAD_MISSING",
    "FORM_URL_UNVERIFIED",
    "SOURCE_NOT_OFFICIAL",
    "EFFECTIVITY_UNKNOWN",
    "FORM_NOT_YET_EFFECTIVE",
    "FORM_EXPIRED",
    "FORM_SUPERSEDED",
    "FORM_CHECKSUM_INVALID",
    "SOURCE_NOT_DOWNLOADABLE",
    "PROCEDURE_RECORD_MISSING",
}

FORM_REVIEW_REQUIRED_REASONS = {
    "BINDING_NOT_APPROVED",
    "FORM_NOT_APPROVED",
    "FORM_APPROVAL_FLAG_MISSING",
    "FORM_NOT_RUNTIME_ELIGIBLE",
    "FORM_NOT_RELEASED_FOR_PROCEDURE",
}

PROCEDURE_ID_ALIASES = {
    "xac_nhan_doc_than": "xac_nhan_tinh_trang_hon_nhan",
    "khieu_nai": "khieu_nai_hanh_chinh",
    "tro_cap_bao_tro_xa_hoi": "tro_cap_xa_hoi",
}

SYNTHETIC_PROCEDURE_RE = re.compile(
    r"(?:^|_)(?:an_sinh|cu_tru|dat_dai|trat_tu|khieu_nai)_bo_sung_\d+$"
    r"|^(?:cap_ban_sao_ho_tich|ghi_chu_ho_tich|xac_nhan_ho_tich|"
    r"xac_minh_cu_tru|xac_nhan_dat_dai)_\d+$"
)


def fold_text(value: str | None) -> str:
    """Normalize Vietnamese text for exact catalog matching."""

    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D").casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _folded_clauses(value: str | None) -> list[str]:
    return [
        folded
        for part in re.split(r"[.!?;:\r\n]+", str(value or ""))
        if (folded := fold_text(part))
    ]


def _contains_folded_phrase(text: str, phrase: str) -> bool:
    return bool(
        phrase
        and re.search(rf"(?:^|\s){re.escape(phrase)}(?:\s|$)", text)
    )


def _form_code_is_actionable(question: str, folded_code: str) -> bool:
    """Require a local form cue and ignore negated/caution-only code mentions."""

    negated_patterns = (
        "khong duoc",
        "khong nen",
        "khong mac dinh",
        "khong su dung",
        "dung su dung",
        "tranh su dung",
        "chua xac minh",
        "neu chua xac minh",
    )
    matched = False
    for clause in _folded_clauses(question):
        if not _contains_folded_phrase(clause, folded_code):
            continue
        matched = True
        if any(_contains_folded_phrase(clause, pattern) for pattern in negated_patterns):
            continue
        clause_tokens = clause.split()
        code_tokens = folded_code.split()
        for start in range(0, len(clause_tokens) - len(code_tokens) + 1):
            if clause_tokens[start : start + len(code_tokens)] != code_tokens:
                continue
            prefix = " ".join(clause_tokens[max(0, start - 3) : start])
            local = " ".join(
                clause_tokens[
                    max(0, start - 4) : min(
                        len(clause_tokens),
                        start + len(code_tokens) + 5,
                    )
                ]
            )
            if all(token.isdigit() for token in code_tokens):
                if re.search(
                    r"(?:mau(?: so)?|phu luc|bieu mau|to khai)$",
                    prefix,
                ):
                    return True
                continue
            if any(
                _contains_folded_phrase(local, cue)
                for cue in (
                    "mau",
                    "mau so",
                    "bieu mau",
                    "to khai",
                    "phu luc",
                    "tai",
                    "dien",
                )
            ):
                return True
    return False if matched else False


def _form_intent_phrases(form: Mapping[str, Any]) -> list[str]:
    """Return deterministic, procedure-like phrases from an approved form name.

    Numeric form codes such as ``01`` are reused by many unrelated legal
    instruments.  The descriptive part of an approved form title is therefore
    useful evidence of intent, while the code by itself is not.  This helper
    only removes presentation wrappers and instrument suffixes; it never
    invents a procedure name or legal metadata.
    """

    values = [form.get("canonical_name"), *(form.get("aliases") or [])]
    phrases: list[str] = []
    wrappers = (
        "van ban de nghi",
        "don de nghi",
        "giay de nghi",
        "to khai de nghi",
        "to khai",
        "don",
        "phieu",
    )
    for value in values:
        folded = fold_text(value)
        if not folded:
            continue
        # Parenthetical text and the ``theo Mau...`` tail describe the
        # distribution artifact, not the user's underlying procedure intent.
        folded = re.split(r"\btheo\s+mau(?:\s+so)?\b", folded, maxsplit=1)[0]
        for wrapper in wrappers:
            if folded.startswith(f"{wrapper} "):
                folded = folded[len(wrapper) + 1 :]
                break
        folded = " ".join(folded.split())
        if len(folded.split()) >= 3:
            phrases.append(folded)
    return list(dict.fromkeys(phrases))


def _procedure_code_is_actionable(question: str, folded_code: str) -> bool:
    """Require an explicit procedure-code cue for short numeric identifiers."""

    if not folded_code or not re.fullmatch(r"\d+(?:\s+\d+)+", folded_code):
        return False
    query = fold_text(question)
    return bool(
        re.search(
            rf"(?:^|\s)(?:ma(?:\s+thu\s+tuc)?|thu\s+tuc\s+ma)\s+"
            rf"{re.escape(folded_code)}(?:\s|$)",
            query,
        )
    )


def _short_alias_is_actionable(question: str, folded_phrase: str) -> bool:
    """Require an action/request cue for short noun-like procedure aliases."""

    phrase_tokens = folded_phrase.split()
    query_tokens = fold_text(question).split()
    if len(query_tokens) <= max(4, len(phrase_tokens) + 2):
        return True

    action_patterns = (
        "dang ky",
        "thu tuc",
        "thuc hien",
        "can",
        "muon",
        "xin",
        "lam",
        "nop",
        "ho so",
        "bieu mau",
        "to khai",
        "tai",
    )
    for clause in _folded_clauses(question):
        if not _contains_folded_phrase(clause, folded_phrase):
            continue
        if any(_contains_folded_phrase(clause, pattern) for pattern in action_patterns):
            return True
    return False


def _ordered_phrase_in_local_window(
    query_tokens: list[str],
    phrase_tokens: list[str],
    *,
    extra_tokens: int = 3,
) -> bool:
    """Allow inserted words, but never match tokens scattered across a question."""

    if len(phrase_tokens) < 3:
        return False
    for start, token in enumerate(query_tokens):
        if token != phrase_tokens[0]:
            continue
        phrase_index = 1
        stop = min(len(query_tokens), start + len(phrase_tokens) + extra_tokens)
        for candidate in query_tokens[start + 1 : stop]:
            if candidate == phrase_tokens[phrase_index]:
                phrase_index += 1
                if phrase_index == len(phrase_tokens):
                    return True
    return False


def _unordered_full_phrase_in_clause(
    question: str,
    phrase_tokens: list[str],
) -> bool:
    """Match reordered official names only when every token is in one clause."""

    if len(phrase_tokens) < 4:
        return False
    action_tokens = {
        "cap",
        "dang",
        "dieu",
        "giai",
        "cong",
        "ho",
        "tro",
        "xac",
        "thu",
        "thuc",
        "de",
        "ky",
    }
    leading_tokens = set(phrase_tokens[:3])
    if not (leading_tokens & action_tokens):
        return False
    required = set(phrase_tokens)
    return any(
        required.issubset(set(clause.split()))
        and bool(leading_tokens & set(clause.split()))
        for clause in _folded_clauses(question)
    )


def normalize_procedure_id(procedure_id: str | None) -> str:
    value = str(procedure_id or "").strip()
    return PROCEDURE_ID_ALIASES.get(value, value)


def is_synthetic_procedure_id(procedure_id: str | None) -> bool:
    return bool(SYNTHETIC_PROCEDURE_RE.search(str(procedure_id or "")))


def classify_requirement(form_title: str, procedure_id: str) -> str:
    """Classify a raw discovery row without guessing legal metadata."""

    if is_synthetic_procedure_id(procedure_id):
        return "SYNTHETIC_PLACEHOLDER"

    title = fold_text(form_title)
    if procedure_id == "cap_phieu_ly_lich_tu_phap":
        return "OUT_OF_WARD_SCOPE"
    if "mau dien tu" in title or "bieu mau dien tu" in title:
        return "ONLINE_EFORM"

    supporting_prefixes = (
        "giay chung sinh",
        "giay bao tu",
        "giay to chung minh",
        "ban dich",
        "hop dong chuyen nhuong",
        "hop dong tang cho",
        "so do vi tri",
        "van ban cua nguoi lam chung",
        "van ban cu nguoi giam ho",
        "van ban thoa thuan",
        "cam doan ve viec sinh",
    )
    if title.startswith(supporting_prefixes):
        return "SUPPORTING_DOCUMENT_NOT_TEMPLATE"

    result_prefixes = (
        "giay xac nhan tinh trang hon nhan",
        "mau giay xac nhan tinh trang hon nhan",
        "mau xac nhan thong tin cu tru",
        "mau phieu ly lich tu phap",
        "phieu xac nhan hien trang nha dat",
    )
    if title.startswith(result_prefixes):
        return "OFFICIAL_RESULT_NOT_TEMPLATE"

    officer_prefixes = (
        "so chung thuc",
        "bien ban",
        "quyet dinh",
        "mau quyet dinh",
        "phieu ra soat",
        "phieu ghi nhan",
    )
    if title.startswith(officer_prefixes):
        return "OFFICER_INTERNAL_FORM"

    if not title or title in {"mau", "don", "to khai", "phieu"}:
        return "NEEDS_LEGAL_REVIEW"
    return "REAL_PROCEDURE_FORM_REQUIREMENT"


def _safe_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return payload if isinstance(payload, dict) else fallback


def _runtime_catalog_paths() -> tuple[Path, Path, Path]:
    configured_root = Path(
        str(os.getenv("OPEN_NOTEBOOK_DATA_DIR") or ROOT / "notebook_data")
    )
    if not configured_root.is_absolute():
        configured_root = ROOT / configured_root
    configured_forms = Path(
        str(os.getenv("LEGAL_FORM_CATALOG_DIR") or configured_root / "forms")
    )
    if not configured_forms.is_absolute():
        configured_forms = ROOT / configured_forms
    return (
        configured_forms / "canonical_procedures_v1.json",
        configured_forms / "canonical_forms_catalog_v1.json",
        configured_forms / "procedure_form_bindings_v1.json",
    )


def _runtime_requirement_manifest_path() -> Path:
    """Return the configured, checksum-bound requirement-manifest location."""

    configured = Path(
        str(
            os.getenv("LEGAL_FORM_REQUIREMENT_MANIFEST")
            or DEFAULT_REQUIREMENT_MANIFEST_PATH
        )
    )
    return configured if configured.is_absolute() else ROOT / configured


def _runtime_requirement_pairs(path: Path) -> set[tuple[str, str]]:
    """Build the exact procedure/form allow-list from approved identities.

    A missing or malformed manifest yields an empty set deliberately: serving a
    technically approved form without the independently captured requirement
    evidence is a legal-safety failure, not a reason to fall back to all
    catalog records.
    """

    payload = _safe_json(path, {})
    identities = {
        str(item.get("identity_id") or "").strip(): item
        for item in payload.get("form_identities") or []
        if isinstance(item, dict) and str(item.get("identity_id") or "").strip()
    }
    pairs: set[tuple[str, str]] = set()
    for procedure in payload.get("procedures") or []:
        if not isinstance(procedure, dict):
            continue
        procedure_id = normalize_procedure_id(procedure.get("procedure_id"))
        if not procedure_id:
            continue
        for identity_id in procedure.get("required_form_identity_ids") or []:
            identity = identities.get(str(identity_id or "").strip())
            if not identity or identity.get("release_status") != "APPROVED_RUNTIME":
                continue
            for form_id in identity.get("approved_catalog_form_ids") or []:
                normalized_form_id = str(form_id or "").strip()
                if normalized_form_id:
                    pairs.add((procedure_id, normalized_form_id))
    return pairs


def _verified_legacy_attestation_pairs(
    *,
    forms: Iterable[dict[str, Any]],
    bindings: Iterable[dict[str, Any]],
    attestations_payload: Mapping[str, Any],
) -> set[tuple[str, str]]:
    """Recover release pairs created by the checksum-bound legacy workflow.

    The first human-review workflow predated the explicit
    ``runtime_eligible`` field and the Feature 006 requirement manifest.  Its
    approved records still carry an exact candidate, form, procedure, checksum
    and reviewer attestation.  Treating those records as permanently pending
    loses a real human decision; treating every old ``approved`` flag as
    runtime-safe would be unsafe.  This compatibility bridge therefore accepts
    only a complete, internally consistent attestation chain and never upgrades
    an explicitly false runtime flag.
    """

    attestations = {
        str(item.get("attestation_id") or "").strip(): item
        for item in attestations_payload.get("attestations") or []
        if isinstance(item, dict)
        and str(item.get("attestation_id") or "").strip()
        and str(item.get("decision") or "").casefold() == "approved"
    }
    approved_bindings = {
        (
            normalize_procedure_id(item.get("procedure_id")),
            str(item.get("form_id") or "").strip(),
        ): item
        for item in bindings
        if isinstance(item, dict) and _binding_is_approved(item)
    }
    pairs: set[tuple[str, str]] = set()
    for form in forms:
        if not isinstance(form, dict) or form.get("runtime_eligible") is not None:
            continue
        if (
            form.get("review_status") != "approved"
            or form.get("legal_review_status") != "approved"
            or form.get("approved") is not True
            or form.get("is_quarantined") is True
        ):
            continue
        form_id = str(form.get("form_id") or "").strip()
        checksum = str(form.get("sha256") or "").strip().casefold()
        provenance = form.get("provenance")
        if not form_id or not checksum or not isinstance(provenance, dict):
            continue
        attestation_id = str(provenance.get("attestation_id") or "").strip()
        candidate_id = str(provenance.get("candidate_id") or "").strip()
        reviewer_id = str(provenance.get("reviewer_id") or "").strip()
        reviewed_at = str(provenance.get("reviewed_at") or "").strip()
        attestation = attestations.get(attestation_id)
        if (
            not attestation
            or not candidate_id
            or not reviewer_id
            or not reviewed_at
            or str(attestation.get("reviewer_id") or "").strip() != reviewer_id
            or str(attestation.get("reviewed_at") or "").strip() != reviewed_at
        ):
            continue

        attested_items = [
            item
            for item in attestation.get("items") or []
            if isinstance(item, dict)
            and str(item.get("decision") or "").casefold() == "approved"
            and str(item.get("canonical_form_id") or "").strip() == form_id
            and str(item.get("candidate_id") or "").strip() == candidate_id
            and str(item.get("sha256") or "").strip().casefold() == checksum
        ]
        for procedure_id in form.get("procedure_ids") or []:
            pid = normalize_procedure_id(procedure_id)
            binding = approved_bindings.get((pid, form_id))
            if (
                not pid
                or binding is None
                or str(binding.get("attestation_id") or "").strip()
                != attestation_id
                or str(binding.get("reviewer_id") or "").strip() != reviewer_id
                or str(binding.get("reviewed_at") or "").strip() != reviewed_at
            ):
                continue
            if any(
                normalize_procedure_id(item.get("procedure_id")) == pid
                for item in attested_items
            ):
                pairs.add((pid, form_id))
    return pairs


def _binding_is_approved(item: dict[str, Any]) -> bool:
    return (
        item.get("binding_status") == "approved"
        and item.get("review_status", "approved") == "approved"
        and item.get("approved", True) is True
    )


def _form_is_runtime_approved(item: dict[str, Any]) -> bool:
    return (
        item.get("review_status") == "approved"
        and item.get("approved") is True
        and item.get("runtime_eligible") is True
        and item.get("is_quarantined") is not True
    )


def _procedure_is_runtime_approved(item: dict[str, Any]) -> bool:
    """Recognize an approved procedure record, including legacy curated rows."""
    return (
        item.get("review_status", "approved") == "approved"
        and item.get("approved", True) is True
    )


def _apply_reviewed_current_form_overlays(
    forms: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace an approved identity with its reviewed superseding official file.

    Keep approved form identities and bindings stable while projecting a
    reviewed current official package.  This avoids a data migration and makes
    the serving correction independently reversible.
    """

    output: list[dict[str, Any]] = []
    for raw in forms:
        form = dict(raw)
        if str(form.get("form_id") or "") == "form-82e0f920688d5a6a5da0":
            previous = dict(form.get("provenance") or {})
            form.update(
                {
                    "official_source_page": (
                        "https://vanban.chinhphu.vn/"
                        "?docid=218509&orggroupid=2&pageid=27160"
                    ),
                    "official_download_url": (
                        "https://datafiles.chinhphu.vn/cpp/files/"
                        "vbpq/2026/6/pl217.pdf"
                    ),
                    # The approved local extraction belongs to the superseded
                    # package.  Force the normal verified-official-URL gate.
                    "local_path": None,
                    "sha256": None,
                    "legal_basis": ["217/2026/NĐ-CP"],
                    "effective_from": "2026-07-01",
                    "effective_to": None,
                    "url_status": "verified",
                    "source_classification": "official_file",
                    "runtime_release_source": "reviewed_current_legal_overlay",
                    "provenance": {
                        "kind": "reviewed_current_legal_overlay",
                        "source_page_url": (
                            "https://vanban.chinhphu.vn/"
                            "?docid=218509&orggroupid=2&pageid=27160"
                        ),
                        "source_download_url": (
                            "https://datafiles.chinhphu.vn/cpp/files/"
                            "vbpq/2026/6/pl217.pdf"
                        ),
                        "legal_as_of": "2026-08-08",
                        "effective_from": "2026-07-01",
                        "supersedes_legal_basis": "175/2024/NĐ-CP",
                        "previous_approved_provenance": previous,
                    },
                }
            )
        elif str(form.get("form_id") or "") == "form-2fd149a36c5485525ab7":
            previous = dict(form.get("provenance") or {})
            source_page = (
                "https://vanban.chinhphu.vn/"
                "?docid=201363&pageid=27160"
            )
            source_download = (
                "https://datafiles.chinhphu.vn/cpp/files/"
                "vbpq/2020/10/124.signed.pdf"
            )
            form.update(
                {
                    "name": "Đơn khiếu nại (Mẫu số 01)",
                    "display_name": "Đơn khiếu nại (Mẫu số 01)",
                    "form_title": "Đơn khiếu nại (Mẫu số 01)",
                    "form_code": "01",
                    "official_source_page": source_page,
                    "official_download_url": source_download,
                    "local_path": None,
                    "sha256": None,
                    "legal_basis": ["02/2011/QH13", "124/2020/NĐ-CP"],
                    "effective_from": "2020-12-10",
                    "effective_to": None,
                    "url_status": "verified",
                    "source_classification": "official_file",
                    "runtime_release_source": "reviewed_current_legal_overlay",
                    "provenance": {
                        "kind": "reviewed_current_legal_overlay",
                        "source_page_url": source_page,
                        "source_download_url": source_download,
                        "legal_as_of": "2026-08-09",
                        "effective_from": "2020-12-10",
                        "previous_approved_provenance": previous,
                    },
                }
            )
        output.append(form)
    return output


def _official_runtime_procedure_bridge(
    *,
    forms: Iterable[dict[str, Any]],
    bindings: Iterable[dict[str, Any]],
    three_tier_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Normalize official procedures referenced by approved runtime bindings."""

    approved_bindings = [
        item for item in bindings
        if isinstance(item, dict) and _binding_is_approved(item)
    ]
    approved_pairs = {
        (
            normalize_procedure_id(item.get("procedure_id")),
            str(item.get("form_id") or ""),
        )
        for item in approved_bindings
    }
    runtime_procedure_ids = {
        normalize_procedure_id(procedure_id)
        for form in forms
        if isinstance(form, dict) and _form_is_runtime_approved(form)
        for procedure_id in (form.get("procedure_ids") or [])
        if (
            normalize_procedure_id(procedure_id),
            str(form.get("form_id") or ""),
        ) in approved_pairs
    }
    attestations_by_procedure: dict[str, set[str]] = {}
    for binding in approved_bindings:
        pid = normalize_procedure_id(binding.get("procedure_id"))
        if pid not in runtime_procedure_ids:
            continue
        attestation = str(binding.get("attestation_id") or "").strip()
        if attestation:
            attestations_by_procedure.setdefault(pid, set()).add(attestation)

    legal_as_of = str(three_tier_payload.get("legal_as_of") or "").strip() or None
    bridged: list[dict[str, Any]] = []
    for row in three_tier_payload.get("procedures") or []:
        if not isinstance(row, dict):
            continue
        code = normalize_procedure_id(row.get("procedure_code"))
        name = str(row.get("procedure_name") or "").strip()
        source_url = str(row.get("official_source_page") or "").strip()
        domain = str(row.get("domain") or "").strip()
        if (
            code not in runtime_procedure_ids
            or not name
            or not domain
            or not _is_official_url(source_url)
        ):
            continue
        aliases: list[str] = []
        for key in ("aliases", "keywords", "case_names"):
            raw = row.get(key)
            values = raw if isinstance(raw, list) else [raw] if raw else []
            aliases.extend(
                str(value).strip()
                for value in values
                if str(value or "").strip()
            )
        bridged.append({
            "procedure_id": code,
            "official_procedure_code": code,
            "name": name,
            "aliases": list(dict.fromkeys(aliases)),
            "domain": domain,
            "authority_level": row.get("executing_level"),
            "receiving_authority": None,
            "decision_authority": None,
            "jurisdiction": "Hai Phong",
            "legal_as_of": legal_as_of,
            "official_procedure_url": source_url,
            "source_status": "verified",
            "review_status": "approved",
            "approved": True,
            "runtime_eligible": True,
            "provenance": {
                "kind": "official_three_tier_runtime_bridge",
                "source_file": THREE_TIER_PROCEDURES_PATH.name,
                "source_uuid": row.get("procedure_id"),
                "source_tier": row.get("source_tier"),
                "portal_state": row.get("portal_state"),
                "publisher": row.get("publisher"),
                "binding_attestation_ids": sorted(
                    attestations_by_procedure.get(code, set())
                ),
            },
        })
    return bridged


def _official_runtime_bridge_pairs(
    *,
    forms: Iterable[dict[str, Any]],
    bindings: Iterable[dict[str, Any]],
    bridged_procedures: Iterable[dict[str, Any]],
) -> set[tuple[str, str]]:
    """Release reviewed three-tier bindings already used by the runtime bridge.

    The official procedure bridge is created only from approved, attested
    bindings. Keep its form pair available to the public resolver even when an
    older compatibility manifest still names only the legacy procedure slug.
    This does not approve a candidate or change an active release pointer: the
    binding, form and matching attestation must already be approved.
    """

    bridge_attestations: dict[str, set[str]] = {}
    for procedure in bridged_procedures:
        procedure_id = normalize_procedure_id(procedure.get("procedure_id"))
        provenance = procedure.get("provenance")
        if not procedure_id or not isinstance(provenance, dict):
            continue
        attestations = {
            str(value).strip()
            for value in provenance.get("binding_attestation_ids") or []
            if str(value or "").strip()
        }
        if attestations:
            bridge_attestations[procedure_id] = attestations

    forms_by_id = {
        str(form.get("form_id") or "").strip(): form
        for form in forms
        if isinstance(form, dict) and str(form.get("form_id") or "").strip()
    }
    pairs: set[tuple[str, str]] = set()
    for binding in bindings:
        if not isinstance(binding, dict) or not _binding_is_approved(binding):
            continue
        procedure_id = normalize_procedure_id(binding.get("procedure_id"))
        form_id = str(binding.get("form_id") or "").strip()
        attestation_id = str(binding.get("attestation_id") or "").strip()
        form = forms_by_id.get(form_id)
        if (
            not procedure_id
            or not form_id
            or not attestation_id
            or attestation_id not in bridge_attestations.get(procedure_id, set())
            or not form
            or not _form_is_runtime_approved(form)
        ):
            continue
        form_attestation = str(
            (form.get("provenance") or {}).get("attestation_id")
            if isinstance(form.get("provenance"), dict)
            else ""
        ).strip()
        form_procedure_ids = {
            normalize_procedure_id(value)
            for value in form.get("procedure_ids") or []
            if str(value or "").strip()
        }
        if form_attestation == attestation_id and procedure_id in form_procedure_ids:
            pairs.add((procedure_id, form_id))
    return pairs


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is_official_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.casefold().rstrip(".")
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in OFFICIAL_HOST_SUFFIXES)


def _file_integrity(path: Path, expected_format: str | None) -> tuple[bool, str]:
    if not path.is_file():
        return False, "FORM_FILE_INVALID"
    try:
        size = path.stat().st_size
        head = path.read_bytes()[:512]
    except OSError:
        return False, "FORM_FILE_INVALID"
    if size < 256:
        return False, "FORM_FILE_INVALID"
    lower = head.lower()
    if b"<html" in lower or b"<!doctype html" in lower:
        return False, "FORM_FILE_INVALID"
    fmt = str(expected_format or path.suffix.lstrip(".")).casefold()
    if fmt == "pdf" and not head.startswith(b"%PDF"):
        return False, "FORM_FILE_INVALID"
    if fmt == "doc" and not head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return False, "FORM_FILE_INVALID"
    if fmt in {"docx", "xlsx"}:
        try:
            if not zipfile.is_zipfile(path):
                return False, "FORM_FILE_INVALID"
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            required = "word/" if fmt == "docx" else "xl/"
            if "[Content_Types].xml" not in names or not any(
                name.startswith(required) for name in names
            ):
                return False, "FORM_FILE_INVALID"
        except (OSError, zipfile.BadZipFile):
            return False, "FORM_FILE_INVALID"
    return True, "ELIGIBLE"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class GateResult:
    eligible: bool
    reason_code: str


class FormCatalog:
    """Read-only, exact-first resolver for canonical procedure/form catalogs."""

    def __init__(
        self,
        *,
        procedures: Iterable[dict[str, Any]],
        forms: Iterable[dict[str, Any]],
        bindings: Iterable[dict[str, Any]],
        runtime_requirement_pairs: Iterable[tuple[str, str]] | None = None,
        project_root: Path = ROOT,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.procedures = [
            dict(item)
            for item in procedures
            if isinstance(item, dict)
            and not is_synthetic_procedure_id(item.get("procedure_id"))
        ]
        self.forms = [dict(item) for item in forms if isinstance(item, dict)]
        self.bindings = [dict(item) for item in bindings if isinstance(item, dict)]
        self._procedures_by_id = {
            normalize_procedure_id(item.get("procedure_id")): item
            for item in self.procedures
            if item.get("procedure_id")
        }
        self._forms_by_id = {
            str(item.get("form_id")): item
            for item in self.forms
            if item.get("form_id")
        }
        self._form_procedure_ids_by_id: dict[str, set[str]] = {}
        self._forms_by_procedure: dict[str, list[dict[str, Any]]] = {}
        for form_id, form in self._forms_by_id.items():
            procedure_ids = {
                normalize_procedure_id(value)
                for value in form.get("procedure_ids") or []
                if str(value).strip()
            }
            self._form_procedure_ids_by_id[form_id] = procedure_ids
            for procedure_id in procedure_ids:
                self._forms_by_procedure.setdefault(procedure_id, []).append(form)
        self._binding_status_by_pair: dict[tuple[str, str], str | None] = {}
        self._bound_form_ids_by_procedure: dict[str, set[str]] = {}
        for binding in self.bindings:
            procedure_id = normalize_procedure_id(binding.get("procedure_id"))
            form_id = str(binding.get("form_id") or "").strip()
            if not procedure_id or not form_id:
                continue
            self._binding_status_by_pair[(procedure_id, form_id)] = (
                "approved"
                if _binding_is_approved(binding)
                else binding.get("binding_status")
            )
            self._bound_form_ids_by_procedure.setdefault(procedure_id, set()).add(
                form_id
            )
        self._approved_binding_pairs = {
            pair
            for pair, status in self._binding_status_by_pair.items()
            if status == "approved"
        }
        self._runtime_requirement_pairs = (
            {
                (normalize_procedure_id(procedure_id), str(form_id).strip())
                for procedure_id, form_id in runtime_requirement_pairs
                if normalize_procedure_id(procedure_id) and str(form_id).strip()
            }
            if runtime_requirement_pairs is not None
            else None
        )
        reviewed_ids = {
            pid
            for pid, item in self._procedures_by_id.items()
            if _procedure_is_runtime_approved(item)
        }
        released_form_ids = {
            pid
            for pid, form_id in self._approved_binding_pairs
            if pid in self._procedures_by_id
            and (self._runtime_requirement_pairs is None
                 or (pid, form_id) in self._runtime_requirement_pairs)
            and _form_is_runtime_approved(self._forms_by_id.get(form_id, {}))
        }
        # Some historical procedure seed records remain pending even though a
        # separately reviewed, release-bound form/binding is already public.
        # Preserve those exact identities without exposing other candidates.
        self._serving_procedure_ids = reviewed_ids | released_form_ids

    @classmethod
    def load_default(cls) -> "FormCatalog":
        procedures_path, forms_path, bindings_path = _runtime_catalog_paths()
        procedures = _safe_json(procedures_path, {"procedures": []}).get(
            "procedures", []
        )
        forms = _safe_json(forms_path, {"forms": []}).get("forms", [])
        bindings = _safe_json(bindings_path, {"bindings": []}).get(
            "bindings", []
        )
        # Admin-managed procedures live in the operational ``ward_procedure``
        # table.  CRUD writes an atomic projection so the deterministic chat
        # resolver sees the same approved procedure/form data without making a
        # database round-trip for every question.  Keep the reviewed static
        # catalog authoritative when an ID collides.
        try:
            from api.procedure_runtime_catalog import load_managed_runtime_catalog

            managed = load_managed_runtime_catalog()
            static_ids = {
                normalize_procedure_id(item.get("procedure_id"))
                for item in procedures
                if isinstance(item, dict) and item.get("procedure_id")
            }
            procedures = list(procedures) + [
                item
                for item in managed.get("procedures", [])
                if isinstance(item, dict)
                and normalize_procedure_id(item.get("procedure_id")) not in static_ids
            ]
            forms = list(forms) + [
                item
                for item in managed.get("forms", [])
                if isinstance(item, dict)
                and str(item.get("form_id") or "").strip()
            ]
            bindings = list(bindings) + [
                item
                for item in managed.get("bindings", [])
                if isinstance(item, dict)
            ]
        except Exception:
            # A stale/missing projection must never make the legal catalog
            # unavailable; the next admin write will repair it.
            pass
        legacy_pairs = _verified_legacy_attestation_pairs(
            forms=forms,
            bindings=bindings,
            attestations_payload=_safe_json(
                forms_path.parent / "legal_review_attestations_v1.json",
                {"attestations": []},
            ),
        )
        legacy_form_ids = {form_id for _pid, form_id in legacy_pairs}
        forms = [
            {
                **item,
                "runtime_eligible": True,
                "runtime_release_source": "verified_legacy_attestation",
            }
            if str(item.get("form_id") or "") in legacy_form_ids
            else item
            for item in forms
        ]
        forms = _apply_reviewed_current_form_overlays(forms)
        three_tier_payload = _safe_json(
            procedures_path.parent / THREE_TIER_PROCEDURES_PATH.name,
            {"procedures": []},
        )
        known_ids = {
            normalize_procedure_id(item.get("procedure_id"))
            for item in procedures
            if isinstance(item, dict) and item.get("procedure_id")
        }
        bridged_procedures = _official_runtime_procedure_bridge(
            forms=forms,
            bindings=bindings,
            three_tier_payload=three_tier_payload,
        )
        procedures = list(procedures) + [
            item
            for item in bridged_procedures
            if normalize_procedure_id(item.get("procedure_id")) not in known_ids
        ]
        requirement_pairs = _runtime_requirement_pairs(
            _runtime_requirement_manifest_path()
        )
        requirement_pairs.update(legacy_pairs)
        requirement_pairs.update(
            _official_runtime_bridge_pairs(
                forms=forms,
                bindings=bindings,
                bridged_procedures=bridged_procedures,
            )
        )
        # Managed procedure rows are explicitly approved by the Admin CRUD
        # gate.  Their projection is still subject to the normal form gate,
        # but must be included in the runtime requirement allow-list so a
        # newly linked approved form is visible without a release-file edit.
        for binding in bindings:
            if not isinstance(binding, dict) or not _binding_is_approved(binding):
                continue
            pid = normalize_procedure_id(binding.get("procedure_id"))
            form_id = str(binding.get("form_id") or "").strip()
            if pid and form_id:
                requirement_pairs.add((pid, form_id))
        return cls(
            procedures=procedures,
            forms=forms,
            bindings=bindings,
            runtime_requirement_pairs=requirement_pairs,
            project_root=ROOT,
        )

    def get_procedure(self, procedure_id: str) -> dict[str, Any] | None:
        item = self._procedures_by_id.get(normalize_procedure_id(procedure_id))
        return dict(item) if item else None

    def is_serving_procedure(self, procedure_id: str) -> bool:
        return normalize_procedure_id(procedure_id) in self._serving_procedure_ids

    def resolve_procedures(
        self,
        question: str,
        *,
        limit: int = 6,
        candidate_procedure_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        query = fold_text(question)
        if not query:
            return {"matches": [], "ambiguous": False}

        candidate_ids = (
            {
                normalize_procedure_id(value)
                for value in candidate_procedure_ids
                if normalize_procedure_id(value)
            }
            if candidate_procedure_ids is not None
            else None
        )

        scored: dict[str, tuple[int, list[str], str]] = {}
        for form in self.forms:
            form_id = str(form.get("form_id") or "")
            if not _form_is_runtime_approved(form):
                continue
            code = fold_text(form.get("form_code"))
            for procedure_id in form.get("procedure_ids") or []:
                pid = normalize_procedure_id(procedure_id)
                if (
                    pid not in self._procedures_by_id
                    or pid not in self._serving_procedure_ids
                    or (candidate_ids is not None and pid not in candidate_ids)
                    or (pid, form_id) not in self._approved_binding_pairs
                ):
                    continue

                for phrase in _form_intent_phrases(form):
                    if _contains_folded_phrase(query, phrase):
                        candidate = (800 + len(phrase), [phrase], "form_name")
                        previous = scored.get(pid)
                        if previous is None or candidate[0] > previous[0]:
                            scored[pid] = candidate

                if code and _form_code_is_actionable(question, code):
                    # A numeric appendix code (especially ``01``) is highly
                    # non-unique.  It may help when no procedure wording is
                    # present, but must never outrank an exact procedure or
                    # descriptive form-name match.
                    code_score = (
                        400 + len(code)
                        if all(token.isdigit() for token in code.split())
                        else 600 + len(code)
                    )
                    candidate = (
                        code_score,
                        [str(form.get("form_code"))],
                        "form_code",
                    )
                    previous = scored.get(pid)
                    if previous is None or candidate[0] > previous[0]:
                        scored[pid] = candidate

        for item in self.procedures:
            pid = normalize_procedure_id(item.get("procedure_id"))
            if candidate_ids is not None and pid not in candidate_ids:
                continue
            official_code = fold_text(item.get("official_procedure_code"))
            phrases = {
                str(item.get("official_procedure_code") or "").strip(),
                str(item.get("name") or "").strip(),
                str(item.get("procedure_id") or "").replace("_", " "),
                *(str(alias).strip() for alias in item.get("aliases") or []),
            }
            phrase_matches: list[str] = []
            best = 0
            best_match_type = "procedure_alias"
            query_tokens = query.split()
            for phrase in phrases:
                folded = fold_text(phrase)
                if not folded:
                    continue
                if re.search(rf"(?:^|\s){re.escape(folded)}(?:\s|$)", query):
                    exact_official_code = bool(
                        official_code
                        and folded == official_code
                        and _procedure_code_is_actionable(
                            question,
                            official_code,
                        )
                    )
                    if (
                        len(folded.split()) <= 2
                        and not exact_official_code
                        and not _short_alias_is_actionable(question, folded)
                    ):
                        continue
                    phrase_matches.append(phrase)
                    candidate_score = (
                        2000 + len(folded)
                        if exact_official_code
                        else 1000 + len(folded)
                    )
                    if candidate_score > best:
                        best = candidate_score
                        best_match_type = "procedure_alias"
                    continue
                phrase_tokens = folded.split()
                # Deterministic fallback for natural word insertion. Every
                # procedure token must remain ordered and locally adjacent.
                if _ordered_phrase_in_local_window(query_tokens, phrase_tokens):
                    phrase_matches.append(phrase)
                    candidate_score = 700 + len(phrase_tokens) * 5
                    if candidate_score > best:
                        best = candidate_score
                        best_match_type = "procedure_ordered_tokens"
                    continue
                if _unordered_full_phrase_in_clause(question, phrase_tokens):
                    phrase_matches.append(phrase)
                    candidate_score = 750 + len(set(phrase_tokens))
                    if candidate_score > best:
                        best = candidate_score
                        best_match_type = "procedure_name_tokens"
            if best:
                previous = scored.get(pid)
                candidate = (
                    best,
                    sorted(set(phrase_matches)),
                    best_match_type,
                )
                if previous is None or candidate[0] > previous[0]:
                    scored[pid] = candidate

        matches: list[dict[str, Any]] = [
            {
                "procedure_id": pid,
                "score": score,
                "matched_phrases": phrases,
                "match_type": match_type,
            }
            for pid, (score, phrases, match_type) in scored.items()
        ]
        # Prefer a reviewed national procedure identity when the question
        # contains an exact remediation alias.  The legacy catalog often has
        # a broad candidate slug (for example ``tro_cap_xa_hoi``) that is not
        # safe to expose as the procedure identity for a named benefit.
        # Mapping is conditional on the official record already being
        # approved; it does not activate a staging or form release.
        remediation_aliases = (
            ("dang ky lai khai sinh", "dang_ky_khai_sinh", "1.004884"),
            ("tro cap huu tri xa hoi", "tro_cap_xa_hoi", "1.014027"),
            ("dang ky tam tru", "dang_ky_tam_tru", "1.004194"),
        )
        for alias, legacy_id, official_id in remediation_aliases:
            if alias not in query or official_id not in self._procedures_by_id:
                continue
            official = self._procedures_by_id[official_id]
            if not (
                str(official.get("official_procedure_code") or "").strip()
                == official_id
                and str(official.get("source_status") or "").casefold()
                == "verified"
                and str(official.get("review_status") or "").casefold()
                == "approved"
                and official.get("approved") is True
            ):
                continue
            for match in matches:
                if match.get("procedure_id") == legacy_id:
                    # A comparison can explicitly name both first-time and
                    # repeat registration. Do not rewrite its first identity
                    # into the second and erase one requested procedure.
                    remainder = query.replace(alias, " ")
                    if alias == "dang ky lai khai sinh" and "dang ky khai sinh" in remainder:
                        matches.append({**match, "procedure_id": official_id})
                    else:
                        match["procedure_id"] = official_id
        # A pending but more specific procedure must suppress a broader
        # released match (for example reissuing versus first issuing a permit).
        # Equal-score reviewed identities win instead of the pending seed.
        pending_top_score = max(
            (
                int(match["score"])
                for match in matches
                if match["procedure_id"] not in self._serving_procedure_ids
            ),
            default=0,
        )
        matches = [
            match
            for match in matches
            if match["procedure_id"] in self._serving_procedure_ids
        ]
        # The curated catalog and the official three-tier bridge can contain
        # the same procedure under a legacy slug and a current national code.
        # Keep one deterministic identity. An exact national code or locked
        # remediation alias selects the official identity; a generic name-only
        # query keeps the already-released compatibility identity.
        preferred_by_name: dict[tuple[str, str], dict[str, Any]] = {}
        for item in matches:
            procedure = self._procedures_by_id.get(item["procedure_id"], {})
            normalized_name = fold_text(procedure.get("name"))
            if normalized_name.startswith("thu tuc "):
                normalized_name = normalized_name[len("thu tuc ") :]
            key = (
                normalized_name,
                str(procedure.get("domain") or ""),
            )
            if not key[0]:
                key = (item["procedure_id"], key[1])
            current = preferred_by_name.get(key)
            if current is None:
                preferred_by_name[key] = item
                continue
            current_procedure = self._procedures_by_id.get(
                current["procedure_id"], {}
            )

            def has_released_form(procedure_id: str) -> bool:
                return any(
                    pair in self._approved_binding_pairs
                    and (
                        self._runtime_requirement_pairs is None
                        or pair in self._runtime_requirement_pairs
                    )
                    and _form_is_runtime_approved(
                        self._forms_by_id.get(pair[1], {})
                    )
                    for pair in (
                        (procedure_id, form_id)
                        for form_id in self._bound_form_ids_by_procedure.get(
                            procedure_id, set()
                        )
                    )
                )

            def procedure_preference(
                candidate: Mapping[str, Any],
                procedure_row: Mapping[str, Any],
            ) -> tuple[bool, bool, bool, int, bool, bool]:
                official_code = str(
                    procedure_row.get("official_procedure_code") or ""
                ).strip()
                code_requested = bool(
                    official_code
                    and _contains_folded_phrase(query, fold_text(official_code))
                )
                is_official_bridge = bool(official_code)
                return (
                    code_requested,
                    has_released_form(str(candidate["procedure_id"])),
                    # Preserve the already-released legacy identity for a
                    # generic name-only query. Exact national codes and the
                    # locked remediation aliases still select the official
                    # identity deterministically.
                    not is_official_bridge,
                    int(candidate["score"]),
                    bool(procedure_row.get("official_procedure_url")),
                    str(candidate["procedure_id"]).replace(".", "").isdigit(),
                )

            item_preference = procedure_preference(item, procedure)
            current_preference = procedure_preference(current, current_procedure)
            if item_preference > current_preference:
                preferred_by_name[key] = item
        matches = list(preferred_by_name.values())
        token_sets = {
            item["procedure_id"]: max(
                (
                    set(fold_text(phrase).split())
                    for phrase in item["matched_phrases"]
                    if fold_text(phrase)
                ),
                key=len,
                default=set(),
            )
            for item in matches
            if item["match_type"] == "procedure_name_tokens"
        }
        matches = [
            item
            for item in matches
            if item["match_type"] != "procedure_name_tokens"
            or not any(
                tokens
                and tokens < other_tokens
                for other_pid, other_tokens in token_sets.items()
                if other_pid != item["procedure_id"]
                for tokens in [token_sets.get(item["procedure_id"], set())]
            )
        ]
        matches.sort(key=lambda item: (-item["score"], item["procedure_id"]))
        if pending_top_score > (matches[0]["score"] if matches else 0):
            return {"matches": [], "ambiguous": False}
        capped = matches[: max(1, min(limit, 6))]
        ambiguous = (
            len(capped) > 1
            and capped[0]["score"] == capped[1]["score"]
            and capped[0]["procedure_id"] != capped[1]["procedure_id"]
        )
        return {"matches": capped, "ambiguous": ambiguous}

    def gate_form(
        self,
        form: dict[str, Any],
        *,
        procedure_id: str,
        role: str,
        as_of: date,
    ) -> GateResult:
        if form.get("catalog_disposition") == "excluded_no_official_form":
            return GateResult(False, "NO_OFFICIAL_STATE_FORM")
        procedure_ids = {
            normalize_procedure_id(value)
            for value in (form.get("procedure_ids") or [])
            if str(value).strip()
        }
        if not procedure_ids:
            return GateResult(False, "FORM_PROCEDURE_ID_MISSING")
        if form.get("review_status") != "approved":
            return GateResult(False, "FORM_NOT_APPROVED")
        if form.get("approved") is not True:
            return GateResult(False, "FORM_APPROVAL_FLAG_MISSING")
        if (
            form.get("runtime_eligible") is not True
            or form.get("is_quarantined") is True
        ):
            return GateResult(False, "FORM_NOT_RUNTIME_ELIGIBLE")
        if form.get("source_classification") not in {
            "official_file",
            "official_eform",
        }:
            return GateResult(False, "SOURCE_NOT_DOWNLOADABLE")
        if normalize_procedure_id(procedure_id) not in procedure_ids:
            return GateResult(False, "WRONG_PROCEDURE")
        procedure = self._procedures_by_id.get(
            normalize_procedure_id(procedure_id)
        )
        if procedure is None:
            return GateResult(False, "PROCEDURE_RECORD_MISSING")
        if normalize_procedure_id(procedure_id) not in self._serving_procedure_ids:
            return GateResult(False, "PROCEDURE_NOT_APPROVED")
        if not form.get("legal_basis"):
            return GateResult(False, "LEGAL_BASIS_MISSING")
        provenance = form.get("provenance")
        if (
            (isinstance(provenance, dict) and not provenance)
            or (not isinstance(provenance, dict) and not str(provenance or "").strip())
        ):
            return GateResult(False, "PROVENANCE_MISSING")
        if procedure and form.get("domain") and procedure.get("domain"):
            if form.get("domain") != procedure.get("domain"):
                return GateResult(False, "WRONG_DOMAIN")
        audience = str(form.get("audience") or "")
        if role == "citizen" and audience not in {"citizen", "both"}:
            return GateResult(False, "ROLE_NOT_ALLOWED")
        if role == "officer" and audience not in {"citizen", "officer", "both"}:
            return GateResult(False, "ROLE_NOT_ALLOWED")
        if role not in {"citizen", "officer", "admin"}:
            return GateResult(False, "ROLE_NOT_ALLOWED")
        start = _parse_date(form.get("effective_from"))
        end = _parse_date(form.get("effective_to"))
        if start is None:
            return GateResult(False, "EFFECTIVITY_UNKNOWN")
        if start > as_of:
            return GateResult(False, "FORM_NOT_YET_EFFECTIVE")
        if end is not None and end < as_of:
            return GateResult(False, "FORM_EXPIRED")
        if form.get("supersedes_form_id"):
            return GateResult(False, "FORM_SUPERSEDED")
        if not _is_official_url(form.get("official_source_page")):
            return GateResult(False, "SOURCE_NOT_OFFICIAL")

        source_class = form.get("source_classification")
        if source_class == "official_eform":
            if (
                str(form.get("file_format") or "") != "online"
                or not _is_official_url(form.get("official_download_url"))
            ):
                return GateResult(False, "EFORM_URL_INVALID")
            return GateResult(True, "ELIGIBLE")

        local_path = str(form.get("local_path") or "").strip()
        download_url = str(form.get("official_download_url") or "").strip()
        if local_path:
            candidate = (self.project_root / local_path).resolve()
            try:
                candidate.relative_to(self.project_root)
            except ValueError:
                return GateResult(False, "FORM_FILE_INVALID")
            valid, reason = _file_integrity(candidate, form.get("file_format"))
            if not valid:
                return GateResult(False, reason)
            expected_hash = str(form.get("sha256") or "").casefold()
            if not expected_hash or _sha256(candidate) != expected_hash:
                return GateResult(False, "FORM_CHECKSUM_INVALID")
            return GateResult(True, "ELIGIBLE")
        if download_url and _is_official_url(download_url):
            url_status = str(
                form.get("url_status")
                or form.get("download_status")
                or ""
            ).casefold()
            if url_status not in {"verified", "reachable", "http_200"}:
                return GateResult(False, "FORM_URL_UNVERIFIED")
            return GateResult(True, "ELIGIBLE")
        return GateResult(False, "FORM_FILE_INVALID")

    def resolve_forms(
        self,
        question: str,
        *,
        role: str,
        as_of: date | None = None,
        procedure_ids: Iterable[str] | None = None,
        limit: int = 3,
    ) -> dict[str, Any]:
        active_date = as_of or date.today()
        explicit_ids = (
            [
                normalize_procedure_id(value)
                for value in procedure_ids
                if normalize_procedure_id(value)
            ]
            if procedure_ids is not None
            else None
        )
        detected = self.resolve_procedures(
            question,
            candidate_procedure_ids=explicit_ids,
        )
        detected_matches = list(detected["matches"])
        # The default resolver may return lower-ranked overlapping procedures
        # for diagnostics. Forms must bind only to the deterministic top
        # procedure; callers that have planned multiple issues must pass their
        # reviewed ``procedure_ids`` explicitly.
        if procedure_ids is None:
            # A shared code such as ``Mau so 01`` can point to dozens of
            # unrelated procedures.  Fail closed until the question contains
            # enough procedure wording to produce a deterministic winner.
            detected_matches = [] if detected["ambiguous"] else detected_matches[:1]
        ids = explicit_ids or [
            normalize_procedure_id(item["procedure_id"])
            for item in detected_matches
        ]
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for pid in ids:
            # Include bindings that no longer agree with the form record as
            # explicit, fail-closed rejections.  This preserves the previous
            # audit behaviour without rescanning every catalog form per query.
            candidates_by_id = {
                str(form.get("form_id") or ""): form
                for form in self._forms_by_procedure.get(pid, [])
            }
            candidates_by_id.update(
                {
                    form_id: self._forms_by_id[form_id]
                    for form_id in self._bound_form_ids_by_procedure.get(pid, set())
                    if form_id in self._forms_by_id
                }
            )
            for form_id, form in candidates_by_id.items():
                if self._binding_status_by_pair.get((pid, form_id)) != "approved":
                    rejected.append(
                        {
                            "form_id": form_id,
                            "procedure_id": pid,
                            "reason_code": "BINDING_NOT_APPROVED",
                        }
                    )
                    continue
                if (
                    self._runtime_requirement_pairs is not None
                    and (pid, form_id) not in self._runtime_requirement_pairs
                ):
                    rejected.append(
                        {
                            "form_id": form_id,
                            "procedure_id": pid,
                            "reason_code": "FORM_NOT_RELEASED_FOR_PROCEDURE",
                        }
                    )
                    continue
                gate = self.gate_form(
                    form,
                    procedure_id=pid,
                    role=role,
                    as_of=active_date,
                )
                if not gate.eligible:
                    rejected.append(
                        {
                            "form_id": form_id,
                            "procedure_id": pid,
                            "reason_code": gate.reason_code,
                        }
                    )
                    continue
                accepted.append(self._public_form(form, pid))

        accepted.sort(
            key=lambda item: (
                item.get("required_or_conditional") != "required",
                item.get("usage") == "officer_internal",
                fold_text(item.get("canonical_name")),
            )
        )
        rejected_reasons = {
            str(item.get("reason_code") or "")
            for item in rejected
            if str(item.get("reason_code") or "")
        }
        data_gap_status: str | None = None
        if ids and not accepted:
            if rejected_reasons & FORM_REVIEW_REQUIRED_REASONS:
                data_gap_status = "LEGAL_REVIEW_REQUIRED"
            elif rejected_reasons and rejected_reasons.issubset(FORM_DATA_GAP_REASONS):
                data_gap_status = "VERIFIED_DATA_GAP"
        return {
            "procedure_matches": detected["matches"],
            "procedure_ambiguous": bool(procedure_ids is None and detected["ambiguous"]),
            # The normal public caller asks for three cards, but an explicit
            # procedure review may request every required form.  Do not cut a
            # legally required fourth form merely because the UI's default is
            # compact; retain a defensive upper bound for broad callers.
            "recommended_forms": accepted[: max(1, min(limit, 12))],
            "rejected_forms": rejected,
            "forms_unavailable": bool(ids) and not accepted,
            "data_gap_status": data_gap_status,
            "data_gap_reasons": sorted(rejected_reasons),
        }

    @staticmethod
    def _public_form(form: dict[str, Any], procedure_id: str) -> dict[str, Any]:
        download_url = form.get("official_download_url")
        if str(form.get("file_format") or "").casefold() not in {"", "online"}:
            download_url = (
                f"/api/procedures/forms-catalog/canonical/{quote(str(form.get('form_id') or ''), safe='')}/download"
                f"?procedure_id={quote(procedure_id, safe='')}"
            )
        return {
            "form_id": form.get("form_id"),
            "procedure_id": procedure_id,
            "name": form.get("canonical_name"),
            "display_name": form.get("canonical_name"),
            "form_title": form.get("canonical_name"),
            "form_code": form.get("form_code"),
            "audience": form.get("audience"),
            "usage": form.get("usage"),
            "required_or_conditional": form.get("required_or_conditional"),
            "condition": form.get("condition"),
            "file_type": form.get("file_format")
            or mimetypes.guess_extension(str(download_url or ""))
            or "online",
            "download_url": download_url,
            "source_url": form.get("official_source_page"),
            "legal_basis": list(form.get("legal_basis") or []),
            "source_classification": form.get("source_classification"),
            "effective_from": form.get("effective_from"),
            "effective_to": form.get("effective_to"),
            "official_level": "official",
            "review_status": "approved",
            "has_official_file": True,
            "needs_official_file": False,
        }
