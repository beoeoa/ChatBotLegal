"""Restore verified form source URLs from the crawler candidate catalogue.

The official index and crawler catalogue use the file SHA-256 as the stable
identity. This script only copies already-recorded HTTP(S) URLs; it never
constructs a URL from a filename or title.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = ROOT / "notebook_data/forms/haiphong_official_form_index.json"
DEFAULT_CATALOGUE = ROOT / "notebook_data/forms/haiphong_official_candidates.json"
DEFAULT_VERIFIED_MAP = ROOT / "notebook_data/forms/verified_form_source_urls.json"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _valid_url(value: Any) -> bool:
    return str(value or "").startswith(("http://", "https://"))


def enrich(
    index_path: Path = DEFAULT_INDEX,
    catalogue_path: Path = DEFAULT_CATALOGUE,
    verified_map_path: Path = DEFAULT_VERIFIED_MAP,
) -> dict[str, Any]:
    index = _load(index_path)
    catalogue = _load(catalogue_path)
    verified_map = _load(verified_map_path) if verified_map_path.is_file() else {}
    catalogue_rows = catalogue.get("records", []) if isinstance(catalogue, dict) else []
    by_sha: dict[str, dict[str, Any]] = {
        str(row.get("sha256")): row
        for row in catalogue_rows
        if isinstance(row, dict) and row.get("sha256")
    }

    missing_before = 0
    restored = 0
    unresolved: list[str] = []
    for row in index.get("forms", []):
        if not isinstance(row, dict):
            continue
        current_page = row.get("source_page_url") or row.get("source_url") or row.get("full_url")
        current_download = row.get("source_download_url") or row.get("download_url")
        if _valid_url(current_page) or _valid_url(current_download):
            continue
        missing_before += 1
        verified = verified_map.get(str(row.get("id")), {})
        source = by_sha.get(str(row.get("source_sha256")))
        page = verified.get("source_page_url") or (source.get("source_page_url") if source else None)
        download = verified.get("source_download_url") or (source.get("download_url") if source else None)
        if not _valid_url(page) and not _valid_url(download):
            unresolved.append(str(row.get("id")))
            continue
        if _valid_url(page):
            row["source_page_url"] = page
        if _valid_url(download):
            row["source_download_url"] = download
        if verified:
            row["url_provenance"] = "verified_source_map"
            row["url_verified_at"] = datetime.now(timezone.utc).isoformat()
        restored += 1

    backup = index_path.with_name(
        f"{index_path.stem}.before-url-enrichment-{datetime.now().strftime('%Y%m%d-%H%M%S')}{index_path.suffix}"
    )
    shutil.copy2(index_path, backup)
    index["generated_at"] = datetime.now(timezone.utc).isoformat()
    index.setdefault("url_enrichment", {})
    index["url_enrichment"].update({
        "source": str(catalogue_path.relative_to(ROOT)).replace("\\", "/"),
        "verified_map": str(verified_map_path.relative_to(ROOT)).replace("\\", "/") if verified_map_path.is_relative_to(ROOT) else str(verified_map_path),
        "restored": restored,
        "unresolved": len(unresolved),
        "unresolved_ids": unresolved,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "missing_before": missing_before,
        "restored": restored,
        "unresolved": len(unresolved),
        "backup": str(backup),
        "unresolved_ids": unresolved,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--catalogue", type=Path, default=DEFAULT_CATALOGUE)
    parser.add_argument("--verified-map", type=Path, default=DEFAULT_VERIFIED_MAP)
    args = parser.parse_args()
    print(json.dumps(enrich(args.index, args.catalogue, args.verified_map), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
