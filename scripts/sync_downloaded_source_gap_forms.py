#!/usr/bin/env python3
"""Sync all downloaded official form gap jobs into the Admin review queue.

The bridge is candidate-only and fail-closed. It never approves, promotes or
embeds a form.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.source_gap_jobs import (  # noqa: E402
    DEFAULT_FORM_REVIEW_QUEUE_PATH,
    DEFAULT_STORE_PATH,
    load_source_gap_jobs,
    sync_downloaded_form_candidate_to_review_queue,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    parser.add_argument("--queue", type=Path, default=DEFAULT_FORM_REVIEW_QUEUE_PATH)
    parser.add_argument("--backup-dir", type=Path, default=ROOT / "backups/form-queue")
    args = parser.parse_args()

    args.backup_dir.mkdir(parents=True, exist_ok=True)
    backup = args.backup_dir / (
        f"{args.queue.stem}-source-gap-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    if args.queue.is_file():
        shutil.copy2(args.queue, backup)

    counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for job in load_source_gap_jobs(args.store):
        if (
            job.get("gap_type") != "MISSING_FORM_SOURCE"
            or job.get("status") != "downloaded_candidate"
        ):
            continue
        try:
            result = sync_downloaded_form_candidate_to_review_queue(
                job,
                project_root=ROOT,
                queue_path=args.queue,
            )
            counts[str(result.get("action") or "unknown")] += 1
        except ValueError as exc:
            counts["failed_fail_closed"] += 1
            reason_counts[str(exc)] += 1

    print(
        json.dumps(
            {
                "status": "candidate_sync_complete",
                "counts": dict(sorted(counts.items())),
                "reason_counts": dict(sorted(reason_counts.items())),
                "backup": str(backup) if backup.is_file() else None,
                "automated_approval": False,
                "runtime_promoted": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
