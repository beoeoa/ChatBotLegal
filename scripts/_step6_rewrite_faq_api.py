from pathlib import Path

# Rewrite FAQ router with proper auth pattern matching users.py
faq_code = r'''"""
FAQ API Router - Câu hỏi thường gặp cho phường/xã (Hải Phòng / Lê Chân)

- GET: public (approved only by default)
- POST/PUT/DELETE/seed: admin only
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from api.auth import get_request_role

router = APIRouter(prefix="/faq", tags=["FAQ"])

FAQ_DATA_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "notebook_data", "faq_store.json"
)
FAQ_SEED_FILE = os.path.join(
    os.path.dirname(__file__), "..", "..", "notebook_data", "faq_seed.json"
)


class FaqCreate(BaseModel):
    question: str = Field(..., min_length=5, max_length=500)
    answer: str = Field(..., min_length=10)
    steps: List[str] = Field(default_factory=list)
    form_ids: List[str] = Field(default_factory=list)
    domain: str
    ward_scope: Optional[str] = None
    review_status: str = Field(default="draft", pattern="^(draft|approved|rejected)$")


class FaqUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None
    steps: Optional[List[str]] = None
    form_ids: Optional[List[str]] = None
    domain: Optional[str] = None
    ward_scope: Optional[str] = None
    review_status: Optional[str] = Field(
        default=None, pattern="^(draft|approved|rejected)$"
    )


class FaqResponse(BaseModel):
    id: str
    question: str
    answer: str
    steps: List[str]
    form_ids: List[str]
    domain: str
    ward_scope: Optional[str] = None
    review_status: str
    created_at: str
    updated_at: str
    approved_by: Optional[str] = None


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
    return role or "admin"


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
    return FaqListResponse(total=total, items=[FaqResponse(**f) for f in page])


@router.get("/match", response_model=FaqListResponse)
async def match_faqs(
    question: str = Query(..., min_length=3),
    domain: Optional[str] = Query(None),
    ward_scope: Optional[str] = Query(None),
    limit: int = Query(5, ge=1, le=20),
):
    """Keyword match FAQs for Ask UI."""
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
    items = [FaqResponse(**f) for _, f in scored[:limit]]
    return FaqListResponse(total=len(items), items=items)


@router.get("/{faq_id}", response_model=FaqResponse)
async def get_faq(faq_id: str, request: Request):
    _ensure_seeded()
    data = _load_faq_data()
    role = get_request_role(request)
    for faq in data.get("faqs") or []:
        if faq.get("id") == faq_id:
            if faq.get("review_status") != "approved" and role != "admin":
                raise HTTPException(status_code=404, detail="FAQ không tồn tại hoặc chưa duyệt")
            return FaqResponse(**faq)
    raise HTTPException(status_code=404, detail="FAQ không tồn tại")


@router.post("/", response_model=FaqResponse, status_code=201)
async def create_faq(faq_data: FaqCreate, request: Request):
    await _require_admin(request)
    data = _load_faq_data()
    faqs = data.get("faqs") or []
    now = datetime.now().isoformat()
    new_faq = {
        "id": _generate_id(),
        "question": faq_data.question,
        "answer": faq_data.answer,
        "steps": faq_data.steps,
        "form_ids": faq_data.form_ids,
        "domain": faq_data.domain,
        "ward_scope": faq_data.ward_scope,
        "review_status": faq_data.review_status or "draft",
        "created_at": now,
        "updated_at": now,
        "approved_by": "admin" if faq_data.review_status == "approved" else None,
    }
    faqs.append(new_faq)
    data["faqs"] = faqs
    _save_faq_data(data)
    return FaqResponse(**new_faq)


@router.put("/{faq_id}", response_model=FaqResponse)
async def update_faq(faq_id: str, faq_update: FaqUpdate, request: Request):
    await _require_admin(request)
    data = _load_faq_data()
    faqs = data.get("faqs") or []
    for i, faq in enumerate(faqs):
        if faq.get("id") == faq_id:
            update_dict = faq_update.model_dump(exclude_unset=True)
            for key, value in update_dict.items():
                faqs[i][key] = value
            faqs[i]["updated_at"] = datetime.now().isoformat()
            if update_dict.get("review_status") == "approved":
                faqs[i]["approved_by"] = "admin"
            data["faqs"] = faqs
            _save_faq_data(data)
            return FaqResponse(**faqs[i])
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
'''

Path("api/routers/faq.py").write_text(faq_code, encoding="utf-8", newline="\n")
print("Rewrote api/routers/faq.py")
