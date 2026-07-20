from pathlib import Path

wp = Path("api/routers/ward_procedures.py")
text = wp.read_text(encoding="utf-8")

# 1) Ensure UploadFile/File/Request imports
if "UploadFile" not in text:
    text = text.replace(
        "from fastapi import APIRouter, HTTPException, Query",
        "from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile",
        1,
    )
    print("OK: fastapi imports")
else:
    print("SKIP: UploadFile already imported")

if "from api.auth import get_request_role" not in text:
    # insert after fastapi imports
    text = text.replace(
        "from fastapi.responses import FileResponse, Response\n",
        "from fastapi.responses import FileResponse, Response\nfrom api.auth import get_request_role\n",
        1,
    )
    print("OK: auth import")
else:
    print("SKIP: auth import")

# 2) Add admin upload endpoint before review endpoints if missing
if "async def upload_official_form" not in text:
    endpoint = '''

@router.post("/forms-catalog/upload")
async def upload_official_form(
    request: Request,
    file: UploadFile = File(...),
    form_title: str = Query(..., min_length=3, max_length=300),
    domain: str = Query("unknown"),
    procedure_id: Optional[str] = Query(None),
    review_status: str = Query("approved", pattern="^(approved|candidate_pending_review|rejected)$"),
    official_level: str = Query("official", pattern="^(official|reference)$"),
):
    """Admin-only upload for real form files.

    - approved + official => copy into priority_official and become downloadable
    - otherwise keep in official_candidates pending review
    """
    role = get_request_role(request)
    if role != "admin":
        raise HTTPException(status_code=403, detail="Chỉ admin mới được upload biểu mẫu")

    filename = file.filename or "form.bin"
    ext = Path(filename).suffix.lower()
    if ext not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".rtf", ".zip"}:
        raise HTTPException(status_code=400, detail="Định dạng file không được hỗ trợ")

    content = await file.read()
    if not content or len(content) < 1024:
        raise HTTPException(status_code=400, detail="File quá nhỏ hoặc rỗng; cần file biểu mẫu thật")

    import hashlib
    from datetime import datetime, timezone
    import re as _re

    digest = hashlib.sha256(content).hexdigest()
    form_id = digest[:24]
    safe = _re.sub(r"[^\\w\\-.]+", "-", filename).strip("-._") or "form"
    target_dir = PRIORITY_FORMS_FILES_DIR if (review_status == "approved" and official_level == "official") else OFFICIAL_FORMS_FILES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    dest_name = f"{form_id}-{safe}"
    dest = target_dir / dest_name
    dest.write_bytes(content)
    rel = str(dest.relative_to(PROJECT_ROOT)).replace("\\\\", "/")
    now = datetime.now(timezone.utc).isoformat()

    record = {
        "id": form_id,
        "form_title": form_title,
        "domain": domain,
        "procedure_id": procedure_id,
        "file_name": dest_name,
        "file_path": rel,
        "local_path": rel,
        "source_package_path": rel,
        "size_bytes": len(content),
        "sha256": digest,
        "review_status": review_status,
        "official_level": official_level,
        "is_approved": review_status == "approved",
        "catalog_status": "available_official_source" if review_status == "approved" else "candidate_pending_review",
        "is_canonical": review_status == "approved" and official_level == "official",
        "publisher": "admin_upload",
        "locality": "Hai Phong",
        "administrative_level": "commune_relevant",
        "retrieved_at": now,
        "uploaded_at": now,
        "uploaded_by_role": role,
    }

    # Upsert classified candidates
    classified = _load_forms_json(CLASSIFIED_FORMS_CANDIDATES_PATH, {"records": [], "summary": {}})
    records = list(classified.get("records") or [])
    replaced = False
    for i, rec in enumerate(records):
        if str(rec.get("id")) == form_id or str(rec.get("sha256")) == digest:
            records[i] = {**rec, **record, "detected_form_name": form_title, "suggested_domain": domain, "suggested_procedure_id": procedure_id}
            replaced = True
            break
    if not replaced:
        records.append({**record, "detected_form_name": form_title, "suggested_domain": domain, "suggested_procedure_id": procedure_id})
    classified["records"] = records
    status_counts = {}
    for rec in records:
        rs = str(rec.get("review_status") or "unknown")
        status_counts[rs] = status_counts.get(rs, 0) + 1
    classified["summary"] = {**(classified.get("summary") or {}), "review_status_counts": status_counts, "total": len(records)}
    _save_classified_candidates(classified)

    # If approved official, upsert priority + index
    if review_status == "approved" and official_level == "official":
        priority_payload = _load_forms_json(PRIORITY_FORMS_SUPPLEMENT_PATH, {"forms": [], "summary": {}})
        pforms = list(priority_payload.get("forms") or [])
        if not any(str(f.get("id")) == form_id for f in pforms):
            pforms.append(record)
        else:
            pforms = [record if str(f.get("id")) == form_id else f for f in pforms]
        priority_payload["forms"] = pforms
        priority_payload["generated_at"] = now
        priority_payload["summary"] = {
            **(priority_payload.get("summary") or {}),
            "total_forms": len(pforms),
            "approved_forms": sum(1 for f in pforms if f.get("review_status") == "approved"),
        }
        PRIORITY_FORMS_SUPPLEMENT_PATH.write_text(
            json.dumps(priority_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        index_payload = _load_forms_json(OFFICIAL_FORMS_INDEX_PATH, {"forms": [], "summary": {}})
        iforms = list(index_payload.get("forms") or [])
        if not any(str(f.get("id")) == form_id for f in iforms):
            iforms.append(record)
        else:
            iforms = [record if str(f.get("id")) == form_id else f for f in iforms]
        index_payload["forms"] = iforms
        index_payload["generated_at"] = now
        OFFICIAL_FORMS_INDEX_PATH.write_text(
            json.dumps(index_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "id": form_id,
        "form_title": form_title,
        "domain": domain,
        "review_status": review_status,
        "official_level": official_level,
        "downloadable": review_status == "approved" and official_level == "official",
        "local_path": rel,
        "size_bytes": len(content),
        "message": "Upload thành công" if review_status == "approved" else "Upload thành công, chờ duyệt",
    }


'''
    # Insert before review_classified endpoint
    anchor = "@router.post(\"/forms-catalog/candidates-full/{form_id}/review\")"
    if anchor not in text:
        raise SystemExit("review endpoint anchor not found")
    text = text.replace(anchor, endpoint + anchor, 1)
    print("OK: upload_official_form endpoint added")
else:
    print("SKIP: upload endpoint exists")

# 3) Soft error Vietnamese for official download endpoint (ensure not crashy message)
old_detail = 'detail="Bieu mau chua duoc duyet hoac khong ton tai."'
new_detail = 'detail="Biểu mẫu chưa được duyệt hoặc không tồn tại."'
if old_detail in text:
    text = text.replace(old_detail, new_detail)
    print("OK: soft VN detail message")

wp.write_text(text, encoding="utf-8", newline="\n")
print("ward_procedures.py updated")
