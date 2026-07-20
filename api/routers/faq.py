"""
FAQ API Router - Câu hỏi thường gặp cho phường/xã (Hải Phòng / Lê Chân)

- GET: public (approved only by default)
- POST/PUT/DELETE/seed: admin only
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from api.auth import get_request_role, get_request_user_id
from api.observability import telemetry
from time import perf_counter
from api.form_discovery_service import discovery_status, queue_missing_form_discovery
from api.data_paths import notebook_data_dir

router = APIRouter(prefix="/faq", tags=["FAQ"])

NOTEBOOK_DATA_DIR = notebook_data_dir()
FAQ_DATA_FILE = NOTEBOOK_DATA_DIR / "faq_store.json"
FAQ_SEED_FILE = NOTEBOOK_DATA_DIR / "faq" / "faq_seed_haiphong_lechan.json"
FORMS_INDEX_FILE = NOTEBOOK_DATA_DIR / "forms" / "haiphong_official_form_index.json"
MAX_FAQ_FORMS = 3


class FaqCreate(BaseModel):
    question: str = Field(..., min_length=5, max_length=500)
    answer: str = Field(..., min_length=10)
    submission_place: str = ""
    legal_basis: List[str] = Field(default_factory=list)
    guidance_label: str = ""
    requires_forms: bool = False
    steps: List[str] = Field(default_factory=list)
    form_ids: List[str] = Field(default_factory=list)
    domain: str
    ward_scope: Optional[str] = None
    review_status: str = Field(default="draft", pattern="^(draft|approved|rejected)$")
    fast_path_enabled: bool = False
    eligible_roles: List[str] = Field(default_factory=list)
    match_phrases: List[str] = Field(default_factory=list)
    verified_source_refs: List[dict[str, Any]] = Field(default_factory=list)
    reviewed_legal_as_of: Optional[str] = None
    reviewed_corpus_revision: Optional[str] = None


class FaqUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    submission_place: Optional[str] = None
    legal_basis: Optional[List[str]] = None
    guidance_label: Optional[str] = None
    requires_forms: Optional[bool] = None
    steps: Optional[List[str]] = None
    form_ids: Optional[List[str]] = None
    domain: Optional[str] = None
    ward_scope: Optional[str] = None
    review_status: Optional[str] = Field(
        default=None, pattern="^(draft|approved|rejected)$"
    )
    fast_path_enabled: Optional[bool] = None
    eligible_roles: Optional[List[str]] = None
    match_phrases: Optional[List[str]] = None
    verified_source_refs: Optional[List[dict[str, Any]]] = None
    reviewed_legal_as_of: Optional[str] = None
    reviewed_corpus_revision: Optional[str] = None


class FaqResponse(BaseModel):
    id: str
    question: str
    answer: str
    submission_place: str = ""
    legal_basis: List[str] = Field(default_factory=list)
    guidance_label: str = ""
    requires_forms: bool = False
    steps: List[str]
    form_ids: List[str]
    forms: List[dict] = Field(default_factory=list)
    forms_unavailable: bool = False
    domain: str
    ward_scope: Optional[str] = None
    review_status: str
    created_at: str
    updated_at: str
    approved_by: Optional[str] = None
    fast_path_enabled: bool = False
    eligible_roles: List[str] = Field(default_factory=list)
    match_phrases: List[str] = Field(default_factory=list)
    verified_source_refs: List[dict[str, Any]] = Field(default_factory=list)
    reviewed_legal_as_of: Optional[str] = None
    reviewed_corpus_revision: Optional[str] = None


class FaqListResponse(BaseModel):
    total: int
    items: List[FaqResponse]


def _load_faq_data() -> dict:
    if os.path.exists(FAQ_DATA_FILE):
        with open(FAQ_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"faqs": [], "version": 1}


def _save_faq_data(data: dict) -> None:
    os.makedirs(os.path.dirname(FAQ_DATA_FILE), exist_ok=True)
    with open(FAQ_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _generate_id() -> str:
    return str(uuid.uuid4())[:8]


async def _require_admin(request: Request) -> str:
    role = get_request_role(request)
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ admin mới có quyền thao tác với FAQ",
        )
    user_id = get_request_user_id(request)
    return f"user:{user_id}" if user_id else "admin"


def _load_official_form_index() -> dict[str, dict[str, Any]]:
    if not FORMS_INDEX_FILE.is_file():
        return {}
    try:
        payload = json.loads(FORMS_INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    records = payload.get("forms") or [] if isinstance(payload, dict) else []
    return {str(record.get("id")): record for record in records if record.get("id")}


def _is_valid_official_form(record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    if record.get("official_level") != "official":
        return False
    if record.get("review_status") != "approved" and record.get("is_approved") is not True:
        return False
    relative_path = str(
        record.get("source_package_path") or record.get("local_path") or record.get("priority_path") or ""
    ).replace("\\", "/")
    if not relative_path or "data/uploads/forms/" not in relative_path:
        return False
    source_path = Path(__file__).resolve().parents[2] / relative_path
    return source_path.is_file() and source_path.stat().st_size > 0


def _enrich_faq_for_public(faq: dict[str, Any]) -> dict[str, Any]:
    """Expose at most three exact, approved, downloadable official forms."""
    item = dict(faq)
    item.setdefault("submission_place", "")
    item.setdefault("legal_basis", [])
    item.setdefault("guidance_label", "")
    item.setdefault("requires_forms", False)
    item.setdefault("fast_path_enabled", False)
    item.setdefault("eligible_roles", [])
    item.setdefault("match_phrases", [])
    item.setdefault("verified_source_refs", [])
    item.setdefault("reviewed_legal_as_of", None)
    item.setdefault("reviewed_corpus_revision", None)
    requested_ids = [str(value) for value in (item.get("form_ids") or [])]
    valid_forms: list[dict[str, Any]] = []
    if item.get("requires_forms") and requested_ids:
        form_index = _load_official_form_index()
        for form_id in requested_ids:
            record = form_index.get(form_id)
            if not _is_valid_official_form(record):
                continue
            valid_forms.append({
                "id": form_id,
                "name": str(record.get("form_title") or record.get("name") or form_id),
                "file_type": str(record.get("file_type") or Path(str(record.get("local_path") or "")).suffix.lstrip(".") or "file"),
                "download_url": f"/api/procedures/forms-catalog/official/{form_id}/download",
                "official_level": "official",
                "review_status": "approved",
            })
            if len(valid_forms) >= MAX_FAQ_FORMS:
                break
    item["forms"] = valid_forms
    # Never expose seed IDs as if they were real downloadable forms.
    item["form_ids"] = [form["id"] for form in valid_forms]
    item["forms_unavailable"] = bool(item.get("requires_forms") and not valid_forms)
    return item


def faq_fast_path_eligible(
    faq: dict[str, Any],
    *,
    role: str,
    domain: str | None,
    ward_scope: str | None,
    corpus_revision: str,
    legal_as_of: str,
) -> bool:
    """Fail closed unless an FAQ has a complete, human-reviewed source lock."""
    if faq.get("review_status") != "approved" or not faq.get("fast_path_enabled"):
        return False
    approved_by = str(faq.get("approved_by") or "")
    if not approved_by.startswith("user:"):
        return False
    if role not in set(faq.get("eligible_roles") or []):
        return False
    if domain and faq.get("domain") != domain:
        return False
    if ward_scope and faq.get("ward_scope") not in {None, "", ward_scope}:
        return False
    if faq.get("reviewed_corpus_revision") != corpus_revision:
        return False
    reviewed_as_of = str(faq.get("reviewed_legal_as_of") or "")
    if not reviewed_as_of or reviewed_as_of > legal_as_of:
        return False
    refs = faq.get("verified_source_refs") or []
    return bool(refs) and all(
        isinstance(ref, dict)
        and ref.get("chunk_id")
        and ref.get("source_url")
        and (ref.get("law_number") or ref.get("document_title"))
        for ref in refs
    )


def _ensure_seeded() -> None:
    """Auto-seed from faq_seed.json if store empty."""
    data = _load_faq_data()
    if data.get("faqs"):
        return
    if not os.path.exists(FAQ_SEED_FILE):
        return
    with open(FAQ_SEED_FILE, "r", encoding="utf-8") as f:
        seed_items = json.load(f)
    now = datetime.now().isoformat()
    faqs = []
    for item in seed_items:
        faqs.append(
            {
                "id": item.get("id") or _generate_id(),
                "question": item["question"],
                "answer": item["answer"],
                "submission_place": item.get("submission_place") or "",
                "legal_basis": item.get("legal_basis") or [],
                "guidance_label": item.get("guidance_label") or "",
                "requires_forms": bool(item.get("requires_forms")),
                "steps": item.get("steps") or [],
                "form_ids": item.get("form_ids") or [],
                "domain": item.get("domain") or "hanh_chinh_cong",
                "ward_scope": item.get("ward_scope"),
                "review_status": item.get("review_status") or "approved",
                "created_at": now,
                "updated_at": now,
                "approved_by": "system_seed",
            }
        )
    data["faqs"] = faqs
    _save_faq_data(data)


@router.get("", response_model=FaqListResponse)
@router.get("/", response_model=FaqListResponse)
async def list_faqs(
    domain: Optional[str] = Query(None),
    ward_scope: Optional[str] = Query(None),
    review_status: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="Keyword filter on question/answer"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    request: Request = None,
):
    """List FAQs. Non-admin only sees approved items."""
    started = perf_counter()
    _ensure_seeded()
    data = _load_faq_data()
    faqs = list(data.get("faqs") or [])

    role = get_request_role(request) if request is not None else None
    is_admin = role == "admin"

    if review_status:
        if not is_admin and review_status != "approved":
            raise HTTPException(status_code=403, detail="Chỉ admin mới xem FAQ chưa duyệt")
        faqs = [f for f in faqs if f.get("review_status") == review_status]
    else:
        if not is_admin:
            faqs = [f for f in faqs if f.get("review_status") == "approved"]

    if domain:
        faqs = [f for f in faqs if f.get("domain") == domain]
    if ward_scope:
        faqs = [
            f
            for f in faqs
            if (f.get("ward_scope") or "").lower() == ward_scope.lower()
            or ward_scope.lower() in (f.get("ward_scope") or "").lower()
        ]
    if q:
        q_low = q.lower()
        faqs = [
            f
            for f in faqs
            if q_low in (f.get("question") or "").lower()
            or q_low in (f.get("answer") or "").lower()
        ]

    faqs.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    total = len(faqs)
    page = faqs[offset : offset + limit]
    telemetry.record_operation(
        category="faq_load", route="/api/faq/", duration_ms=(perf_counter() - started) * 1000,
        metadata={"cached": False},
    )
    return FaqListResponse(total=total, items=[FaqResponse(**_enrich_faq_for_public(f)) for f in page])


@router.get("/match", response_model=FaqListResponse)
async def match_faqs(
    question: str = Query(..., min_length=3),
    domain: Optional[str] = Query(None),
    ward_scope: Optional[str] = Query(None),
    limit: int = Query(5, ge=1, le=20),
):
    """Keyword match FAQs for Ask UI."""
    started = perf_counter()
    _ensure_seeded()
    data = _load_faq_data()
    faqs = [f for f in (data.get("faqs") or []) if f.get("review_status") == "approved"]
    if domain:
        faqs = [f for f in faqs if f.get("domain") == domain]
    if ward_scope:
        faqs = [
            f
            for f in faqs
            if not f.get("ward_scope")
            or ward_scope.lower() in (f.get("ward_scope") or "").lower()
        ]

    tokens = [t for t in question.lower().replace("?", " ").split() if len(t) > 2]
    scored = []
    for f in faqs:
        blob = f"{f.get('question','')} {f.get('answer','')}".lower()
        score = sum(1 for t in tokens if t in blob)
        # boost if key phrases present
        for phrase in [
            "khai sinh",
            "sang tên",
            "sổ đỏ",
            "đỗ xe",
            "kết hôn",
            "xây dựng",
            "hộ kinh doanh",
            "khiếu nại",
            "chứng tử",
            "tạm trú",
        ]:
            if phrase in question.lower() and phrase in blob:
                score += 3
        if score > 0:
            scored.append((score, f))
    scored.sort(key=lambda x: x[0], reverse=True)
    items = [FaqResponse(**_enrich_faq_for_public(f)) for _, f in scored[:limit]]
    telemetry.record_operation(
        category="faq_load", route="/api/faq/match", duration_ms=(perf_counter() - started) * 1000,
        metadata={"cached": False},
    )
    return FaqListResponse(total=len(items), items=items)


@router.post("/discover-missing-forms")
async def discover_missing_faq_forms(request: Request, limit: int = Query(50, ge=1, le=100)):
    """Queue official-source discovery for FAQ forms that have no file.

    Discovery only creates ``candidate_pending_review`` records. Admin review
    is still required before a form becomes visible in chat.
    """
    await _require_admin(request)
    return queue_missing_form_discovery(limit=limit)


@router.get("/discover-missing-forms/status")
async def missing_form_discovery_status(request: Request):
    await _require_admin(request)
    return discovery_status()


@router.get("/{faq_id}", response_model=FaqResponse)
async def get_faq(faq_id: str, request: Request):
    _ensure_seeded()
    data = _load_faq_data()
    role = get_request_role(request)
    for faq in data.get("faqs") or []:
        if faq.get("id") == faq_id:
            if faq.get("review_status") != "approved" and role != "admin":
                raise HTTPException(status_code=404, detail="FAQ không tồn tại hoặc chưa duyệt")
            return FaqResponse(**_enrich_faq_for_public(faq))
    raise HTTPException(status_code=404, detail="FAQ không tồn tại")


@router.post("", response_model=FaqResponse, status_code=201)
@router.post("/", response_model=FaqResponse, status_code=201)
async def create_faq(faq_data: FaqCreate, request: Request):
    admin_actor = await _require_admin(request)
    data = _load_faq_data()
    faqs = data.get("faqs") or []
    now = datetime.now().isoformat()
    new_faq = {
        "id": _generate_id(),
        "question": faq_data.question,
        "answer": faq_data.answer,
        "submission_place": faq_data.submission_place,
        "legal_basis": faq_data.legal_basis,
        "guidance_label": faq_data.guidance_label,
        "requires_forms": faq_data.requires_forms,
        "steps": faq_data.steps,
        "form_ids": faq_data.form_ids,
        "domain": faq_data.domain,
        "ward_scope": faq_data.ward_scope,
        "review_status": faq_data.review_status or "draft",
        "fast_path_enabled": faq_data.fast_path_enabled,
        "eligible_roles": faq_data.eligible_roles,
        "match_phrases": faq_data.match_phrases,
        "verified_source_refs": faq_data.verified_source_refs,
        "reviewed_legal_as_of": faq_data.reviewed_legal_as_of,
        "reviewed_corpus_revision": faq_data.reviewed_corpus_revision,
        "created_at": now,
        "updated_at": now,
        "approved_by": admin_actor if faq_data.review_status == "approved" else None,
    }
    faqs.append(new_faq)
    data["faqs"] = faqs
    _save_faq_data(data)
    return FaqResponse(**_enrich_faq_for_public(new_faq))


@router.put("/{faq_id}", response_model=FaqResponse)
async def update_faq(faq_id: str, faq_update: FaqUpdate, request: Request):
    admin_actor = await _require_admin(request)
    data = _load_faq_data()
    faqs = data.get("faqs") or []
    for i, faq in enumerate(faqs):
        if faq.get("id") == faq_id:
            update_dict = faq_update.model_dump(exclude_unset=True)
            for key, value in update_dict.items():
                faqs[i][key] = value
            faqs[i]["updated_at"] = datetime.now().isoformat()
            if update_dict.get("review_status") == "approved":
                faqs[i]["approved_by"] = admin_actor
            data["faqs"] = faqs
            _save_faq_data(data)
            return FaqResponse(**_enrich_faq_for_public(faqs[i]))
    raise HTTPException(status_code=404, detail="FAQ không tồn tại")


@router.delete("/{faq_id}", status_code=204)
async def delete_faq(faq_id: str, request: Request):
    await _require_admin(request)
    data = _load_faq_data()
    faqs = data.get("faqs") or []
    new_faqs = [f for f in faqs if f.get("id") != faq_id]
    if len(new_faqs) == len(faqs):
        raise HTTPException(status_code=404, detail="FAQ không tồn tại")
    data["faqs"] = new_faqs
    _save_faq_data(data)
    return None


@router.post("/seed")
async def seed_faqs(request: Request):
    await _require_admin(request)
    if not os.path.exists(FAQ_SEED_FILE):
        raise HTTPException(status_code=404, detail="File faq_seed.json không tồn tại")
    with open(FAQ_SEED_FILE, "r", encoding="utf-8") as f:
        seed_data = json.load(f)
    data = _load_faq_data()
    existing = data.get("faqs") or []
    existing_ids = {f.get("id") for f in existing}
    imported = 0
    now = datetime.now().isoformat()
    for item in seed_data:
        if item.get("id") in existing_ids:
            continue
        existing.append(
            {
                "id": item.get("id") or _generate_id(),
                "question": item["question"],
                "answer": item["answer"],
                "submission_place": item.get("submission_place") or "",
                "legal_basis": item.get("legal_basis") or [],
                "guidance_label": item.get("guidance_label") or "",
                "requires_forms": bool(item.get("requires_forms")),
                "steps": item.get("steps") or [],
                "form_ids": item.get("form_ids") or [],
                "domain": item.get("domain") or "hanh_chinh_cong",
                "ward_scope": item.get("ward_scope"),
                "review_status": item.get("review_status") or "approved",
                "created_at": now,
                "updated_at": now,
                "approved_by": "system_seed",
            }
        )
        imported += 1
    data["faqs"] = existing
    _save_faq_data(data)
    return {"imported": imported, "total": len(existing)}
