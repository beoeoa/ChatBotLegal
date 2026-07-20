import sys, json
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
pod = Path("data/uploads/forms/priority_official")
files = [p for p in pod.rglob("*") if p.is_file() and p.stat().st_size > 1024]
print("priority_official real files:", len(files))
prio = json.loads(Path("notebook_data/forms/priority_official_forms.json").read_text(encoding="utf-8"))
forms = prio.get("forms") or []
print("priority catalog forms:", len(forms))
print("approved:", sum(1 for f in forms if f.get("review_status")=="approved"))
report = json.loads(Path("notebook_data/forms/dvc_forms_crawl_report.json").read_text(encoding="utf-8"))
print("dvc seed_count", report.get("seed_count"))
print("probe_status_counts", report.get("probe_status_counts"))
print("downloaded_from_network", report.get("downloaded_from_network"))
print("promoted", report.get("promote", {}).get("promoted_count"))
inv = json.loads(Path("notebook_data/forms/forms_inventory_report.json").read_text(encoding="utf-8"))
summary = inv.get("summary") or {}
for k in ["total_forms","official_forms","approved_forms","forms_with_real_file","forms_without_file","broken_download_url"]:
    print("inv", k, summary.get(k))
print("grouped_by_source", summary.get("grouped_by_source"))
