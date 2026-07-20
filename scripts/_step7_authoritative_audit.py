import sys, json, ast
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

print("=== STEP 7 AUTHORITATIVE COMPLETION AUDIT ===")
results = []

def check(name, cond, detail=None):
    print(("PASS" if cond else "FAIL"), name, detail if detail is not None else "")
    results.append(bool(cond))
    return bool(cond)

# 1 audit gap
audit_script = Path("scripts/audit_forms_inventory.py")
inv_path = Path("notebook_data/forms/forms_inventory_report.json")
inv = json.loads(inv_path.read_text(encoding="utf-8")) if inv_path.exists() else {}
summary = inv.get("summary") or {}
broken = summary.get("broken_download_url")
check("audit_gap", audit_script.exists() and inv_path.exists() and int(summary.get("total_forms") or 0) > 0 and broken == 0, {
    "total": summary.get("total_forms"),
    "official": summary.get("official_forms"),
    "approved": summary.get("approved_forms"),
    "real": summary.get("forms_with_real_file"),
    "without": summary.get("forms_without_file"),
    "broken": broken,
    "uploads_priority_official": (summary.get("grouped_by_source") or {}).get("uploads_priority_official"),
})

# 2 crawl dvc max 200
crawl = Path("scripts/crawl_forms_from_dvc.py")
report_path = Path("notebook_data/forms/dvc_forms_crawl_report.json")
crawl_text = crawl.read_text(encoding="utf-8") if crawl.exists() else ""
rpt = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
check("crawl_dvc_max_200", crawl.exists() and "dichvucong" in crawl_text and "default=200" in crawl_text and int(rpt.get("max_items") or 0) >= 200 and int(rpt.get("seed_count") or 0) > 0, {
    "max_items": rpt.get("max_items"),
    "seed_count": rpt.get("seed_count"),
    "probe": rpt.get("probe_status_counts"),
    "downloaded": rpt.get("downloaded_from_network"),
    "priority_files_report": (rpt.get("promote") or {}).get("priority_official_files"),
})

# 3 admin upload
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
check("admin_upload_form", "async def upload_official_form" in wp and "/forms-catalog/upload" in wp and "UploadFile" in wp and ("role != \"admin\"" in wp or "role != 'admin'" in wp))

# 4 approved only download
check("approved_only_download", "def _get_approved_form_ids" in wp and "async def download_official_form_source" in wp and "approved_ids" in wp)

# 5 soft missing file
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
check("soft_missing_file", "if (!canDownload)" in sr and "toast.error" in sr and "Chưa có file chính thức" in sr and ("form_unavailable" in wp or "Chưa có file chính thức" in wp or "chưa được duyệt" in wp))

# 6 >=50 real files
pod = Path("data/uploads/forms/priority_official")
files = [p for p in pod.rglob("*") if p.is_file() and p.stat().st_size > 1024] if pod.exists() else []
exts = {}
for f in files:
    exts[f.suffix.lower()] = exts.get(f.suffix.lower(), 0) + 1
check("priority_official_ge_50", len(files) >= 50, {"count": len(files), "exts": exts})

# 7 no crash
check("ask_download_no_crash", "handleDownloadForm" in sr and "if (!canDownload)" in sr and "toast.error" in sr and not ("throw new Error" in sr and "Download failed" in sr))

# syntax
for p in ["api/routers/ward_procedures.py", "scripts/crawl_forms_from_dvc.py", "scripts/audit_forms_inventory.py"]:
    try:
        ast.parse(Path(p).read_text(encoding="utf-8"))
        check(f"syntax:{p}", True)
    except SyntaxError as e:
        check(f"syntax:{p}", False, str(e))

# catalog consistency report
cat = json.loads(Path("notebook_data/forms/priority_official_forms.json").read_text(encoding="utf-8"))
forms = cat.get("forms") or []
check("priority_catalog_present", len(forms) >= 50, {
    "total": len(forms),
    "approved": sum(1 for f in forms if f.get("review_status") == "approved" or f.get("is_approved") is True),
    "with_path": sum(1 for f in forms if f.get("local_path") or f.get("source_package_path")),
})

print("\nOVERALL", "PASS" if all(results) else "FAIL")
print("PASS_COUNT", sum(1 for x in results if x), "/", len(results))
