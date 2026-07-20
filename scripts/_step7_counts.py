import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
import json

# Count total real form files across all dirs
total = 0
for d in [
    "data/uploads/forms/priority_official",
    "data/uploads/forms/official_candidates",
    "data/uploads/forms",
]:
    dd = Path(d)
    if not dd.exists():
        continue
    files = [f for f in dd.rglob("*") if f.is_file() and f.stat().st_size > 1024]
    exts = {}
    for f in files:
        ext = f.suffix.lower() or f.suffixes[-1].lower() if f.suffixes else ""
        exts[ext] = exts.get(ext, 0) + 1
    print(f"{d}: {len(files)} files (>1KB)")
    for ext, cnt in sorted(exts.items()):
        print(f"  {ext}: {cnt}")
    total += len(files)
print(f"\nTotal real form files (>1KB): {total}")

# Check if DVC crawler exists
dvc_script = Path("scripts/crawl_forms_from_dvc.py")
print(f"\ncrawl_forms_from_dvc.py exists: {dvc_script.exists()}")

# Check admin upload endpoint in ward_procedures
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
print(f"\nupload_form endpoint: {'upload_form' in wp}")
print(f"POST /forms-catalog/upload: {'upload' in wp and 'POST' in wp}")

# Check if main.py registers upload
main = Path("api/main.py").read_text(encoding="utf-8")
print(f"\nmain.py has upload router: {'upload' in main.lower()}")

# Check forms_manifest.json structure for DVC URLs
fm = json.loads(Path("notebook_data/forms/forms_manifest.json").read_text(encoding="utf-8"))
forms = fm.get("forms", {})
dvc_urls = 0
for domain, items in forms.items():
    if isinstance(items, list):
        for item in items:
            url = item.get("full_url") or item.get("url") or ""
            if "dichvucong" in url.lower() or "dvc.gov.vn" in url.lower():
                dvc_urls += 1
print(f"DVC URLs in forms_manifest: {dvc_urls}")
