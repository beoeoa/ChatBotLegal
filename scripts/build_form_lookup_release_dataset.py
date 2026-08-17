"""Build the checksum-bound 418-procedure form lookup release dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    ROOT / "reports" / "feature006" / "form-requirement-manifest-2026-07-30.json"
)
DEFAULT_OUTPUT = ROOT / "reports/feature006/form-lookup-release-dataset.json"
DEFAULT_BINDINGS = ROOT / "notebook_data/forms/procedure_form_bindings_v1.json"
DEFAULT_CATALOG = ROOT / "notebook_data/forms/canonical_forms_catalog_v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_dataset(
    manifest_path: Path,
    bindings_path: Path = DEFAULT_BINDINGS,
    catalog_path: Path = DEFAULT_CATALOG,
) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    bindings_payload = json.loads(bindings_path.read_text(encoding="utf-8-sig"))
    catalog_payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    runtime_form_ids = {
        str(item.get("form_id") or "").strip()
        for item in catalog_payload.get("forms") or []
        if (
            isinstance(item, dict)
            and item.get("review_status") == "approved"
            and item.get("approved") is True
            and item.get("runtime_eligible") is True
            and item.get("is_quarantined") is not True
        )
    }
    approved_pairs = {
        (
            str(item.get("procedure_id") or "").strip(),
            str(item.get("form_id") or "").strip(),
        )
        for item in bindings_payload.get("bindings") or []
        if (
            isinstance(item, dict)
            and item.get("binding_status") == "approved"
            and item.get("review_status", "approved") == "approved"
            and item.get("approved", True) is True
        )
    }
    identities = {
        str(item.get("identity_id") or ""): item
        for item in payload.get("form_identities") or []
        if isinstance(item, dict) and item.get("identity_id")
    }
    cases: list[dict[str, Any]] = []
    for procedure in payload.get("procedures") or []:
        if not isinstance(procedure, dict):
            continue
        procedure_id = str(procedure.get("procedure_id") or "").strip()
        procedure_name = str(procedure.get("procedure_name") or "").strip()
        if not procedure_id or not procedure_name:
            raise ValueError("Every procedure must have an ID and official name")
        required_ids = {
            str(value)
            for value in procedure.get("required_form_identity_ids") or []
            if str(value).strip()
        }
        expected_form_ids = sorted({
            str(form_id)
            for identity_id in required_ids
            for form_id in (
                identities.get(identity_id, {}).get("approved_catalog_form_ids")
                or []
            )
            if (
                identities.get(identity_id, {}).get("release_status")
                == "APPROVED_RUNTIME"
                and str(form_id).strip()
                and (procedure_id, str(form_id)) in approved_pairs
                and str(form_id) in runtime_form_ids
            )
        })
        pending_count = 0
        for identity_id in required_ids:
            identity = identities.get(identity_id, {})
            approved_form_ids = {
                str(value)
                for value in identity.get("approved_catalog_form_ids") or []
                if str(value).strip()
            }
            if (
                identity.get("release_status") != "APPROVED_RUNTIME"
                or not any(
                    (procedure_id, form_id) in approved_pairs
                    and form_id in runtime_form_ids
                    for form_id in approved_form_ids
                )
            ):
                pending_count += 1
        coverage_status = str(procedure.get("coverage_status") or "")
        if expected_form_ids:
            expected_state = "APPROVED_RUNTIME_FORMS"
        elif pending_count:
            expected_state = "LEGAL_REVIEW_REQUIRED"
        elif coverage_status == "OFFICIAL_EFORM_ONLY":
            expected_state = "OFFICIAL_EFORM_ONLY"
        else:
            expected_state = "NO_RUNTIME_FORM"
        cases.append({
            "case_id": f"form-lookup-{procedure_id}",
            "procedure_id": procedure_id,
            "procedure_name": procedure_name,
            "domain": procedure.get("domain"),
            "query": (
                f"Thủ tục {procedure_name} (mã {procedure_id}) cần những "
                "biểu mẫu chính thức nào đang còn hiệu lực?"
            ),
            "expected_form_ids": expected_form_ids,
            "expected_state": expected_state,
            "pending_identity_count": pending_count,
            "coverage_status": coverage_status,
        })

    unique_ids = {case["procedure_id"] for case in cases}
    if len(cases) != 418 or len(unique_ids) != 418:
        raise ValueError(
            f"Expected 418 unique procedures, got {len(cases)}/{len(unique_ids)}"
        )
    return {
        "schema_version": "feature006-form-lookup-dataset-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": payload.get("legal_as_of"),
        "source_manifest": str(manifest_path.relative_to(ROOT)).replace("\\", "/"),
        "source_manifest_sha256": _sha256(manifest_path),
        "bindings_sha256": _sha256(bindings_path),
        "catalog_sha256": _sha256(catalog_path),
        "procedure_count": len(cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--bindings", type=Path, default=DEFAULT_BINDINGS)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_dataset(
        args.manifest.resolve(),
        args.bindings.resolve(),
        args.catalog.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(args.output),
        "procedure_count": result["procedure_count"],
        "source_manifest_sha256": result["source_manifest_sha256"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
