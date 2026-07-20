from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(".").resolve()
PRIORITY_DIR = ROOT / "data" / "uploads" / "forms" / "priority_official"
CATALOG = ROOT / "notebook_data" / "forms" / "priority_official_forms.json"
INDEX = ROOT / "notebook_data" / "forms" / "haiphong_official_form_index.json"
CLASSIFIED = ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json"

def now():
    return datetime.now(timezone.utc).isoformat()

def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

files = [p for p in PRIORITY_DIR.rglob("*") if p.is_file() and p.stat().st_size > 1024]
print("disk files", len(files))

catalog = json.loads(CATALOG.read_text(encoding="utf-8")) if CATALOG.exists() else {"forms": [], "summary": {}}
forms = list(catalog.get("forms") or [])
by_id = {str(f.get("id")): f for f in forms}
by_path = {str(f.get("local_path") or f.get("source_package_path") or "").replace("\\", "/"): f for f in forms}

added = 0
updated = 0
for f in files:
    rel = str(f.relative_to(ROOT)).replace("\\", "/")
    digest = sha256(f)
    form_id = digest[:24]
    title = f.stem
    # try recover nicer title from classified
    rec = None
    if CLASSIFIED.exists():
        data = json.loads(CLASSIFIED.read_text(encoding="utf-8"))
        for r in data.get("records") or []:
            if str(r.get("id")) == form_id or str(r.get("sha256")) == digest:
                rec = r
                break
            rp = str(r.get("priority_path") or r.get("local_path") or r.get("file_path") or "").replace("\\", "/")
            if rp.endswith(f.name) or rp == rel:
                rec = r
                break
    title = (rec or {}).get("detected_form_name") or (rec or {}).get("form_title") or title
    domain = (rec or {}).get("suggested_domain") or (rec or {}).get("domain") or "unknown"
    row = {
        "id": form_id,
        "form_title": title,
        "domain": domain,
        "source_page_url": (rec or {}).get("source_page_url") or (rec or {}).get("source_url") or (rec or {}).get("page_url") or "https://local.invalid/admin-reviewed-form",
        "source_download_url": (rec or {}).get("source_download_url") or (rec or {}).get("download_url"),
        "source_package_path": rel,
        "local_path": rel,
        "file_name": f.name,
        "file_type": f.suffix.lstrip(".").lower() or "bin",
        "size_bytes": f.stat().st_size,
        "source_sha256": digest,
        "review_status": "approved",
        "official_level": "official",
        "is_approved": True,
        "catalog_status": "available_official_source",
        "is_canonical": True,
        "publisher": (rec or {}).get("publisher") or "Hai Phong / priority_official sync",
        "locality": "Hai Phong",
        "administrative_level": "commune_relevant",
        "priority_tier": "step7_priority_official",
        "retrieved_at": now(),
    }
    if form_id in by_id:
        by_id[form_id].update(row)
        updated += 1
    elif rel in by_path:
        old = by_path[rel]
        old.update(row)
        by_id[str(old.get("id") or form_id)] = old
        updated += 1
    else:
        by_id[form_id] = row
        added += 1

synced = list(by_id.values())
catalog = {
    "generated_at": now(),
    "summary": {
        "total_forms": len(synced),
        "total_available": len(synced),
        "approved_forms": sum(1 for x in synced if x.get("review_status") == "approved"),
        "with_real_file": sum(1 for x in synced if x.get("local_path")),
        "standalone_files": sum(1 for x in synced if not x.get("is_verbatim_page_slice")),
        "verbatim_pdf_slices": sum(1 for x in synced if x.get("is_verbatim_page_slice")),
        "disk_files": len(files),
        "errors": 0,
        "step7_synced": True,
    },
    "forms": synced,
    "errors": [],
}
CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
print("catalog total", len(synced), "added", added, "updated", updated)

# Ensure index has approved flags for these ids
if INDEX.exists():
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    iforms = list(idx.get("forms") or [])
    ids = {str(x.get("id")) for x in synced}
    paths = {str(x.get("local_path") or "") for x in synced}
    changed = 0
    seen = {str(f.get("id")) for f in iforms}
    for f in iforms:
        fid = str(f.get("id"))
        lp = str(f.get("source_package_path") or f.get("local_path") or "").replace("\\", "/")
        if fid in ids or lp in paths:
            f["review_status"] = "approved"
            f["is_approved"] = True
            f["catalog_status"] = "available_official_source"
            f["is_canonical"] = True
            changed += 1
    # append missing synced rows
    for row in synced:
        if str(row.get("id")) not in seen:
            iforms.append(row)
            changed += 1
    idx["forms"] = iforms
    idx["generated_at"] = now()
    INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    print("index updated rows", changed)

print("DONE sync")
