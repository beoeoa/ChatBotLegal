"""Build a deterministic legal-review packet for canonical forms."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import (
    FormCatalog,
    _binding_is_approved,
    _file_integrity,
    _is_official_url,
    _sha256,
    normalize_procedure_id,
)


REPORT_PATH = (
    ROOT
    / "reports"
    / "feature005"
    / "forms-completion-20260724"
    / "legal-review-packet.json"
)


def _review_form(
    catalog: FormCatalog,
    form: dict[str, Any],
) -> dict[str, Any]:
    missing: list[str] = []
    procedure_ids = [
        str(value)
        for value in form.get("procedure_ids") or []
        if str(value).strip()
    ]
    if not procedure_ids:
        missing.append("PROCEDURE_BINDING_MISSING")
    if not str(form.get("canonical_name") or "").strip():
        missing.append("CANONICAL_NAME_MISSING")
    if form.get("audience") not in {"citizen", "officer", "both"}:
        missing.append("AUDIENCE_INVALID")
    if form.get("usage") not in {
        "applicant_form",
        "officer_internal",
        "conditional_form",
    }:
        missing.append("USAGE_INVALID")
    if not _is_official_url(form.get("official_source_page")):
        missing.append("OFFICIAL_SOURCE_MISSING")
    if not (form.get("legal_basis") or form.get("official_source_page")):
        missing.append("LEGAL_BASIS_OR_PROCEDURE_PAGE_MISSING")
    if not form.get("effective_from"):
        missing.append("EFFECTIVITY_MISSING")
    if form.get("supersedes_form_id"):
        missing.append("FORM_SUPERSEDED")
    if form.get("review_status") != "approved":
        missing.append("LEGAL_APPROVAL_MISSING")
    if form.get("runtime_eligible") is not True:
        missing.append("RUNTIME_ELIGIBILITY_MISSING")

    source_class = form.get("source_classification")
    if source_class == "official_eform":
        if (
            form.get("file_format") != "online"
            or not _is_official_url(form.get("official_download_url"))
        ):
            missing.append("EFORM_URL_INVALID")
    elif source_class in {"official_file", "official_package_page"}:
        local_path = str(form.get("local_path") or "").strip()
        official_download = str(form.get("official_download_url") or "").strip()
        if local_path:
            path = (catalog.project_root / local_path).resolve()
            valid, _reason = _file_integrity(path, form.get("file_format"))
            if not valid:
                missing.append("FILE_INVALID")
            elif not form.get("sha256") or _sha256(path) != form.get("sha256"):
                missing.append("CHECKSUM_INVALID")
        elif not _is_official_url(official_download):
            missing.append("FILE_OR_DOWNLOAD_MISSING")
    else:
        missing.append("SOURCE_CLASSIFICATION_NOT_SERVABLE")

    for procedure_id in procedure_ids:
        normalized_procedure_id = normalize_procedure_id(procedure_id)
        approved_binding_exists = any(
            _binding_is_approved(binding)
            and normalize_procedure_id(binding.get("procedure_id"))
            == normalized_procedure_id
            and str(binding.get("form_id") or "")
            == str(form.get("form_id") or "")
            for binding in catalog.bindings
        )
        if not approved_binding_exists:
            missing.append("BINDING_NOT_APPROVED")
        procedure = catalog.get_procedure(procedure_id)
        if not procedure:
            missing.append("PROCEDURE_NOT_FOUND")
            continue
        if form.get("domain") != procedure.get("domain"):
            missing.append("DOMAIN_MISMATCH")
        if form.get("administrative_level") not in {
            procedure.get("authority_level"),
            "national",
            "other",
        }:
            missing.append("ADMINISTRATIVE_LEVEL_MISMATCH")

    unique_missing = sorted(set(missing))
    return {
        "form_id": form.get("form_id"),
        "procedure_ids": procedure_ids,
        "canonical_name": form.get("canonical_name"),
        "form_code": form.get("form_code"),
        "audience": form.get("audience"),
        "usage": form.get("usage"),
        "domain": form.get("domain"),
        "source_page": form.get("official_source_page"),
        "download_available": bool(
            form.get("official_download_url") or form.get("local_path")
        ),
        "file_format": form.get("file_format"),
        "sha256_available": bool(form.get("sha256")),
        "effective_from": form.get("effective_from"),
        "effective_to": form.get("effective_to"),
        "current_review_status": form.get("review_status"),
        "runtime_eligible": form.get("runtime_eligible") is True,
        "hard_gate_pass": not unique_missing,
        "missing_gates": unique_missing,
        "decision": (
            "LEGAL_REVIEW_REQUIRED"
            if form.get("review_status") != "approved" or unique_missing
            else "APPROVED_FOR_RUNTIME"
        ),
    }


def build_review_packet(
    catalog: FormCatalog,
    *,
    write: bool = True,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    forms = [_review_form(catalog, form) for form in catalog.forms]
    missing_counts = Counter(
        reason
        for form in forms
        for reason in form["missing_gates"]
    )
    approved = sum(
        form["decision"] == "APPROVED_FOR_RUNTIME" for form in forms
    )
    packet = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": "2026-07-24",
        "form_count": len(forms),
        "auto_approved_count": 0,
        "approved_for_runtime_count": approved,
        "legal_review_required_count": len(forms) - approved,
        "missing_gate_counts": dict(sorted(missing_counts.items())),
        "legal_review_recorded": approved > 0,
        "forms": forms,
    }
    if write:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description="Build form legal-review packet")
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    packet = build_review_packet(
        FormCatalog.load_default(),
        write=True,
        report_path=args.report,
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in packet.items()
                if key != "forms"
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
