#!/usr/bin/env python3
"""Refresh only live catalog counts in an existing form campaign report.

This does not rerun source resolution and does not touch the catalog. It fixes
the common case where a human attestation happens after a campaign baseline was
created, so the report cannot accidentally show an old runtime-approved count.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from api.form_resolution_campaign import (  # noqa: E402
    ATTESTATIONS_PATH,
    CATALOG_PATH,
    build_campaign_baseline,
)


def _write(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def refresh(report_path: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    inventory_path = ROOT / "notebook_data" / "forms" / "three_tier_form_inventory_v1.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    occurrences = [
        *inventory.get("verified_data_gaps", []),
        *inventory.get("canonical_forms", []),
    ]
    current = build_campaign_baseline(
        run_id=str(report.get("run_id") or ""),
        legal_as_of=str(report.get("legal_as_of") or ""),
        occurrences=occurrences,
        catalog_path=CATALOG_PATH,
        attestations_path=ATTESTATIONS_PATH,
        feature_flag=False,
        active_collection=str(
            report.get("active_collection") or "legal_chunks_lechan_primary_v20260723"
        ),
    )
    previous_runtime = int((report.get("counts") or {}).get("runtime_approved") or 0)
    report["baseline_runtime_approved_forms"] = previous_runtime
    report.setdefault("counts", {})["runtime_approved"] = int(
        current["counts"]["runtime_approved_forms"]
    )
    report["current_catalog_state"] = {
        "catalog_forms": current["counts"]["catalog_forms"],
        "approved_catalog_forms": current["counts"]["approved_catalog_forms"],
        "runtime_approved_forms": current["counts"]["runtime_approved_forms"],
        "attestations": current["counts"]["attestations"],
        "catalog_checksum": current["checksums"]["catalog"],
        "attestation_checksum": current["checksums"]["attestations"],
    }
    _write(report_path, report)
    return report["current_catalog_state"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    state = refresh(args.report.resolve())
    print(json.dumps(state, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
