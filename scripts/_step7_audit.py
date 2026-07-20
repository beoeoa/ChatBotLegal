import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
import json

# 1. Check current priority_official file count
pod = Path("data/uploads/forms/priority_official")
real_files = [f for f in pod.rglob("*") if f.is_file() and f.stat().st_size > 1024]
print(f"priority_official real files (>1KB): {len(real_files)}")
for f in sorted(real_files)[:10]:
    print(f"  {f.name} ({f.stat().st_size:,} bytes)")

# 2. Check download endpoint in ward_procedures.py
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
print("\n=== download_official_form_source ===")
idx = wp.find("async def download_official_form_source")
if idx >= 0:
    for i, line in enumerate(wp[idx:idx+200].splitlines(), 1):
        print(f"{idx+i}: {line[:160]}")

# 3. Check StreamingResponse download handler
sr = Path("frontend/src/components/search/StreamingResponse.tsx").read_text(encoding="utf-8")
print("\n=== handleDownloadForm ===")
idx = sr.find("handleDownloadForm")
if idx >= 0:
    for i, line in enumerate(sr[idx:idx+150].splitlines(), 1):
        print(f"{idx+i}: {line[:160]}")

# 4. Check forms_manifest structure
fm = json.loads(Path("notebook_data/forms/forms_manifest.json").read_text(encoding="utf-8"))
forms = fm.get("forms", {})
print(f"\n=== forms_manifest domains ===")
for domain, items in forms.items():
    if isinstance(items, list):
        print(f"  {domain}: {len(items)} items")
        if items:
            print(f"    sample keys: {list(items[0].keys())[:10]}")

# 5. Check classified candidates
cc = Path("notebook_data/forms/official_forms_candidates_classified.json")
if cc.exists():
    data = json.loads(cc.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        print(f"\n=== classified candidates ===")
        for k,v in data.items():
            if isinstance(v, list):
                print(f"  {k}: {len(v)} items")
                if v:
                    print(f"    sample: {json.dumps(v[0], ensure_ascii=False)[:300]}")
