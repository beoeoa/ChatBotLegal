"""Attach candidate-only source evidence to Procedure/Form Catalog records.

This is deliberately not a promotion step: approved/review/runtime flags stay
false or pending, and evidence is namespaced as candidate evidence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, payload.get(key, [])


def enrich(root: Path = ROOT) -> dict[str, int]:
    proc_path = root / "notebook_data/forms/canonical_procedures_v1.json"
    form_path = root / "notebook_data/forms/canonical_forms_catalog_v1.json"
    proc_src_path = root / "notebook_data/forms/canonical_procedure_sources_v1.json"
    form_src_path = root / "notebook_data/forms/canonical_form_source_findings_v1.json"

    proc_payload, procedures = _load(proc_path, "procedures")
    form_payload, forms = _load(form_path, "forms")
    _, proc_sources = _load(proc_src_path, "procedures")
    _, form_sources = _load(form_src_path, "findings")

    proc_map = {str(item.get("procedure_id") or ""): item for item in proc_sources}
    proc_updates = 0
    for procedure in procedures:
        pid = str(procedure.get("procedure_id") or "")
        proc_evidence = proc_map.get(pid)
        if not proc_evidence:
            continue
        procedure["candidate_source_evidence"] = {
            "status": proc_evidence.get("status"),
            "reason_code": proc_evidence.get("reason_code"),
            "official_procedure_code": proc_evidence.get("official_procedure_code"),
            "official_procedure_url": proc_evidence.get("official_procedure_url"),
            "official_name": proc_evidence.get("official_name"),
            "portal_state": proc_evidence.get("portal_state"),
            "legal_basis": proc_evidence.get("legal_basis") or [],
            "source_file": "notebook_data/forms/canonical_procedure_sources_v1.json",
        }
        procedure["approved"] = False
        procedure["runtime_eligible"] = False
        procedure["review_status"] = "pending"
        procedure["legal_review_status"] = "not_reviewed"
        proc_updates += 1

    form_map: dict[str, list[dict[str, Any]]] = {}
    for evidence in form_sources:
        form_map.setdefault(str(evidence.get("form_id") or ""), []).append(evidence)
    form_updates = 0
    for form in forms:
        form_id = str(form.get("form_id") or "")
        form_evidence = form_map.get(form_id)
        if not form_evidence:
            continue
        form["candidate_source_evidence"] = [
            {
                "status": item.get("status"),
                "reason_code": item.get("reason_code"),
                "source_page": item.get("source_page"),
                "download_url": item.get("download_url"),
                "local_path": item.get("local_path"),
                "file_format": item.get("file_format"),
                "sha256": item.get("sha256"),
                "legal_basis": item.get("legal_basis") or [],
                "source_file": "notebook_data/forms/canonical_form_source_findings_v1.json",
            }
            for item in form_evidence
        ]
        form["approved"] = False
        form["review_status"] = "candidate_pending_review"
        form["legal_review_status"] = "not_reviewed"
        form_updates += 1

    proc_path.write_text(
        json.dumps(proc_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    form_path.write_text(
        json.dumps(form_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"procedure_updates": proc_updates, "form_updates": form_updates}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(enrich(args.root), ensure_ascii=False))


if __name__ == "__main__":
    main()
