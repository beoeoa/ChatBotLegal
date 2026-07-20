#!/usr/bin/env python3
"""Audit form file integrity (PDF/DOCX/DOC) without modifying data.

Checks magic bytes / container validity for:
- notebook_data/forms/*.json catalogs
- data/uploads/forms/**

Outputs:
- console summary
- notebook_data/forms/forms_integrity_report.json
"""

from __future__ import annotations

import json
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = PROJECT_ROOT / "notebook_data" / "forms"
UPLOADS_DIR = PROJECT_ROOT / "data" / "uploads" / "forms"
REPORT_PATH = FORMS_DIR / "forms_integrity_report.json"

SOURCE_FILES = {
    "forms_manifest": FORMS_DIR / "forms_manifest.json",
    "official_catalog": FORMS_DIR / "haiphong_official_forms_catalog.json",
    "official_index": FORMS_DIR / "haiphong_official_form_index.json",
    "priority_official": FORMS_DIR / "priority_official_forms.json",
}

FORM_EXTS = {".doc", ".docx", ".pdf", ".xls", ".xlsx", ".rtf", ".zip"}
MIN_BYTES = 256


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _first_str(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _resolve_local_path(raw: Any) -> Optional[Path]:
    text = _first_str(raw)
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://") or text.startswith("/api/"):
        return None
    candidate = Path(text)
    candidates = []
    if candidate.is_absolute():
        candidates.append(candidate)
    else:
        candidates.append(PROJECT_ROOT / candidate)
        candidates.append(PROJECT_ROOT / Path(text.replace("\\", "/")))
        candidates.append(UPLOADS_DIR / candidate)
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def _check_pdf(path: Path) -> Tuple[bool, str]:
    try:
        head = path.read_bytes()[:8]
    except OSError as exc:
        return False, f"read_error:{exc}"
    if not head.startswith(b"%PDF"):
        if head.lstrip().startswith(b"<") or b"html" in head.lower():
            return False, "html_or_error_page_not_pdf"
        return False, f"bad_pdf_magic:{head[:8]!r}"
    return True, "ok_pdf"


def _check_docx(path: Path) -> Tuple[bool, str]:
    try:
        if not zipfile.is_zipfile(path):
            head = path.read_bytes()[:32]
            if head.lstrip().startswith(b"<") or b"html" in head.lower():
                return False, "html_or_error_page_not_docx"
            return False, "not_zip_container"
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            if "[Content_Types].xml" not in names:
                return False, "missing_content_types_xml"
            if not any(n.startswith("word/") for n in names):
                return False, "missing_word_parts"
        return True, "ok_docx"
    except Exception as exc:  # noqa: BLE001
        return False, f"docx_open_error:{exc}"


def _check_doc(path: Path) -> Tuple[bool, str]:
    try:
        head = path.read_bytes()[:8]
    except OSError as exc:
        return False, f"read_error:{exc}"
    # OLE compound file magic
    if head.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"):
        return True, "ok_doc_ole"
    if head.startswith(b"PK"):
        return False, "zip_container_but_doc_extension"
    if head.lstrip().startswith(b"<") or b"html" in head.lower():
        return False, "html_or_error_page_not_doc"
    # Unknown binary: mark needs_review rather than hard invalid if size is decent
    size = path.stat().st_size
    if size >= 2048:
        return False, "unknown_doc_magic_needs_review"
    return False, f"bad_doc_magic:{head[:8]!r}"


def validate_form_file(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "path": str(path.relative_to(PROJECT_ROOT)) if str(path).startswith(str(PROJECT_ROOT)) else str(path),
        "absolute_path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "extension": path.suffix.lower(),
        "valid": False,
        "reason": "unknown",
    }
    if not path.exists() or not path.is_file():
        result["reason"] = "missing_file"
        return result
    size = result["size_bytes"]
    if size < MIN_BYTES:
        result["reason"] = f"too_small:{size}"
        return result

    # Detect mislabeled HTML/error pages regardless of extension
    try:
        head = path.read_bytes()[:256]
    except OSError as exc:
        result["reason"] = f"read_error:{exc}"
        return result
    lowered = head.lower()
    if b"mock word document" in lowered or b"seed form" in lowered:
        result["reason"] = "mock_or_seed_placeholder"
        return result
    if b"<!doctype html" in lowered or b"<html" in lowered:
        result["reason"] = "html_error_page"
        return result

    ext = path.suffix.lower()
    # Handle double extensions like .pdf.pdf / .docx.docx
    name_lower = path.name.lower()
    if name_lower.endswith(".pdf") or name_lower.endswith(".pdf.pdf"):
        ok, reason = _check_pdf(path)
    elif name_lower.endswith(".docx") or name_lower.endswith(".docx.docx"):
        ok, reason = _check_docx(path)
    elif name_lower.endswith(".doc") or name_lower.endswith(".doc.doc"):
        ok, reason = _check_doc(path)
    elif ext == ".pdf":
        ok, reason = _check_pdf(path)
    elif ext == ".docx":
        ok, reason = _check_docx(path)
    elif ext == ".doc":
        ok, reason = _check_doc(path)
    else:
        # Other form-like files: only basic size/html checks
        ok, reason = True, f"skipped_deep_check:{ext or 'no_ext'}"

    result["valid"] = bool(ok)
    result["reason"] = reason
    return result


def iter_catalog_paths() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for source_name, path in SOURCE_FILES.items():
        data = _load_json(path)
        if data is None:
            continue
        items = data.get("forms") if isinstance(data, dict) else data
        if isinstance(items, dict):
            # forms_manifest style: domain -> list
            flat = []
            for domain, vals in items.items():
                for item in vals if isinstance(vals, list) else []:
                    if isinstance(item, dict):
                        item = dict(item)
                        item.setdefault("domain", domain)
                        flat.append(item)
            items = flat
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            local = _resolve_local_path(
                _first_str(
                    item.get("local_path"),
                    item.get("source_package_path"),
                    item.get("priority_path"),
                    item.get("file_path"),
                    item.get("path"),
                )
            )
            if not local:
                continue
            rec = validate_form_file(local)
            rec.update(
                {
                    "source_dataset": source_name,
                    "form_id": _first_str(item.get("id"), item.get("form_id")),
                    "form_title": _first_str(
                        item.get("form_title"),
                        item.get("title"),
                        item.get("name"),
                        item.get("file_name"),
                    ),
                    "review_status": _first_str(item.get("review_status")),
                    "official_level": _first_str(item.get("official_level")),
                }
            )
            records.append(rec)
    return records


def iter_upload_paths() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not UPLOADS_DIR.exists():
        return records
    for path in sorted(UPLOADS_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in FORM_EXTS and not any(
            path.name.lower().endswith(ext + ext) for ext in [".pdf", ".doc", ".docx"]
        ):
            # still include double-ext files
            if not (
                path.name.lower().endswith(".pdf.pdf")
                or path.name.lower().endswith(".doc.doc")
                or path.name.lower().endswith(".docx.docx")
            ):
                continue
        rec = validate_form_file(path)
        parent = path.parent.relative_to(UPLOADS_DIR).as_posix() if path.parent != UPLOADS_DIR else "."
        rec.update(
            {
                "source_dataset": f"uploads:{parent}",
                "form_id": path.name,
                "form_title": path.name,
            }
        )
        records.append(rec)
    return records


def dedupe(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = {}
    for rec in records:
        key = rec.get("absolute_path") or rec.get("path")
        prev = seen.get(key)
        if prev is None:
            seen[key] = rec
            continue
        # Prefer invalid over valid? keep first invalid reason if either invalid
        if not rec.get("valid") and prev.get("valid"):
            seen[key] = rec
    return list(seen.values())


def build_report() -> Dict[str, Any]:
    records = dedupe(iter_catalog_paths() + iter_upload_paths())
    valid = [r for r in records if r.get("valid")]
    invalid = [r for r in records if not r.get("valid")]
    reason_counts = Counter(r.get("reason") for r in invalid)
    ext_valid = Counter(r.get("extension") for r in valid)
    ext_invalid = Counter(r.get("extension") for r in invalid)

    # Priority official focus
    priority = [
        r
        for r in records
        if "priority_official" in str(r.get("path") or "")
        or str(r.get("source_dataset") or "").endswith("priority_official")
        or "uploads:priority_official" == str(r.get("source_dataset") or "")
    ]
    priority_invalid = [r for r in priority if not r.get("valid")]

    report = {
        "generated_at": _now_iso(),
        "project_root": str(PROJECT_ROOT),
        "summary": {
            "total_checked": len(records),
            "valid_files": len(valid),
            "invalid_files": len(invalid),
            "priority_official_checked": len(priority),
            "priority_official_invalid": len(priority_invalid),
            "invalid_reason_counts": dict(sorted(reason_counts.items(), key=lambda x: (-x[1], str(x[0])))),
            "valid_by_extension": dict(sorted(ext_valid.items())),
            "invalid_by_extension": dict(sorted(ext_invalid.items())),
        },
        "invalid_files": [
            {
                "path": r.get("path"),
                "form_id": r.get("form_id"),
                "form_title": r.get("form_title"),
                "size_bytes": r.get("size_bytes"),
                "reason": r.get("reason"),
                "source_dataset": r.get("source_dataset"),
                "review_status": r.get("review_status"),
                "official_level": r.get("official_level"),
            }
            for r in sorted(invalid, key=lambda x: (str(x.get("reason")), str(x.get("path"))))
        ],
        "valid_files_sample": [
            {
                "path": r.get("path"),
                "form_title": r.get("form_title"),
                "size_bytes": r.get("size_bytes"),
                "reason": r.get("reason"),
            }
            for r in valid[:40]
        ],
        "priority_official_invalid": [
            {
                "path": r.get("path"),
                "form_title": r.get("form_title"),
                "size_bytes": r.get("size_bytes"),
                "reason": r.get("reason"),
            }
            for r in priority_invalid
        ],
        "notes": [
            "Read-only integrity audit. No form data modified.",
            "PDF must start with %PDF.",
            "DOCX must be a valid ZIP with [Content_Types].xml and word/ parts.",
            "DOC expects OLE magic D0 CF 11 E0; otherwise marked invalid/needs_review.",
            "HTML error pages and mock/seed placeholders are invalid.",
        ],
    }
    return report


def print_summary(report: Dict[str, Any]) -> None:
    s = report["summary"]
    print("=== FORM FILE INTEGRITY AUDIT ===")
    print(f"generated_at: {report['generated_at']}")
    print(f"total_checked: {s['total_checked']}")
    print(f"valid_files: {s['valid_files']}")
    print(f"invalid_files: {s['invalid_files']}")
    print(f"priority_official_checked: {s['priority_official_checked']}")
    print(f"priority_official_invalid: {s['priority_official_invalid']}")
    print("\nInvalid reasons:")
    for reason, count in (s.get("invalid_reason_counts") or {}).items():
        print(f"  - {reason}: {count}")
    print("\nSample invalid files:")
    for item in report.get("invalid_files", [])[:25]:
        print(f"  - [{item.get('reason')}] {item.get('path')} ({item.get('size_bytes')} bytes)")
    print(f"\nReport written to: {REPORT_PATH}")


def main() -> int:
    report = build_report()
    FORMS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
