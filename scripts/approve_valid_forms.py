import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMS_DATA_DIR = PROJECT_ROOT / "notebook_data" / "forms"
REPORT_PATH = FORMS_DATA_DIR / "forms_inventory_report.json"

CATALOG_FILES = [
    FORMS_DATA_DIR / "forms_manifest.json",
    FORMS_DATA_DIR / "haiphong_official_forms_catalog.json",
    FORMS_DATA_DIR / "haiphong_official_form_index.json",
    FORMS_DATA_DIR / "official_forms_candidates_classified.json",
    FORMS_DATA_DIR / "priority_official_forms.json",
    FORMS_DATA_DIR / "priority_200_forms.json"
]

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {path.name}: {e}")
        return {}

def save_json(path: Path, data: dict):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Successfully updated {path.name}")
    except Exception as e:
        print(f"Error saving {path.name}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Approve valid official forms automatically.")
    parser.add_argument("--dry-run", action="store_true", help="Log the planned changes without applying them.")
    parser.add_argument("--apply", action="store_true", help="Apply changes directly to the catalog JSON files.")
    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        print("Please specify either --dry-run or --apply")
        return

    if not REPORT_PATH.exists():
        print(f"Report path {REPORT_PATH.name} not found. Run scripts/audit_forms_inventory.py first.")
        return

    report = load_json(REPORT_PATH)
    records = report.get("records", [])
    
    # Map valid form IDs
    valid_form_ids = {}
    for r in records:
        if r.get("has_physical_file") and r.get("is_valid"):
            valid_form_ids[str(r.get("form_id"))] = {
                "physical_path": r.get("physical_path"),
                "size_bytes": r.get("size_bytes")
            }

    print(f"Loaded inventory report. Found {len(valid_form_ids)} valid official forms with physical files.")

    approved_count = 0

    # Process each catalog file
    for filepath in CATALOG_FILES:
        if not filepath.exists():
            continue
        
        data = load_json(filepath)
        modified = False
        
        # 1. Handle manifest structure (which has "forms": { "domain_name": [ ... ] })
        if "forms" in data and isinstance(data["forms"], dict):
            for domain, items in data["forms"].items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    fid = str(item.get("id") or item.get("form_id") or "")
                    if fid in valid_form_ids:
                        info = valid_form_ids[fid]
                        # Plan changes
                        old_status = item.get("review_status")
                        old_level = item.get("official_level")
                        old_has_file = item.get("has_official_file")
                        
                        if old_status != "approved" or old_level != "official" or not old_has_file:
                            modified = True
                            approved_count += 1
                            if args.apply:
                                item["review_status"] = "approved"
                                item["official_level"] = "official"
                                item["has_official_file"] = True
                                # update paths and download URL
                                item["priority_path"] = info["physical_path"]
                                item["local_path"] = info["physical_path"]
                                item["download_url"] = f"/api/procedures/forms-catalog/official/{fid}/download"
                                if "has_download" not in item or not item["has_download"]:
                                    item["has_download"] = True
                            
                            if args.dry_run:
                                title_safe = str(item.get('title') or item.get('name') or '').encode('ascii', errors='ignore').decode('ascii')
                                print(f"[DRY-RUN] Plan to approve form in {filepath.name}: ID={fid}, Title={title_safe}")
                                print(f"          Status: {old_status} -> approved, Level: {old_level} -> official")

        # 2. Handle flat list structure (e.g. priority_official_forms.json may have "forms": [ ... ] or be a list directly)
        elif "forms" in data and isinstance(data["forms"], list):
            for item in data["forms"]:
                fid = str(item.get("id") or item.get("form_id") or "")
                if fid in valid_form_ids:
                    info = valid_form_ids[fid]
                    old_status = item.get("review_status")
                    old_level = item.get("official_level")
                    old_has_file = item.get("has_official_file")
                    
                    if old_status != "approved" or old_level != "official" or not old_has_file:
                        modified = True
                        approved_count += 1
                        if args.apply:
                            item["review_status"] = "approved"
                            item["official_level"] = "official"
                            item["has_official_file"] = True
                            item["priority_path"] = info["physical_path"]
                            item["local_path"] = info["physical_path"]
                            item["download_url"] = f"/api/procedures/forms-catalog/official/{fid}/download"
                            if "has_download" not in item or not item["has_download"]:
                                item["has_download"] = True
                        
                        if args.dry_run:
                            title_safe = str(item.get('title') or item.get('name') or '').encode('ascii', errors='ignore').decode('ascii')
                            print(f"[DRY-RUN] Plan to approve form in {filepath.name}: ID={fid}, Title={title_safe}")
                            print(f"          Status: {old_status} -> approved, Level: {old_level} -> official")

        # 3. Handle flat records list (official_forms_candidates_classified.json)
        elif "records" in data and isinstance(data["records"], list):
            for item in data["records"]:
                fid = str(item.get("id") or item.get("form_id") or "")
                if fid in valid_form_ids:
                    info = valid_form_ids[fid]
                    old_status = item.get("review_status")
                    old_level = item.get("official_level")
                    old_has_file = item.get("has_official_file")
                    
                    if old_status != "approved" or old_level != "official" or not old_has_file:
                        modified = True
                        approved_count += 1
                        if args.apply:
                            item["review_status"] = "approved"
                            item["official_level"] = "official"
                            item["has_official_file"] = True
                            item["priority_path"] = info["physical_path"]
                            item["local_path"] = info["physical_path"]
                            item["download_url"] = f"/api/procedures/forms-catalog/official/{fid}/download"
                            if "has_download" not in item or not item["has_download"]:
                                item["has_download"] = True
                        
                        if args.dry_run:
                            title_safe = str(item.get('title') or item.get('name') or '').encode('ascii', errors='ignore').decode('ascii')
                            print(f"[DRY-RUN] Plan to approve form in {filepath.name}: ID={fid}, Title={title_safe}")
                            print(f"          Status: {old_status} -> approved, Level: {old_level} -> official")

        if modified and args.apply:
            save_json(filepath, data)

    print(f"Processed catalog files.")
    if args.dry_run:
        print(f"[DRY-RUN] Planned to approve/update {approved_count} form entries.")
    if args.apply:
        print(f"[APPLY] Approved/updated {approved_count} form entries successfully.")

if __name__ == "__main__":
    main()
