import sys, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# priority official
p = Path("notebook_data/forms/priority_official_forms.json")
data = json.loads(p.read_text(encoding="utf-8"))
print("priority type", type(data).__name__)
if isinstance(data, dict):
    print("keys", list(data.keys())[:20])
    items = data.get("forms") or data.get("items") or data.get("priority_forms") or []
    if not items and any(isinstance(v, list) for v in data.values()):
        for k,v in data.items():
            if isinstance(v, list):
                print("list key", k, len(v))
                items = v
                break
elif isinstance(data, list):
    items = data
else:
    items = []
print("priority count", len(items))
if items:
    print("sample keys", list(items[0].keys()) if isinstance(items[0], dict) else type(items[0]))
    print("sample", json.dumps(items[0], ensure_ascii=False)[:500])

# inventory report
inv = json.loads(Path("notebook_data/forms/forms_inventory_report.json").read_text(encoding="utf-8"))
print("\n=== inventory summary keys ===", list(inv.keys())[:30] if isinstance(inv, dict) else type(inv))
if isinstance(inv, dict):
    for k in ["total_forms","official_forms","reference_forms","candidate_forms","approved_forms","forms_with_real_file","forms_without_file","synthetic_or_seed_forms","broken_download_url"]:
        if k in inv:
            print(k, inv[k])
    if "summary" in inv and isinstance(inv["summary"], dict):
        for k,v in inv["summary"].items():
            print("summary.", k, v)

# real files in priority_official dir
pod = Path("data/uploads/forms/priority_official")
print("\n=== priority_official dir ===", pod.exists())
if pod.exists():
    files = [f for f in pod.rglob("*") if f.is_file()]
    print("files", len(files))
    for f in files[:20]:
        print(" ", f.relative_to(pod), f.stat().st_size)

# candidates
cand = Path("data/uploads/forms/official_candidates")
if cand.exists():
    files = [f for f in cand.rglob("*") if f.is_file()]
    print("official_candidates files", len(files))
    for f in files[:10]:
        print(" ", f.relative_to(cand), f.stat().st_size)
