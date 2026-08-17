from __future__ import annotations

import json
from pathlib import Path

from api.form_resolution_campaign import (
    build_campaign_baseline,
    build_campaign_report,
    build_occurrences_from_inventory,
    completion_input_paths,
    launch_form_resolution_campaign,
    list_campaign_gaps,
    mark_campaign_attested,
    mark_campaign_batch_attested,
)
from api.form_resolution_registry import build_occurrence_registry


def _inventory():
    return {
        "canonical_forms": [
            {
                "candidate_id": "resolved",
                "component_kind": "applicant_form",
                "prior_approval_status": False,
                "procedure_id": "1.001193",
                "procedure_name": "Đăng ký khai sinh",
                "form_name": "Mẫu số 01 ban hành kèm theo 04/2020/TT-BTP",
                "form_code": "Mẫu số 01",
                "issuing_instruments": ["04/2020/TT-BTP"],
                "source_tier": "central",
            }
        ],
        "verified_data_gaps": [
            {
                "candidate_id": "gap",
                "component_kind": "applicant_form",
                "prior_approval_status": False,
                "procedure_id": "1.001194",
                "form_name": "Đơn đề nghị",
                "source_tier": "central",
            },
            {
                "candidate_id": "seed",
                "component_kind": "supporting_document",
                "prior_approval_status": False,
                "procedure_id": "1.001195",
                "form_name": "Giấy chứng nhận đã cấp",
                "source_tier": "central",
            },
        ],
    }


def test_occurrences_from_inventory_are_scoped_and_not_duplicated():
    occurrences = build_occurrences_from_inventory(_inventory())

    assert [item["candidate_id"] for item in occurrences] == ["gap", "resolved"]


def test_campaign_baseline_has_counts_checksums_and_no_raw_content(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"forms":[{"form_id":"form-1"}]}', encoding="utf-8")
    attestations = tmp_path / "attestations.json"
    attestations.write_text('{"attestations":[]}', encoding="utf-8")

    baseline = build_campaign_baseline(
        run_id="run-1",
        legal_as_of="2026-07-27",
        occurrences=build_occurrences_from_inventory(_inventory()),
        catalog_path=catalog,
        attestations_path=attestations,
        feature_flag=False,
        active_collection="legal_chunks_lechan_primary_v20260723",
    )

    assert baseline["run_id"] == "run-1"
    assert baseline["counts"]["occurrences"] == 2
    assert baseline["feature_flag"] is False
    assert baseline["active_collection"] == "legal_chunks_lechan_primary_v20260723"
    assert baseline["checksums"]["catalog"]
    serialized = json.dumps(baseline, ensure_ascii=False)
    assert "Đăng ký khai sinh" not in serialized


def test_campaign_report_separates_occurrences_groups_bindings_and_gaps():
    registry = build_occurrence_registry(
        occurrences=build_occurrences_from_inventory(_inventory()),
        run_id="run-1",
        legal_as_of="2026-07-27",
    )
    report = build_campaign_report(
        registry=registry,
        baseline={"counts": {"runtime_approved": 13}},
        gap_records=[
            {
                "occurrence_id": "gap",
                "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
            }
        ],
        shortlist=[
            {"candidate_id": "candidate-1"},
        ],
    )

    assert report["counts"]["occurrences"] == 2
    assert report["counts"]["canonical_identity_groups"] == 1
    assert report["counts"]["procedure_bindings"] == 1
    assert report["counts"]["verified_data_gap"] == 1
    assert report["counts"]["ready_for_attestation"] == 1
    assert report["automated_approval"] is False


def test_campaign_with_mojibake_shortlist_is_not_ready_for_attestation():
    registry = build_occurrence_registry(
        occurrences=build_occurrences_from_inventory(_inventory()),
        run_id="run-1",
        legal_as_of="2026-07-27",
    )
    report = build_campaign_report(
        registry=registry,
        baseline={"counts": {}},
        gap_records=[],
        shortlist=[
            {
                "candidate_id": "candidate-1",
                "canonical_form_name": (
                    "Gi\u00e1\u00ba\u00a5y \u00c4\u2018\u00e1\u00bb\u0081 ngh\u00e1\u00bb\u2039"
                ),
            }
        ],
    )

    assert report["status"] == "BLOCKED_RELEASE"
    assert report["counts"]["ready_for_attestation"] == 0
    assert report["counts"]["invalid_shortlist_records"] == 1
    assert report["reason_counts"]["INVALID_UNICODE_METADATA"] == 1


def test_blocked_external_is_not_counted_as_verified_gap():
    registry = build_occurrence_registry(
        occurrences=build_occurrences_from_inventory(_inventory()),
        run_id="run-1",
        legal_as_of="2026-07-27",
    )
    report = build_campaign_report(
        registry=registry,
        baseline={"counts": {}},
        gap_records=[
            {
                "occurrence_id": "resolved",
                "reason_code": "BLOCKED_EXTERNAL",
            },
            {
                "occurrence_id": "gap",
                "reason_code": "FORM_IDENTITY_UNRESOLVED",
            },
        ],
        shortlist=[],
    )

    assert report["status"] == "BLOCKED_EXTERNAL"
    assert report["counts"]["blocked_external"] == 1
    assert report["counts"]["verified_data_gap"] == 1


def test_granular_external_source_reason_is_release_blocker():
    registry = build_occurrence_registry(
        occurrences=build_occurrences_from_inventory(_inventory()),
        run_id="run-1",
        legal_as_of="2026-07-27",
    )
    report = build_campaign_report(
        registry=registry,
        baseline={"counts": {}},
        gap_records=[
            {
                "occurrence_id": "resolved",
                "reason_code": "OFFICIAL_SOURCE_ENDPOINT_CHANGED",
            },
            {
                "occurrence_id": "gap",
                "reason_code": "FORM_IDENTITY_UNRESOLVED",
            },
        ],
        shortlist=[],
    )

    assert report["status"] == "BLOCKED_EXTERNAL"
    assert report["counts"]["blocked_external"] == 1
    assert report["counts"]["verified_data_gap"] == 1


def test_partial_resolver_timeout_is_external_and_preserves_found_source():
    registry = build_occurrence_registry(
        occurrences=build_occurrences_from_inventory(_inventory()),
        run_id="run-1",
        legal_as_of="2026-07-27",
    )
    report = build_campaign_report(
        registry=registry,
        baseline={"counts": {}},
        gap_records=[
            {
                "occurrence_id": "resolved",
                "reason_code": "OFFICIAL_SOURCE_FOUND_ARTIFACT_PENDING",
            },
            {
                "occurrence_id": "gap",
                "reason_code": "EXACT_CODE_RESOLVER_PARTIAL_TIMEOUT",
            },
        ],
        shortlist=[],
    )

    assert report["status"] == "BLOCKED_EXTERNAL"
    assert report["counts"]["blocked_external"] == 1
    assert report["counts"]["verified_data_gap"] == 0


def test_gap_listing_is_aggregate_only():
    registry = build_occurrence_registry(
        occurrences=build_occurrences_from_inventory(_inventory()),
        run_id="run-1",
        legal_as_of="2026-07-27",
    )
    gaps = list_campaign_gaps(
        registry=registry,
        gap_records=[
            {
                "occurrence_id": "gap",
                "procedure_id": "1.001194",
                "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
                "source_attempts": [
                    {"source_domain": "vbpl.vn", "status": "not_found"}
                ],
            }
        ],
    )

    assert gaps == [
        {
            "occurrence_id": "gap",
            "procedure_id": "1.001194",
            "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
            "source_attempt_count": 1,
        }
    ]


def test_launch_is_idempotent_for_ready_campaign_on_same_legal_date(
    tmp_path: Path,
):
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "form-resolution-campaign-v1",
                "run_id": "run-ready",
                "legal_as_of": "2026-07-28",
                "status": "READY_FOR_HUMAN_ATTESTATION",
                "stage": "complete",
                "counts": {"ready_for_attestation": 3},
                "reason_counts": {},
                "automated_approval": False,
                "human_attestation_required": True,
                "feature_flag_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    launched = []

    result = launch_form_resolution_campaign(
        project_root=tmp_path,
        legal_as_of="2026-07-28",
        status_path=status_path,
        launcher=lambda *args, **kwargs: launched.append((args, kwargs)),
    )

    assert result["run_id"] == "run-ready"
    assert result["launch_status"] == "already_available"
    assert launched == []


def test_launch_replaces_stale_ready_campaign_with_current_baseline(
    tmp_path: Path,
):
    import hashlib

    requirement_manifest = tmp_path / "manifest.json"
    source_snapshot = tmp_path / "source.json"
    requirement_manifest.write_text('{"requirements":"current"}', encoding="utf-8")
    source_snapshot.write_text('{"sources":"current"}', encoding="utf-8")
    manifest_sha = hashlib.sha256(requirement_manifest.read_bytes()).hexdigest()
    source_sha = hashlib.sha256(source_snapshot.read_bytes()).hexdigest()
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "form-resolution-campaign-v1",
                "run_id": "run-stale",
                "legal_as_of": "2026-07-28",
                "status": "READY_FOR_HUMAN_ATTESTATION",
                "stage": "complete",
                "counts": {"ready_for_attestation": 3},
                "reason_counts": {},
                "automated_approval": False,
                "human_attestation_required": True,
                "feature_flag_enabled": False,
                "manifest_sha256": "a" * 64,
                "source_snapshot_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    launched = []

    result = launch_form_resolution_campaign(
        project_root=tmp_path,
        legal_as_of="2026-07-28",
        status_path=status_path,
        launcher=lambda *args, **kwargs: launched.append((args, kwargs)),
        manifest_sha256=manifest_sha,
        source_snapshot_sha256=source_sha,
        requirement_manifest_path=requirement_manifest,
        source_snapshot_path=source_snapshot,
    )

    assert result["status"] == "queued"
    assert result["launch_status"] == "started"
    assert len(launched) == 1


def test_launch_uses_checksum_inputs_for_the_requested_legal_date(
    tmp_path: Path,
):
    import hashlib

    legal_as_of = "2026-07-30"
    manifest, snapshot = completion_input_paths(
        project_root=tmp_path,
        legal_as_of=legal_as_of,
    )
    manifest.parent.mkdir(parents=True)
    snapshot.parent.mkdir(parents=True)
    manifest.write_text('{"manifest":"current"}', encoding="utf-8")
    snapshot.write_text('{"snapshot":"current"}', encoding="utf-8")
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
    source_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    launched: list[list[str]] = []

    result = launch_form_resolution_campaign(
        project_root=tmp_path,
        legal_as_of=legal_as_of,
        status_path=tmp_path / "campaign-status.json",
        launcher=lambda command, **_kwargs: launched.append(command),
        manifest_sha256=manifest_sha,
        source_snapshot_sha256=source_sha,
    )

    assert result["launch_status"] == "started"
    assert launched
    command = launched[0]
    assert command[command.index("--requirement-manifest") + 1] == str(manifest)
    assert command[command.index("--source-snapshot") + 1] == str(snapshot)


def test_launch_resumes_blocked_campaign_instead_of_creating_duplicate(
    tmp_path: Path,
):
    requirement_manifest = tmp_path / "manifest.json"
    source_snapshot = tmp_path / "source.json"
    requirement_manifest.write_text('{"fixed":true}', encoding="utf-8")
    source_snapshot.write_text('{"fixed":true}', encoding="utf-8")
    import hashlib

    manifest_sha = hashlib.sha256(requirement_manifest.read_bytes()).hexdigest()
    source_sha = hashlib.sha256(source_snapshot.read_bytes()).hexdigest()
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "form-resolution-campaign-v1",
                "run_id": "run-blocked",
                "legal_as_of": "2026-07-28",
                "status": "BLOCKED_EXTERNAL",
                "stage": "complete",
                "counts": {},
                "reason_counts": {},
                "automated_approval": False,
                "human_attestation_required": True,
                "feature_flag_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    launched = []

    result = launch_form_resolution_campaign(
        project_root=tmp_path,
        legal_as_of="2026-07-28",
        status_path=status_path,
        launcher=lambda command, **kwargs: launched.append(command),
        manifest_sha256=manifest_sha,
        source_snapshot_sha256=source_sha,
        requirement_manifest_path=requirement_manifest,
        source_snapshot_path=source_snapshot,
    )

    assert result["run_id"] == "run-blocked"
    assert result["launch_status"] == "resumed"
    assert launched
    assert "--resume-run-id" in launched[0]
    assert "run-blocked" in launched[0]


def test_mark_campaign_attested_is_atomic_and_idempotent(tmp_path: Path):
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "form-resolution-campaign-v1",
                "run_id": "run-1",
                "legal_as_of": "2026-07-28",
                "status": "READY_FOR_HUMAN_ATTESTATION",
                "stage": "complete",
                "counts": {"ready_for_attestation": 59},
                "automated_approval": False,
                "human_attestation_required": True,
                "feature_flag_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    first = mark_campaign_attested(
        run_id="run-1",
        attestation_id="attestation-1",
        release_gate_status="queued",
        path=status_path,
    )
    second = mark_campaign_attested(
        run_id="run-1",
        attestation_id="attestation-1",
        release_gate_status="running",
        path=status_path,
    )

    assert first["status"] == "ATTESTED_PENDING_RELEASE_GATES"
    assert second["status"] == "ATTESTED_PENDING_RELEASE_GATES"
    assert second["attestation_id"] == "attestation-1"
    assert second["human_attestation_required"] is False
    assert second["feature_flag_enabled"] is False
    persisted = json.loads(status_path.read_text(encoding="utf-8"))
    assert persisted["release_gate_status"] == "running"


def test_batch_attestation_keeps_later_batch_open_and_only_closes_after_last(
    tmp_path: Path,
):
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "form-resolution-campaign-v1",
                "run_id": "run-1",
                "status": "READY_FOR_HUMAN_ATTESTATION",
                "human_attestation_required": True,
                "feature_flag_enabled": False,
            }
        ),
        encoding="utf-8",
    )

    first = mark_campaign_batch_attested(
        run_id="run-1",
        batch_id="form-review-001",
        attestation_id="attestation-1",
        release_gate_status="queued",
        total_batch_count=2,
        path=status_path,
    )
    second = mark_campaign_batch_attested(
        run_id="run-1",
        batch_id="form-review-002",
        attestation_id="attestation-2",
        release_gate_status="queued",
        total_batch_count=2,
        path=status_path,
    )

    assert first["status"] == "READY_FOR_HUMAN_ATTESTATION"
    assert first["remaining_batch_count"] == 1
    assert first["human_attestation_required"] is True
    assert second["status"] == "ATTESTED_PENDING_RELEASE_GATES"
    assert second["remaining_batch_count"] == 0
    assert second["human_attestation_required"] is False
    assert second["feature_flag_enabled"] is False


def test_attested_campaign_cannot_be_replaced_by_a_new_campaign(tmp_path: Path):
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "form-resolution-campaign-v1",
                "run_id": "run-attested",
                "legal_as_of": "2026-07-28",
                "status": "ATTESTED_RELEASE_GATE_BLOCKED",
                "stage": "post_attestation_release_gates",
                "counts": {},
                "automated_approval": False,
                "human_attestation_required": False,
                "feature_flag_enabled": False,
            }
        ),
        encoding="utf-8",
    )
    launched = []

    result = launch_form_resolution_campaign(
        project_root=tmp_path,
        legal_as_of="2026-07-28",
        status_path=status_path,
        launcher=lambda *args, **kwargs: launched.append((args, kwargs)),
    )

    assert result["launch_status"] == "already_available"
    assert result["run_id"] == "run-attested"
    assert launched == []
