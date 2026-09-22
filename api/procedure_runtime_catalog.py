"""Project Admin-managed procedures into the deterministic chat catalog.

The public procedure directory is backed by ``ward_procedure`` while the
chat resolver intentionally reads a reviewed, file-backed catalog.  Keeping
these two stores separate is useful for legal release controls, but a managed
procedure that has been explicitly approved must still become visible to the
chatbot after CRUD.  This module writes a small, replaceable projection; it
does not rewrite the legal document corpus or promote unreviewed form assets.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from open_notebook.database.repository import repo_query


def runtime_catalog_path() -> Path:
    root = Path(str(os.getenv("OPEN_NOTEBOOK_DATA_DIR") or "notebook_data"))
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[1] / root
    forms_dir = Path(str(os.getenv("LEGAL_FORM_CATALOG_DIR") or root / "forms"))
    if not forms_dir.is_absolute():
        forms_dir = Path(__file__).resolve().parents[1] / forms_dir
    return forms_dir / "managed_procedures_runtime_v1.json"


def _record_id(value: Any) -> str:
    return str(value or "").strip().removeprefix("ward_procedure:")


def _form_projection(form: Any, procedure_id: str, procedure_name: str) -> dict[str, Any] | None:
    if not isinstance(form, dict):
        return None
    url = str(form.get("download_url") or "").strip()
    form_id = str(form.get("form_id") or "").strip()
    if not url:
        logger.warning(
            "Skipping managed procedure form without an approved identity/url: {}",
            form_id or "missing-form-id",
        )
        return None
    # The admin row stores a binding to a reviewed form.  Reuse the canonical
    # release metadata rather than making a second, weaker approval path.
    canonical: dict[str, Any] = {}
    try:
        catalog_path = runtime_catalog_path().parent / "canonical_forms_catalog_v1.json"
        payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
        catalog_forms = [
            dict(item)
            for item in payload.get("forms", [])
            if isinstance(item, dict)
        ]
        # ``ward_procedure.forms`` is an older schema that persists only
        # name/file_type/download_url.  The write endpoint validates form_id
        # against the active reviewed release, but SurrealDB drops that extra
        # field. Recover the identity from the authenticated internal download
        # route (or an exact reviewed catalog URL), then re-validate it here.
        internal_match = re.fullmatch(
            r"/api/procedures/forms-catalog/assets/([^/]+)/download",
            url,
        )
        if not form_id and internal_match:
            form_id = internal_match.group(1)
        matches = [
            item
            for item in catalog_forms
            if (
                form_id
                and str(item.get("form_id") or item.get("id") or "") == form_id
            )
            or (
                not form_id
                and url
                in {
                    str(item.get("official_download_url") or "").strip(),
                    str(item.get("download_url") or "").strip(),
                    str(item.get("source_url") or "").strip(),
                }
            )
        ]
        canonical = matches[0] if len(matches) == 1 else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Managed form catalog validation could not read {}: {}",
            catalog_path,
            type(exc).__name__,
        )
        canonical = {}
    if not canonical:
        logger.warning(
            "Skipping managed procedure form {} because it is absent from {}",
            form_id,
            catalog_path,
        )
        return None
    form_id = str(canonical.get("form_id") or canonical.get("id") or form_id).strip()
    approved = bool(
        canonical.get("approved") is True
        or str(canonical.get("review_status") or "").casefold() == "approved"
        or str(canonical.get("legal_review_status") or "").casefold() == "approved"
    )
    if not form_id or not approved or canonical.get("is_quarantined") is True:
        logger.warning(
            "Skipping managed procedure form {} because reviewed eligibility is missing",
            form_id or "missing-form-id",
        )
        return None
    approved_urls = {
        str(canonical.get(key) or "").strip()
        for key in ("official_download_url", "download_url", "source_url")
        if str(canonical.get(key) or "").strip()
    }
    # The management catalog intentionally exposes an authenticated local
    # download route instead of sending the browser straight to a remote
    # ministry URL.  It is equivalent only when the path embeds the same
    # reviewed form identity; arbitrary internal or external URLs stay
    # rejected.
    approved_urls.add(
        f"/api/procedures/forms-catalog/assets/{form_id}/download"
    )
    if approved_urls and url not in approved_urls:
        logger.warning(
            "Skipping managed procedure form {} because its download URL changed",
            form_id,
        )
        return None
    procedure_ids = [procedure_id]
    catalog_procedure_id = str(form.get("catalog_procedure_id") or "").strip()
    if catalog_procedure_id and catalog_procedure_id not in procedure_ids:
        procedure_ids.append(catalog_procedure_id)
    for canonical_procedure_id in canonical.get("procedure_ids") or []:
        canonical_procedure_id = str(canonical_procedure_id or "").strip()
        if canonical_procedure_id and canonical_procedure_id not in procedure_ids:
            procedure_ids.append(canonical_procedure_id)
    return {
        **canonical,
        "form_id": form_id,
        "form_code": str(form.get("form_code") or canonical.get("form_code") or "").strip() or None,
        "name": str(form.get("name") or canonical.get("canonical_name") or "").strip() or procedure_name,
        "form_title": str(form.get("name") or canonical.get("canonical_name") or "").strip() or procedure_name,
        "procedure_ids": procedure_ids,
        "procedure_id": procedure_id,
        "procedure_name": procedure_name,
        "download_url": url,
        "official_download_url": url,
        "source_url": str(form.get("source_url") or canonical.get("official_source_page") or "").strip() or url,
        "official_level": str(form.get("official_level") or canonical.get("official_level") or "official"),
        "review_status": "approved",
        "approved": True,
        "runtime_eligible": True,
        "is_quarantined": False,
        "has_official_file": True,
        "audience": form.get("audience") or canonical.get("audience") or "both",
    }


def _procedure_projection(row: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    procedure_id = _record_id(row.get("id"))
    name = str(row.get("name") or "").strip()
    procedure = {
        "procedure_id": procedure_id,
        "name": name,
        "aliases": [name] if name else [],
        "official_procedure_code": str(row.get("official_procedure_code") or "").strip() or None,
        "domain": str(row.get("domain_slug") or "").strip(),
        "receiving_authority": str(row.get("department") or "").strip(),
        "department": str(row.get("department") or "").strip(),
        "steps": list(row.get("steps") or []),
        "documents_required": list(row.get("documents_required") or []),
        "guidance": str(row.get("guidance") or ""),
        "submission_place": str(row.get("submission_place") or ""),
        "legal_basis": list(row.get("legal_basis") or []),
        "duration": str(row.get("duration") or ""),
        "fee": str(row.get("fee") or ""),
        "forms": [],
        "source_status": "managed_live",
        "review_status": "approved",
        "approved": True,
        "catalog_status": str(row.get("catalog_status") or "approved"),
        "primary_organization_unit_id": row.get("primary_organization_unit_id"),
        "supporting_organization_unit_ids": list(row.get("supporting_organization_unit_ids") or []),
        "source_url": row.get("source_url"),
    }
    forms: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for raw_form in row.get("forms") or []:
        form = _form_projection(raw_form, procedure_id, name)
        if not form:
            continue
        forms.append(form)
        procedure["forms"].append(form)
        bindings.append({
            "procedure_id": procedure_id,
            "form_id": form["form_id"],
            "binding_status": "approved",
            "review_status": "approved",
            "approved": True,
        })
    return procedure, forms, bindings


async def sync_managed_procedure_catalog() -> Path:
    """Atomically refresh the runtime projection after an admin write."""

    rows = await repo_query(
        "SELECT * FROM ward_procedure WHERE archived != true AND catalog_status = 'approved';"
    )
    procedures: list[dict[str, Any]] = []
    forms: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not _record_id(row.get("id")):
            continue
        procedure, procedure_forms, procedure_bindings = _procedure_projection(row)
        if not procedure["name"]:
            continue
        procedures.append(procedure)
        forms.extend(procedure_forms)
        bindings.extend(procedure_bindings)
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "procedures": procedures,
        "forms": forms,
        "bindings": bindings,
    }
    path = runtime_catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return path


def load_managed_runtime_catalog() -> dict[str, Any]:
    path = runtime_catalog_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"procedures": [], "forms": [], "bindings": []}
    return payload if isinstance(payload, dict) else {"procedures": [], "forms": [], "bindings": []}


def _form_is_visible_to_audience(form: dict[str, Any], audience: str) -> bool:
    raw = form.get("audience") or "both"
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    allowed = {str(value or "").strip().casefold() for value in values}
    return bool({"both", "all", audience.casefold()} & allowed)


def managed_public_catalog_items(
    *,
    audience: str = "citizen",
    payload: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Project the approved runtime overlay into the public catalog shape."""

    managed = payload if isinstance(payload, dict) else load_managed_runtime_catalog()
    forms_by_procedure: dict[str, list[dict[str, Any]]] = {}
    for raw in managed.get("forms") or []:
        if not isinstance(raw, dict):
            continue
        procedure_id = _record_id(raw.get("procedure_id"))
        form_id = str(raw.get("form_id") or "").strip()
        download_url = str(
            raw.get("official_download_url") or raw.get("download_url") or ""
        ).strip()
        if (
            not procedure_id
            or not form_id
            or not download_url
            or raw.get("approved") is not True
            or str(raw.get("review_status") or "").casefold() != "approved"
            or not _form_is_visible_to_audience(raw, audience)
        ):
            continue
        forms_by_procedure.setdefault(procedure_id, []).append(
            {
                "form_id": form_id,
                "form_code": raw.get("form_code"),
                "name": raw.get("form_title") or raw.get("name") or form_id,
                "display_name": raw.get("form_title") or raw.get("name") or form_id,
                "file_type": raw.get("file_type") or raw.get("file_format") or "file",
                "download_url": download_url,
                "source_url": raw.get("source_url") or download_url,
                "official_level": "official",
                "review_status": "approved",
                "audience": raw.get("audience") or "both",
            }
        )

    items: list[dict[str, Any]] = []
    for raw in managed.get("procedures") or []:
        if not isinstance(raw, dict):
            continue
        procedure_id = _record_id(raw.get("procedure_id"))
        name = str(raw.get("name") or "").strip()
        if (
            not procedure_id
            or not name
            or raw.get("approved") is not True
            or str(raw.get("review_status") or "").casefold() != "approved"
        ):
            continue
        forms = forms_by_procedure.get(procedure_id, [])
        items.append(
            {
                "procedure_id": procedure_id,
                "procedure_code": raw.get("official_procedure_code") or procedure_id,
                "name": name,
                "domain": raw.get("domain") or "hanh_chinh_cong",
                "authority": raw.get("receiving_authority") or raw.get("department"),
                "department": raw.get("department") or raw.get("receiving_authority"),
                "official_source_url": raw.get("source_url"),
                "coverage_status": "managed_live",
                "primary_organization_unit_id": raw.get("primary_organization_unit_id"),
                "primary_organization_unit_name": raw.get("department") or raw.get("receiving_authority"),
                "supporting_organization_unit_ids": list(raw.get("supporting_organization_unit_ids") or []),
                "form_status": "resolved" if forms else "not_requested",
                "forms_unavailable": False,
                "reason": None,
                "data_gap_reasons": [],
                "forms": forms,
                "steps": list(raw.get("steps") or []),
                "documents_required": list(raw.get("documents_required") or []),
                "guidance": str(raw.get("guidance") or ""),
                "submission_place": str(raw.get("submission_place") or ""),
                "legal_basis": list(raw.get("legal_basis") or []),
                "duration": str(raw.get("duration") or ""),
                "fee": str(raw.get("fee") or ""),
                "catalog_status": "approved",
                "publication_source": "managed_runtime",
            }
        )
    return items


def merge_managed_public_catalog(
    base: dict[str, Any],
    *,
    audience: str = "citizen",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge approved managed records without rewriting the active release.

    Release identifiers remain authoritative on collision. The runtime overlay
    is rebuilt after every admin CRUD operation, so archive/delete naturally
    removes a managed item on the next uncached request.
    """

    managed = payload if isinstance(payload, dict) else load_managed_runtime_catalog()
    release_items = [dict(item) for item in base.get("items") or [] if isinstance(item, dict)]
    release_ids = {
        _record_id(item.get("procedure_id")).casefold()
        for item in release_items
        if _record_id(item.get("procedure_id"))
    }
    overlay = [
        item
        for item in managed_public_catalog_items(audience=audience, payload=managed)
        if _record_id(item.get("procedure_id")).casefold() not in release_ids
    ]
    items = [*release_items, *overlay]
    return {
        **base,
        "items": items,
        "total": len(items),
        "managed_overlay_total": len(overlay),
        "managed_overlay_updated_at": managed.get("updated_at"),
    }
