"""Build a read-only evidence report for Step 1's seven priority groups.

The report never approves a record. It combines the canonical catalog,
candidate-only source findings, and binding statuses so missing official
sources are explicit VERIFIED_DATA_GAP records rather than runtime forms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.legal_form_catalog import FormCatalog

PRIORITY = [
    ("dang_ky_khai_sinh", "Đăng ký khai sinh", "Tờ khai đăng ký khai sinh"),
    ("sang_ten_so_do", "Đăng ký biến động đất đai", "Mẫu 09/ĐK"),
    (
        "xac_nhan_tinh_trang_hon_nhan",
        "Xác nhận tình trạng hôn nhân",
        "Tờ khai cấp Giấy xác nhận tình trạng hôn nhân",
    ),
    (
        "dang_ky_khai_sinh_nuoc_ngoai",
        "Khai sinh có yếu tố nước ngoài",
        "Tờ khai đăng ký khai sinh",
    ),
    ("khieu_nai_hanh_chinh", "Đơn khiếu nại xử phạt", "Đơn khiếu nại"),
    (
        "dang_ky_ket_hon_nuoc_ngoai",
        "Đăng ký kết hôn có yếu tố nước ngoài",
        "Tờ khai đăng ký kết hôn",
    ),
    (
        "cap_giay_phep_xay_dung",
        "Giấy phép xây dựng nhà ở riêng lẻ",
        "Đơn đề nghị cấp GPXD nhà ở riêng lẻ",
    ),
]


def _load(path: Path, key: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload
    return payload.get(key, [])


def _checksum(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_report(root: Path = ROOT, *, as_of: str | None = None) -> dict[str, Any]:
    forms = _load(root / "notebook_data/forms/canonical_forms_catalog_v1.json", "forms")
    findings = _load(
        root / "notebook_data/forms/canonical_form_source_findings_v1.json",
        "findings",
    )
    bindings = _load(
        root / "notebook_data/forms/procedure_form_bindings_v1.json",
        "bindings",
    )
    procedures = _load(
        root / "notebook_data/forms/canonical_procedures_v1.json",
        "procedures",
    )
    source_gap_store = root / "data/source_gap_jobs/source_gap_jobs_v1.json"
    source_gap_jobs = (
        _load(source_gap_store, "jobs") if source_gap_store.is_file() else []
    )
    procedure_ids = {str(item.get("procedure_id") or "") for item in procedures}
    binding_map = {
        (str(item.get("procedure_id") or ""), str(item.get("form_id") or "")): item
        for item in bindings
    }
    active_date = date.fromisoformat(as_of) if as_of else date.today()
    catalog = FormCatalog(
        procedures=procedures,
        forms=forms,
        bindings=bindings,
        project_root=root,
    )
    finding_map: dict[str, list[dict[str, Any]]] = {}
    for item in findings:
        finding_map.setdefault(str(item.get("procedure_id") or ""), []).append(item)

    groups: list[dict[str, Any]] = []
    for pid, label, required_name in PRIORITY:
        if pid not in procedure_ids:
            pending_candidates = [
                item
                for item in source_gap_jobs
                if item.get("procedure_id") == pid
                and item.get("gap_type") == "MISSING_FORM_SOURCE"
                and item.get("status") == "downloaded_candidate"
                and isinstance(item.get("candidate"), dict)
            ]
            if pending_candidates:
                candidate_rows: list[dict[str, Any]] = []
                for job in pending_candidates:
                    candidate = job["candidate"]
                    provenance = dict(candidate.get("provenance") or {})
                    local = str(candidate.get("local_path") or "")
                    local_abs = (root / local).resolve() if local else None
                    candidate_rows.append(
                        {
                            "form_id": None,
                            "candidate_job_id": job.get("job_id"),
                            "procedure_id": pid,
                            "form_code": job.get("expected_code"),
                            "canonical_name": candidate.get("canonical_name")
                            or job.get("expected_name"),
                            "official_source_page": candidate.get("source_page"),
                            "official_download_url": candidate.get("download_url"),
                            "local_path": local or None,
                            "file_present": bool(local_abs and local_abs.is_file()),
                            "file_sha256": _checksum(local_abs) if local_abs else None,
                            "declared_sha256": candidate.get("sha256"),
                            "legal_basis": [],
                            "effective_from": None,
                            "effective_to": None,
                            "jurisdiction": "Hai Phong",
                            "scope": {
                                "domain": "ho_tich_chung_thuc",
                                "administrative_level": "commune",
                                "jurisdiction": "Hai Phong",
                            },
                            "approved": False,
                            "provenance": provenance,
                            "legal_review_status": "pending",
                            "binding_status": "candidate_pending_review",
                            "runtime_eligible": False,
                            "hard_gate_reason": "HUMAN_LEGAL_REVIEW_REQUIRED",
                            "source_finding_status": "AVAILABLE_OFFICIAL_FILE",
                            "source_finding_reason": job.get("reason_code"),
                        }
                    )
                groups.append(
                    {
                        "procedure_id": pid,
                        "label": label,
                        "required_form": required_name,
                        "status": "LEGAL_REVIEW_REQUIRED",
                        "forms_unavailable": True,
                        "data_gap_status": "LEGAL_REVIEW_REQUIRED",
                        "data_gap_reasons": ["HUMAN_LEGAL_REVIEW_REQUIRED"],
                        "evidence": {
                            "catalog": "canonical_procedures_v1.json",
                            "candidate_store": str(
                                source_gap_store.relative_to(root)
                            ).replace("\\", "/"),
                            "finding": (
                                "Official candidate is queued; canonical procedure "
                                "and binding remain absent until human attestation."
                            ),
                        },
                        "forms": candidate_rows,
                    }
                )
                continue
            groups.append(
                {
                    "procedure_id": pid,
                    "label": label,
                    "required_form": required_name,
                    "status": "VERIFIED_DATA_GAP",
                    "forms_unavailable": True,
                    "data_gap_status": "VERIFIED_DATA_GAP",
                    "data_gap_reasons": ["PROCEDURE_ID_NOT_IN_CATALOG"],
                    "evidence": {
                        "catalog": "canonical_procedures_v1.json",
                        "finding": "No canonical procedure_id exists; no mapping was invented.",
                    },
                    "forms": [],
                }
            )
            continue

        rows = [item for item in forms if pid in (item.get("procedure_ids") or [])]
        group_findings = finding_map.get(pid, [])
        form_rows: list[dict[str, Any]] = []
        reasons: set[str] = set()
        for form in rows:
            form_id = str(form.get("form_id") or "")
            related = [item for item in group_findings if item.get("form_id") == form_id]
            finding = related[0] if related else {}
            local = str(form.get("local_path") or finding.get("local_path") or "")
            local_abs = (root / local).resolve() if local else None
            actual_hash = _checksum(local_abs) if local_abs else None
            binding = binding_map.get((pid, form_id), {})
            gate = catalog.gate_form(
                form,
                procedure_id=pid,
                role="admin",
                as_of=active_date,
            )
            runtime_eligible = bool(
                gate.eligible
                and binding.get("binding_status") == "approved"
            )
            finding_status = str(finding.get("status") or "")
            if finding_status in {"NEEDS_SOURCE_MAPPING", "NO_PUBLIC_DOWNLOAD_VERIFIED"}:
                reasons.add(finding.get("reason_code") or finding_status)
            if form.get("review_status") != "approved" or form.get("approved") is not True:
                reasons.add("LEGAL_REVIEW_REQUIRED")
            form_rows.append(
                {
                    "form_id": form_id,
                    "procedure_id": pid,
                    "form_code": form.get("form_code"),
                    "canonical_name": form.get("canonical_name"),
                    "official_source_page": form.get("official_source_page")
                    or finding.get("source_page"),
                    "official_download_url": form.get("official_download_url")
                    or finding.get("download_url"),
                    "local_path": local or None,
                    "file_present": bool(local_abs and local_abs.is_file()),
                    "file_sha256": actual_hash,
                    "declared_sha256": form.get("sha256") or finding.get("sha256"),
                    "legal_basis": form.get("legal_basis") or finding.get("legal_basis") or [],
                    "effective_from": form.get("effective_from"),
                    "effective_to": form.get("effective_to"),
                    "jurisdiction": form.get("jurisdiction"),
                    "scope": {
                        "domain": form.get("domain"),
                        "administrative_level": form.get("administrative_level"),
                        "jurisdiction": form.get("jurisdiction"),
                    },
                    "approved": form.get("approved") is True,
                    "provenance": form.get("provenance") or finding,
                    "legal_review_status": form.get("legal_review_status")
                    or form.get("review_status")
                    or finding.get("review_status"),
                    "binding_status": binding.get("binding_status"),
                    "runtime_eligible": runtime_eligible,
                    "hard_gate_reason": gate.reason_code,
                    "source_finding_status": finding_status or None,
                    "source_finding_reason": finding.get("reason_code"),
                }
            )

        has_verified_gap = any(
            item.get("source_finding_status") in {"NEEDS_SOURCE_MAPPING", "NO_PUBLIC_DOWNLOAD_VERIFIED"}
            for item in group_findings
        )
        runtime_rows = [
            item for item in form_rows if item.get("runtime_eligible") is True
        ]
        if runtime_rows:
            status = "AVAILABLE_AND_HARD_GATE_PASS"
        elif has_verified_gap and not rows:
            status = "VERIFIED_DATA_GAP"
        elif rows and all(
            item.get("review_status") != "approved" or item.get("approved") is not True
            for item in rows
        ):
            status = "LEGAL_REVIEW_REQUIRED"
        elif has_verified_gap:
            status = "LEGAL_REVIEW_REQUIRED"
        else:
            status = "LEGAL_REVIEW_REQUIRED"
        groups.append(
            {
                "procedure_id": pid,
                "label": label,
                "required_form": required_name,
                "status": status,
                "forms_unavailable": not bool(runtime_rows),
                "data_gap_status": (
                    None
                    if runtime_rows
                    else "VERIFIED_DATA_GAP"
                    if has_verified_gap
                    else "LEGAL_REVIEW_REQUIRED"
                ),
                "data_gap_reasons": sorted(reasons),
                "forms": form_rows,
            }
        )

    overall = "PASS" if all(
        item["status"] in {"AVAILABLE_AND_HARD_GATE_PASS", "VERIFIED_DATA_GAP"}
        for item in groups
    ) else "BLOCKED_EXTERNAL"
    return {
        "step": "1",
        "generated_at": date.today().isoformat(),
        "as_of": as_of or date.today().isoformat(),
        "overall_status": overall,
        "auto_approved_count": 0,
        "runtime_policy": "hard_gate_only",
        "groups": groups,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"overall_status": report["overall_status"], "groups": len(report["groups"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
