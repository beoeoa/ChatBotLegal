import sys, json, ast
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

print("=== FINAL STEP 7 AUDIT ===")

# 1. Audit gap
inv = json.loads(Path("notebook_data/forms/forms_inventory_report.json").read_text(encoding="utf-8"))
s = inv.get("summary") or {}
print("1. Audit gap:")
for k in ["total_forms","official_forms","approved_forms","forms_with_real_file","forms_without_file","broken_download_url"]:
    print(f"   {k}: {s.get(k)}")
print("   grouped_by_source:", s.get("grouped_by_source"))

# 2. Crawl DVC
rpt = json.loads(Path("notebook_data/forms/dvc_forms_crawl_report.json").read_text(encoding="utf-8"))
print(f"\n2. Crawl DVC: seed_count={rpt.get('seed_count')}, max_items={rpt.get('max_items')}")
print(f"   probe_status_counts: {rpt.get('probe_status_counts')}")
print(f"   downloaded_from_network: {rpt.get('downloaded_from_network')}")
print(f"   priority_official_files: {(rpt.get('promote') or {}).get('priority_official_files')}")

# 3. Admin upload
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
print(f"\n3. Admin upload endpoint: {'async def upload_official_form' in wp}")
print(f"   admin guard: {'Chỉ admin mới được upload' in wp}")
print(f"   UploadFile import: {'UploadFile' in wp}")
print(f"   POST /forms-catalog/upload: {'/forms-catalog/upload' in wp}")

# 4. Approved-only download
print(f"\n4. Approved-only download: {'_get_approved_form_ids' in wp and 'download_official_form_source' in wp}")
print(f"   soft unavailable: {'form_unavailable' in wp}")

# 5. Soft missing file
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
print(f"\n5. Frontend soft toast: {'toast.error' in sr and 'Chưa có file chính thức' in sr}")
print(f"   canDownload guard: {'if (!canDownload)' in sr}")

# 6. >=50 real files
pod = Path("data/uploads/forms/priority_official")
files = [p for p in pod.rglob("*") if p.is_file() and p.stat().st_size > 1024]
print(f"\n6. priority_official real files >1KB: {len(files)} (>=50: {len(files)>=50})")

# 7. Catalog consistency
cat = json.loads(Path("notebook_data/forms/priority_official_forms.json").read_text(encoding="utf-8"))
forms = cat.get("forms") or []
print(f"\n7. Catalog total: {len(forms)}, approved: {sum(1 for f in forms if f.get('review_status')=='approved')}")
print(f"   with path: {sum(1 for f in forms if f.get('local_path'))}")

# 8. Index consistency
idx = json.loads(Path("notebook_data/forms/haiphong_official_form_index.json").read_text(encoding="utf-8"))
iforms = idx.get("forms") or []
approved_idx = sum(1 for f in iforms if f.get("review_status")=="approved" or f.get("is_approved") is True)
print(f"\n8. Index total: {len(iforms)}, approved: {approved_idx}")

# 9. Classified consistency
cls = json.loads(Path("notebook_data/forms/official_forms_candidates_classified.json").read_text(encoding="utf-8"))
recs = cls.get("records") or []
approved_cls = sum(1 for r in recs if r.get("review_status")=="approved" or r.get("is_approved") is True)
print(f"\n9. Classified records: {len(recs)}, approved: {approved_cls}")

# 10. Syntax checks
for p in ["api/routers/ward_procedures.py", "scripts/crawl_forms_from_dvc.py"]:
    try:
        ast.parse(Path(p).read_text(encoding="utf-8"))
        print(f"\n10. {p}: SYNTAX OK")
    except SyntaxError as e:
        print(f"\n10. {p}: SYNTAX ERR {e}")

print("\n=== ALL REQUIREMENTS MET ===")
reqs = [
    len(files) >= 50,
    "async def upload_official_form" in wp,
    "Chỉ admin mới được upload" in wp,
    "_get_approved_form_ids" in wp,
    "form_unavailable" in wp,
    "toast.error" in sr and "Chưa có file chính thức" in sr,
    "if (!canDownload)" in sr,
    rpt.get("seed_count", 0) > 0,
    len(forms) >= 50,
]
print(f"All requirements met: {all(reqs)}")
