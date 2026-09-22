"""Admin Control Center aggregation API.

Read models stay thin and use existing domain routers/services as the source of
truth. Sensitive detail/export/action endpoints require a business reason and
write an append-only audit event before disclosure or mutation.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from api import readiness
from api.admin_config_history import (
    get_config_revision,
    list_config_revisions,
    record_config_revision,
)
from api.admin_operations_service import project_operational_alerts
from api.auth import get_request_role, get_request_user_id
from api.data_paths import notebook_data_dir
from api.legal_crawl_service import LegalCrawlService
from api.legal_effectivity_service import (
    latest_effectivity_report,
    run_effectivity_check,
)
from api.legal_profile_access import assert_legal_case_access
from api.legal_serving_dashboard import load_active_serving_release
from api.observability import telemetry
from api.retention_service import run_retention_purge, upcoming_expiration_report
from api.routers import faq as faq_router
from api.routers import live_support, ward_procedures
from api.support_repository import SupportActor
from api.system_settings import active_organization_units, normalize_legal_domains
from api.user_service import (
    get_user_profile,
    list_audit_logs,
    list_users_with_profiles,
    write_audit_log,
)
from open_notebook.ai.models import DefaultModels
from open_notebook.database.repository import repo_query
from open_notebook.domain.content_settings import ContentSettings

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


class DashboardSection(BaseModel):
    """Common availability envelope for every independently loaded panel."""

    model_config = ConfigDict(extra="allow")

    status: str
    observed_at: str | None = None
    reason_code: str | None = None


class DashboardFreshness(BaseModel):
    generated_at: str
    cache_ttl_seconds: int
    cached: bool = False
    refreshing: bool = False
    stale: bool = False
    age_seconds: float | None = None


class DashboardAttentionItem(BaseModel):
    code: str
    severity: Literal["critical", "warning", "info"]
    title: str
    description: str
    count: int
    href: str | None = None
    observed_at: str


class AdminDashboardResponse(BaseModel):
    """Stable, metadata-only contract for the Admin operations dashboard."""

    model_config = ConfigDict(extra="allow")

    observed_at: str
    freshness: DashboardFreshness
    attention_items: list[DashboardAttentionItem]
    operational_alerts: list[dict[str, Any]] = Field(default_factory=list)
    health: DashboardSection
    legal_repository: DashboardSection
    crawl_import: DashboardSection
    knowledge: DashboardSection
    users: DashboardSection
    organization: DashboardSection = Field(
        default_factory=lambda: DashboardSection(
            status="unavailable",
            reason_code="not_loaded",
        )
    )
    support: DashboardSection
    models: DashboardSection
    runtime_metrics: DashboardSection
    audit_7d: DashboardSection
    recent_audit: list[dict[str, Any]] | DashboardSection
    # Compatibility fields used by the first dashboard client.
    legal_cases: dict[str, Any]
    metrics: dict[str, Any]


DASHBOARD_CACHE_TTL_SECONDS = 300
# The repository client is intentionally serialized.  Keep each optional panel
# bounded so a slow projection cannot hold the connection for tens of seconds
# while the dashboard is warming.
DASHBOARD_SECTION_TIMEOUT_SECONDS = 1.0
LEGAL_REPOSITORY_SECTION_TIMEOUT_SECONDS = 8.0
# Settings and the active procedure release are compact, but a cold local
# settings repository can take a little longer than the operational panels.
# They are still bounded independently and never block a browser request.
DASHBOARD_METADATA_SECTION_TIMEOUT_SECONDS = 3.0
_dashboard_cache: dict[str, Any] | None = None
_dashboard_cache_created_at = 0.0
_dashboard_cache_lock = asyncio.Lock()
_dashboard_refresh_task: asyncio.Task[None] | None = None
_dashboard_refresh_last_attempt = 0.0
_dashboard_timezone = ZoneInfo("Asia/Ho_Chi_Minh")


def invalidate_dashboard_cache() -> None:
    """Mark the projection stale without making the next page load block."""

    global _dashboard_cache_created_at
    _dashboard_cache_created_at = 0.0


def _internal_admin_request() -> Request:
    """Build a content-free request for startup/background projections."""

    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/admin/control/dashboard",
        "headers": [],
    })
    request.state.user_role = "admin"
    request.state.user_id = "system:dashboard-projection"
    request.state.username = "system"
    return request


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


def _health_state(component: dict[str, Any], *, checked_at: str) -> dict[str, Any]:
    """Normalize readiness data without exposing URLs, credentials or exceptions."""
    healthy = bool(component.get("healthy"))
    code = str(component.get("code") or "unknown")[:80]
    if healthy:
        status = "healthy"
    elif code in {"disabled", "not_started", "stale", "configured", "missing_report"}:
        status = "degraded"
    else:
        status = "unavailable"
    return {"status": status, "code": code, "checked_at": checked_at}


async def _system_health() -> dict[str, Any]:
    checked_at = _now().isoformat()
    database, legal_retrieval = await asyncio.gather(
        readiness.check_database(), readiness.check_legal_retrieval()
    )
    crawler_enabled = os.getenv("LEGAL_CRAWLER_ENABLED", "true").lower() not in {"false", "0", "no"}
    effectivity = latest_effectivity_report()
    components = {
        "api": {"status": "healthy", "code": "ready", "checked_at": checked_at},
        "ask_retrieval": _health_state(legal_retrieval, checked_at=checked_at),
        "import_worker": _health_state(readiness.import_worker_component(), checked_at=checked_at),
        "embedding": _health_state(legal_retrieval, checked_at=checked_at),
        "crawler": _health_state({"healthy": crawler_enabled, "code": "ready" if crawler_enabled else "disabled"}, checked_at=checked_at),
        "database": _health_state(database, checked_at=checked_at),
        "effectivity_monitor": _health_state({"healthy": bool(effectivity), "code": "ready" if effectivity else "missing_report"}, checked_at=checked_at),
    }
    return {"checked_at": checked_at, "components": components}


async def _model_configuration_summary() -> dict[str, Any]:
    """Return only operational model metadata; credentials are never selected."""

    credential_rows, model_rows, defaults = await asyncio.gather(
        repo_query(
            "SELECT id, name, provider, modalities FROM credential ORDER BY provider, name;"
        ),
        repo_query(
            "SELECT id, name, provider, type, credential FROM model ORDER BY type, provider, name;"
        ),
        DefaultModels.get_instance(),
    )
    by_type = Counter(str(row.get("type") or "unknown") for row in model_rows)
    credential_providers = sorted(
        {
            str(row.get("provider") or "").strip()
            for row in credential_rows
            if str(row.get("provider") or "").strip()
        }
    )
    provider_set = set(credential_providers)
    env_provider_vars = {
        "openai": ("OPENAI_API_KEY",), "anthropic": ("ANTHROPIC_API_KEY",), "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
        "deepseek": ("DEEPSEEK_API_KEY",), "openrouter": ("OPENROUTER_API_KEY",), "ollama": ("OLLAMA_API_BASE",),
        "mistral": ("MISTRAL_API_KEY",), "groq": ("GROQ_API_KEY",), "voyage": ("VOYAGE_API_KEY",),
        "xai": ("XAI_API_KEY",), "dashscope": ("DASHSCOPE_API_KEY",), "minimax": ("MINIMAX_API_KEY",),
        "openai_compatible": ("OPENAI_COMPATIBLE_BASE_URL", "OPENAI_COMPATIBLE_API_KEY"),
    }
    provider_set.update(
        provider for provider, variables in env_provider_vars.items()
        if any(os.environ.get(variable) for variable in variables)
    )
    credential_providers = sorted(provider_set)
    model_by_id = {str(row.get("id")): row for row in model_rows if row.get("id")}
    default_fields = (
        "default_chat_model",
        "large_context_model",
        "default_embedding_model",
        "default_tools_model",
        "default_text_to_speech_model",
        "default_speech_to_text_model",
    )
    def resolved(model_id: Any, modality: str) -> dict[str, Any]:
        wanted = str(model_id or "").strip()
        row = model_by_id.get(wanted)
        provider = str((row or {}).get("provider") or "").strip() or None
        configured = bool(wanted and row)
        available = bool(configured and provider and provider in provider_set)
        return {
            "id": wanted or None,
            "display_name": str((row or {}).get("name") or "Chưa cấu hình") if configured else "Chưa cấu hình",
            "provider": provider,
            "modality": modality,
            "configured": configured,
            "available": available,
            "source": "database" if configured else "none",
        }

    chat = resolved(getattr(defaults, "default_chat_model", None), "chat")
    embedding = resolved(getattr(defaults, "default_embedding_model", None), "embedding")
    config_revision = None
    try:
        revisions = await list_config_revisions("model_defaults", limit=1)
        if revisions:
            latest = revisions[0]
            config_revision = str(latest.get("id") or latest.get("created_at") or "") or None
    except Exception:
        config_revision = None

    active_vector_embedding: dict[str, Any] = {
        "id": None,
        "display_name": "Chưa xác định",
        "provider": None,
        "modality": "embedding",
        "configured": False,
        "available": True,
        "source": "index_manifest",
        "fingerprint": None,
    }
    try:
        release = load_active_serving_release()
        payload = release.manifest.payload
        vector = payload.get("vector_index") if isinstance(payload.get("vector_index"), dict) else {}
        active_id = str(vector.get("embedding_model_id") or vector.get("model_id") or "").strip() or None
        active_vector_embedding["id"] = active_id
        fingerprint = str(
            vector.get("embedding_fingerprint")
            or payload.get("embedding_recipe_fingerprint")
            or payload.get("model_artifact_fingerprint")
            or ""
        ).strip() or None
        active_vector_embedding["fingerprint"] = fingerprint
        active_vector_embedding["display_name"] = str(vector.get("model") or vector.get("embedding_model") or "Đang phục vụ theo manifest")
        active_vector_embedding["configured"] = bool(fingerprint)
    except Exception:
        pass
    embedding_warning = bool(
        embedding.get("configured")
        and active_vector_embedding.get("id")
        and embedding.get("id") != active_vector_embedding.get("id")
    )
    return {
        "credential_count": len(credential_rows),
        "model_count": len(model_rows),
        "providers": credential_providers,
        "available_providers": credential_providers,
        "models_by_type": dict(by_type),
        "defaults": {
            field: getattr(defaults, field, None)
            for field in default_fields
        },
        "resolved_defaults": {"chat": chat, "embedding": embedding},
        "active_vector_embedding": active_vector_embedding,
        "embedding_index_warning": embedding_warning,
        "config_revision": config_revision,
        "observed_at": _now().isoformat(),
        "ready_for_answers": bool(chat["configured"] and embedding["configured"] and chat["available"]),
    }


def _dashboard_unavailable(reason_code: str = "read_failed") -> dict[str, Any]:
    """Keep dashboard partially useful without disclosing exception internals."""

    return {"status": "unavailable", "reason_code": reason_code}


def _dashboard_warming_snapshot() -> dict[str, Any]:
    """Minimal first paint while the metadata projection refreshes in background."""
    observed = _now().isoformat()
    section = {"status": "warming", "reason_code": "refresh_in_progress", "observed_at": observed}
    return {
        "observed_at": observed,
        "freshness": {"generated_at": observed, "cache_ttl_seconds": DASHBOARD_CACHE_TTL_SECONDS,
                       "cached": False, "refreshing": True, "stale": False, "age_seconds": 0},
        "attention_items": [], "operational_alerts": [],
        "health": section, "legal_repository": section, "crawl_import": section,
        "knowledge": section, "users": section, "organization": section,
        "support": section, "models": section, "runtime_metrics": section,
        "audit_7d": section, "recent_audit": section,
        "legal_cases": {"total": 0, "open": 0}, "metrics": {},
    }


async def _dashboard_read(
    operation: Any,
    *,
    timeout_seconds: float = DASHBOARD_SECTION_TIMEOUT_SECONDS,
) -> Any:
    try:
        return await asyncio.wait_for(operation, timeout=timeout_seconds)
    except TimeoutError:
        return _dashboard_unavailable("timeout")
    except Exception:
        return _dashboard_unavailable()


def _is_unavailable(value: Any) -> bool:
    return isinstance(value, dict) and value.get("status") == "unavailable"


def _dashboard_section(
    value: Any,
    *,
    observed_at: str,
    status: str = "available",
) -> dict[str, Any]:
    if _is_unavailable(value):
        return {"observed_at": observed_at, **value}
    payload = value if isinstance(value, dict) else {"value": value}
    return {"status": status, "observed_at": observed_at, **payload}


def _safe_users_summary(users: Any) -> dict[str, Any]:
    if _is_unavailable(users):
        return users
    rows = users if isinstance(users, list) else []
    role_counts = Counter(str(row.get("role") or "unknown") for row in rows)
    active_role_counts = Counter(str(row.get("role") or "unknown") for row in rows
                                 if row.get("is_active") is not False and not row.get("is_deleted"))
    domain_counts: Counter[str] = Counter()
    ward_counts: Counter[str] = Counter()
    organization_unit_counts: Counter[str] = Counter()
    officers_without_unit = 0
    for row in rows:
        if str(row.get("role") or "") != "officer" or row.get("is_active") is False or row.get("is_deleted"):
            continue
        profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
        unit_id = str(profile.get("organization_unit_id") or "").strip()
        if unit_id:
            organization_unit_counts[unit_id] += 1
        else:
            officers_without_unit += 1
        for domain in profile.get("allowed_domains") or []:
            if str(domain).strip():
                domain_counts[str(domain).strip()] += 1
        ward = str(profile.get("ward_scope") or profile.get("ward") or "").strip()
        if ward:
            ward_counts[ward] += 1
    deleted = sum(1 for row in rows if bool(row.get("is_deleted")))
    locked = sum(
        1
        for row in rows
        if row.get("is_active") is False and not bool(row.get("is_deleted"))
    )
    return {
        "total": len(rows),
        "active": sum(
            1
            for row in rows
            if row.get("is_active") is not False and not bool(row.get("is_deleted"))
        ),
        "locked": locked,
        "deleted": deleted,
        "must_change_password": sum(
            1
            for row in rows
            if bool((row.get("profile") or {}).get("must_change_password"))
            and not bool(row.get("is_deleted"))
        ),
        "by_role": dict(role_counts),
        "active_by_role": dict(active_role_counts),
        "officers_by_domain": dict(sorted(domain_counts.items())),
        "officers_by_ward": dict(sorted(ward_counts.items())),
        "officers_by_organization_unit": dict(
            sorted(organization_unit_counts.items())
        ),
        "officers_without_organization_unit": officers_without_unit,
    }


async def _organization_summary() -> dict[str, Any]:
    from api.system_settings import active_settings

    settings = await active_settings()
    units = await active_organization_units(settings)
    domains = normalize_legal_domains(getattr(settings, "legal_domains", None))
    return {
        "units": [
            {
                "id": unit.id,
                "code": unit.code,
                "name": unit.name,
                "short_name": unit.short_name,
                "is_active": unit.is_active,
                "support_enabled": unit.support_enabled,
            }
            for unit in units
        ],
        "domains": [
            {
                "code": domain.code,
                "name": domain.name,
                "is_active": domain.is_active,
                "sort_order": domain.sort_order,
            }
            for domain in domains
        ],
        # The expensive cutover-readiness query includes crawler candidates.
        # It is not a statement about whether the current legal corpus has a
        # responsible unit, so the dashboard derives that badge directly from
        # its corpus assignment totals instead of timing this whole panel out.
        "readiness": {"status": "corpus_assignment_reported_separately"},
        "ready_for_unit_primary": None,
    }


def _active_form_release_summary(release: Any) -> dict[str, Any]:
    """Project only active-release counts for the dashboard.

    Legacy FAQ JSON and the old file inventory are deliberately excluded: they
    are not the authority for the published Procedure/Form experience.
    """

    release_data = release if isinstance(release, dict) else {}
    manifest = release_data.get("manifest") if isinstance(release_data.get("manifest"), dict) else {}
    procedures = [item for item in manifest.get("procedures", []) if isinstance(item, dict)]
    assets = [item for item in manifest.get("assets", []) if isinstance(item, dict)]
    procedure_statuses = Counter(
        str(item.get("coverage_status") or "unknown") for item in procedures
    )
    asset_statuses = Counter(
        str(item.get("coverage_status") or "unknown") for item in assets
    )
    active = bool(release_data.get("release_id"))
    return {
        "active_release": {
            "id": release_data.get("release_id"),
            "version": release_data.get("version"),
            "status": release_data.get("status") if active else "missing",
        },
        "procedures": {
            "total": len(procedures),
            "released": int(procedure_statuses.get("released", 0)),
            "owner_deferred": int(procedure_statuses.get("owner_deferred", 0)),
            "verified_gap": int(procedure_statuses.get("verified_gap", 0)),
            "by_coverage_status": dict(procedure_statuses),
        },
        "forms": {
            "total": len(assets),
            "released": int(asset_statuses.get("released", 0)),
            "official": int(asset_statuses.get("released", 0)),
            "pending": 0,
            "by_coverage_status": dict(asset_statuses),
        },
        # Compatibility envelope only.  The dashboard no longer presents the
        # retired FAQ store as a published source.
        "faqs": {"total": 0, "by_status": {}},
        "ocr_failures": 0,
    }


async def _knowledge_summary() -> dict[str, Any]:
    """Return published Procedure/Form metrics from the active release only."""

    from api.form_governance_service import get_form_governance_service

    release = await asyncio.to_thread(
        get_form_governance_service().repository.active_release
    )
    return _active_form_release_summary(release)


async def _dashboard_crawl_summary() -> dict[str, Any]:
    """Small dashboard projection; avoid the 15-query crawler summary."""
    candidate_rows, job_rows = await asyncio.gather(
        repo_query("SELECT status, count() AS count FROM legal_crawl_candidate GROUP BY status;"),
        repo_query("SELECT status, count() AS count FROM legal_import_job GROUP BY status;"),
    )
    counts = {str(row.get("status") or "unknown"): int(row.get("count") or 0)
              for row in candidate_rows if isinstance(row, dict)}
    jobs = {str(row.get("status") or "unknown"): int(row.get("count") or 0)
            for row in job_rows if isinstance(row, dict)}
    pending = counts.get("pending", 0)
    imported = counts.get("imported", 0)
    return {
        "total_candidates": sum(counts.values()),
        "pending_candidates": pending,
        "pending_document_candidates": pending,
        "approved_candidates": counts.get("approved", 0),
        "imported_candidates": imported,
        "activated_candidates": 0,
        "unverified_imported_candidates": imported,
        "rejected_candidates": counts.get("rejected", 0),
        "needs_attention_candidates": sum(counts.get(key, 0) for key in ("changes_requested", "import_failed", "replacement_review")),
        "import_queue": {key: jobs.get(key, 0) for key in ("queued", "running", "failed", "completed")},
        "by_domain": {}, "by_source": {},
        "by_organization_unit": {},
        "by_assignment_state": {"assigned": 0, "shared": 0, "unassigned": 0},
    }


async def _support_summary() -> dict[str, Any]:
    repository = live_support._canonical_support_repository()
    if repository is not None:
        try:
            rows = await asyncio.to_thread(repository.admin_ticket_metadata, SupportActor(user_id="dashboard", role="admin"))
        except Exception:
            rows = []
    else:
        rows = [_ticket_record(ticket) for ticket in live_support._list_all_tickets()]
    by_organization_unit = Counter(
        str(row.get("primary_organization_unit_id") or "").strip()
        for row in rows
        if str(row.get("primary_organization_unit_id") or "").strip()
    )
    return {
        "total": len(rows),
        "waiting": sum(1 for row in rows if row["status"] in {"waiting", "queued"}),
        "active": sum(1 for row in rows if row["status"] in {"assigned", "active"}),
        "closed": sum(1 for row in rows if row["status"] == "closed"),
        "unassigned": sum(
            1
            for row in rows
            if row["status"] in {"waiting", "queued"} and not row.get("assigned_officer_id")
        ),
        "overdue": sum(1 for row in rows if row.get("sla_overdue", row.get("overdue", False))),
        "by_domain": dict(Counter(str(row.get("domain") or row.get("canonical_domain") or "unknown") for row in rows)),
        "by_organization_unit": dict(sorted(by_organization_unit.items())),
        "without_organization_unit": sum(
            1
            for row in rows
            if not str(row.get("primary_organization_unit_id") or "").strip()
        ),
    }


def _parse_dashboard_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _audit_action_group(action: Any) -> str:
    normalized = str(action or "").lower()
    if any(token in normalized for token in ("user", "account", "password", "role")):
        return "accounts"
    if any(token in normalized for token in ("legal", "crawl", "import", "form", "faq", "source")):
        return "legal_data"
    if any(token in normalized for token in ("credential", "model", "config", "setting")):
        return "configuration"
    if "support" in normalized or "ticket" in normalized:
        return "support"
    if any(token in normalized for token in ("auth", "session", "security", "totp")):
        return "security"
    return "other"


def _safe_audit_rows(audits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": row.get("id"),
            "action": row.get("action"),
            "resource_type": row.get("entity_type") or row.get("resource_type"),
            "actor_role": row.get("actor_role"),
            "created_at": row.get("created") or row.get("created_at"),
        }
        for row in audits[:10]
    ]


def _audit_seven_day_summary(audits: Any, *, observed_at: datetime) -> dict[str, Any]:
    if _is_unavailable(audits):
        return audits
    rows = audits if isinstance(audits, list) else []
    local_today = observed_at.astimezone(_dashboard_timezone).date()
    dates = [local_today - timedelta(days=offset) for offset in range(6, -1, -1)]
    daily = {day.isoformat(): 0 for day in dates}
    by_group: Counter[str] = Counter()
    for row in rows:
        parsed = _parse_dashboard_datetime(row.get("created"))
        if parsed is None:
            continue
        local_day = parsed.astimezone(_dashboard_timezone).date().isoformat()
        if local_day in daily:
            daily[local_day] += 1
            by_group[_audit_action_group(row.get("action"))] += 1
    return {
        "timezone": "Asia/Ho_Chi_Minh",
        "total": sum(daily.values()),
        "daily": [{"date": day, "count": count} for day, count in daily.items()],
        "by_group": dict(sorted(by_group.items())),
        "truncated": len(rows) >= 5001,
    }


def _number(record: Any, *keys: str) -> int:
    if not isinstance(record, dict):
        return 0
    for key in keys:
        value = record.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, int(value))
    return 0


def _build_attention_items(
    *,
    observed_at: str,
    health: dict[str, Any],
    legal: dict[str, Any],
    crawl: dict[str, Any],
    knowledge: dict[str, Any],
    users: dict[str, Any],
    support: dict[str, Any],
    models: dict[str, Any],
    organization: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def add(
        code: str,
        severity: Literal["critical", "warning", "info"],
        title: str,
        description: str,
        count: int,
        href: str | None,
    ) -> None:
        if count <= 0:
            return
        items.append({
            "code": code,
            "severity": severity,
            "title": title,
            "description": description,
            "count": count,
            "href": href,
            "observed_at": observed_at,
        })

    components = health.get("components") if isinstance(health, dict) else {}
    component_links = {
        "database": "/settings",
        "ask_retrieval": "/legal-management",
        "embedding": "/legal-management",
        "import_worker": "/legal-import?tab=import",
        "crawler": "/legal-import?tab=import",
        "effectivity_monitor": "/legal-management/validity",
    }
    component_labels = {
        "api": "Máy chủ ứng dụng",
        "database": "Cơ sở dữ liệu",
        "ask_retrieval": "Trợ lý hỏi đáp pháp luật",
        "embedding": "Kho tìm kiếm ngữ nghĩa",
        "import_worker": "Tiến trình nạp dữ liệu",
        "crawler": "Tiến trình thu thập văn bản",
        "effectivity_monitor": "Tiến trình theo dõi hiệu lực",
    }
    status_labels = {
        "available": "có thể sử dụng",
        "degraded": "hoạt động chưa ổn định",
        "unavailable": "đang gián đoạn",
        "unknown": "chưa xác định",
    }
    for name, component in (components or {}).items():
        status = str((component or {}).get("status") or "unavailable")
        if status == "healthy":
            continue
        severity: Literal["critical", "warning", "info"] = (
            "critical"
            if status == "unavailable" and name in {"database", "ask_retrieval", "embedding", "import_worker"}
            else "warning"
        )
        add(
            f"health.{name}.{status}", severity,
            f"{component_labels.get(name, 'Dịch vụ hệ thống')} cần kiểm tra",
            f"Trạng thái hiện tại: {status_labels.get(status, 'cần kiểm tra')}.",
            1,
            component_links.get(name),
        )

    queue = crawl.get("import_queue") if isinstance(crawl, dict) else {}
    add("import.failed", "critical", "Tác vụ nạp dữ liệu bị lỗi", "Mở hàng chờ để kiểm tra nguyên nhân đã làm sạch.", _number(queue, "failed"), "/legal-import?tab=proposals&status=needs_attention")
    add("import.running", "info", "Tác vụ nạp dữ liệu đang chạy", "Theo dõi tiến độ chuẩn hóa và lập chỉ mục tìm kiếm.", _number(queue, "running") + _number(queue, "queued"), "/legal-import?tab=proposals&status=import_queued")
    add("candidate.pending", "warning", "Đề xuất văn bản chờ duyệt", "Đề xuất chưa được dùng cho trả lời trước khi Admin duyệt và kích hoạt.", _number(crawl, "pending_document_candidates", "pending_candidates"), "/legal-import?tab=proposals&status=pending")
    add("candidate.unverified_import", "warning", "Bản nhập chưa xác nhận kích hoạt", "Kiểm tra văn bản, các đoạn nội dung và trạng thái kích hoạt trước khi đưa vào tra cứu.", _number(crawl, "unverified_imported_candidates"), "/legal-import?tab=proposals&status=imported")

    documents = legal.get("documents") if isinstance(legal, dict) else {}
    structure = legal.get("structure") if isinstance(legal, dict) else {}
    add("legal.missing_source", "warning", "Văn bản thiếu nguồn chính thức", "Bổ sung và xác minh URL nguồn trước khi dùng làm căn cứ.", _number(documents, "missing_source", "missing_sources", "missing_source_count"), "/legal-management?data_quality=missing_source")
    add("legal.missing_metadata", "warning", "Văn bản thiếu thông tin nhận diện", "Kiểm tra số hiệu, cơ quan ban hành và mốc hiệu lực.", _number(documents, "missing_metadata", "missing_metadata_count"), "/legal-management?data_quality=missing_metadata")
    add("legal.zero_chunks", "critical", "Văn bản chưa được chia đoạn", "Văn bản chưa thể tra cứu khi nội dung chưa được chia thành các đoạn hợp lệ.", _number(documents, "zero_chunks", "zero_chunk_documents") + _number(structure, "zero_chunks", "zero_chunk_documents"), "/legal-management?data_quality=zero_chunks")
    add("legal.unknown_validity", "warning", "Văn bản chưa rõ hiệu lực", "Đối chiếu nguồn chính thức trước khi kích hoạt cho câu trả lời.", _number(documents, "unknown", "unknown_status", "unknown_validity"), "/legal-management?validity_status=unknown")

    forms = knowledge.get("forms") if isinstance(knowledge, dict) else {}
    faqs = knowledge.get("faqs") if isinstance(knowledge, dict) else {}
    faq_status = faqs.get("by_status") if isinstance(faqs, dict) else {}
    add("forms.pending", "warning", "Biểu mẫu chờ duyệt", "Xác minh tệp và nguồn chính thức của biểu mẫu.", _number(forms, "pending"), "/legal-import?tab=forms&form_status=candidate_pending_review")
    add("forms.missing_files", "warning", "Biểu mẫu thiếu tệp vật lý", "Danh mục có metadata nhưng chưa có tệp chính thức hợp lệ.", _number(forms, "missing_files"), "/legal-import?tab=forms")
    add("knowledge.ocr_failures", "warning", "Không đọc được nội dung tệp", "Kiểm tra lại tệp nguồn hoặc công cụ đọc tài liệu.", _number(knowledge, "ocr_failures"), "/legal-import?tab=proposals&status=needs_attention")
    add("faq.pending", "info", "Câu hỏi thường gặp chưa được duyệt", "Nội dung nháp chưa được hiển thị như hướng dẫn đã xác minh.", _number(faq_status, "draft", "pending"), "/faq-management")

    add("users.locked", "warning", "Tài khoản đang bị khóa", "Rà soát trạng thái và lý do khóa tài khoản.", _number(users, "locked"), "/users?status=locked")
    add("users.must_change_password", "info", "Tài khoản cần đổi mật khẩu", "Tài khoản chưa thể sử dụng nghiệp vụ trước khi đổi mật khẩu tạm.", _number(users, "must_change_password"), "/users?status=password-change")
    readiness = organization.get("readiness") if isinstance(organization, dict) else {}
    readiness_blockers = readiness.get("blocking_issue_count") if readiness else None
    if readiness_blockers is None:
        readiness_blockers = sum(
            int(value)
            for key, value in (readiness or {}).items()
            if key not in {"ready_for_unit_primary", "pending_candidates_without_assignment"}
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
    add(
        "organization.cutover_not_ready",
        "warning",
        "Dữ liệu phòng ban chưa sẵn sàng",
        "Mở cài đặt để xử lý các bản ghi chưa phân công trước khi chuyển chế độ vận hành.",
        readiness_blockers,
        "/settings",
    )
    add("support.overdue", "critical", "Phiên hỗ trợ quá SLA", "Cần điều phối cán bộ đúng lĩnh vực; Dashboard không hiển thị nội dung trao đổi.", _number(support, "overdue"), "/admin/support?sla=overdue")
    add("support.unassigned", "warning", "Phiên hỗ trợ chưa có cán bộ", "Kiểm tra phân công cán bộ và phạm vi lĩnh vực.", _number(support, "unassigned"), None)
    if isinstance(models, dict) and not _is_unavailable(models) and models.get("ready_for_answers") is False:
        add("models.not_ready", "critical", "Cấu hình AI chưa sẵn sàng", "Cần có model chat và embedding mặc định.", 1, "/settings/api-keys")

    for section_name, section, href in (
        ("legal_repository", legal, "/legal-management"),
        ("crawl_import", crawl, "/legal-import"),
        ("knowledge", knowledge, "/faq-management"),
        ("users", users, "/users"),
        ("models", models, "/settings/api-keys"),
    ):
        if _is_unavailable(section):
            add(f"dashboard.{section_name}.unavailable", "warning", f"Không đọc được {section_name}", "Dashboard giữ các khu vực còn lại và không thay lỗi bằng số 0.", 1, href)

    order = {"critical": 0, "warning": 1, "info": 2}
    return sorted(items, key=lambda item: (order[item["severity"]], item["code"]))


async def _build_dashboard_snapshot(
    request: Request,
    *,
    section_timeout_seconds: float = DASHBOARD_SECTION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    # Local import avoids changing router initialization order.
    from api.routers import legal_search as legal_search_router

    observed_dt = _now()
    observed_at = observed_dt.isoformat()
    audit_start = (
        observed_dt.astimezone(_dashboard_timezone).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=6)
    ).astimezone(timezone.utc).isoformat()
    # The Surreal client is guarded by one operation lock.  Launching these
    # reads with gather() only queues eight cancellations behind the first
    # query, which both loses data and prolongs the lock.  Keep the projection
    # bounded but read it in a deterministic sequence instead.
    operations = (
        (_system_health(), section_timeout_seconds),
        (
            legal_search_router.legal_management_summary(request),
            max(section_timeout_seconds, LEGAL_REPOSITORY_SECTION_TIMEOUT_SECONDS),
        ),
        (_dashboard_crawl_summary(), section_timeout_seconds),
        (
            _knowledge_summary(),
            max(section_timeout_seconds, DASHBOARD_METADATA_SECTION_TIMEOUT_SECONDS),
        ),
        (list_users_with_profiles(), section_timeout_seconds),
        (
            _organization_summary(),
            max(section_timeout_seconds, DASHBOARD_METADATA_SECTION_TIMEOUT_SECONDS),
        ),
        (_support_summary(), section_timeout_seconds),
        (_model_configuration_summary(), section_timeout_seconds),
        (list_audit_logs(limit=500, date_from=audit_start), section_timeout_seconds),
    )
    results: list[Any] = []
    for operation, timeout_seconds in operations:
        results.append(
            await _dashboard_read(
                operation,
                timeout_seconds=timeout_seconds,
            )
        )
    health_raw, legal_raw, crawl_raw, knowledge_raw, users_raw, organization_raw, support_raw, models_raw, audits_raw = results

    health_status = "available"
    if not _is_unavailable(health_raw):
        component_statuses = {
            str(item.get("status") or "unavailable")
            for item in (health_raw.get("components") or {}).values()
            if isinstance(item, dict)
        }
        health_status = "healthy" if component_statuses <= {"healthy"} else (
            "unavailable" if "unavailable" in component_statuses else "degraded"
        )
    health = _dashboard_section(health_raw, observed_at=observed_at, status=health_status)
    legal = _dashboard_section(legal_raw, observed_at=observed_at)
    crawl = _dashboard_section(crawl_raw, observed_at=observed_at)
    knowledge = _dashboard_section(knowledge_raw, observed_at=observed_at)
    users = _dashboard_section(_safe_users_summary(users_raw), observed_at=observed_at)
    organization = _dashboard_section(organization_raw, observed_at=observed_at)
    support = _dashboard_section(support_raw, observed_at=observed_at)
    models = _dashboard_section(models_raw, observed_at=observed_at)
    metrics_raw = telemetry.summary()
    runtime_metrics = _dashboard_section(metrics_raw, observed_at=observed_at)
    audit_7d = _dashboard_section(
        _audit_seven_day_summary(audits_raw, observed_at=observed_dt),
        observed_at=observed_at,
    )
    safe_audits: Any = (
        _dashboard_section(audits_raw, observed_at=observed_at)
        if _is_unavailable(audits_raw)
        else _safe_audit_rows(audits_raw)
    )
    cases = _legal_case_metadata()
    attention_items = _build_attention_items(
        observed_at=observed_at,
        health=health,
        legal=legal,
        crawl=crawl,
        knowledge=knowledge,
        users=users,
        organization=organization,
        support=support,
        models=models,
    )
    return {
        "observed_at": observed_at,
        "freshness": {
            "generated_at": observed_at,
            "cache_ttl_seconds": DASHBOARD_CACHE_TTL_SECONDS,
            "cached": False,
            "refreshing": False,
            "stale": False,
            "age_seconds": 0.0,
        },
        "attention_items": attention_items,
        "operational_alerts": project_operational_alerts(attention_items),
        "health": health,
        "legal_repository": legal,
        "crawl_import": crawl,
        "knowledge": knowledge,
        "users": users,
        "organization": organization,
        "support": support,
        "models": models,
        "runtime_metrics": runtime_metrics,
        "audit_7d": audit_7d,
        "recent_audit": safe_audits,
        "legal_cases": {
            "total": len(cases),
            "open": sum(1 for row in cases if row.get("status") != "closed"),
        },
        # Kept for the first dashboard client; new clients use runtime_metrics.
        "metrics": metrics_raw,
    }


async def _refresh_dashboard_cache(
    *,
    section_timeout_seconds: float = 1.0,
) -> None:
    """Refresh the projection without delaying a normal dashboard read."""

    global _dashboard_cache, _dashboard_cache_created_at
    async with _dashboard_cache_lock:
        snapshot = await _build_dashboard_snapshot(
            _internal_admin_request(),
            section_timeout_seconds=section_timeout_seconds,
        )
        snapshot = _preserve_last_good_dashboard_sections(
            previous=_dashboard_cache,
            current=snapshot,
        )
        _dashboard_cache = deepcopy(snapshot)
        _dashboard_cache_created_at = monotonic()


def _dashboard_refresh_done(task: asyncio.Task[None]) -> None:
    global _dashboard_refresh_task
    _dashboard_refresh_task = None
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        # The stale projection remains valid. Errors are intentionally not
        # exposed to the dashboard client and the next poll can retry.
        pass


async def _delayed_dashboard_refresh() -> None:
    # Give the first page paint and adjacent read-only requests a chance to
    # complete before the aggregate projection starts using the serialized DB
    # connection.
    await asyncio.sleep(0.35)
    await _refresh_dashboard_cache()


def _schedule_dashboard_refresh(*, force: bool = False) -> None:
    """Start one background projection without ever delaying a dashboard read.

    A manual refresh is a request for newer data, not permission to make the
    admin's browser wait for every serialized database/read-model operation.
    It may bypass the ordinary retry cooldown, but it still joins an already
    running refresh rather than creating a second competing projection.
    """

    global _dashboard_refresh_task, _dashboard_refresh_last_attempt
    if _dashboard_refresh_task is not None and not _dashboard_refresh_task.done():
        return
    now = monotonic()
    if not force and now - _dashboard_refresh_last_attempt < 30.0:
        return
    _dashboard_refresh_last_attempt = now
    _dashboard_refresh_task = asyncio.create_task(_delayed_dashboard_refresh())
    _dashboard_refresh_task.add_done_callback(_dashboard_refresh_done)


def _dashboard_cached_response(*, force_stale: bool = False) -> dict[str, Any]:
    """Return the last coherent snapshot while a newer one is prepared."""

    cached = deepcopy(_dashboard_cache or {})
    cache_age = max(0.0, monotonic() - _dashboard_cache_created_at)
    refreshing = (
        _dashboard_refresh_task is not None
        and not _dashboard_refresh_task.done()
    )
    cached.setdefault("freshness", {})
    cached["freshness"].update(
        {
            "cached": True,
            "refreshing": refreshing,
            # Keep polling if a manual refresh is in flight even when the
            # existing cached projection is younger than its normal TTL.
            "stale": bool(force_stale or refreshing or cache_age >= DASHBOARD_CACHE_TTL_SECONDS),
            "age_seconds": round(cache_age, 3),
        }
    )
    return cached


async def prewarm_admin_dashboard_cache() -> dict[str, Any]:
    """Build the first complete projection before the frontend is declared ready."""

    await _refresh_dashboard_cache(section_timeout_seconds=30.0)
    return deepcopy(_dashboard_cache or {})


def _preserve_last_good_dashboard_sections(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Do not replace usable cached sections with a transient timeout."""

    if not previous:
        return current
    merged = deepcopy(current)
    for key in (
        "health",
        "legal_repository",
        "crawl_import",
        "knowledge",
        "users",
        "organization",
        "support",
        "models",
        "runtime_metrics",
        "audit_7d",
    ):
        if _is_unavailable(merged.get(key)) and not _is_unavailable(previous.get(key)):
            merged[key] = deepcopy(previous[key])
    if isinstance(merged.get("recent_audit"), dict) and _is_unavailable(
        merged["recent_audit"]
    ):
        if not isinstance(previous.get("recent_audit"), dict):
            merged["recent_audit"] = deepcopy(previous["recent_audit"])
    return merged


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def dashboard_overview(
    request: Request,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """One safe, read-only Admin snapshot across the system's control planes."""

    await _require_admin(request)
    global _dashboard_cache, _dashboard_cache_created_at

    now = monotonic()
    if force_refresh:
        # Do not serialize a manual click behind the dashboard's aggregate
        # database projection.  The frontend receives the last valid values
        # immediately and polls until this one refresh has completed.
        _schedule_dashboard_refresh(force=True)
        if _dashboard_cache is not None:
            return _dashboard_cached_response(force_stale=True)
        return _dashboard_warming_snapshot()

    if not force_refresh and _dashboard_cache is not None:
        cache_age = max(0.0, now - _dashboard_cache_created_at)
        refresh_active = (
            _dashboard_refresh_task is not None
            and not _dashboard_refresh_task.done()
        )
        if cache_age >= DASHBOARD_CACHE_TTL_SECONDS and not refresh_active:
            _schedule_dashboard_refresh()
        return _dashboard_cached_response()

    # A warming request must never wait behind the background projection.  The
    # frontend polls this lightweight response until the cache is available.
    if not force_refresh and _dashboard_cache is None:
        _schedule_dashboard_refresh()
        return _dashboard_warming_snapshot()

    # Both branches above are exhaustive. Keep a truthful non-blocking
    # fallback in case a future caller changes the cache lifecycle.
    _schedule_dashboard_refresh()
    return _dashboard_warming_snapshot()


def _ticket_record(ticket: dict[str, Any]) -> dict[str, Any]:
    created = str(ticket.get("created_at") or "")
    updated = str(ticket.get("updated_at") or "")
    age_minutes = None
    try:
        age_minutes = int((_now() - datetime.fromisoformat(created.replace("Z", "+00:00"))).total_seconds() // 60)
    except (TypeError, ValueError):
        pass
    projection = {
        "first_response_overdue": False,
        "resolution_overdue": False,
        "sla_overdue": False,
        "overdue_minutes": 0,
        "sla_basis": "unknown",
    }
    return {
        "id": str(ticket.get("id") or ""),
        "domain": ticket.get("domain"),
        "primary_organization_unit_id": ticket.get(
            "primary_organization_unit_id"
        ),
        "status": live_support._normalize_status(ticket.get("status")),
        "priority": ticket.get("priority") or "normal",
        "assigned_officer_id": ticket.get("assigned_officer_id"),
        "created_at": created,
        "updated_at": updated,
        "age_minutes": age_minutes,
        **projection,
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


@router.get("/health")
async def system_health(request: Request) -> dict[str, Any]:
    await _require_admin(request)
    return await _system_health()


@router.get("/legal-effectivity")
async def legal_effectivity(request: Request) -> dict[str, Any]:
    await _require_admin(request)
    report = latest_effectivity_report()
    if report is None:
        report = await run_effectivity_check()
    return report


@router.post("/legal-effectivity/run")
async def rerun_legal_effectivity(body: ReasonRequest, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    reason = _reason(body.reason)
    await _audit(
        request,
        action="admin.legal_effectivity.run",
        resource_type="legal_effectivity_report",
        resource_id="latest",
        reason=reason,
    )
    return await run_effectivity_check()


@router.get("/config-history")
async def config_history(request: Request, config_type: Literal["settings", "model_defaults", "crawler_sources"] | None = None) -> dict[str, Any]:
    await _require_admin(request)
    return {"revisions": await list_config_revisions(config_type)}


@router.post("/config-history/{revision_id}/restore")
async def restore_config_history(revision_id: str, body: ReasonRequest, request: Request) -> dict[str, Any]:
    """Restore a redacted configuration snapshot and always append a new revision/audit."""
    admin_id = await _require_admin(request)
    reason = _reason(body.reason)
    revision = await get_config_revision(revision_id)
    if not revision:
        raise HTTPException(status_code=404, detail="Không tìm thấy phiên bản cấu hình.")
    target = revision.get("before")
    config_type = str(revision.get("config_type") or "")
    if not isinstance(target, dict):
        raise HTTPException(status_code=422, detail="Phiên bản cấu hình không hợp lệ.")
    if config_type == "settings":
        settings = await ContentSettings.get_instance()
        before = {
            "default_content_processing_engine_doc": settings.default_content_processing_engine_doc,
            "default_content_processing_engine_url": settings.default_content_processing_engine_url,
            "default_embedding_option": settings.default_embedding_option,
            "auto_delete_files": settings.auto_delete_files,
            "youtube_preferred_languages": settings.youtube_preferred_languages,
        }
        for key in before:
            if key in target:
                setattr(settings, key, target[key])
        await settings.update()
        after = {key: getattr(settings, key) for key in before}
    elif config_type == "model_defaults":
        defaults = await DefaultModels.get_instance()
        keys = (
            "default_chat_model", "large_context_model", "default_text_to_speech_model",
            "default_speech_to_text_model", "default_embedding_model", "default_tools_model",
        )
        before = {key: getattr(defaults, key, None) for key in keys}
        for key in keys:
            if key in target:
                setattr(defaults, key, target[key])
        await defaults.update()
        after = {key: getattr(defaults, key, None) for key in keys}
    elif config_type == "crawler_sources":
        source_id = str(target.get("id") or (revision.get("after") or {}).get("id") or "")
        existing = {str(item.get("id") or ""): item for item in await LegalCrawlService.list_sources()}
        if not target:
            if not source_id or source_id not in existing:
                raise HTTPException(status_code=422, detail="No crawler source is available for this restore.")
            before = existing[source_id]
            await LegalCrawlService.delete_source(source_id)
            after = {}
        elif source_id in existing:
            before = existing[source_id]
            fields = (
                "name", "base_url", "sitemap_scope", "enabled", "interval_minutes",
                "lookback_days", "max_documents_per_run", "max_listing_pages_per_run",
                "rate_limit_seconds", "filter_keyword",
            )
            after = await LegalCrawlService.update_source(
                source_id, {key: target[key] for key in fields if key in target}
            )
        else:
            before = {}
            after = await LegalCrawlService.create_source({
                key: target[key]
                for key in (
                    "name", "base_url", "sitemap_scope", "interval_minutes", "lookback_days",
                    "max_documents_per_run", "max_listing_pages_per_run", "rate_limit_seconds", "filter_keyword",
                ) if key in target
            })
    else:
        raise HTTPException(status_code=409, detail="Khôi phục toàn bộ nguồn crawl phải được thực hiện từng nguồn tại Nạp dữ liệu luật để giữ provenance.")
    await record_config_revision(
        config_type=config_type,
        before=before,
        after=after,
        actor_user_id=admin_id,
        reason=f"Khôi phục từ phiên bản {revision_id}: {reason}",
    )
    await _audit(
        request,
        action="admin.config_history.restore",
        resource_type="admin_config_revision",
        resource_id=revision_id,
        reason=reason,
        details={"config_type": config_type},
    )
    return {"restored": True, "config_type": config_type, "value": after}


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


async def _retired_review_candidate(candidate_id: str, body: CandidateActionRequest, request: Request) -> dict[str, Any]:
    await _require_admin(request)
    from api.system_settings import active_settings

    settings = await active_settings()
    reason = _reason(body.reason)
    candidate = await LegalCrawlService.review_candidate(
        candidate_id, body.decision, body.review_note or reason,
        reviewed_by=get_request_user_id(request), reviewed_role="admin",
        organization_routing_mode=getattr(
            settings, "organization_routing_mode", "legacy"
        ),
    )
    await _audit(
        request, action="admin.knowledge.candidate.review", resource_type="legal_crawl_candidate",
        resource_id=candidate_id, reason=reason, details={"decision": body.decision, "review_note": body.review_note},
    )
    return {"candidate": candidate}


@router.get("/metrics")
async def runtime_metrics(request: Request) -> dict[str, Any]:
    """Admin-only, content-free performance and error summary for pilot ops."""
    await _require_admin(request)
    return telemetry.summary()


def _audit_export_rows(rows: list[dict[str, Any]], content: str) -> list[dict[str, str]]:
    """Return a deliberately small, non-sensitive audit export projection."""
    result: list[dict[str, str]] = []
    for row in rows:
        action = str(row.get("action") or "")
        entity_type = str(row.get("entity_type") or "")
        if content == "sensitive" and not (action.startswith("admin.") or entity_type in {"credential", "user_account"}):
            continue
        if content == "legal" and not ("legal" in action or "legal" in entity_type or "form" in entity_type):
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        result.append({
            "Thời điểm": str(row.get("created") or ""),
            "Vai trò": str(row.get("actor_role") or ""),
            "Thao tác": action,
            "Loại dữ liệu": entity_type,
            "Định danh": str(row.get("entity_id") or ""),
            "Lý do": str(details.get("reason") or ""),
        })
    return result


def _audit_export_bytes(
    rows: list[dict[str, str]], export_format: str, *, columns: list[str] | None = None,
) -> tuple[bytes, str]:
    columns = columns or ["Thời điểm", "Vai trò", "Thao tác", "Loại dữ liệu", "Định danh", "Lý do"]
    if export_format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows([{column: row.get(column, "") for column in columns} for row in rows])
        return stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8"
    if export_format == "xlsx":
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
            from openpyxl.utils import get_column_letter
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="Chưa cài thành phần xuất Excel.") from exc
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Audit"
        sheet.append(columns)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            sheet.append([row.get(column, "") for column in columns])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        widths = (24, 14, 36, 24, 34, 48)
        for index, column in enumerate(columns, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = widths[index - 1] if index <= len(widths) else 32
        stream = io.BytesIO()
        workbook.save(stream)
        return stream.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if export_format == "pdf":
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib.units import mm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.platypus import (
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as exc:
            raise HTTPException(status_code=503, detail="Chưa cài thành phần xuất PDF.") from exc
        candidates = [
            Path(os.getenv("AUDIT_PDF_FONT_PATH", "")),
            ROOT / "assets" / "fonts" / "NotoSans-Regular.ttf",
            Path("C:/Windows/Fonts/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        ]
        font_path = next((path for path in candidates if str(path) and path.is_file()), None)
        if font_path is None:
            raise HTTPException(status_code=503, detail="Không tìm thấy font Noto Sans cho PDF tiếng Việt.")
        pdfmetrics.registerFont(TTFont("NotoSans", str(font_path)))
        stream = io.BytesIO()
        doc = SimpleDocTemplate(stream, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
        styles = getSampleStyleSheet()
        normal = styles["BodyText"]
        normal.fontName = "NotoSans"
        normal.fontSize = 7
        title = styles["Title"]
        title.fontName = "NotoSans"
        title.fontSize = 14
        data = [[Paragraph(column, normal) for column in columns]]
        data.extend([[Paragraph(str(row.get(column, "")), normal) for column in columns] for row in rows])
        base_widths = [31, 20, 46, 30, 47, 62] if len(columns) == 6 else [31] * len(columns)
        max_width = 270
        scale = min(1, max_width / sum(base_widths))
        table = Table(data, repeatRows=1, colWidths=[width * scale * mm for width in base_widths])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8F0FE")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8CDD4")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        doc.build([Paragraph("Báo cáo kiểm toán", title), Spacer(1, 5 * mm), table])
        return stream.getvalue(), "application/pdf"
    raise HTTPException(status_code=400, detail="Định dạng xuất không hợp lệ.")


@router.get("/audit/export")
async def export_audit(
    request: Request,
    format: Literal["xlsx", "csv", "pdf"] = "xlsx",
    content: Literal["all", "sensitive", "legal"] = "all",
    actor_role: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    action: str | None = None,
    reason: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> StreamingResponse:
    await _require_admin(request)
    rows = await list_audit_logs(
        limit=5001, actor_role=actor_role, action=action, resource_type=resource_type,
        resource_id=resource_id, reason=reason, date_from=date_from, date_to=date_to,
    )
    if len(rows) > 5000:
        raise HTTPException(status_code=422, detail="Có hơn 5.000 dòng. Hãy thu hẹp khoảng ngày hoặc bộ lọc trước khi xuất.")
    export_rows = _audit_export_rows(rows, content)
    payload, media_type = _audit_export_bytes(export_rows, format)
    checksum = hashlib.sha256(payload).hexdigest()
    timestamp = _now().strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        io.BytesIO(payload),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="audit-{content}-{timestamp}.{format}"',
            "X-Content-SHA256": checksum,
            "X-Record-Count": str(len(export_rows)),
        },
    )


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
