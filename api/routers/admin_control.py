"""Admin Control Center aggregation API.

Read models stay thin and use existing domain routers/services as the source of
truth. Sensitive detail/export/action endpoints require a business reason and
write an append-only audit event before disclosure or mutation.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id
from api.legal_profile_access import assert_legal_case_access
from api.user_service import get_user_profile, list_audit_logs, list_users_with_profiles, write_audit_log
from api.routers import live_support
from api.routers import faq as faq_router
from api.routers import ward_procedures
from api.legal_crawl_service import LegalCrawlService
from api.observability import telemetry
from api.data_paths import notebook_data_dir
from api.retention_service import run_retention_purge, upcoming_expiration_report
from open_notebook.database.repository import repo_query

router = APIRouter(prefix="/admin/control", tags=["admin-control"])
ROOT = Path(__file__).resolve().parents[2]
LEGAL_CASES_DIR = ROOT / "data" / "legal_cases"
FORMS_DIR = notebook_data_dir() / "forms"


class ReasonRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class CandidateActionRequest(ReasonRequest):
    decision: Literal["approved", "rejected", "imported"]
    review_note: str = Field(default="", max_length=2000)


class RetentionRunRequest(ReasonRequest):
    """Only policy-controlled execution options are accepted; timestamps are never client-controlled."""
    dry_run: bool = True


async def _require_admin(request: Request) -> str:
    if get_request_role(request) != "admin":
        raise HTTPException(status_code=403, detail="Ch? admin c? quy?n truy c?p Control Center.")
    return str(get_request_user_id(request) or "")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _reason(value: str | None) -> str:
    reason = (value or "").strip()
    if len(reason) < 3:
        raise HTTPException(status_code=400, detail="C?n nh?p l? do nghi?p v? cho thao t?c n?y.")
    return reason


async def _audit(request: Request, *, action: str, resource_type: str, resource_id: str, reason: str, details: dict[str, Any] | None = None) -> None:
    try:
        await write_audit_log(
            action=action,
            entity_type=resource_type,
            entity_id=resource_id,
            actor_user_id=get_request_user_id(request),
            actor_role="admin",
            details={"reason": reason, **(details or {})},
            request=request,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Không thể ghi audit nên chưa thể thực hiện thao tác.") from exc


def _ticket_record(ticket: dict[str, Any]) -> dict[str, Any]:
    created = str(ticket.get("created_at") or "")
    updated = str(ticket.get("updated_at") or "")
    age_minutes = None
    try:
        age_minutes = int((_now() - datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds() // 60)
    except (TypeError, ValueError):
        pass
    return {
        "id": str(ticket.get("id") or ""),
        "domain": ticket.get("domain"),
        "status": live_support._normalize_status(ticket.get("status")),
        "priority": ticket.get("priority") or "normal",
        "assigned_officer_id": ticket.get("assigned_officer_id"),
        "created_at": created,
        "updated_at": updated,
        "age_minutes": age_minutes,
        "sla_overdue": bool(age_minutes is not None and age_minutes > 60 and live_support._normalize_status(ticket.get("status")) in {"waiting", "assigned", "active"}),
        "rating": ticket.get("rating"),
        "message_count": len(ticket.get("messages") or []),
    }


def _load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else fallback
    except (OSError, json.JSONDecodeError):
        return fallback


def _legal_case_metadata() -> list[dict[str, Any]]:
    if not LEGAL_CASES_DIR.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in LEGAL_CASES_DIR.glob("*.json"):
        payload = _load_json(path, {})
        if not isinstance(payload, dict):
            continue
        result.append({
            "id": str(payload.get("id") or path.stem),
            "owner_user_id": str(payload.get("owner_user_id") or ""),
            "assigned_officer_id": payload.get("assigned_officer_id"),
            "status": payload.get("status") or "open",
            "closed_at": payload.get("closed_at"),
            "updated_at": payload.get("updated_at") or payload.get("created_at"),
        })
    return sorted(result, key=lambda item: str(item.get("updated_at") or ""), reverse=True)


@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    await _require_admin(request)
    tickets = live_support._list_all_tickets()
    ticket_rows = [_ticket_record(ticket) for ticket in tickets]
    statuses = Counter(item["status"] for item in ticket_rows)
    users = await list_users_with_profiles()
    candidate_rows = await LegalCrawlService.list_candidates(limit=500)
    candidate_by_status = Counter(str(item.get("status") or item.get("review_status") or "unknown") for item in candidate_rows)
    candidates = {"total": len(candidate_rows), "by_status": dict(candidate_by_status)}
    forms_status = _load_json(FORMS_DIR / "forms_inventory_report.json", {})
    faq_payload = faq_router._load_faq_data()
    faq_rows = faq_payload.get("faqs") or []
    cases = _legal_case_metadata()
    try:
        insufficient = await repo_query("SELECT count() AS count FROM user_ask_history WHERE grounding_status = 'insufficient_evidence' GROUP ALL;")
        insufficient_count = int((insufficient or [{}])[0].get("count") or 0)
    except Exception:
        insufficient_count = 0
    return {
        "generated_at": _now().isoformat(),
        "users": {"total": len(users), "officer": sum(item.get("role") == "officer" for item in users), "online": 0},
        "live_support": {**dict(statuses), "total": len(ticket_rows), "sla_overdue": sum(item["sla_overdue"] for item in ticket_rows)},
        "legal_cases": {"total": len(cases), "open": sum(item["status"] != "closed" for item in cases)},
        "knowledge": {
            "candidate_total": candidates.get("total", 0),
            "candidate_by_status": candidates.get("by_status", {}),
            "forms_total": forms_status.get("total_forms", 0),
            "forms_without_file": forms_status.get("forms_without_file", 0),
            "faq_total": len(faq_rows),
            "faq_approved": sum(item.get("review_status") == "approved" for item in faq_rows),
        },
        "quality": {"insufficient_evidence": insufficient_count},
        "retention": (await upcoming_expiration_report()) ["counts"],
    }


@router.get("/retention/upcoming")
async def retention_upcoming(
    request: Request,
    within_days: int = Query(default=30, ge=1, le=180),
) -> dict[str, Any]:
    """Metadata-only expiration report. This endpoint never discloses message/case contents."""
    await _require_admin(request)
    return await upcoming_expiration_report(within_days=within_days)


@router.post("/retention/run")
async def run_retention(
    body: RetentionRunRequest,
    request: Request,
) -> dict[str, Any]:
    """Run the deterministic retention policy; client input cannot set any retention date."""
    await _require_admin(request)
    reason = _reason(body.reason)
    await _audit(
        request,
        action="admin.retention.run",
        resource_type="retention_job",
        resource_id="scheduled_policy",
        reason=reason,
        details={"dry_run": body.dry_run},
    )
    return await run_retention_purge(dry_run=body.dry_run)


@router.get("/users")
async def users_overview(request: Request) -> list[dict[str, Any]]:
    await _require_admin(request)
    users = await list_users_with_profiles()
    result = []
    for user in users:
        profile = user.get("profile") or {}
        result.append({
            "id": user.get("id"), "username": user.get("username"), "email": user.get("email"),
            "role": user.get("role"), "is_active": user.get("is_active"), "last_login_at": user.get("last_login_at"),
            "department": profile.get("department"), "allowed_domains": profile.get("allowed_domains") or [],
            "ward_scope": profile.get("ward_scope"), "must_change_password": profile.get("must_change_password", False),
            "online": live_support.hub.is_user_online(str(user.get("id") or "")),
        })
    return result


@router.get("/live-support")
async def live_support_overview(request: Request, status: str | None = None, only_overdue: bool = False) -> list[dict[str, Any]]:
    await _require_admin(request)
    rows = [_ticket_record(ticket) for ticket in live_support._list_all_tickets()]
    if status:
        rows = [item for item in rows if item["status"] == status]
    if only_overdue:
        rows = [item for item in rows if item["sla_overdue"]]
    return rows


@router.get("/live-support/{ticket_id}")
async def support_detail(ticket_id: str, request: Request, reason: str = Query(min_length=3)) -> dict[str, Any]:
    await _require_admin(request)
    value = _reason(reason)
    ticket = live_support._load_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên hỗ trợ.")
    await _audit(request, action="admin.support.detail.view", resource_type="support_session", resource_id=ticket_id, reason=value)
    return ticket


@router.get("/legal-cases")
async def legal_cases_overview(request: Request) -> list[dict[str, Any]]:
    await _require_admin(request)
    return _legal_case_metadata()


@router.get("/legal-cases/{case_id}")
async def legal_case_detail(case_id: str, request: Request, reason: str = Query(min_length=3)) -> dict[str, Any]:
    """Admin-only view with audit. Access is enforced via centralized policy."""
    await _require_admin(request)
    value = _reason(reason)
    # Centralized access check enforces ownership/assignment rules
    await assert_legal_case_access(case_id, request, action="view")
    path = LEGAL_CASES_DIR / f"{case_id}.json"
    payload = _load_json(path, None)
    if not payload:
        raise HTTPException(status_code=404, detail="Không tìm thấy hồ sơ pháp lý.")
    await _audit(request, action="admin.legal_case.detail.view", resource_type="legal_case", resource_id=case_id, reason=value)
    return payload


@router.get("/knowledge")
async def knowledge_overview(request: Request) -> dict[str, Any]:
    await _require_admin(request)
    candidates = await LegalCrawlService.list_candidates(limit=100)
    form_candidates = ward_procedures._load_classified_candidates()
    faq_payload = faq_router._load_faq_data()
    inventory = _load_json(FORMS_DIR / "forms_inventory_report.json", {})
    by_status = Counter(str(item.get("status") or item.get("review_status") or "unknown") for item in candidates)
    ocr_fail = sum(1 for item in candidates if str((item.get("raw_metadata") or {}).get("ocr_status") or "").lower() in {"failed", "error"})
    return {
        "candidates": candidates[:100],
        "candidate_status": dict(by_status),
        "forms": {"summary": inventory, "pending": (form_candidates.get("summary") or {}).get("pending_count", 0)},
        "faqs": {"total": len(faq_payload.get("faqs") or []), "items": faq_payload.get("faqs") or []},
        "ocr_failures": ocr_fail,
    }


@router.post("/knowledge/candidates/{candidate_id}/review")
async def review_candidate(candidate_id: str, body: CandidateActionRequest, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    reason = _reason(body.reason)
    candidate = await LegalCrawlService.review_candidate(
        candidate_id, body.decision, body.review_note or reason,
        reviewed_by=get_request_user_id(request), reviewed_role="admin",
    )
    await _audit(
        request, action="admin.knowledge.candidate.review", resource_type="legal_crawl_candidate",
        resource_id=candidate_id, reason=reason, details={"decision": body.decision, "review_note": body.review_note},
    )
    return {"candidate": candidate}


@router.get("/quality")
async def quality_overview(request: Request) -> dict[str, Any]:
    await _require_admin(request)
    from api.routers.legal_quality import quality_summary
    history = await repo_query("SELECT * FROM user_ask_history ORDER BY created DESC LIMIT 500;")
    rows = []
    dead_citations = 0
    for item in history or []:
        if item.get("grounding_status") == "insufficient_evidence":
            rows.append({"id": str(item.get("id")), "question": item.get("question"), "domain": item.get("domain"), "created": str(item.get("created") or "")})
        for source in item.get("sources") or []:
            try:
                source_data = json.loads(source) if isinstance(source, str) else source
            except json.JSONDecodeError:
                source_data = {}
            url = str((source_data or {}).get("source_url") or "")
            if not url or "vbpq-toanvan.aspx?itemid=" in url.lower():
                dead_citations += 1
    inventory = _load_json(FORMS_DIR / "forms_inventory_report.json", {})
    tickets = live_support._list_all_tickets()
    feedback = [
        {"ticket_id": item.get("id"), "rating": item.get("rating"), "feedback": item.get("feedback")}
        for item in tickets if item.get("rating") is not None
    ]
    return {
        "insufficient": rows,
        "citation_dead": dead_citations,
        "form_broken": int(inventory.get("broken_download_url") or 0),
        "forms_without_file": int(inventory.get("forms_without_file") or 0),
        "ocr_fail": sum(1 for item in (await LegalCrawlService.list_candidates(limit=500)) if str((item.get("raw_metadata") or {}).get("ocr_status") or "").lower() in {"failed", "error"}),
        "feedback": feedback,
        "legal_quality": await quality_summary(),
        "runtime": telemetry.summary(),
    }


@router.get("/metrics")
async def runtime_metrics(request: Request) -> dict[str, Any]:
    """Admin-only, content-free performance and error summary for pilot ops."""
    await _require_admin(request)
    return telemetry.summary()


@router.get("/audit")
async def audit_overview(
    request: Request,
    actor_role: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    action: str | None = None,
    reason: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict[str, Any]]:
    await _require_admin(request)
    return await list_audit_logs(
        limit=limit, actor_role=actor_role, action=action, resource_type=resource_type,
        resource_id=resource_id, reason=reason, date_from=date_from, date_to=date_to,
    )
