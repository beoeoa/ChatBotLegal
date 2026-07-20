import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
import json

root = Path(".")
print("=== FORMS DIRS ===")
for p in [
    "notebook_data/forms",
    "data/uploads/forms",
    "scripts",
    "api/routers",
]:
    pp = Path(p)
    print(p, "exists" if pp.exists() else "MISSING")
    if pp.exists() and pp.is_dir():
        for child in sorted(pp.iterdir())[:40]:
            print(" ", child.name, child.stat().st_size if child.is_file() else "[dir]")

print("\n=== KEY FORM FILES ===")
for p in [
    "notebook_data/forms/forms_manifest.json",
    "notebook_data/forms/haiphong_official_forms_catalog.json",
    "notebook_data/forms/haiphong_official_form_index.json",
    "notebook_data/forms/priority_official_forms.json",
    "notebook_data/forms/forms_inventory_report.json",
    "scripts/audit_forms_inventory.py",
    "scripts/crawl_forms_from_dvc.py",
]:
    pp = Path(p)
    print(("OK" if pp.exists() else "MISS"), p, pp.stat().st_size if pp.exists() else "")
