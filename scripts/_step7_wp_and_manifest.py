import sys, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# Check ward_procedures router for download/form endpoints
wp = Path("api/routers/ward_procedures.py").read_text(encoding="utf-8")
print("=== ward_procedures endpoints ===")
for i, line in enumerate(wp.splitlines(), 1):
    if "@router" in line or "def " in line:
        print(f"{i}: {line}")

# Check forms_manifest structure
fm = json.loads(Path("notebook_data/forms/forms_manifest.json").read_text(encoding="utf-8"))
print("\n=== forms_manifest ===")
if isinstance(fm, dict):
    print("keys", list(fm.keys())[:20])
    items = fm.get("forms") or fm.get("items") or []
    if not items and any(isinstance(v, list) for v in fm.values()):
        for k,v in fm.items():
            if isinstance(v, list):
                items = v
                print("list key", k, len(v))
                break
elif isinstance(fm, list):
    items = fm
else:
    items = []
print("manifest count", len(items))
if items:
    print("sample keys", list(items[0].keys()))
    print("sample", json.dumps(items[0], ensure_ascii=False)[:500])

# Check haiphong_official_form_index
fi = json.loads(Path("notebook_data/forms/haiphong_official_form_index.json").read_text(encoding="utf-8"))
print("\n=== haiphong_official_form_index ===")
if isinstance(fi, dict):
    print("keys", list(fi.keys())[:20])
    items = fi.get("forms") or fi.get("items") or []
    if not items and any(isinstance(v, list) for v in fi.values()):
        for k,v in fi.items():
            if isinstance(v, list):
                items = v
                print("list key", k, len(v))
                break
elif isinstance(fi, list):
    items = fi
else:
    items = []
print("index count", len(items))
if items:
    print("sample keys", list(items[0].keys()))
    print("sample", json.dumps(items[0], ensure_ascii=False)[:500])
