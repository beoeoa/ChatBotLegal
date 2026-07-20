import sys, json, ast
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

root = Path(".")
print("=== REQUIREMENT AUDIT STEP 7 ===")

# 1. Audit gap forms
audit_script = Path("scripts/audit_forms_inventory.py")
inv = Path("notebook_data/forms/forms_inventory_report.json")
print("audit_script", audit_script.exists())
print("inventory_report", inv.exists())
if inv.exists():
    data = json.loads(inv.read_text(encoding="utf-8"))
    s = data.get("summary") or {}
    for k in ["total_forms","official_forms","approved_forms","forms_with_real_file","forms_without_file","broken_download_url"]:
        print("inv", k, s.get(k))
    print("grouped_by_source", s.get("grouped_by_source"))

# 2. Crawl DVC max 200
crawl = Path("scripts/crawl_forms_from_dvc.py")
report = Path("notebook_data/forms/dvc_forms_crawl_report.json")
print("\ncrawl_script", crawl.exists())
print("crawl_report", report.exists())
if crawl.exists():
    t = crawl.read_text(encoding="utf-8")
    print("has max arg", "--max" in t)
    print("has promote_target", "promote-target" in t or "promote_target" in t)
    print("has dichvucong", "dichvucong" in t)
    try:
        ast.parse(t)
        print("crawl syntax OK")
    except SyntaxError as e:
        print("crawl syntax ERR", e)
if report.exists():
    r = json.loads(report.read_text(encoding="utf-8"))
    print("seed_count", r.get("seed_count"))
    print("max_items", r.get("max_items"))
    print("probe_status_counts", r.get("probe_status_counts"))
    print("downloaded_from_network", r.get("downloaded_from_network"))
    print("promoted", (r.get("promote") or {}).get("promoted_count"))
    print("priority_official_files report", (r.get("promote") or {}).get("priority_official_files"))

# 3. Admin upload-form
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
print("\nupload endpoint", "async def upload_official_form" in wp)
print("admin guard", "Chỉ admin mới được upload biểu mẫu" in wp or "get_request_role" in wp)
print("UploadFile import", "UploadFile" in wp)
print("route forms-catalog/upload", "/forms-catalog/upload" in wp)

# 4. only official/approved downloadable
print("\napproved-only download", "_get_approved_form_ids" in wp and "download_official_form_source" in wp)
print("seed soft unavailable", "form_unavailable" in wp or "Chưa có file chính thức" in wp)

# 5. soft missing file
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
print("\nfrontend soft toast", "toast.error" in sr and "Chưa có file chính thức" in sr)
print("canDownload guard", "if (!canDownload)" in sr)
print("no throw Download failed", not ("throw new Error" in sr and "Download failed" in sr))

# 6. >=50 real files in priority_official
pod = Path("data/uploads/forms/priority_official")
files = [p for p in pod.rglob("*") if p.is_file() and p.stat().st_size > 1024]
print("\npriority_official real files >1KB:", len(files))
print("threshold_met", len(files) >= 50)
exts = {}
for f in files:
    exts[f.suffix.lower()] = exts.get(f.suffix.lower(), 0) + 1
print("exts", exts)

# 7. priority catalog consistency
prio = json.loads(Path("notebook_data/forms/priority_official_forms.json").read_text(encoding="utf-8"))
forms = prio.get("forms") or []
print("priority catalog forms", len(forms))
print("priority catalog approved", sum(1 for f in forms if f.get("review_status")=="approved" or f.get("is_approved") is True))
print("priority catalog with path", sum(1 for f in forms if f.get("local_path") or f.get("source_package_path")))

# syntax ward_procedures
try:
    ast.parse(wp)
    print("\nward_procedures syntax OK")
except SyntaxError as e:
    print("\nward_procedures syntax ERR", e)

# list first 10 priority files
print("\nSample priority files:")
for f in sorted(files, key=lambda x: x.name)[:10]:
    print(" ", f.name, f.stat().st_size)
