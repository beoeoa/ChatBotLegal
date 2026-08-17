from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts import run_form_resolution_campaign as runner


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_runner_writes_versioned_latest_and_terminal_report(
    tmp_path: Path, monkeypatch
):
    inventory_path = tmp_path / "inventory.json"
    catalog_path = tmp_path / "catalog.json"
    attestations_path = tmp_path / "attestations.json"
    campaign_dir = tmp_path / "campaign"
    status_path = campaign_dir / "status.json"
    latest_path = campaign_dir / "latest.json"
    _write(
        inventory_path,
        {
            "canonical_forms": [],
            "verified_data_gaps": [
                {
                    "candidate_id": "occ-1",
                    "component_kind": "applicant_form",
                    "prior_approval_status": False,
                    "procedure_id": "1.001193",
                    "form_name": "Đơn đề nghị",
                    "source_tier": "central",
                }
            ],
        },
    )
    _write(catalog_path, {"forms": []})
    _write(attestations_path, {"attestations": []})
    monkeypatch.setattr(runner, "INVENTORY_PATH", inventory_path)
    monkeypatch.setattr(runner, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(runner, "ATTESTATIONS_PATH", attestations_path)
    monkeypatch.setattr(runner, "CAMPAIGN_DIR", campaign_dir)
    monkeypatch.setattr(runner, "STATUS_PATH", status_path)
    monkeypatch.setattr(runner, "LATEST_PATH", latest_path)
    monkeypatch.setattr(
        runner,
        "FEATURE006_CAMPAIGN_REPORT_PATH",
        tmp_path / "feature006-campaign-report.json",
    )

    def fake_resolver(**kwargs):
        run_dir = kwargs["run_dir"]
        _write(run_dir / "code-groups.json", {"groups": []})
        _write(
            run_dir / "code-resolution.json",
            {
                "run_id": "source-run",
                "pending_records": [],
                "resolved_groups": [],
                "verified_data_gaps": [],
            },
        )
        return {"status": "ok"}

    monkeypatch.setattr(runner, "_run_existing_code_resolver", fake_resolver)

    report = runner.execute(
        legal_as_of="2026-07-27",
        network=False,
        timeout=1,
    )

    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    assert report["counts"]["occurrences"] == 1
    assert report["counts"]["terminal_occurrences"] == 1
    assert report["current_catalog_state"]["catalog_forms"] == 0
    assert report["current_catalog_state"]["runtime_approved_forms"] == 0
    assert report["status"] == "VERIFIED_DATA_GAP"
    assert (Path(latest["manifest"]) / "occurrence-registry.json").is_file()
    assert json.loads(status_path.read_text(encoding="utf-8"))[
        "feature_flag_enabled"
    ] is False


def test_aggregate_timeout_preserves_completed_source_results(
    tmp_path: Path, monkeypatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    completed = {
        "run_id": "source-run",
        "pending_records": [],
        "resolved_groups": [],
        "verified_data_gaps": [
            {
                "group_id": "group-1",
                "reason_code": "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND",
            }
        ],
        "source_attempts": [
            {
                "issuing_instrument": "02/2024/TT-BYT",
                "reason_code": "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND",
            }
        ],
    }
    _write(run_dir / "code-resolution.json", completed)
    _write(run_dir / "code-groups.json", {"groups": [{"group_id": "group-1"}]})

    class TimeoutProcess:
        pid = 4242
        returncode = None

        def communicate(self, **_kwargs):
            raise subprocess.TimeoutExpired(cmd=["resolver"], timeout=60)

        def wait(self, **_kwargs):
            self.returncode = 1
            return 1

    monkeypatch.setattr(
        runner.subprocess,
        "Popen",
        lambda *_args, **_kwargs: TimeoutProcess(),
    )
    monkeypatch.setattr(runner.subprocess, "call", lambda *_args, **_kwargs: 0)

    report = runner._run_existing_code_resolver(
        inventory_path=tmp_path / "inventory.json",
        legal_as_of="2026-07-28",
        run_dir=run_dir,
        workers=1,
        timeout=1,
        network=True,
    )

    assert report["status"] == "partial_timeout"
    assert report["verified_data_gaps"] == completed["verified_data_gaps"]
    assert report["source_attempts"] == completed["source_attempts"]


def test_offline_resume_reuses_cached_resolution_without_erasing_groups(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    groups = {"groups": [{"group_id": "group-1"}]}
    cached = {
        "run_id": "source-run",
        "status": "partial_timeout",
        "pending_records": [{"candidate_id": "candidate-1"}],
        "resolved_groups": [{"group_id": "group-1"}],
        "verified_data_gaps": [],
        "source_attempts": [
            {
                "instrument": "02/2024/TT-BYT",
                "reason_code": "EXACT_OFFICIAL_DOCUMENT_FOUND",
            }
        ],
    }
    _write(run_dir / "code-groups.json", groups)
    _write(run_dir / "code-resolution.json", cached)

    report = runner._run_existing_code_resolver(
        inventory_path=tmp_path / "inventory.json",
        legal_as_of="2026-07-28",
        run_dir=run_dir,
        workers=1,
        timeout=1,
        network=False,
    )

    assert report == cached
    assert json.loads((run_dir / "code-groups.json").read_text(encoding="utf-8")) == groups
    assert json.loads((run_dir / "code-resolution.json").read_text(encoding="utf-8")) == cached


def test_candidate_identity_preserves_flattened_dvc_extraction_evidence() -> None:
    candidate = {
        "id": "candidate-dvc-07",
        "three_tier_group_id": "group-dvc-07",
        "procedure_id": "1.013870",
        "form_code": "07",
        "canonical_form_name": "Mẫu số 07",
        "issuing_instrument": "91/2016/NĐ-CP",
        "source_page_url": (
            "https://dichvucong.gov.vn/quyet-dinh-cong-bo/"
            "019eb146-cf41-72c8-9f55-1d3df27dd74a"
        ),
        "source_download_url": (
            "https://dichvucong.gov.vn/quyet-dinh-cong-bo/"
            "019eb146-cf41-72c8-9f55-1d3df27dd74a"
        ),
        "sha256": "a" * 64,
        "source_package_sha256": "b" * 64,
        "source_package_size_bytes": 170394,
        "source_attachment_id": "019eb4af-a3af-7143-b664-6dd27b38b9de",
        "source_retrieval_url": (
            "https://dichvucong.gov.vn/api/v1/submitting/preview-attachment"
        ),
        "source_retrieval_method": "POST",
        "source_file_name": "QĐ 1684 BYT.docx",
        "publication_decision_number": "1684/QĐ-BYT",
        "provenance": {
            "kind": "official_dvc_attachment",
            "publisher": "Cổng Dịch vụ công quốc gia",
        },
        "extraction": {
            "kind": "structural_docx_form_boundary",
            "complete": True,
            "element_range": [12, 31],
        },
    }
    group = {
        "canonical_identity_key": "group-dvc-07",
        "form_name": "Mẫu số 07",
        "source_tier": "central",
        "procedure_metadata": {
            "1.013870": {
                "requirement_identity_id": "6177c8ce5c0604a921478bb4",
                "occurrence_id": "feature006-dcb82eb8baee1c3b6252befd",
            }
        },
    }

    prepared = runner._candidate_with_identity(candidate, group)

    assert prepared["source_sha256"] == "b" * 64
    assert prepared["provenance"]["source_attachment_id"] == candidate[
        "source_attachment_id"
    ]
    assert prepared["provenance"]["source_package_sha256"] == "b" * 64
    assert prepared["extraction"] == candidate["extraction"]


def test_candidate_review_projection_normalizes_nfc_without_changing_source() -> None:
    candidate = {
        "id": "candidate-nfd",
        "canonical_form_name": "To\u031b\u0300 khai co\u0302ng chu\u031b\u0301ng",
        "publisher": "Bo\u0323\u0302 Tu\u031b pha\u0301p",
    }

    prepared = runner._candidate_with_identity(
        candidate,
        {"canonical_identity_key": "identity-nfc"},
    )

    assert prepared["canonical_name"] == "Tờ khai công chứng"
    assert prepared["publisher"] == "Bộ Tư pháp"
    assert prepared["metadata_normalization"]["form"] == "NFC"
    assert candidate["canonical_form_name"] == "To\u031b\u0300 khai co\u0302ng chu\u031b\u0301ng"


def test_runner_failure_replaces_matching_queued_status_fail_closed(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    manifest_sha = "a" * 64
    source_sha = "b" * 64
    _write(
        status_path,
        {
            "status": "queued",
            "stage": "queued",
            "legal_as_of": "2026-07-30",
            "manifest_sha256": manifest_sha,
            "source_snapshot_sha256": source_sha,
            "automated_approval": False,
            "feature_flag_enabled": False,
        },
    )

    runner._mark_runner_failed_fail_closed(
        legal_as_of="2026-07-30",
        manifest_sha256=manifest_sha,
        source_snapshot_sha256=source_sha,
        exc=ValueError("BRIDGE_MANIFEST_CHECKSUM_DRIFT"),
        status_path=status_path,
    )

    persisted = json.loads(status_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed_fail_closed"
    assert persisted["stage"] == "runner_failed"
    assert persisted["reason_code"] == "BRIDGE_MANIFEST_CHECKSUM_DRIFT"
    assert persisted["automated_approval"] is False
