import hashlib
import json
from datetime import date
from pathlib import Path

from scripts.package_runtime_forms import build_runtime_form_package


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_release_package_contains_only_forms_that_pass_every_runtime_gate(tmp_path):
    asset = tmp_path / "data/uploads/forms/approved.pdf"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"%PDF-1.7\n" + b"x" * 512)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    forms_dir = tmp_path / "notebook_data/forms"
    procedure = {
        "procedure_id": "approved-procedure",
        "name": "Approved procedure",
        "domain": "ho_tich",
    }
    approved = {
        "form_id": "approved-form",
        "canonical_name": "Approved form",
        "procedure_ids": ["approved-procedure"],
        "review_status": "approved",
        "approved": True,
        "runtime_eligible": True,
        "source_classification": "official_file",
        "legal_basis": ["Official basis"],
        "provenance": {"source": "official"},
        "domain": "ho_tich",
        "audience": "both",
        "effective_from": "2025-01-01",
        "effective_to": None,
        "official_source_page": "https://dichvucong.gov.vn/example",
        "official_download_url": "https://dichvucong.gov.vn/example.pdf",
        "local_path": "data/uploads/forms/approved.pdf",
        "file_format": "pdf",
        "sha256": digest,
    }
    pending = {
        **approved,
        "form_id": "pending-form",
        "canonical_name": "Pending form",
        "review_status": "pending",
        "approved": False,
    }
    _write_json(forms_dir / "canonical_procedures_v1.json", {"procedures": [procedure]})
    _write_json(forms_dir / "canonical_forms_catalog_v1.json", {"forms": [approved, pending]})
    _write_json(
        forms_dir / "procedure_form_bindings_v1.json",
        {
            "bindings": [
                {
                    "procedure_id": "approved-procedure",
                    "form_id": "approved-form",
                    "binding_status": "approved",
                },
                {
                    "procedure_id": "approved-procedure",
                    "form_id": "pending-form",
                    "binding_status": "approved",
                },
            ]
        },
    )

    result = build_runtime_form_package(
        project_root=tmp_path,
        catalog_dir=forms_dir,
        output_root=tmp_path / "release-data",
        as_of=date(2026, 7, 27),
    )

    assert result["runtime_form_count"] == 1
    packaged = json.loads(
        (tmp_path / "release-data/notebook_data/forms/canonical_forms_catalog_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["form_id"] for item in packaged["forms"]] == ["approved-form"]
    assert not any(
        item["form_id"] == "pending-form"
        for item in packaged["forms"]
    )
    assert (tmp_path / "release-data/forms/approved.pdf").is_file()


def test_release_package_includes_official_numeric_procedure_bridge(tmp_path):
    asset = tmp_path / "data/uploads/forms/m19.pdf"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"%PDF-1.7\n" + b"x" * 512)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    forms_dir = tmp_path / "notebook_data/forms"
    _write_json(forms_dir / "canonical_procedures_v1.json", {"procedures": []})
    _write_json(
        forms_dir / "three_tier_procedure_catalog_v1.json",
        {
            "legal_as_of": "2026-07-30",
            "procedures": [
                {
                    "procedure_id": "official-uuid",
                    "procedure_code": "1.013873",
                    "procedure_name": "Cấp lại giấy chứng nhận cơ sở đủ điều kiện xét nghiệm khẳng định HIV dương tính.",
                    "domain": "an_sinh_y_te_giao_duc",
                    "executing_level": "province",
                    "source_tier": "central",
                    "portal_state": "ACTIVE",
                    "publisher": "Bộ Y tế",
                    "official_source_page": "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/official-uuid",
                }
            ],
        },
    )
    _write_json(
        forms_dir / "canonical_forms_catalog_v1.json",
        {
            "forms": [
                {
                    "form_id": "m19",
                    "form_code": "19",
                    "canonical_name": "Đơn đề nghị cấp lại theo Mẫu số 19",
                    "procedure_ids": ["1.013873"],
                    "review_status": "approved",
                    "approved": True,
                    "runtime_eligible": True,
                    "source_classification": "official_file",
                    "legal_basis": ["141/2024/NĐ-CP"],
                    "provenance": {"source": "official"},
                    "domain": "an_sinh_y_te_giao_duc",
                    "audience": "citizen",
                    "effective_from": "2024-12-15",
                    "effective_to": None,
                    "official_source_page": "https://vbpl.vn/van-ban/141-2024",
                    "official_download_url": "https://vbpl.vn/van-ban/141-2024/download",
                    "local_path": "data/uploads/forms/m19.pdf",
                    "file_format": "pdf",
                    "sha256": digest,
                }
            ]
        },
    )
    _write_json(
        forms_dir / "procedure_form_bindings_v1.json",
        {
            "bindings": [
                {
                    "procedure_id": "1.013873",
                    "form_id": "m19",
                    "binding_status": "approved",
                    "review_status": "approved",
                    "approved": True,
                    "attestation_id": "attestation-1",
                }
            ]
        },
    )

    result = build_runtime_form_package(
        project_root=tmp_path,
        catalog_dir=forms_dir,
        output_root=tmp_path / "release-data",
        as_of=date(2026, 7, 30),
    )

    assert result["runtime_form_count"] == 1
    assert result["runtime_binding_count"] == 1
    assert result["runtime_procedure_count"] == 1
    packaged = json.loads(
        (
            tmp_path
            / "release-data/notebook_data/forms/canonical_procedures_v1.json"
        ).read_text(encoding="utf-8")
    )
    assert packaged["procedures"][0]["procedure_id"] == "1.013873"
    assert packaged["procedures"][0]["official_procedure_url"].startswith(
        "https://dichvucong.gov.vn/"
    )
