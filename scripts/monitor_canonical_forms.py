"""Read-only health monitor for approved canonical form assets."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import (
    FormCatalog,
    _file_integrity,
    _is_official_url,
    _sha256,
)


REPORT_PATH = (
    ROOT
    / "reports"
    / "feature005"
    / "forms-completion-20260724"
    / "f7-form-health.json"
)


def monitor_catalog(
    catalog: FormCatalog,
    *,
    network: bool = False,
    write: bool = True,
    report_path: Path = REPORT_PATH,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    client = httpx.Client(follow_redirects=True, timeout=10) if network else None
    try:
        for form in catalog.forms:
            status = "HEALTHY"
            reason = "NO_CHANGE"
            local_path = str(form.get("local_path") or "").strip()
            source_url = str(form.get("official_source_page") or "").strip()
            download_url = str(form.get("official_download_url") or "").strip()
            if form.get("review_status") != "approved":
                status = "NOT_SERVED"
                reason = "FORM_NOT_APPROVED"
            elif local_path:
                path = (catalog.project_root / local_path).resolve()
                valid, _ = _file_integrity(path, form.get("file_format"))
                if not valid:
                    status = "NEEDS_REVALIDATION"
                    reason = "LOCAL_FILE_INVALID"
                elif not form.get("sha256") or _sha256(path) != form.get("sha256"):
                    status = "NEEDS_REVALIDATION"
                    reason = "CHECKSUM_CHANGED"
            elif not download_url:
                status = "NEEDS_REVALIDATION"
                reason = "DOWNLOAD_MISSING"
            if (
                network
                and client is not None
                and status == "HEALTHY"
                and _is_official_url(source_url)
            ):
                try:
                    response = client.head(source_url)
                    if response.status_code >= 400:
                        status = "NEEDS_REVALIDATION"
                        reason = f"SOURCE_HTTP_{response.status_code}"
                except httpx.HTTPError as exc:
                    status = "SOURCE_CHECK_FAILED"
                    reason = type(exc).__name__
            checks.append(
                {
                    "form_id": form.get("form_id"),
                    "status": status,
                    "reason_code": reason,
                }
            )
    finally:
        if client is not None:
            client.close()

    counts = Counter(item["status"] for item in checks)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "form_count": len(checks),
        "status_counts": dict(sorted(counts.items())),
        "network_enabled": network,
        "catalog_mutated": False,
        "contains_user_content": False,
        "contains_credentials": False,
        "checks": checks,
    }
    if write:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor canonical form catalog")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    report = monitor_catalog(
        FormCatalog.load_default(),
        network=args.network,
        write=True,
        report_path=args.report,
    )
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key != "checks"
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
