"""Feature 017 workflow API. Public prefix: /api/procedures/forms-catalog."""

from __future__ import annotations

import hashlib
import asyncio
import json
import mimetypes
import os
import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id, get_request_username
from api.form_governance_models import ActorContext, FormReviewSubmission, FormWorkflowStatus
from api.form_governance_service import FormGovernanceService, get_form_governance_service
from api.form_governance_release_validator import HttpFormSourceVerifier
from api.legal_domains import canonicalize_legal_domain
from api.organization_service import find_unit, route_for_domain
from api.system_settings import active_organization_units
from api.user_service import write_audit_log
from open_notebook.database.repository import repo_query


router = APIRouter(prefix="/procedures/forms-catalog", tags=["procedure-forms-catalog"])
ROOT = Path(__file__).resolve().parents[2]
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


@router.get("/canonical/{form_id}/download")
async def download_canonical_form(form_id: str, procedure_id: str, request: Request):
    from api.legal_form_catalog import FormCatalog
    from api.form_download_delivery import read_form_bytes
    role = get_request_role(request)
    catalog = await asyncio.to_thread(FormCatalog.load_default)
    result = await asyncio.to_thread(catalog.resolve_forms, "", role=role,
                                     procedure_ids=[procedure_id], limit=12)
    if not any(str(f.get("form_id")) == form_id for f in result.get("recommended_forms", [])):
        raise HTTPException(404, "Biểu mẫu chưa được duyệt cho thủ tục hoặc vai trò này.")
    form = next((f for f in catalog.forms if str(f.get("form_id")) == form_id), None)
    if not form:
        raise HTTPException(404, "Không tìm thấy biểu mẫu.")
    try:
        data, filename = await read_form_bytes(form, catalog.project_root)
    except (TimeoutError, OSError) as exc:
        raise HTTPException(502, "Chưa tải được tệp biểu mẫu. Vui lòng thử lại sau.") from exc
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(502, "Nguồn biểu mẫu đang gặp lỗi tải tệp.") from exc
    return Response(data, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


PROCEDURE_COMPATIBILITY_MANIFEST = (
    ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-30.json"
)
PROCEDURE_CATALOG_FALLBACK = ROOT / "notebook_data" / "forms" / "three_tier_procedure_catalog_v1.json"


def _fold_search(value: Any) -> str:
    normalized = unicodedata.normalize("NFD", str(value or ""))
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    normalized = normalized.replace("đ", "d").replace("Đ", "D").casefold()
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


@lru_cache(maxsize=1)
def _compatibility_procedure_candidates() -> tuple[dict[str, Any], ...]:
    # The generated Feature 006 report is optional and may be absent in a
    # cleaned local checkout.  Use the immutable, already-serving procedure
    # catalog as a read-only fallback instead of making the admin picker fail
    # with 503.  This does not approve forms or change any release pointer.
    source_path = PROCEDURE_COMPATIBILITY_MANIFEST
    if not source_path.is_file():
        source_path = PROCEDURE_CATALOG_FALLBACK
    if not source_path.is_file():
        raise RuntimeError("FORM_PROCEDURE_SCOPE_UNAVAILABLE")
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    return tuple(
        {
            "procedure_id": str(
                item.get("procedure_code")
                or item.get("official_procedure_code")
                or item.get("procedure_id")
                or ""
            ).strip(),
            "procedure_code": str(
                item.get("procedure_code")
                or item.get("official_procedure_code")
                or item.get("procedure_id")
                or ""
            ).strip(),
            "name": str(item.get("procedure_name") or item.get("name") or "").strip(),
            "domain": str(item.get("domain") or "").strip(),
            "official_source_url": item.get("official_source_page"),
            "coverage_status": item.get("coverage_status"),
        }
        for item in payload.get("procedures") or []
        if str(item.get("executing_level") or "").casefold() == "commune"
        and item.get("procedure_id")
        and item.get("procedure_name")
        and item.get("domain")
    )


def _procedure_candidates(service: FormGovernanceService) -> tuple[list[dict[str, Any]], str]:
    release = service.repository.active_release()
    manifest = (release or {}).get("manifest") or release or {}
    active = []
    for item in manifest.get("procedures") or []:
        procedure_id = str(item.get("procedure_id") or "").strip()
        name = str(item.get("name") or item.get("canonical_name") or "").strip()
        domain = str(item.get("domain") or "").strip()
        if procedure_id and name and domain:
            active.append({
                "procedure_id": procedure_id,
                "procedure_code": item.get("procedure_code") or procedure_id,
                "name": name,
                "domain": domain,
                "official_source_url": item.get("official_source_url"),
                "coverage_status": item.get("coverage_status"),
                "primary_organization_unit_id": item.get(
                    "primary_organization_unit_id"
                ),
                "primary_organization_unit_name": item.get(
                    "primary_organization_unit_name"
                ),
                "supporting_organization_unit_ids": list(
                    item.get("supporting_organization_unit_ids") or []
                ),
            })
    if active:
        return active, "active_release"
    return list(_compatibility_procedure_candidates()), "read_only_compatibility"


def _plain_record_id(value: Any) -> str:
    text = str(value or "").strip()
    return text.split(":", 1)[1] if text.startswith("ward_procedure:") else text


async def _procedure_unit_assignments(
    candidates: list[dict[str, Any]],
    *, allow_domain_fallback: bool = True,
    published_authority: bool = False,
) -> dict[str, dict[str, Any]]:
    """Resolve one server-owned unit projection for procedure release writes."""

    procedure_ids = {
        str(item.get("procedure_id") or "").strip()
        for item in candidates
        if str(item.get("procedure_id") or "").strip()
    }
    if not procedure_ids:
        return {}
    if published_authority:
        # Do not read the mutable projection at all: stale rows and a secondary
        # database outage cannot alter the approved assignment.
        return {
            str(item["procedure_id"]): {
                "primary_organization_unit_id": item.get("primary_organization_unit_id"),
                "primary_organization_unit_name": item.get("primary_organization_unit_name"),
                "supporting_organization_unit_ids": list(dict.fromkeys(
                    item.get("supporting_organization_unit_ids") or [])),
            }
            for item in candidates if str(item.get("procedure_id") or "").strip()
        }
    result: dict[str, dict[str, Any]] = {}
    try:
        relation_rows, procedure_rows, units = await asyncio.gather(
            repo_query(
                "SELECT * FROM procedure_organization_unit "
                "WHERE procedure_id IN $procedure_ids;",
                {"procedure_ids": list(procedure_ids)},
            ),
            repo_query(
                "SELECT id, primary_organization_unit_id, "
                "supporting_organization_unit_ids FROM ward_procedure;",
                {},
            ),
            active_organization_units(),
        )
    except Exception:
        relation_rows, procedure_rows, units = [], [], []

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in relation_rows:
        grouped.setdefault(str(row.get("procedure_id") or "").strip(), []).append(row)
    stored = {
        _plain_record_id(row.get("id")): row
        for row in procedure_rows
        if _plain_record_id(row.get("id"))
    }
    by_domain = {
        str(item.get("procedure_id") or "").strip(): str(item.get("domain") or "")
        for item in candidates
    }
    candidate_projection = {
        str(item.get("procedure_id") or "").strip(): item for item in candidates
    }
    unit_names = {unit.id: unit.name for unit in units}
    for procedure_id in procedure_ids:
        relations = grouped.get(procedure_id) or []
        primary = next(
            (
                str(row.get("organization_unit_id") or "").strip()
                for row in relations
                if row.get("responsibility") == "primary"
            ),
            "",
        )
        supporting = [
            str(row.get("organization_unit_id") or "").strip()
            for row in relations
            if row.get("responsibility") == "support"
            and str(row.get("organization_unit_id") or "").strip()
        ]
        row = stored.get(procedure_id) or {}
        primary = primary or str(
            row.get("primary_organization_unit_id") or ""
        ).strip()
        primary = primary or str(
            candidate_projection.get(procedure_id, {}).get(
                "primary_organization_unit_id"
            )
            or ""
        ).strip()
        if not supporting:
            supporting = [
                str(value).strip()
                for value in row.get("supporting_organization_unit_ids") or []
                if str(value).strip()
            ]
        if not supporting:
            supporting = [
                str(value).strip()
                for value in candidate_projection.get(procedure_id, {}).get(
                    "supporting_organization_unit_ids"
                )
                or []
                if str(value).strip()
            ]
        if not primary and allow_domain_fallback:
            routed = route_for_domain(units, by_domain.get(procedure_id)) if units else None
            primary = routed[0].id if routed else ""
        result[procedure_id] = {
            "primary_organization_unit_id": primary or None,
            "primary_organization_unit_name": unit_names.get(primary),
            "supporting_organization_unit_ids": list(dict.fromkeys(supporting)),
        }
    return result


async def actor_context(request: Request) -> ActorContext:
    role = get_request_role(request)
    user_id = get_request_user_id(request) or get_request_username(request)
    if (
        not user_id
        and role == "admin"
        and str(getattr(request.state, "auth_mode", "")) == "legacy_password"
    ):
        # The configured local Admin password is an authenticated technical
        # principal but deliberately has no user_account record. Give release
        # and audit workflows a stable actor ID without impersonating or
        # querying the real ``admin`` account.
        user_id = "system:legacy-admin"
    if not user_id:
        raise HTTPException(status_code=403, detail="Authenticated user identity required")
    domains: list[str] = []
    mode = "legacy"
    managed_procedure_ids: list[str] = []
    if role == "officer":
        from api.user_service import get_user_profile
        from api.organization_service import current_officer_scope
        profile = await get_user_profile(user_id)
        scope = await current_officer_scope(user_id, profile or {})
        domains = list(scope.domains)
        mode = scope.mode
        if mode in {"hybrid", "unit_primary"}:
            candidates, source = _procedure_candidates(get_form_governance_service())
            assignments = await _procedure_unit_assignments(candidates, allow_domain_fallback=False,
                published_authority=source == "active_release")
            managed_procedure_ids = [str(item["procedure_id"]) for item in candidates if scope.permits(
                [assignments.get(str(item["procedure_id"]), {}).get("primary_organization_unit_id"),
                 *(assignments.get(str(item["procedure_id"]), {}).get("supporting_organization_unit_ids") or [])],
                item.get("domain"),
            )]
    return ActorContext(user_id=user_id, role=role, domains=domains,
                        organization_routing_mode=mode, managed_procedure_ids=managed_procedure_ids)


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class DraftEditRequest(BaseModel):
    version: int = Field(ge=1)
    submission: FormReviewSubmission


class DraftDeleteRequest(ReasonRequest):
    version: int = Field(ge=1)


class ReplacementRequest(BaseModel):
    procedure_id: str = Field(min_length=1)


class LegalMetadataRequest(BaseModel):
    procedure: dict[str, Any]
    asset: dict[str, Any]
    bindings: list[dict[str, Any]] = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)


class AttestRequest(BaseModel):
    fingerprint: str = Field(pattern="^[a-f0-9]{64}$")


class ReleasePreviewRequest(BaseModel):
    case_ids: list[str] = Field(min_length=1)
    legal_as_of: date
    source_snapshot_sha256: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")


class VerifiedGapRequest(BaseModel):
    target_type: str = Field(pattern="^(procedure|identity|binding)$")
    target_id: str = Field(min_length=1, max_length=240)
    reason_code: str = Field(min_length=3, max_length=240)
    evidence_source_url: str = Field(min_length=8, max_length=3000)
    evidence_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    legal_as_of: date
    procedure_ids: list[str] = Field(default_factory=list)
    domain: str | None = Field(default=None, max_length=160)
    procedure_name: str | None = Field(default=None, max_length=1000)
    procedure_source_url: str | None = Field(default=None, max_length=3000)
    aliases: list[str] = Field(default_factory=list)


@router.get("/source-proposal-metadata")
async def source_proposal_metadata(
    actor: ActorContext = Depends(actor_context),
):
    if actor.role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="FORM_CASE_FORBIDDEN")
    return {
        "steps": [
            {
                "id": "procedure_selected",
                "label": "Chọn đúng thủ tục",
                "action": "Xác nhận thủ tục",
                "public_after_step": False,
            },
            {
                "id": "source_verified",
                "label": "Xác minh nguồn chính thức",
                "action": "Xác minh nguồn chính thức",
                "public_after_step": False,
            },
            {
                "id": "legal_metadata_completed",
                "label": "Hoàn thiện dữ liệu pháp lý",
                "action": "Lưu dữ liệu pháp lý",
                "public_after_step": False,
            },
            {
                "id": "legal_attested",
                "label": "Xác nhận pháp lý",
                "action": "Xác nhận bản khóa checksum",
                "public_after_step": False,
            },
            {
                "id": "release_validated",
                "label": "Kiểm tra bản phát hành",
                "action": "Chạy Release Gate",
                "public_after_step": False,
            },
            {
                "id": "released",
                "label": "Phát hành",
                "action": "Phát hành cho người dân",
                "public_after_step": True,
            },
        ],
        "source_inputs": ["official_url", "pdf", "docx", "eform"],
        "file_contract": {
            "accepted_extensions": [".pdf", ".docx"],
            "accepted_mime_types": [
                "application/pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ],
            "checksum": "sha256",
            "transport": "client_metadata_only_until_secure_upload_gate",
        },
        "source_approval_changes_public_release": False,
    }


@router.get("/procedure-candidates")
async def procedure_candidates(
    q: str | None = Query(default=None, max_length=500),
    domain: str | None = Query(default=None, max_length=160),
    limit: int = Query(default=20, ge=1, le=1000),
    actor: ActorContext = Depends(actor_context),
    service: FormGovernanceService = Depends(get_form_governance_service),
    organization_unit_id: str | None = None,
):
    if actor.role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="FORM_CASE_FORBIDDEN")
    if actor.role == "officer" and actor.organization_routing_mode in {"legacy", "shadow"} and domain and domain not in actor.domains:
        raise HTTPException(status_code=403, detail="FORM_DOMAIN_FORBIDDEN")

    candidates, source = _call(_procedure_candidates, service)
    assignments = await _procedure_unit_assignments(candidates,
        published_authority=source == "active_release")
    # The form release is immutable and may still contain assignments from a
    # retired department layout.  FAQ editing must nevertheless use the
    # current operating-unit projection.  Re-project only the display/filter
    # fields here; the approved release is never mutated by a picker request.
    current_units = []
    if source == "active_release":
        try:
            current_units = await active_organization_units()
        except Exception:
            # Release assignments remain authoritative even if the optional
            # current-unit projection is temporarily unavailable.
            current_units = []

    if source == "active_release" and current_units:
        for item in candidates:
            procedure_id = str(item.get("procedure_id") or "").strip()
            if not procedure_id:
                continue
            assignment = assignments.setdefault(procedure_id, {})
            primary = str(assignment.get("primary_organization_unit_id") or "").strip()
            current = find_unit(current_units, unit_id=primary) if primary else None
            item_domain_value = canonicalize_legal_domain(item.get("domain")) or str(item.get("domain") or "").strip()
            item["domain"] = item_domain_value
            current_domain_codes = set(getattr(current, "domain_codes", []) or [])
            current_domain_codes.update(
                str(value.get("domain_code") or "").strip()
                if isinstance(value, dict)
                else str(getattr(value, "domain_code", "") or "").strip()
                for value in (getattr(current, "domain_assignments", []) or [])
            )
            if current is None or not current.is_active or item_domain_value not in current_domain_codes:
                routed = route_for_domain(current_units, item_domain_value)
                if routed:
                    assignment["primary_organization_unit_id"] = routed[0].id
                    assignment["primary_organization_unit_name"] = routed[0].name
                    assignment["supporting_organization_unit_ids"] = list(
                        assignment.get("supporting_organization_unit_ids") or []
                    )
    if current_units:
        current_unit_names = {unit.id: unit.name for unit in current_units}
        for assignment in assignments.values():
            primary_id = str(
                assignment.get("primary_organization_unit_id") or ""
            ).strip()
            if primary_id and primary_id in current_unit_names:
                # The stable ID remains release-owned; only its display label
                # follows the current saved department name.
                assignment["primary_organization_unit_name"] = (
                    current_unit_names[primary_id]
                )
    allowed_domains = set(actor.domains) if actor.role == "officer" else None
    folded_query = _fold_search(q)
    filtered: list[dict[str, Any]] = []
    for item in candidates:
        item = {
            **item,
            **assignments.get(str(item.get("procedure_id") or ""), {}),
        }
        item_domain = str(item.get("domain") or "")
        if organization_unit_id and organization_unit_id not in {
            str(item.get("primary_organization_unit_id") or ""),
            *(str(value) for value in item.get("supporting_organization_unit_ids") or []),
        }:
            continue
        if actor.role == "officer" and not service._procedure_allowed(actor, str(item.get("procedure_id") or ""), item_domain):
            continue
        if domain and item_domain != domain:
            continue
        searchable = _fold_search(" ".join(
            str(item.get(key) or "")
            for key in ("procedure_code", "procedure_id", "name", "domain")
        ))
        if folded_query and folded_query not in searchable:
            continue
        filtered.append(item)

    def rank(item: dict[str, Any]) -> tuple[int, str, str]:
        code = _fold_search(item.get("procedure_code"))
        name = _fold_search(item.get("name"))
        exact = 0 if folded_query and folded_query in {code, name} else 1
        prefix = 0 if folded_query and (code.startswith(folded_query) or name.startswith(folded_query)) else 1
        return exact + prefix, name, code

    filtered.sort(key=rank)
    return {
        "items": filtered[:limit],
        "total": len(filtered),
        "source": source,
        "read_only": True,
    }


@router.post("/review-cases")
async def submit_case(payload: FormReviewSubmission, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.submit, actor, payload)


@router.get("/management-catalog")
async def management_catalog(actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.management_catalog, actor)


@router.post("/managed-assets/{form_id}/replace")
async def replace_managed_form(form_id: str, payload: ReplacementRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.replace_form, actor, form_id, payload.procedure_id)


@router.post("/managed-assets/{form_id}/withdraw-preview")
async def remove_managed_form(form_id: str, payload: ReasonRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.prepare_form_removal, actor, form_id, payload.reason)


@router.get("/managed-assets/{form_id}/history")
async def asset_history(form_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    _call(service._require, actor, "admin")
    events = list(_call(service.repository.events, form_id))
    for case in _call(service.list_cases, actor):
        if case.legal_metadata.get("asset", {}).get("form_id") == form_id:
            events.extend(_call(service.repository.events, case.case_id))
    return sorted(events, key=lambda item: item.occurred_at, reverse=True)


@router.put("/review-cases/{case_id}/draft")
async def edit_managed_draft(case_id: str, payload: DraftEditRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.edit_draft, actor, case_id, payload.submission, version=payload.version)


@router.post("/review-cases/{case_id}/delete-draft")
async def delete_managed_draft(case_id: str, payload: DraftDeleteRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.delete_draft, actor, case_id, version=payload.version, reason=payload.reason)


@router.get("/review-cases/{case_id}/history")
async def managed_history(case_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    _call(service.get_case, actor, case_id)
    return _call(service.repository.events, case_id)


@router.get("/review-cases/{case_id}/test-download")
async def test_managed_download(case_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    from api.form_download_delivery import read_form_bytes
    _call(service._require, actor, "admin")
    case = _call(service.get_case, actor, case_id)
    source = case.current_submission
    if source.asset_kind.value != "file":
        raise HTTPException(409, "Nguồn này là trang trực tuyến, không có tệp tải trực tiếp.")
    extension = Path(source.source_url.split("?", 1)[0]).suffix.lstrip(".").lower()
    data, filename = await read_form_bytes({"form_id": case.case_id,
        "file_format": extension, "official_download_url": source.source_url,
        "sha256": source.source_checksum}, ROOT)
    return Response(data, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/review-cases/mine")
async def mine(status: str | None = Query(None), actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.list_cases, actor, status=status)


@router.get("/review-cases")
async def list_cases(status: str | None = Query(None), domain: str | None = Query(None), actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    if actor.role != "admin": raise HTTPException(status_code=403, detail="Admin role required")
    return _call(service.list_cases, actor, status=status, domain=domain)


@router.get("/review-cases/{case_id}")
async def get_case(case_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.get_case, actor, case_id)


@router.post("/review-cases/{case_id}/supplement")
async def supplement(case_id: str, payload: FormReviewSubmission, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.supplement, actor, case_id, payload)


@router.post("/review-cases/{case_id}/withdraw")
async def withdraw(case_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.transition, actor, case_id, FormWorkflowStatus.WITHDRAWN)


@router.post("/review-cases/{case_id}/request-supplement")
async def request_supplement(case_id: str, payload: ReasonRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.transition, actor, case_id, FormWorkflowStatus.NEEDS_SUPPLEMENT, reason=payload.reason)


@router.post("/review-cases/{case_id}/approve-source")
async def approve_source(case_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    case = _call(service.get_case, actor, case_id)
    checksum = case.current_submission.source_checksum
    if not checksum:
        checksum, reason = await asyncio.to_thread(
            HttpFormSourceVerifier().calculate_checksum,
            case.current_submission.source_url,
        )
        if reason or not checksum:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": reason or "FORM_SOURCE_UNAVAILABLE",
                    "message": "Không thể xác minh nội dung tại URL nguồn chính thức.",
                },
            )
    result = _call(service.approve_source, actor, case_id, checksum)
    try:
        await write_audit_log(
            action="form.source.approve", entity_type="form_review_case", entity_id=case_id,
            actor_user_id=actor.user_id, actor_role=actor.role,
            details={"result": "success"},
        )
    except Exception:
        # The canonical PostgreSQL workflow event is already committed. A
        # secondary audit projection outage must not force users to repeat a
        # legally significant transition or create a duplicate revision.
        pass
    return result


@router.post("/review-cases/{case_id}/reject-source")
async def reject_source(case_id: str, payload: ReasonRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    # Persist the canonical workflow transition first.  The audit row is a
    # secondary projection and must not turn a successfully committed reject
    # decision into a misleading 500 or leave the reviewer unsure whether to
    # retry the action.
    result = _call(service.transition, actor, case_id, FormWorkflowStatus.REJECTED, reason=payload.reason)
    try:
        await write_audit_log(
            action="form.source.reject",
            entity_type="form_review_case",
            entity_id=case_id,
            actor_user_id=actor.user_id,
            actor_role=actor.role,
            details={"result": "success", "reason": payload.reason},
        )
    except Exception:
        # The workflow event is the canonical decision history.  A projection
        # outage must be observable in logs but cannot require a duplicate
        # transition from the admin.
        import logging
        logging.getLogger(__name__).exception(
            "Form source rejection committed but audit projection failed: %s", case_id
        )
    return result


@router.put("/review-cases/{case_id}/legal-metadata")
async def legal_metadata(case_id: str, payload: LegalMetadataRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    case = _call(service.get_case, actor, case_id)
    metadata = payload.model_dump(mode="json")
    assignments = await _procedure_unit_assignments([{
        "procedure_id": case.procedure_id,
        "domain": case.domain,
    }])
    assignment = assignments.get(case.procedure_id, {
        "primary_organization_unit_id": None,
        "supporting_organization_unit_ids": [],
    })
    metadata["procedure"].update({
        "primary_organization_unit_id": assignment.get(
            "primary_organization_unit_id"
        ),
        "supporting_organization_unit_ids": assignment.get(
            "supporting_organization_unit_ids"
        ) or [],
    })
    return _call(service.enrich, actor, case_id, metadata)


@router.post("/review-cases/{case_id}/ready-for-attestation")
async def ready_for_attestation(case_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.ready_for_attestation, actor, case_id)


@router.post("/review-cases/{case_id}/reopen-for-correction")
async def reopen_for_correction(case_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    """Return a blocked release candidate to legal editing without stale attestation."""
    return _call(service.reopen_for_correction, actor, case_id)


@router.get("/review-cases/{case_id}/attestation-preview")
async def attestation_preview(case_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.attestation_preview, actor, case_id)


@router.post("/review-cases/{case_id}/attest")
async def attest(case_id: str, payload: AttestRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    result = _call(service.attest, actor, case_id, payload.fingerprint)
    await write_audit_log(
        action="form.attest", entity_type="form_review_case", entity_id=case_id,
        actor_user_id=actor.user_id, actor_role=actor.role,
        details={"result": "success", "fingerprint": payload.fingerprint},
    )
    return result


@router.post("/releases/preview")
async def release_preview(payload: ReleasePreviewRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.build_release, actor, payload.case_ids, legal_as_of=payload.legal_as_of, source_snapshot_sha256=payload.source_snapshot_sha256)


@router.post("/releases/{release_id}/validate")
async def validate_release(release_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.validate_release, actor, release_id)


@router.post("/releases/{release_id}/activate")
async def activate_release(release_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    result = _call(service.activate_release, actor, release_id)
    await write_audit_log(
        action="form.release.activate", entity_type="form_release", entity_id=release_id,
        actor_user_id=actor.user_id, actor_role=actor.role,
        details={"result": "success"},
    )
    return result


@router.post("/releases/{release_id}/rollback")
async def rollback_release(release_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.rollback_release, actor, release_id)


@router.get("/releases/active")
async def active_release(service: FormGovernanceService = Depends(get_form_governance_service)):
    release = await asyncio.to_thread(_call, service.repository.active_release)
    if not release: raise HTTPException(status_code=404, detail="FORM_RELEASE_NOT_FOUND")
    return {key: release.get(key) for key in ("release_id", "version", "legal_as_of", "manifest_sha256", "status")}


@router.get("/public-catalog")
async def public_form_catalog(
    request: Request,
    response: Response,
    audience: str = Query("citizen", pattern="^(citizen|officer)$"),
    as_of: date | None = Query(None, alias="legal_as_of"),
    service: FormGovernanceService = Depends(get_form_governance_service),
):
    """Return the same active-release projection consumed by chatbot forms."""

    from api.form_router_v3 import public_catalog_from_manifest

    if audience == "officer" and get_request_role(request) not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="FORM_CASE_FORBIDDEN")
    release = await asyncio.to_thread(_call, service.repository.active_release)
    if not release:
        raise HTTPException(status_code=404, detail="FORM_RELEASE_NOT_FOUND")
    manifest = release.get("manifest") or release
    result = await asyncio.to_thread(
        _call,
        public_catalog_from_manifest,
        manifest,
        audience=audience,
        legal_as_of=as_of or date.today(),
    )
    from api.procedure_runtime_catalog import merge_managed_public_catalog

    result = await asyncio.to_thread(
        merge_managed_public_catalog,
        result,
        audience=audience,
    )
    # Release activation is the cache invalidation mechanism.  Explicitly
    # prevent browser/proxy caching so a withdraw or replacement is visible on
    # the next page load without restarting either service.
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Form-Release-Id"] = str(result.get("release_id") or "")
    response.headers["X-Managed-Catalog-Updated-At"] = str(
        result.get("managed_overlay_updated_at") or ""
    )
    return result


@router.get("/releases/pending")
async def pending_release(
    actor: ActorContext = Depends(actor_context),
    service: FormGovernanceService = Depends(get_form_governance_service),
):
    if actor.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    release = _call(service.repository.latest_pending_release)
    if not release:
        raise HTTPException(status_code=404, detail="FORM_RELEASE_NOT_FOUND")
    return release


@router.get("/coverage")
async def coverage(actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.coverage, actor)


@router.get("/notifications/mine")
async def my_form_notifications(
    request: Request,
    unread_only: bool = Query(False),
    actor: ActorContext = Depends(actor_context),
):
    if actor.role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="FORM_CASE_FORBIDDEN")
    from api.form_notification_projection import list_notifications_for_user

    return {
        "notifications": await list_notifications_for_user(
            actor.user_id,
            user_alias=get_request_username(request),
            unread_only=unread_only,
        )
    }


@router.post("/notifications/{notification_id}/read")
async def read_form_notification(
    request: Request,
    notification_id: str,
    actor: ActorContext = Depends(actor_context),
):
    if actor.role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="FORM_CASE_FORBIDDEN")
    from api.form_notification_projection import mark_notification_read

    try:
        return {
            "notification": await mark_notification_read(
                actor.user_id,
                notification_id,
                user_alias=get_request_username(request),
            )
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/coverage/verified-gap")
async def verified_gap(payload: VerifiedGapRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.verify_gap, actor, payload.model_dump(mode="json"))


@router.get("/resolve")
async def resolve_public_forms(
    request: Request,
    q: str = Query(min_length=3, max_length=2000),
    audience: str = Query("citizen", pattern="^(citizen|officer)$"),
    as_of: date | None = Query(None, alias="legal_as_of"),
):
    from api.form_router_v3 import resolve_from_configured_release
    request_role = get_request_role(request)
    if audience == "officer" and request_role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="FORM_CASE_FORBIDDEN")
    result = _call(resolve_from_configured_release, question=q, audience=audience, legal_as_of=as_of or date.today())
    if result is None: raise HTTPException(status_code=503, detail="FORM_GOVERNANCE_ROUTER_NOT_ACTIVE")
    return result


@router.get("/assets/{form_id}/download")
async def download_released_form_asset(
    form_id: str,
    service: FormGovernanceService = Depends(get_form_governance_service),
):
    release = _call(service.repository.active_release)
    if not release:
        raise HTTPException(status_code=404, detail="FORM_RELEASE_NOT_FOUND")
    manifest = release.get("manifest") or release
    asset = next(
        (
            item
            for item in manifest.get("assets") or []
            if str(item.get("form_id") or "") == form_id
        ),
        None,
    )
    if (
        not asset
        or asset.get("coverage_status") != "released"
        or asset.get("asset_kind") != "file"
    ):
        raise HTTPException(status_code=404, detail="FORM_ASSET_NOT_RELEASED")
    expected_url = f"/api/procedures/forms-catalog/assets/{form_id}/download"
    if asset.get("download_url") != expected_url:
        raise HTTPException(status_code=409, detail="FORM_RUNTIME_ASSET_INELIGIBLE")
    runtime_root = Path(
        str(os.getenv("FORM_RELEASE_ASSET_ROOT") or ROOT / "release-data")
    ).resolve()
    runtime_path = str(asset.get("runtime_path") or "").strip()
    candidate = (runtime_root / runtime_path).resolve()
    try:
        candidate.relative_to(runtime_root)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="FORM_RUNTIME_ASSET_PATH_INVALID") from exc
    if not runtime_path or not candidate.is_file():
        raise HTTPException(status_code=404, detail="FORM_RUNTIME_ASSET_MISSING")
    expected_checksum = str(asset.get("source_checksum") or "").casefold()
    if not SHA256_RE.fullmatch(expected_checksum):
        raise HTTPException(status_code=409, detail="FORM_CHECKSUM_MISMATCH")
    digest = hashlib.sha256()
    with candidate.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected_checksum:
        raise HTTPException(status_code=409, detail="FORM_CHECKSUM_MISMATCH")
    safe_stem = re.sub(
        r"[^A-Za-z0-9._-]+",
        "-",
        str(asset.get("form_code") or form_id),
    ).strip("-.") or "official-form"
    return FileResponse(
        candidate,
        media_type=mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
        filename=f"{safe_stem}{candidate.suffix.casefold()}",
    )
