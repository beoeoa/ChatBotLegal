import sys, json, ast
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

print("=== STEP 7 FRESH COMPLETION AUDIT ===")

results = {}

# 1) audit gap
audit_script = Path("scripts/audit_forms_inventory.py")
inv_path = Path("notebook_data/forms/forms_inventory_report.json")
inv = json.loads(inv_path.read_text(encoding="utf-8")) if inv_path.exists() else {}
summary = inv.get("summary") or {}
results["audit_gap"] = bool(
    audit_script.exists()
    and inv_path.exists()
    and int(summary.get("total_forms") or 0) > 0
    and int(summary.get("broken_download_url") or -1) == 0
)

# 2) crawl dvc max 200
crawl = Path("scripts/crawl_forms_from_dvc.py")
report_path = Path("notebook_data/forms/dvc_forms_crawl_report.json")
crawl_text = crawl.read_text(encoding="utf-8") if crawl.exists() else ""
rpt = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
results["crawl_dvc"] = bool(
    crawl.exists()
    and "dichvucong" in crawl_text
    and "default=200" in crawl_text
    and int(rpt.get("max_items") or 0) >= 200
    and int(rpt.get("seed_count") or 0) > 0
)

# 3) admin upload
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
results["admin_upload"] = bool(
    "async def upload_official_form" in wp
    and "/forms-catalog/upload" in wp
    and "UploadFile" in wp
    and ("role != \"admin\"" in wp or "role != 'admin'" in wp)
)

# 4) official/approved only
results["approved_only"] = bool(
    "def _get_approved_form_ids" in wp
    and "async def download_official_form_source" in wp
)

# 5) soft missing
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
results["soft_missing"] = bool(
    "if (!canDownload)" in sr
    and "toast.error" in sr
    and "Chưa có file chính thức" in sr
    and ("form_unavailable" in wp or "Chưa có file chính thức" in wp or "chưa được duyệt" in wp)
)

# 6) >=50 real files
pod = Path("data/uploads/forms/priority_official")
files = [p for p in pod.rglob("*") if p.is_file() and p.stat().st_size > 1024] if pod.exists() else []
results["priority_ge_50"] = len(files) >= 50

# 7) no crash
results["no_crash"] = bool(
    "handleDownloadForm" in sr
    and "if (!canDownload)" in sr
    and "toast.error" in sr
    and not ("throw new Error" in sr and "Download failed" in sr)
)

# syntax
for p in [
    "api/routers/ward_procedures.py",
    "scripts/crawl_forms_from_dvc.py",
    "scripts/audit_forms_inventory.py",
]:
    try:
        ast.parse(Path(p).read_text(encoding="utf-8"))
        results[f"syntax:{p}"] = True
    except SyntaxError:
        results[f"syntax:{p}"] = False

# details
print("priority_files", len(files))
print("inventory", {
    "total": summary.get("total_forms"),
    "official": summary.get("official_forms"),
    "approved": summary.get("approved_forms"),
    "real": summary.get("forms_with_real_file"),
    "broken": summary.get("broken_download_url"),
    "uploads_priority_official": (summary.get("grouped_by_source") or {}).get("uploads_priority_official"),
})
print("crawl", {
    "max_items": rpt.get("max_items"),
    "seed_count": rpt.get("seed_count"),
    "probe": rpt.get("probe_status_counts"),
    "downloaded": rpt.get("downloaded_from_network"),
})

cat = json.loads(Path("notebook_data/forms/priority_official_forms.json").read_text(encoding="utf-8"))
forms = cat.get("forms") or []
print("catalog", {
    "total": len(forms),
    "approved": sum(1 for f in forms if f.get("review_status") == "approved" or f.get("is_approved") is True),
    "with_path": sum(1 for f in forms if f.get("local_path") or f.get("source_package_path")),
})

print("\nRESULTS:")
for k, v in results.items():
    print(("PASS" if v else "FAIL"), k)

overall = all(results.values())
print("\nOVERALL", "PASS" if overall else "FAIL")
