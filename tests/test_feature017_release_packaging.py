from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.package_feature017_release_assets import package_release


def test_packaging_binds_runtime_file_and_official_source_package(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    release_root = tmp_path / "release-data"
    source_file = source_root / "data" / "uploads" / "forms" / "form.docx"
    source_file.parent.mkdir(parents=True)
    source_file.write_bytes(b"exact extracted form")
    form_hash = hashlib.sha256(source_file.read_bytes()).hexdigest()
    package = b"official legal instrument package"
    package_hash = hashlib.sha256(package).hexdigest()
    manifest = {
        "schema_version": "form-release-v1",
        "release_id": "release-test",
        "version": 1,
        "legal_as_of": "2026-08-11",
        "source_snapshot_sha256": "a" * 64,
        "previous_release_id": None,
        "procedures": [{
            "procedure_id": "p1",
            "name": "Thủ tục thử",
            "domain": "cu_tru_an_ninh",
            "official_source_url": "https://dichvucong.gov.vn/p1",
            "coverage_status": "released",
        }],
        "assets": [{
            "form_id": "f1",
            "canonical_name": "Mẫu thử",
            "asset_kind": "file",
            "source_url": "https://vbpl.vn/source-page",
            "source_checksum": form_hash,
            "audiences": ["citizen"],
            "coverage_status": "released",
            "provenance": {"canonical_artifact": {
                "staging_path": "data/uploads/forms/form.docx",
                "source_download_url": "https://vbpl.vn/package.docx",
                "source_package_sha256": package_hash,
            }},
        }],
        "bindings": [{
            "binding_id": "b1",
            "procedure_id": "p1",
            "form_id": "f1",
            "requirement": "required",
            "audience": "citizen",
            "coverage_status": "released",
        }],
        "aliases": [{"procedure_id": "p1", "alias": "thủ tục thử", "alias_kind": "natural"}],
        "gaps": [],
        "exclusions": [],
        "coverage": {
            "procedure_total": 1,
            "procedure_decided": 1,
            "identity_total": 1,
            "identity_decided": 1,
            "binding_total": 1,
            "binding_decided": 1,
            "complete": True,
        },
        "build": {"pipeline_version": "test"},
    }

    result = package_release(
        manifest,
        source_root=source_root,
        release_root=release_root,
        fetch_package=lambda _url: package,
    )
    asset = result["manifest"]["assets"][0]

    assert result["gate_report"]["passed"] is True
    assert asset["runtime_path"].startswith("feature017/release-test/")
    assert asset["download_url"].endswith("/f1/download")
    assert (release_root / asset["runtime_path"]).read_bytes() == source_file.read_bytes()
    assert asset["provenance"]["canonical_artifact"]["source_package_sha256"] == package_hash
