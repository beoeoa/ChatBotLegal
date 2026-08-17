#!/usr/bin/env python3
"""Run the Feature 017 remote source/checksum gate without activation."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_governance_models import canonical_sha256  # noqa: E402
from api.form_governance_release_validator import (  # noqa: E402
    HttpFormSourceVerifier,
    validate_release_manifest,
)


DEFAULT_MANIFEST = (
    ROOT
    / "outputs"
    / "feature017-full-release-candidate-20260812-packaged"
    / "form-release-v1.json"
)
DEFAULT_OUTPUT = ROOT / "reports" / "feature017" / "remote-source-gate.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    assets = list(manifest.get("assets") or [])
    verifier = HttpFormSourceVerifier(timeout_seconds=args.timeout)
    decisions: dict[str, str | None] = {}
    failures: list[dict[str, str]] = []
    provenance_counts: Counter[str] = Counter()
    for index, asset in enumerate(assets, start=1):
        form_id = str(asset.get("form_id") or "")
        provenance = str((asset.get("provenance") or {}).get("source") or "unknown")
        provenance_counts[provenance] += 1
        reason = verifier(asset)
        decisions[form_id] = reason
        if reason:
            failures.append(
                {
                    "form_id": form_id,
                    "reason": reason,
                    "source_url": str(asset.get("source_url") or ""),
                }
            )
        if index % 5 == 0 or index == len(assets):
            print(
                json.dumps(
                    {
                        "checked": index,
                        "total": len(assets),
                        "failures": len(failures),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    gate = validate_release_manifest(
        manifest,
        expected_manifest_sha256=canonical_sha256(manifest),
        source_verifier=lambda asset: decisions.get(str(asset.get("form_id") or "")),
    )
    report = {
        "schema_version": "feature017-remote-source-gate-v1",
        "passed": gate.get("passed") is True and not failures,
        "manifest_sha256": gate.get("manifest_sha256"),
        "asset_count": len(assets),
        "source_checks": gate.get("source_checks"),
        "provenance_counts": dict(provenance_counts),
        "failure_count": len(failures),
        "failures": failures,
        "gate_errors": gate.get("errors") or [],
        "activation_allowed": False,
        "active_pointer_changed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
