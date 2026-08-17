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
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id, get_request_username
from api.form_governance_models import ActorContext, FormReviewSubmission, FormWorkflowStatus
from api.form_governance_service import FormGovernanceService, get_form_governance_service
from api.form_governance_release_validator import HttpFormSourceVerifier
from api.user_service import write_audit_log


router = APIRouter(prefix="/procedures/forms-catalog", tags=["procedure-forms-catalog"])
ROOT = Path(__file__).resolve().parents[2]
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
PROCEDURE_COMPATIBILITY_MANIFEST = (
    ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-30.json"
)


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
    if not PROCEDURE_COMPATIBILITY_MANIFEST.is_file():
        raise RuntimeError("FORM_PROCEDURE_SCOPE_UNAVAILABLE")
    payload = json.loads(PROCEDURE_COMPATIBILITY_MANIFEST.read_text(encoding="utf-8"))
    return tuple(
        {
            "procedure_id": str(item.get("procedure_id") or "").strip(),
            "procedure_code": str(item.get("procedure_id") or "").strip(),
            "name": str(item.get("procedure_name") or "").strip(),
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
            })
    if active:
        return active, "active_release"
    return list(_compatibility_procedure_candidates()), "read_only_compatibility"


async def actor_context(request: Request) -> ActorContext:
    role = get_request_role(request)
    user_id = get_request_user_id(request) or get_request_username(request)
    if not user_id:
        raise HTTPException(status_code=403, detail="Authenticated user identity required")
    domains: list[str] = []
    if role == "officer":
        from api.user_service import get_user_profile
        profile = await get_user_profile(user_id)
        if profile:
            domains = list(profile.get("allowed_domains") or (profile.get("profile") or {}).get("allowed_domains") or [])
    return ActorContext(user_id=user_id, role=role, domains=domains)


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
    limit: int = Query(default=20, ge=1, le=100),
    actor: ActorContext = Depends(actor_context),
    service: FormGovernanceService = Depends(get_form_governance_service),
):
    if actor.role not in {"officer", "admin"}:
        raise HTTPException(status_code=403, detail="FORM_CASE_FORBIDDEN")
    if actor.role == "officer" and domain and domain not in actor.domains:
        raise HTTPException(status_code=403, detail="FORM_DOMAIN_FORBIDDEN")

    candidates, source = _call(_procedure_candidates, service)
    allowed_domains = set(actor.domains) if actor.role == "officer" else None
    folded_query = _fold_search(q)
    filtered: list[dict[str, Any]] = []
    for item in candidates:
        item_domain = str(item.get("domain") or "")
        if allowed_domains is not None and item_domain not in allowed_domains:
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
    return _call(service.transition, actor, case_id, FormWorkflowStatus.REJECTED, reason=payload.reason)


@router.put("/review-cases/{case_id}/legal-metadata")
async def legal_metadata(case_id: str, payload: LegalMetadataRequest, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.enrich, actor, case_id, payload.model_dump(mode="json"))


@router.post("/review-cases/{case_id}/ready-for-attestation")
async def ready_for_attestation(case_id: str, actor: ActorContext = Depends(actor_context), service: FormGovernanceService = Depends(get_form_governance_service)):
    return _call(service.ready_for_attestation, actor, case_id)


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
    release = _call(service.repository.active_release)
    if not release: raise HTTPException(status_code=404, detail="FORM_RELEASE_NOT_FOUND")
    return {key: release.get(key) for key in ("release_id", "version", "legal_as_of", "manifest_sha256", "status")}


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
