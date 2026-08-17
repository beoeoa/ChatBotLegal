"""Audit Capability Registry coverage for release gates.

This command is read-only.  It never enables, disables or redirects routes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT_PATH = Path(__file__).resolve().parents[1]
if str(ROOT_PATH) not in sys.path:
    sys.path.insert(0, str(ROOT_PATH))

from api.capability_registry import (
    DEFAULT_REGISTRY_PATH,
    ROOT,
    load_registry,
    reconcile_registry,
)


def audit(
    *,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    root: Path = ROOT,
) -> dict[str, Any]:
    records = load_registry(registry_path)
    reconciliation = reconcile_registry(records, root=root)
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for record in records:
        by_status[record.status] = by_status.get(record.status, 0) + 1
        by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
    return {
        "schema_version": "capability-audit-v1",
        "status": "passed" if reconciliation.passed else "failed",
        "registry": str(registry_path),
        "capability_count": len(records),
        "by_kind": dict(sorted(by_kind.items())),
        "by_status": dict(sorted(by_status.items())),
        "reconciliation": reconciliation.as_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    result = audit(registry_path=args.registry, root=args.root)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
