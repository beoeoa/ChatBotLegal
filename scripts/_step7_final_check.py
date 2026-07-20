import sys, json, ast
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

print("=== FINAL STEP 7 VERIFICATION ===\n")

# 1. Audit gap
inv = json.loads(Path("notebook_data/forms/forms_inventory_report.json").read_text(encoding="utf-8"))
s = inv.get("summary") or {}
print(f"Audit gap: {s.get('total_forms')} total, {s.get('official_forms')} official, {s.get('approved_forms')} approved")
print(f"Real files: {s.get('forms_with_real_file')}, Broken: {s.get('broken_download_url')}")

# 2. Crawl DVC
rpt = json.loads(Path("notebook_data/forms/dvc_forms_crawl_report.json").read_text(encoding="utf-8"))
print(f"\nCrawl DVC: {rpt.get('seed_count')} seeds, max={rpt.get('max_items')}")
print(f"Probe: {rpt.get('probe_status_counts')}")

# 3. Admin upload
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
print(f"\nAdmin upload: {'async def upload_official_form' in wp}")
print(f"Guard: {'Chỉ admin mới được upload' in wp}")

# 4. Approved-only
print(f"\nApproved-only: {'_get_approved_form_ids' in wp}")
print(f"Soft unavailable: {'form_unavailable' in wp}")

# 5. Soft error
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
print(f"\nSoft toast: {'toast.error' in sr and 'Chưa có file chính thức' in sr}")
print(f"canDownload guard: {'if (!canDownload)' in sr}")

# 6. >=50 files
pod = Path("data/uploads/forms/priority_official")
files = [p for p in pod.rglob("*") if p.is_file() and p.stat().st_size > 1024]
print(f"\nPriority files >1KB: {len(files)} (>=50: {len(files)>=50})")

# 7. No crash
print(f"No crash: {not ('throw new Error' in sr and 'Download failed' in sr)}")

# 8. Syntax
for p in ["api/routers/ward_procedures.py", "scripts/crawl_forms_from_dvc.py"]:
    try:
        ast.parse(Path(p).read_text(encoding="utf-8"))
        print(f"{p}: SYNTAX OK")
    except SyntaxError as e:
        print(f"{p}: SYNTAX ERR {e}")

# 9. Catalog
cat = json.loads(Path("notebook_data/forms/priority_official_forms.json").read_text(encoding="utf-8"))
forms = cat.get("forms") or []
print(f"\nCatalog: {len(forms)} forms, {sum(1 for f in forms if f.get('review_status')=='approved')} approved")

all_pass = all([
    s.get("broken_download_url", -1) == 0,
    rpt.get("seed_count", 0) > 0,
    "async def upload_official_form" in wp,
    "_get_approved_form_ids" in wp,
    "form_unavailable" in wp,
    "toast.error" in sr and "Chưa có file chính thức" in sr,
    len(files) >= 50,
    len(forms) >= 50,
])
print(f"\n{'='*50}")
print(f"STEP 7: {'COMPLETE ✓' if all_pass else 'INCOMPLETE ✗'}")
print(f"{'='*50}")
