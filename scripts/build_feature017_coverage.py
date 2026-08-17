"""Build a read-only Feature 017 compatibility coverage report and review queue."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-30.json"
EXPECTED = {"procedures": 191, "identities": 131, "bindings": 229}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(path: Path = DEFAULT) -> dict:
    source = json.loads(path.read_text(encoding="utf-8"))
    all_procedures = list(source.get("procedures") or [])
    # The Feature 006 manifest intentionally contains both province and
    # commune procedures. Feature 017 is limited to the commune slice; using
    # the unfiltered manifest inflated 191/131/229 to 418/278/470.
    procedures = [
        item for item in all_procedures
        if str(item.get("executing_level") or "").casefold() == "commune"
    ]
    scoped_procedure_ids = {
        str(item.get("procedure_id") or "") for item in procedures
    }
    procedure_domains = {
        str(item.get("procedure_id") or ""): str(item.get("domain") or "")
        for item in procedures
    }
    identities = [
        item for item in list(source.get("form_identities") or [])
        if scoped_procedure_ids.intersection(
            str(value or "") for value in item.get("procedure_ids") or []
        )
    ]
    bindings = sum(len(item.get("required_form_identity_ids") or []) for item in procedures)
    pending = [item for item in identities if item.get("release_status") != "APPROVED_RUNTIME"]
    actual = {"procedures": len(procedures), "identities": len(identities), "bindings": bindings}
    return {
        "schema_version": "feature017-coverage-compat-v1",
        "source_path": str(path), "source_sha256": sha256(path), "legal_as_of": source.get("legal_as_of"),
        "candidate_only": True, "runtime_mutated": False, "expected_commune_scope_baseline": EXPECTED,
        "observed_source_scope": actual, "baseline_matches": actual == EXPECTED,
        "scope_filter": {"executing_level": "commune"},
        "source_procedure_count_before_scope": len(all_procedures),
        "warning": None if actual == EXPECTED else "SOURCE_SCOPE_REQUIRES_REFRESH_AND_EXPLICIT_COMMUNE_SCOPE_MAPPING",
        "pending_identity_count": len(pending),
        "released_identity_count": len(identities) - len(pending),
        "review_queue": [{
            "identity_id": x.get("identity_id"),
            "identity_status": x.get("identity_status"),
            "release_status": x.get("release_status"),
            "form_code": x.get("form_code"),
            "issuing_instrument": x.get("issuing_instrument"),
            "canonical_titles": x.get("canonical_titles"),
            "distribution_variants": x.get("distribution_variants"),
            "domains": sorted({
                procedure_domains[procedure_id]
                for procedure_id in x.get("procedure_ids") or []
                if procedure_id in procedure_domains
            }),
            "procedure_ids": sorted(
                scoped_procedure_ids.intersection(x.get("procedure_ids") or [])
            ),
            "official_source_pages": x.get("official_source_pages"),
        } for x in pending],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--input", type=Path, default=DEFAULT); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--compat-json", action="store_true")
    args = parser.parse_args(); payload = build(args.input); args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); print(json.dumps({k: payload[k] for k in ("observed_source_scope", "baseline_matches", "pending_identity_count", "runtime_mutated")}, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
