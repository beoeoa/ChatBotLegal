# -*- coding: utf-8 -*-
"""Promote verified dulieuphapluat.vn form files into the downloadable catalog.

Only records with a downloaded, byte-validated local file are promoted. Broken
or missing download links remain pending for manual review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.crawl_legal_forms import detect_file_type

CLASSIFIED_PATH = ROOT / "notebook_data" / "forms" / "official_forms_candidates_classified.json"
INDEX_PATH = ROOT / "notebook_data" / "forms" / "haiphong_official_form_index.json"
CATALOG_PATH = ROOT / "notebook_data" / "forms" / "haiphong_official_forms_catalog.json"
SUPPLEMENT_PATH = ROOT / "notebook_data" / "forms" / "priority_official_forms.json"
PRIORITY_DIR = ROOT / "data" / "uploads" / "forms" / "priority_official"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_local_path(record: dict[str, Any]) -> Path | None:
    rel = record.get("file_path") or record.get("local_path") or record.get("source_package_path")
    if not rel:
        return None
    path = (ROOT / str(rel).replace("\\", "/")).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def verified_file(path: Path, record: dict[str, Any]) -> tuple[bool, str | None]:
    content = path.read_bytes()
    file_type = detect_file_type(
        content,
        str(record.get("content_type") or ""),
        str(record.get("source_download_url") or record.get("file_name") or path.name),
    )
    if not file_type:
        return False, None
    return True, file_type


def copy_to_priority(record: dict[str, Any], source: Path, file_type: str) -> dict[str, str]:
    PRIORITY_DIR.mkdir(parents=True, exist_ok=True)
    form_id = str(record.get("id"))
    name = source.name
    if not name.lower().endswith(f".{file_type}"):
        name = f"{source.stem}.{file_type}"
    dest = PRIORITY_DIR / f"{form_id}-{name}"
    if not dest.exists():
        shutil.copy2(source, dest)
    rel = str(dest.relative_to(ROOT)).replace("\\", "/")
    return {
        "priority_path": rel,
        "local_path": rel,
        "source_package_path": rel,
        "file_name": dest.name,
    }


def catalog_entry(record: dict[str, Any], file_paths: dict[str, str], file_type: str) -> dict[str, Any]:
    now = utcnow()
    title = record.get("detected_form_name") or record.get("form_title") or record.get("file_name") or record.get("id")
    domain = record.get("suggested_domain") or record.get("domain") or "unknown"
    digest = record.get("sha256") or record.get("source_sha256")
    return {
        "id": str(record.get("id")),
        "form_title": title,
        "detected_form_name": title,
        "domain": domain,
        "procedure_id": record.get("suggested_procedure_id") or record.get("procedure_id") or "unknown",
        "source_package_title": title,
        "source_package_path": file_paths["source_package_path"],
        "local_path": file_paths["local_path"],
        "priority_path": file_paths["priority_path"],
        "file_name": file_paths["file_name"],
        "file_type": file_type,
        "size_bytes": record.get("size_bytes"),
        "sha256": digest,
        "source_sha256": digest,
        "source_page_url": record.get("source_page_url") or record.get("page_url") or record.get("source_url"),
        "source_download_url": record.get("source_download_url"),
        "source_category_url": record.get("source_category_url"),
        "publisher": record.get("publisher") or record.get("source_name") or "dulieuphapluat.vn",
        "source_name": record.get("source_name") or "dulieuphapluat.vn",
        "locality": record.get("locality") or "Toàn quốc",
        "administrative_level": record.get("administrative_level") or "reference_candidate",
        "review_status": "approved",
        "is_approved": True,
        "official_level": "reference",
        "catalog_status": "available_official_source",
        "is_canonical": True,
        "legal_status": "reference_admin_reviewed",
        "review_note": "Admin requested bulk promotion of verified downloaded form files; source is reference, not legal authority.",
        "ward_scope": bool(record.get("ward_scope")),
        "priority_tier": record.get("priority_tier") or ("high" if record.get("ward_scope") else "low_non_core"),
        "approved_at": now,
        "retrieved_at": record.get("retrieved_at") or now,
    }


def upsert_by_id(rows: list[dict[str, Any]], entry: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    target = str(entry.get("id"))
    for index, row in enumerate(rows):
        if str(row.get("id")) == target:
            rows[index] = {**row, **entry}
            return rows, False
    rows.append(entry)
    return rows, True


def recompute_summary(rows: list[dict[str, Any]], base: dict[str, Any] | None = None) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("review_status") or "unknown")
        domain = str(row.get("domain") or row.get("suggested_domain") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
    return {
        **(base or {}),
        "total": len(rows),
        "total_forms": len(rows),
        "total_available": len(rows),
        "approved_count": status_counts.get("approved", 0),
        "approved_forms": status_counts.get("approved", 0),
        "review_status_counts": status_counts,
        "domain_counts": domain_counts,
    }


def promote(args: argparse.Namespace) -> dict[str, Any]:
    classified = load_json(CLASSIFIED_PATH, {"records": [], "summary": {}})
    index_payload = load_json(INDEX_PATH, {"forms": [], "summary": {}})
    catalog_payload = load_json(CATALOG_PATH, {"forms": [], "summary": {}})
    supplement_payload = load_json(SUPPLEMENT_PATH, {"forms": [], "summary": {}, "errors": []})

    selected: list[tuple[int, dict[str, Any], Path, str]] = []
    skipped: list[dict[str, str]] = []
    for idx, record in enumerate(classified.get("records") or []):
        if record.get("source_name") != "dulieuphapluat.vn":
            continue
        if record.get("crawl_status") != "downloaded_verified":
            continue
        if not args.include_low_priority and record.get("ward_scope") is False:
            continue
        source = resolve_local_path(record)
        if not source:
            skipped.append({"id": str(record.get("id")), "reason": "missing_local_file"})
            continue
        ok, file_type = verified_file(source, record)
        if not ok or not file_type:
            skipped.append({"id": str(record.get("id")), "reason": "invalid_file"})
            continue
        selected.append((idx, record, source, file_type))

    promoted = 0
    added_index = 0
    added_catalog = 0
    added_supplement = 0
    now = utcnow()
    index_forms = list(index_payload.get("forms") or [])
    catalog_forms = list(catalog_payload.get("forms") or [])
    supplement_forms = list(supplement_payload.get("forms") or [])
    records = list(classified.get("records") or [])

    for idx, record, source, file_type in selected:
        digest = sha256_file(source)
        record = dict(record)
        record["sha256"] = digest
        record["source_sha256"] = digest
        record["size_bytes"] = source.stat().st_size
        file_paths = copy_to_priority(record, source, file_type)
        entry = catalog_entry(record, file_paths, file_type)

        record.update(entry)
        record["review_status"] = "approved"
        record["is_approved"] = True
        record["reviewed_at"] = now
        records[idx] = record

        index_forms, inserted = upsert_by_id(index_forms, entry)
        added_index += 1 if inserted else 0
        catalog_forms, inserted = upsert_by_id(catalog_forms, entry)
        added_catalog += 1 if inserted else 0
        supplement_forms, inserted = upsert_by_id(supplement_forms, entry)
        added_supplement += 1 if inserted else 0
        promoted += 1

    classified["records"] = records
    classified["generated_at"] = now
    classified["summary"] = recompute_summary(
        records,
        {
            **(classified.get("summary") or {}),
            "dulieuphapluat_promoted_at": now,
            "dulieuphapluat_promoted_verified_files": promoted,
            "dulieuphapluat_promote_skipped": len(skipped),
        },
    )
    save_json(CLASSIFIED_PATH, classified)

    index_payload["forms"] = index_forms
    index_payload["generated_at"] = now
    index_payload["summary"] = recompute_summary(index_forms, index_payload.get("summary") or {})
    save_json(INDEX_PATH, index_payload)

    catalog_payload["forms"] = catalog_forms
    catalog_payload["generated_at"] = now
    catalog_payload["summary"] = recompute_summary(catalog_forms, catalog_payload.get("summary") or {})
    save_json(CATALOG_PATH, catalog_payload)

    supplement_payload["forms"] = supplement_forms
    supplement_payload["generated_at"] = now
    supplement_payload["summary"] = {
        **recompute_summary(supplement_forms, supplement_payload.get("summary") or {}),
        "errors": len(supplement_payload.get("errors") or []),
        "with_real_file": sum(1 for row in supplement_forms if row.get("local_path")),
        "disk_files": len([p for p in PRIORITY_DIR.rglob("*") if p.is_file()]),
    }
    supplement_payload.setdefault("errors", [])
    save_json(SUPPLEMENT_PATH, supplement_payload)

    report = {
        "generated_at": now,
        "promoted": promoted,
        "selected": len(selected),
        "skipped": skipped[:50],
        "added_index": added_index,
        "added_catalog": added_catalog,
        "added_supplement": added_supplement,
        "include_low_priority": args.include_low_priority,
    }
    report_path = ROOT / "notebook_data" / "forms" / "dulieuphapluat_promote_report.json"
    save_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-low-priority",
        action="store_true",
        help="Also promote non-core business/personnel reference forms.",
    )
    return parser.parse_args()


def main() -> int:
    report = promote(parse_args())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
