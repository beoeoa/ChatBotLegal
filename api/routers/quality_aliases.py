"""Stable quality endpoints used by the admin dashboard and CI smoke tests."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from api.auth import get_request_user_id
from api.expert_review_service import ExpertReviewError, load_reviews, update_review
from api.routers.legal_quality import _forms_quality_summary, _require_admin, _role_summary, _retrieval_summary
from api.user_service import write_audit_log

router = APIRouter(tags=["quality"])
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "notebook_data"
FAQ_PATH = DATA / "faq_store.json"
GOLDEN_PATH = DATA / "legal-golden-set.json"
EXPERT_PATH = DATA / "legal-golden-expert-review.json"
LIVE_334_PATH = DATA / "quality_runs" / "live-334-latest.json"
GATE_334_PATH = DATA / "quality_runs" / "quality-gate-334.json"
ASSET_HEALTH_334_PATH = DATA / "quality_runs" / "asset-health-334.json"
AUTHORITY_MATRIX_PATH = DATA / "legal_quality" / "authority_matrix.json"
PROCEDURE_MATRIX_PATH = DATA / "legal_quality" / "procedure_matrix.json"
RUN_DIR = DATA / "quality_runs"


class ExpertReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_documents: list[str] | None = None
    expected_articles: list[str] | None = None
    forbidden_documents: list[str] | None = None
    expected_authority: str | list[str] | None = None
    forbidden_authority: list[str] | None = None
    mandatory_documents: list[str] | None = None
    conditional_documents: list[str] | None = None
    processing_time: str | None = None
    fee: str | None = None
    penalty_range: str | None = None
    remedial_measures: str | list[str] | None = None
    official_form_ids: list[str] | None = None
    expected_conclusion: str | None = None
    allowed_conditional_conclusions: list[str] | None = None
    critical_errors: list[str] | None = None
    missing_facts_to_ask: list[str] | None = None
    expert_review_status: Literal["pending", "approved", "expert_disputed", "needs_revalidation"] | None = None
    expert_score: float | None = Field(default=None, ge=0, le=10)
    expert_name: str | None = Field(default=None, min_length=2, max_length=200)
    review_version: str | None = Field(default=None, min_length=1, max_length=50)
    second_expert_name: str | None = Field(default=None, min_length=2, max_length=200)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _has_mojibake(value: str) -> bool:
    return any(token in value for token in ("Ã", "Â", "á»", "�"))


def _faq_quality() -> dict[str, Any]:
    payload = _read_json(FAQ_PATH, {"faqs": []})
    items = list(payload.get("faqs") or []) if isinstance(payload, dict) else []
    index = _read_json(DATA / "forms" / "haiphong_official_form_index.json", {"forms": []})
    form_ids = {str(item.get("id")) for item in (index.get("forms") or [])}
    invalid_forms: list[dict[str, Any]] = []
    domains: dict[str, int] = {}
    mojibake = 0
    for item in items:
        domain = str(item.get("domain") or "unknown")
        domains[domain] = domains.get(domain, 0) + 1
        if _has_mojibake(json.dumps(item, ensure_ascii=False)):
            mojibake += 1
        for form_id in item.get("form_ids") or []:
            if str(form_id) not in form_ids:
                invalid_forms.append({"faq_id": item.get("id"), "form_id": form_id})
    golden = _read_json(GOLDEN_PATH, {"questions": []})
    golden_items = [item for item in (golden.get("questions") or []) if isinstance(item, dict)]
    golden_domains: dict[str, int] = {}
    for item in golden_items:
        domain = str(item.get("domain") or "unknown")
        golden_domains[domain] = golden_domains.get(domain, 0) + 1
    return {
        "total": len(items),
        "approved": sum(1 for item in items if item.get("review_status") == "approved"),
        "domains": domains,
        "invalid_form_references": invalid_forms,
        "mojibake_records": mojibake,
        "quality_gate": not invalid_forms and mojibake == 0,
        "golden_set": {
            "total": len(golden_items),
            "by_domain": golden_domains,
            "target_total": 150,
            "ready": len(golden_items) >= 150,
        },
    }


def _build_report() -> dict[str, Any]:
    role = _read_json(DATA / "role-evaluation.json", None)
    retrieval = _read_json(DATA / "retrieval-evaluation.json", None)
    faq = _faq_quality()
    forms = _forms_quality_summary()
    expert = _read_json(EXPERT_PATH, {"records": []})
    expert_records = list(expert.get("records") or [])
    expert_status: dict[str, int] = {}
    for item in expert_records:
        status = str(item.get("expert_review_status") or "missing")
        expert_status[status] = expert_status.get(status, 0) + 1
    live_334 = _read_json(LIVE_334_PATH, {"results": []})
    gate_334 = _read_json(GATE_334_PATH, {})
    asset_health_334 = _read_json(ASSET_HEALTH_334_PATH, {})
    authority_matrix = _read_json(AUTHORITY_MATRIX_PATH, {"rows": []})
    procedure_matrix = _read_json(PROCEDURE_MATRIX_PATH, {"rows": []})
    report = {
        "run_id": uuid.uuid4().hex,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "faq": faq,
        "forms": forms,
        "role_evaluation": _role_summary(role),
        "retrieval_evaluation": _retrieval_summary(retrieval),
        "expert_review": {
            "total": len(expert_records),
            "status_counts": expert_status,
            "ready": len(expert_records) == 334 and expert_status.get("approved", 0) == 334,
        },
        "live_334": {
            "target": live_334.get("target_count", 334),
            "completed": sum(1 for item in live_334.get("results") or [] if item.get("status") == "completed"),
            "failed": sum(1 for item in live_334.get("results") or [] if item.get("status") == "failed"),
        },
        "legal_matrices": {
            "authority_rows": len(authority_matrix.get("rows") or []),
            "procedure_rows": len(procedure_matrix.get("rows") or []),
        },
        "release_gate_334": gate_334,
        "asset_health_334": asset_health_334,
        "quality_gate": bool(gate_334.get("pass", False)),
    }
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / f"{report['run_id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


@router.get("/faq/quality")
async def faq_quality(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return _faq_quality()


@router.get("/forms/quality")
async def forms_quality(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return _forms_quality_summary()


@router.get("/admin/legal-quality")
async def admin_legal_quality(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return _build_report()


@router.post("/quality/run")
async def start_quality_run(request: Request) -> dict[str, Any]:
    _require_admin(request)
    return _build_report()


@router.post("/quality/run/live-334")
async def start_live_334(request: Request) -> dict[str, Any]:
    _require_admin(request)
    import subprocess
    import sys

    script = ROOT / "scripts" / "collect_334_live_answers.py"
    subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return {"started": True, "run": "live-334", "output": str(LIVE_334_PATH)}


@router.get("/quality/expert-reviews")
async def list_expert_reviews(
    request: Request,
    status: str | None = Query(default=None),
    domain: str | None = Query(default=None),
    role: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    _require_admin(request)
    records = list(load_reviews().get("records") or [])
    if status:
        records = [item for item in records if item.get("expert_review_status") == status]
    if domain:
        records = [item for item in records if item.get("domain") == domain]
    if role:
        records = [item for item in records if item.get("role") == role]
    return {"total": len(records), "offset": offset, "limit": limit, "records": records[offset : offset + limit]}


@router.get("/quality/expert-reviews/{review_id}")
async def get_expert_review(review_id: str, request: Request) -> dict[str, Any]:
    _require_admin(request)
    record = next(
        (item for item in load_reviews().get("records") or [] if item.get("review_id") == review_id),
        None,
    )
    if not record:
        raise HTTPException(status_code=404, detail="Không tìm thấy ca duyệt chuyên gia.")
    return record


@router.patch("/quality/expert-reviews/{review_id}")
async def patch_expert_review(
    review_id: str,
    body: ExpertReviewUpdate,
    request: Request,
    x_business_reason: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_admin(request)
    reason = str(x_business_reason or "").strip()
    if len(reason) < 3:
        raise HTTPException(status_code=400, detail="Cần nhập lý do nghiệp vụ để cập nhật duyệt chuyên gia.")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Không có nội dung cập nhật.")
    try:
        await write_audit_log(
            action="legal_quality.expert_review.update",
            entity_type="legal_expert_review",
            entity_id=review_id,
            actor_user_id=get_request_user_id(request),
            actor_role="admin",
            details={"reason": reason, "updated_fields": sorted(changes)},
            request=request,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Không thể ghi audit nên chưa cập nhật ca duyệt.") from exc
    try:
        return update_review(review_id, changes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Không tìm thấy ca duyệt chuyên gia.") from exc
    except ExpertReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/quality/runs/{run_id}")
async def get_quality_run(run_id: str, request: Request) -> dict[str, Any]:
    _require_admin(request)
    path = RUN_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Không tìm thấy lần chạy quality này.")
    return _read_json(path, {})
