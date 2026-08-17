"""Convert already-gated form bindings into V2 evidence rows."""

from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from api.legal_section_grounding import LegalIssue


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _official_https(value: Any) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except ValueError:
        return False
    return parsed.scheme.casefold() == "https" and bool(parsed.hostname)


def _trusted_download(value: Any, *, form_id: str, asset_kind: str) -> bool:
    download_url = str(value or "").strip()
    if asset_kind == "file":
        expected = f"/api/procedures/forms-catalog/assets/{form_id}/download"
        if download_url == expected:
            return True
    return _official_https(download_url)


def build_form_evidence_rows(
    *,
    request_id: str,
    issue: LegalIssue,
    procedure_id: str,
    forms: Sequence[Mapping[str, Any]],
    legal_as_of: str | date,
) -> list[dict[str, Any]]:
    """Build evidence only from public forms that already passed catalog gates.

    This adapter repeats the critical public checks defensively. It does not
    discover forms and cannot turn a pending or mismatched binding into runtime
    evidence.
    """

    as_of = legal_as_of if isinstance(legal_as_of, date) else _date(legal_as_of)
    if as_of is None:
        return []
    rows: list[dict[str, Any]] = []
    for form in forms:
        form_id = str(form.get("form_id") or "").strip()
        asset_kind = str(form.get("asset_kind") or "file").strip().casefold()
        if (
            str(form.get("procedure_id") or "") != procedure_id
            or str(form.get("review_status") or "") != "approved"
            or not (
                form.get("has_official_resource") is True
                or form.get("has_official_file") is True
                or str(form.get("asset_kind") or "") == "eform"
            )
            or not _official_https(form.get("source_url"))
            or not form_id
            or not _trusted_download(
                form.get("download_url"),
                form_id=form_id,
                asset_kind=asset_kind,
            )
        ):
            continue
        effective_from = _date(form.get("effective_from"))
        effective_to = _date(form.get("effective_to"))
        if effective_from is None or effective_from > as_of:
            continue
        if effective_to is not None and effective_to < as_of:
            continue
        name = str(
            form.get("display_name")
            or form.get("form_title")
            or form.get("name")
            or ""
        ).strip()
        if not name:
            continue
        requirement = str(form.get("required_or_conditional") or "required")
        condition = str(form.get("condition") or "").strip()
        if requirement == "conditional":
            content = f"Biểu mẫu có điều kiện đã duyệt: {name}."
            if condition:
                content += f" Điều kiện sử dụng: {condition}."
        else:
            content = f"Biểu mẫu bắt buộc đã duyệt: {name}."
        digest = hashlib.sha256(
            f"{procedure_id}:{form_id}".encode("utf-8")
        ).hexdigest()[:24]
        rows.append(
            {
                "source_id": f"form-evidence-{digest}",
                "request_id": request_id,
                "issue_id": issue.issue_id,
                "domain": issue.domain if issue.domain != "unknown" else "administrative",
                "domain_slug": issue.domain if issue.domain != "unknown" else "administrative",
                "effective_status": "active",
                "article_status": "active",
                "effective_from": effective_from.isoformat(),
                "effective_to": effective_to.isoformat() if effective_to else None,
                "official": True,
                "official_level": "haiphong",
                "scope": "haiphong",
                "issuing_agency": "Cơ quan công bố thủ tục hành chính Hải Phòng",
                "source_url": str(form.get("source_url")),
                "document_title": name,
                "content": content,
                "clean_content": content,
                "supported_facets": ["form"],
                "evidence_kind": "approved_form_catalog",
                "form_id": form_id,
                "procedure_id": procedure_id,
                "download_url": str(form.get("download_url")),
                "form_binding_verified": True,
                "form_checksum_verified": True,
                "source_checksum": str(form.get("source_checksum") or ""),
                "asset_kind": asset_kind,
                "required_or_conditional": requirement,
                "condition": condition or None,
            }
        )
    return rows
