import sys, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

inv = json.loads(Path("notebook_data/forms/forms_inventory_report.json").read_text(encoding="utf-8"))
print("top-level keys:", list(inv.keys()))
print("summary keys:", list(inv.get("summary", {}).keys()))
print("broken_download_url in summary:", "broken_download_url" in inv.get("summary", {}))
print("broken_download_url value:", inv.get("summary", {}).get("broken_download_url"))
print("broken_download_url type:", type(inv.get("summary", {}).get("broken_download_url")))

# Check if it's somewhere else
for k, v in inv.items():
    if "broken" in k.lower():
        print(f"found 'broken' in key '{k}': {v}")
    if isinstance(v, dict):
        for kk, vv in v.items():
            if "broken" in kk.lower():
                print(f"found 'broken' in summary.{kk}: {vv}")
