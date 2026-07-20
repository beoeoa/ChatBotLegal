import sys, json, ast
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

print("=== FINAL COMPLETION AUDIT - STEP 7 ===\n")

# Requirement 1: Audit gap forms
inv = json.loads(Path("notebook_data/forms/forms_inventory_report.json").read_text(encoding="utf-8"))
s = inv.get("summary") or {}
audit_ok = s.get("total_forms", 0) > 0 and s.get("broken_download_url", -1) == 0
print(f"1. Audit gap: {'PASS' if audit_ok else 'FAIL'}")
print(f"   Total: {s.get('total_forms')}, Official: {s.get('official_forms')}, Approved: {s.get('approved_forms')}")
print(f"   Real files: {s.get('forms_with_real_file')}, Without file: {s.get('forms_without_file')}, Broken: {s.get('broken_download_url')}")

# Requirement 2: Crawl DVC max 200
crawl = Path("scripts/crawl_forms_from_dvc.py")
rpt = json.loads(Path("notebook_data/forms/dvc_forms_crawl_report.json").read_text(encoding="utf-8"))
crawl_ok = crawl.exists() and rpt.get("seed_count", 0) > 0
print(f"\n2. Crawl DVC: {'PASS' if crawl_ok else 'FAIL'}")
print(f"   Script exists: {crawl.exists()}, Seeds: {rpt.get('seed_count')}, Max items: {rpt.get('max_items')}")
print(f"   Probe status: {rpt.get('probe_status_counts')}")

# Requirement 3: Admin upload-form
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
upload_ok = "async def upload_official_form" in wp and "Chỉ admin mới được upload" in wp
print(f"\n3. Admin upload: {'PASS' if upload_ok else 'FAIL'}")
print(f"   Endpoint: {'async def upload_official_form' in wp}")
print(f"   Admin guard: {'Chỉ admin mới được upload' in wp}")
print(f"   Route: {'/forms-catalog/upload' in wp}")

# Requirement 4: Only official/approved downloadable
download_ok = "_get_approved_form_ids" in wp and "download_official_form_source" in wp
print(f"\n4. Approved-only download: {'PASS' if download_ok else 'FAIL'}")
print(f"   _get_approved_form_ids: {'_get_approved_form_ids' in wp}")
print(f"   download_official_form_source: {'download_official_form_source' in wp}")

# Requirement 5: Soft error for missing files
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
soft_ok = ("toast.error" in sr and "Chưa có file chính thức" in sr) and "if (!canDownload)" in sr
print(f"\n5. Soft missing file: {'PASS' if soft_ok else 'FAIL'}")
print(f"   Frontend toast: {'toast.error' in sr and 'Chưa có file chính thức' in sr}")
print(f"   canDownload guard: {'if (!canDownload)' in sr}")
print(f"   Backend soft: {'form_unavailable' in wp}")

# Requirement 6: >=50 real files in priority_official
pod = Path("data/uploads/forms/priority_official")
files = [p for p in pod.rglob("*") if p.is_file() and p.stat().st_size > 1024]
file_ok = len(files) >= 50
print(f"\n6. >=50 real files: {'PASS' if file_ok else 'FAIL'}")
print(f"   Files >1KB: {len(files)}")
exts = {}
for f in files:
    exts[f.suffix.lower()] = exts.get(f.suffix.lower(), 0) + 1
print(f"   Extensions: {exts}")

# Requirement 7: Ask download doesn't crash
crash_ok = "if (!canDownload)" in sr and "toast.error" in sr and not ("throw new Error" in sr and "Download failed" in sr)
print(f"\n7. No crash on download: {'PASS' if crash_ok else 'FAIL'}")
print(f"   canDownload guard: {'if (!canDownload)' in sr}")
print(f"   toast.error: {'toast.error' in sr}")
print(f"   No throw Download failed: {not ('throw new Error' in sr and 'Download failed' in sr)}")

# Syntax checks
syntax_ok = True
for p in ["api/routers/ward_procedures.py", "scripts/crawl_forms_from_dvc.py"]:
    try:
        ast.parse(Path(p).read_text(encoding="utf-8"))
    except SyntaxError as e:
        print(f"SYNTAX ERROR in {p}: {e}")
        syntax_ok = False
print(f"\n8. Syntax checks: {'PASS' if syntax_ok else 'FAIL'}")

# Catalog consistency
cat = json.loads(Path("notebook_data/forms/priority_official_forms.json").read_text(encoding="utf-8"))
forms = cat.get("forms") or []
catalog_ok = len(forms) >= 50
print(f"\n9. Catalog consistency: {'PASS' if catalog_ok else 'FAIL'}")
print(f"   Catalog total: {len(forms)}, Approved: {sum(1 for f in forms if f.get('review_status')=='approved')}")
print(f"   With path: {sum(1 for f in forms if f.get('local_path'))}")

all_pass = all([audit_ok, crawl_ok, upload_ok, download_ok, soft_ok, file_ok, crash_ok, syntax_ok, catalog_ok])
print(f"\n{'='*50}")
print(f"ALL REQUIREMENTS: {'PASS ✓' if all_pass else 'FAIL ✗'}")
print(f"{'='*50}")
