import sys, json, ast
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

print("=== STEP 7 CURRENT-STATE COMPLETION AUDIT ===")

checks = []

# 1 Audit gap
audit_script = Path("scripts/audit_forms_inventory.py")
inv_path = Path("notebook_data/forms/forms_inventory_report.json")
inv = json.loads(inv_path.read_text(encoding="utf-8")) if inv_path.exists() else {}
summary = inv.get("summary") or {}
c1 = audit_script.exists() and inv_path.exists() and int(summary.get("total_forms") or 0) > 0 and int(summary.get("broken_download_url") or 0) == 0
checks.append(("audit_gap", c1, {
    "audit_script": audit_script.exists(),
    "report": inv_path.exists(),
    "total_forms": summary.get("total_forms"),
    "official_forms": summary.get("official_forms"),
    "approved_forms": summary.get("approved_forms"),
    "forms_with_real_file": summary.get("forms_with_real_file"),
    "forms_without_file": summary.get("forms_without_file"),
    "broken_download_url": summary.get("broken_download_url"),
    "uploads_priority_official": (summary.get("grouped_by_source") or {}).get("uploads_priority_official"),
}))

# 2 Crawl DVC max 200
crawl = Path("scripts/crawl_forms_from_dvc.py")
report_path = Path("notebook_data/forms/dvc_forms_crawl_report.json")
rpt = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
crawl_text = crawl.read_text(encoding="utf-8") if crawl.exists() else ""
c2 = (
    crawl.exists()
    and "dichvucong" in crawl_text
    and "--max" in crawl_text
    and "default=200" in crawl_text
    and report_path.exists()
    and int(rpt.get("max_items") or 0) >= 200
    and int(rpt.get("seed_count") or 0) > 0
)
checks.append(("crawl_dvc_max_200", c2, {
    "script": crawl.exists(),
    "has_dvc": "dichvucong" in crawl_text,
    "default_max_200": "default=200" in crawl_text,
    "report_max_items": rpt.get("max_items"),
    "seed_count": rpt.get("seed_count"),
    "probe_status_counts": rpt.get("probe_status_counts"),
    "downloaded_from_network": rpt.get("downloaded_from_network"),
    "priority_official_files_report": (rpt.get("promote") or {}).get("priority_official_files"),
}))

# 3 Admin upload form
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
c3 = (
    "async def upload_official_form" in wp
    and "/forms-catalog/upload" in wp
    and "UploadFile" in wp
    and ("role != \"admin\"" in wp or "role != 'admin'" in wp)
)
checks.append(("admin_upload_form", c3, {
    "endpoint": "async def upload_official_form" in wp,
    "route": "/forms-catalog/upload" in wp,
    "UploadFile": "UploadFile" in wp,
    "admin_guard": ("role != \"admin\"" in wp or "role != 'admin'" in wp),
}))

# 4 only official/approved downloadable
c4 = (
    "def _get_approved_form_ids" in wp
    and "async def download_official_form_source" in wp
    and "approved_ids" in wp
)
checks.append(("official_approved_only_download", c4, {
    "approved_ids_helper": "def _get_approved_form_ids" in wp,
    "download_official": "async def download_official_form_source" in wp,
}))

# 5 missing file soft report
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
c5 = (
    "if (!canDownload)" in sr
    and "toast.error" in sr
    and ("Chưa có file chính thức" in sr or "chua co file" in sr.lower())
    and ("form_unavailable" in wp or "Chưa có file chính thức" in wp or "chua duoc duyet" in wp.lower() or "chưa được duyệt" in wp)
)
checks.append(("soft_missing_file", c5, {
    "frontend_guard": "if (!canDownload)" in sr,
    "frontend_toast": "toast.error" in sr,
    "frontend_msg": ("Chưa có file chính thức" in sr),
    "backend_soft": ("form_unavailable" in wp or "Chưa có file chính thức" in wp or "chưa được duyệt" in wp),
}))

# 6 >=50 real files in priority_official
pod = Path("data/uploads/forms/priority_official")
files = [p for p in pod.rglob("*") if p.is_file() and p.stat().st_size > 1024] if pod.exists() else []
c6 = len(files) >= 50
exts = {}
for f in files:
    exts[f.suffix.lower()] = exts.get(f.suffix.lower(), 0) + 1
checks.append(("priority_official_ge_50", c6, {
    "count": len(files),
    "exts": exts,
}))

# 7 Ask download no crash
c7 = (
    "const handleDownloadForm" in sr or "handleDownloadForm = async" in sr
    and "if (!canDownload)" in sr
    and "toast.error" in sr
    and not ("throw new Error" in sr and "Download failed" in sr)
)
checks.append(("ask_download_no_crash", c7, {
    "handler": ("handleDownloadForm" in sr),
    "guard": "if (!canDownload)" in sr,
    "toast": "toast.error" in sr,
    "no_throw_download_failed": not ("throw new Error" in sr and "Download failed" in sr),
}))

# syntax
for p in ["api/routers/ward_procedures.py", "scripts/crawl_forms_from_dvc.py", "scripts/audit_forms_inventory.py"]:
    try:
        ast.parse(Path(p).read_text(encoding="utf-8"))
        ok = True
        err = None
    except SyntaxError as e:
        ok = False
        err = str(e)
    checks.append((f"syntax:{p}", ok, {"error": err}))

# catalog consistency useful but not strict objective wording; still report
cat = json.loads(Path("notebook_data/forms/priority_official_forms.json").read_text(encoding="utf-8"))
forms = cat.get("forms") or []
approved = [f for f in forms if f.get("review_status") == "approved" or f.get("is_approved") is True]
with_path = [f for f in forms if f.get("local_path") or f.get("source_package_path")]
checks.append(("priority_catalog_report", True, {
    "total": len(forms),
    "approved": len(approved),
    "with_path": len(with_path),
}))

all_required = [c for c in checks if not c[0].startswith("syntax:") and c[0] != "priority_catalog_report"]
syntax = [c for c in checks if c[0].startswith("syntax:")]
passed = all(c[1] for c in all_required) and all(c[1] for c in syntax)

for name, ok, detail in checks:
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")

print("\nOVERALL_REQUIRED_PASS" if passed else "\nOVERALL_REQUIRED_FAIL")
print(passed)
