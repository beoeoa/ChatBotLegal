#!/usr/bin/env python3
"""Idempotently sync a completed shadow resolver shortlist into the form queue.

This command only prepares candidates for human attestation.  It never approves
or serves a form and it quarantines stale generated candidates fail-closed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_source_resolution import (  # noqa: E402
    merge_pending_records,
    quarantine_stale_pending_records,
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sync(shortlist_path: Path, queue_path: Path, backup_dir: Path) -> dict[str, Any]:
    shortlist = _load(shortlist_path)
    queue = _load(queue_path)
    incoming = [
        dict(item)
        for item in shortlist.get("records", [])
        if isinstance(item, Mapping)
    ]
    for item in incoming:
        if item.get("approved") is not False or item.get("runtime_eligible") is not False:
            raise ValueError("shortlist_must_be_pending_only")
    existing = [dict(item) for item in queue.get("records", [])]
    active_ids = {str(item.get("id") or "") for item in incoming}
    quarantined = quarantine_stale_pending_records(existing, active_ids=active_ids)
    merged = merge_pending_records(quarantined["records"], incoming)

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{queue_path.stem}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    shutil.copy2(queue_path, backup_path)
    summary = dict(queue.get("summary") or {})
    summary.update(
        {
            "three_tier_pending_review": len(incoming),
            "three_tier_last_resolution_run_id": str(shortlist.get("run_id") or ""),
            "three_tier_auto_approved": 0,
            "three_tier_stale_quarantined": quarantined["quarantined_count"],
        }
    )
    output = {**queue, "summary": summary, "records": merged["records"]}
    _write_atomic(queue_path, output)
    return {
        "incoming": len(incoming),
        "created": merged["action_counts"]["created"],
        "updated": merged["action_counts"]["updated"],
        "unchanged": merged["action_counts"]["unchanged"],
        "stale_quarantined": quarantined["quarantined_count"],
        "queue_checksum": _checksum(queue_path),
        "backup": str(backup_path),
        "human_attestation_required": True,
        "automated_approval": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shortlist", type=Path, required=True)
    parser.add_argument("--queue", type=Path, default=ROOT / "notebook_data/forms/official_forms_candidates_classified.json")
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "backups/form-queue")
    args = parser.parse_args()
    print(json.dumps(sync(args.shortlist, args.queue, args.backup_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
