"""Build a fail-closed Feature 018 Go/No-Go report from evidence artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_audit_chain import load_private_key_from_file  # noqa: E402
from api.release_evidence import (  # noqa: E402
    ReleaseEvidence,
    evaluate_release_evidence,
)
from api.release_signing import sign_release_report  # noqa: E402


def _load_evidence_file(path: Path) -> list[ReleaseEvidence]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_items = payload.get("evidence") if isinstance(payload, dict) else None
    if raw_items is None:
        raw_items = [payload]
    if not isinstance(raw_items, list):
        raise ValueError("evidence must be an object or list envelope")
    return [ReleaseEvidence.model_validate(item) for item in raw_items]


def verify(
    *,
    release_id: str,
    release_fingerprint: str,
    evidence_dir: Path,
) -> dict[str, Any]:
    evidence: list[ReleaseEvidence] = []
    invalid_artifacts: list[str] = []
    if evidence_dir.is_dir():
        for path in sorted(evidence_dir.glob("*.json")):
            try:
                evidence.extend(_load_evidence_file(path))
            except (OSError, json.JSONDecodeError, ValidationError, ValueError):
                invalid_artifacts.append(path.name)

    result = evaluate_release_evidence(
        evidence,
        release_id=release_id,
        release_fingerprint=release_fingerprint,
    ).model_dump(mode="json")
    result["schema_version"] = "production-readiness-v1"
    result["evidence_count"] = len(evidence)
    result["invalid_artifacts"] = invalid_artifacts
    result["signature_status"] = "unsigned"
    result["signature"] = None
    if invalid_artifacts:
        result["decision"] = "NO-GO"
    return result


def _write_json_atomic(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(rendered + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--release-fingerprint", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--signing-key", type=Path)
    parser.add_argument("--signer-id")
    parser.add_argument("--signed-at")
    args = parser.parse_args()
    if bool(args.signing_key) != bool(args.signer_id):
        parser.error("--signing-key and --signer-id must be provided together")

    result = verify(
        release_id=args.release,
        release_fingerprint=args.release_fingerprint,
        evidence_dir=args.evidence_dir,
    )
    if args.signing_key:
        result = sign_release_report(
            result,
            private_key=load_private_key_from_file(str(args.signing_key)),
            signer_id=args.signer_id,
            signed_at=(
                args.signed_at
                or datetime.now(timezone.utc).isoformat()
            ),
        )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        _write_json_atomic(args.output, rendered)
    print(rendered)
    return 0 if result["decision"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
