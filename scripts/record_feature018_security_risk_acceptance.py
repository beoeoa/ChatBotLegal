"""Record a signed, release-bound decision for unfixed Trivy OS findings."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_audit_chain import load_private_key_from_file  # noqa: E402
from api.release_signing import sign_release_report  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_acceptance(
    *,
    release_id: str,
    release_fingerprint: str,
    trivy_report: Path,
    signer_id: str,
    signed_at: str,
) -> dict:
    trivy = json.loads(trivy_report.read_text(encoding="utf-8"))
    totals = trivy.get("totals") or {}
    remediable = int(totals.get("remediable_count") or 0)
    unfixed_os = int(totals.get("unfixed_os_count") or 0)
    if remediable != 0:
        raise ValueError("remediable_high_critical_must_be_zero")
    if unfixed_os <= 0:
        raise ValueError("unfixed_os_findings_required_for_acceptance")
    return {
        "schema_version": "feature018-security-risk-acceptance-v1",
        "decision": "ACCEPT",
        "release_id": release_id,
        "release_fingerprint": release_fingerprint,
        "scan_artifact_sha256": sha256(trivy_report),
        "accepted_unfixed_os_high_critical": unfixed_os,
        "remediable_high_critical": remediable,
        "scope": "current release image digests and fingerprint only",
        "rationale_code": "RELEASE_OWNER_EXPLICIT_APPROVAL",
        "signer_id": signer_id,
        "signed_at": signed_at,
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--release-fingerprint", required=True)
    parser.add_argument("--trivy-report", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--signer-id", required=True)
    parser.add_argument("--signed-at")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    signed_at = args.signed_at or datetime.now(timezone.utc).isoformat()
    acceptance = build_acceptance(
        release_id=args.release,
        release_fingerprint=args.release_fingerprint,
        trivy_report=args.trivy_report,
        signer_id=args.signer_id,
        signed_at=signed_at,
    )
    signed = sign_release_report(
        acceptance,
        private_key=load_private_key_from_file(str(args.signing_key)),
        signer_id=args.signer_id,
        signed_at=signed_at,
    )
    write_json_atomic(args.output, signed)
    print(json.dumps({"output": str(args.output), "signature_status": "signed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
