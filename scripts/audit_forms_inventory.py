import json
import os
import re
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMS_DATA_DIR = PROJECT_ROOT / "notebook_data" / "forms"
OFFICIAL_FORMS_FILES_DIR = PROJECT_ROOT / "data" / "uploads" / "forms" / "official_candidates"
PRIORITY_FORMS_FILES_DIR = PROJECT_ROOT / "data" / "uploads" / "forms" / "priority_official"
REPORT_PATH = FORMS_DATA_DIR / "forms_inventory_report.json"

def _validate_form_file_integrity(path_obj: Path) -> tuple[bool, str]:
    if not path_obj.exists() or not path_obj.is_file():
        return False, "missing_file"
    try:
        size = path_obj.stat().st_size
    except OSError:
        return False, "stat_error"
    if size < 256:
        return False, f"too_small:{size}"

    try:
        with open(path_obj, "rb") as f:
            head = f.read(256)
    except OSError:
        return False, "read_error"

    lowered = head.lower()
    if b"mock word document" in lowered or b"seed form" in lowered:
        return False, "mock_or_seed_placeholder"
    if b"<!doctype html" in lowered or b"<html" in lowered:
        return False, "html_error_page"

    name_lower = path_obj.name.lower()
    # PDF
    if name_lower.endswith(".pdf") or name_lower.endswith(".pdf.pdf"):
        if head.startswith(b"%PDF"):
            return True, "ok_pdf"
        return False, "bad_pdf_magic"
    # DOCX
    if name_lower.endswith(".docx") or name_lower.endswith(".docx.docx"):
        try:
            if not zipfile.is_zipfile(path_obj):
                return False, "not_zip_container"
            with zipfile.ZipFile(path_obj, "r") as zf:
                names = set(zf.namelist())
            if "[Content_Types].xml" not in names:
                return False, "missing_content_types_xml"
            if not any(n.startswith("word/") for n in names):
                return False, "missing_word_parts"
            return True, "ok_docx"
        except Exception as exc:
            return False, f"docx_open_error:{exc}"
    # DOC (OLE)
    if name_lower.endswith(".doc") or name_lower.endswith(".doc.doc"):
        if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return True, "ok_doc_ole"
        if head.startswith(b"PK"):
            return False, "zip_container_but_doc_extension"
        return False, "bad_doc_magic"

    return True, "ok_other"

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {path.name}: {e}")
        return {}

def main():
    print("Starting forms inventory audit...")
    os.makedirs(FORMS_DATA_DIR, exist_ok=True)

    # 1. Scan physical files
    physical_files = {}
    for folder, folder_type in [(OFFICIAL_FORMS_FILES_DIR, "official_candidates"), (PRIORITY_FORMS_FILES_DIR, "priority_official")]:
        if not folder.exists():
            continue
        for file_path in folder.glob("**/*"):
            if not file_path.is_file():
                continue
            rel_path = file_path.relative_to(PROJECT_ROOT)
            ok, reason = _validate_form_file_integrity(file_path)
            physical_files[str(rel_path).replace("\\", "/")] = {
                "name": file_path.name,
                "size_bytes": file_path.stat().st_size,
                "folder_type": folder_type,
                "is_valid": ok,
                "integrity_status": reason
            }

    print(f"Scanned {len(physical_files)} physical form files.")

    # 2. Match with metadata databases
    matched_records = []
    
    # Load all metadata catalogs
    manifest = load_json(FORMS_DATA_DIR / "forms_manifest.json")
    classified = load_json(FORMS_DATA_DIR / "official_forms_candidates_classified.json")
    catalog = load_json(FORMS_DATA_DIR / "haiphong_official_forms_catalog.json")
    index = load_json(FORMS_DATA_DIR / "haiphong_official_form_index.json")
    priority_200 = load_json(FORMS_DATA_DIR / "priority_200_forms.json")
    priority_official = load_json(FORMS_DATA_DIR / "priority_official_forms.json")

    # Combine all records
    all_metadata_forms = []
    
    # Extract from manifest
    if isinstance(manifest, dict) and "forms" in manifest:
        for domain, items in manifest["forms"].items():
            for item in items:
                all_metadata_forms.append(item)
                
    # Extract from classified candidates
    if isinstance(classified, dict) and "records" in classified:
        all_metadata_forms.extend(classified["records"])
        
    # Extract from catalog
    if isinstance(catalog, dict) and "forms" in catalog:
        all_metadata_forms.extend(catalog["forms"])
        
    # Extract from index
    if isinstance(index, dict) and "forms" in index:
        all_metadata_forms.extend(index["forms"])

    # Extract from priority catalogs
    if isinstance(priority_200, dict) and "forms" in priority_200:
        all_metadata_forms.extend(priority_200["forms"])
    if isinstance(priority_official, dict) and "forms" in priority_official:
        all_metadata_forms.extend(priority_official["forms"])

    # Dedup and evaluate
    seen_ids = set()
    for form in all_metadata_forms:
        if not isinstance(form, dict):
            continue
        form_id = str(form.get("id") or form.get("form_id") or "")
        if not form_id or form_id in seen_ids:
            continue
        seen_ids.add(form_id)

        # Check paths
        possible_paths = []
        for key in ["priority_path", "local_path", "file_path", "source_package_path"]:
            p = form.get(key)
            if p:
                possible_paths.append(str(p).replace("\\", "/"))

        matched_file = None
        for path_hint in possible_paths:
            # Check direct match
            if path_hint in physical_files:
                matched_file = path_hint
                break
            # Check relative match
            for k in physical_files:
                if k.endswith(path_hint) or path_hint.endswith(k):
                    matched_file = k
                    break
            if matched_file:
                break

        # Fallback search by form name or ID in filename
        if not matched_file:
            for k, val in physical_files.items():
                if form_id in val["name"]:
                    matched_file = k
                    break

        # Record audit details
        audit_record = {
            "form_id": form_id,
            "title": form.get("title") or form.get("name") or form.get("display_name") or "Không rõ tên",
            "domain": form.get("domain") or "unknown",
            "official_level": form.get("official_level") or "reference",
            "review_status": form.get("review_status") or "candidate_pending_review",
            "has_physical_file": matched_file is not None,
            "physical_path": matched_file,
            "is_valid": physical_files[matched_file]["is_valid"] if matched_file else False,
            "integrity_status": physical_files[matched_file]["integrity_status"] if matched_file else "missing_file",
            "size_bytes": physical_files[matched_file]["size_bytes"] if matched_file else 0,
        }
        matched_records.append(audit_record)

    # 3. Save report
    report = {
        "generated_at": "2026-07-10T14:14:00Z",
        "total_metadata_records": len(matched_records),
        "total_physical_files": len(physical_files),
        "valid_official_forms_count": sum(1 for r in matched_records if r["has_physical_file"] and r["is_valid"]),
        "missing_files_count": sum(1 for r in matched_records if not r["has_physical_file"]),
        "invalid_files_count": sum(1 for r in matched_records if r["has_physical_file"] and not r["is_valid"]),
        "records": matched_records
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Inventory report written to {REPORT_PATH}")
    print(f"  Valid official forms: {report['valid_official_forms_count']}")
    print(f"  Missing files: {report['missing_files_count']}")
    print(f"  Invalid files: {report['invalid_files_count']}")

if __name__ == "__main__":
    main()
