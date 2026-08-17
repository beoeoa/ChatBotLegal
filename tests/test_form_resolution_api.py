from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request


def _request(*, role: str = "admin", user_id: str | None = "user_account:admin"):
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/procedures/forms-catalog/form-resolution/status/current",
            "headers": [],
        }
    )
    request.state.user_role = role
    request.state.user_id = user_id
    return request


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_form_resolution_contract_alias_routes_are_exposed() -> None:
    from api.routers.ward_procedures import router

    paths = {route.path for route in router.routes}
    assert {
        "/procedures/forms-catalog/form-resolution/current",
        "/procedures/forms-catalog/form-resolution/{run_id}",
        "/procedures/forms-catalog/form-resolution/{run_id}/review-shortlist",
    } <= paths
    assert {
        "/procedures/forms-catalog/form-resolution/status/current",
        "/procedures/forms-catalog/form-resolution/{run_id}/status",
        "/procedures/forms-catalog/form-resolution/{run_id}/shortlist",
    } <= paths


def test_current_campaign_status_is_admin_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api import form_resolution_campaign
    from api.routers.ward_procedures import get_current_form_resolution_status

    status_path = tmp_path / "status.json"
    _write(
        status_path,
        {
            "schema_version": "form-resolution-campaign-v1",
            "run_id": "run-1",
            "status": "running",
            "stage": "identity_resolution",
            "counts": {"occurrences": 715},
            "reason_counts": {},
            "automated_approval": False,
            "human_attestation_required": True,
            "feature_flag_enabled": False,
        },
    )
    monkeypatch.setattr(form_resolution_campaign, "STATUS_PATH", status_path)

    result = asyncio.run(get_current_form_resolution_status(_request()))
    assert result["counts"]["occurrences"] == 715
    assert result["feature_flag_enabled"] is False

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            get_current_form_resolution_status(
                _request(role="citizen", user_id="user_account:citizen")
            )
        )
    assert exc_info.value.status_code == 403


def test_gap_endpoint_returns_only_opaque_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api import form_resolution_campaign
    from api.routers.ward_procedures import get_form_resolution_gaps

    monkeypatch.setattr(form_resolution_campaign, "CAMPAIGN_DIR", tmp_path)
    _write(
        tmp_path / "runs" / "run-1" / "gaps.json",
        {
            "records": [
                {
                    "occurrence_id": "opaque-1",
                    "procedure_id": "1.001193",
                    "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
                    "source_attempts": [{"url": "https://example.invalid/private"}],
                    "form_name": "Không được trả ra",
                }
            ]
        },
    )

    result = asyncio.run(get_form_resolution_gaps("run-1", _request()))

    assert result["records"] == [
        {
            "occurrence_id": "opaque-1",
            "procedure_id": "1.001193",
            "reason_code": "OFFICIAL_FORM_FILE_NOT_FOUND",
            "source_attempt_count": 1,
        }
    ]
    assert "form_name" not in json.dumps(result)


def test_shortlist_endpoint_removes_internal_paths_and_ocr_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api import form_resolution_campaign
    from api.routers.ward_procedures import get_form_resolution_shortlist

    monkeypatch.setattr(form_resolution_campaign, "CAMPAIGN_DIR", tmp_path)
    _write(
        tmp_path / "runs" / "run-1" / "review-shortlist.json",
        {
            "records": [
                {
                    "candidate_id": "candidate-1",
                    "canonical_name": "Mẫu số 01",
                    "local_path": "C:/private/form.pdf",
                    "ocr_text": "private extracted text",
                    "official_download_url": "https://vbpl.vn/form.pdf",
                    "sha256": "a" * 64,
                }
            ]
        },
    )

    result = asyncio.run(get_form_resolution_shortlist("run-1", _request()))
    record = result["records"][0]

    assert record["has_official_file"] is True
    assert "local_path" not in record
    assert "ocr_text" not in record


def test_shortlist_endpoint_rejects_mojibake_and_reports_canonical_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api import form_resolution_campaign
    from api.routers.ward_procedures import get_form_resolution_shortlist

    monkeypatch.setattr(form_resolution_campaign, "CAMPAIGN_DIR", tmp_path)
    _write(
        tmp_path / "runs" / "run-1" / "review-shortlist.json",
        {
            "records": [
                {
                    "id": "candidate-valid",
                    "proposed_canonical_form_id": "form-valid",
                    "procedure_id": "1.000001",
                    "canonical_form_name": "Gi\u1ea5y \u0111\u1ec1 ngh\u1ecb",
                    "effective_from": "2026-01-01",
                    "jurisdiction": "Hai Phong",
                    "administrative_level": "commune",
                    "official_download_url": "https://vbpl.vn/form.pdf",
                    "sha256": "a" * 64,
                },
                {
                    "id": "candidate-invalid",
                    "proposed_canonical_form_id": "form-invalid",
                    "procedure_id": "1.000002",
                    "canonical_form_name": (
                        "Gi\u00e1\u00ba\u00a5y \u00c4\u2018\u00e1\u00bb\u0081 ngh\u00e1\u00bb\u2039"
                    ),
                    "effective_from": "2026-01-01",
                    "jurisdiction": "Hai Phong",
                    "administrative_level": "commune",
                    "official_download_url": "https://vbpl.vn/form-2.pdf",
                    "sha256": "b" * 64,
                },
            ]
        },
    )

    result = asyncio.run(get_form_resolution_shortlist("run-1", _request()))

    assert [item["canonical_form_id"] for item in result["records"]] == [
        "form-valid"
    ]
    assert result["canonical_form_count"] == 1
    assert result["procedure_binding_count"] == 1
    assert result["invalid_record_count"] == 1
    assert len(result["preview_fingerprint"]) == 64
    assert result["attestation_id"].startswith("form-resolution-run-1-")


def test_shortlist_moves_to_the_next_unattested_campaign_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api import form_resolution_campaign
    from api.routers.ward_procedures import get_form_resolution_shortlist

    monkeypatch.setattr(form_resolution_campaign, "CAMPAIGN_DIR", tmp_path)
    monkeypatch.setattr(
        form_resolution_campaign, "STATUS_PATH", tmp_path / "status_v1.json"
    )
    record = {
        "candidate_id": "candidate-1",
        "canonical_form_id": "form-1",
        "procedure_id": "1.000001",
        "canonical_name": "Giấy đề nghị",
        "official_download_url": "https://vbpl.vn/form.pdf",
        "sha256": "a" * 64,
    }
    _write(
        tmp_path / "runs" / "run-1" / "review-batches.json",
        {
            "batches": [
                {
                    "batch_id": "form-review-001",
                    "preview_fingerprint": "1" * 64,
                    "manifest_sha256": "a" * 64,
                    "source_snapshot_sha256": "b" * 64,
                    "records": [record],
                },
                {
                    "batch_id": "form-review-002",
                    "preview_fingerprint": "2" * 64,
                    "manifest_sha256": "a" * 64,
                    "source_snapshot_sha256": "b" * 64,
                    "records": [{**record, "candidate_id": "candidate-2"}],
                },
            ]
        },
    )
    _write(
        tmp_path / "status_v1.json",
        {
            "run_id": "run-1",
            "status": "READY_FOR_HUMAN_ATTESTATION",
            "feature_flag_enabled": False,
            "attested_batches": [
                {"batch_id": "form-review-001", "attestation_id": "already-done"}
            ],
        },
    )

    result = asyncio.run(get_form_resolution_shortlist("run-1", _request()))

    assert result["batch_id"] == "form-review-002"
    assert result["batch_count"] == 2
    assert result["preview_fingerprint"] == "2" * 64
    assert result["records"][0]["candidate_id"] == "candidate-2"


def test_matching_campaign_attestation_is_recognized_for_handoff_recovery() -> None:
    from api.routers.ward_procedures import _has_matching_campaign_attestation

    attestation = {
        "attestation_id": "form-resolution-run-1-batch-2",
        "reviewer_id": "user_account:admin",
        "decision": "approved",
        "batch_id": "form-review-002",
        "preview_fingerprint": "c" * 64,
        "manifest_sha256": "a" * 64,
        "source_snapshot_sha256": "b" * 64,
    }
    records = [
        {
            "candidate_id": "candidate-2",
            "canonical_form_id": "form-2",
            "procedure_id": "1.000002",
        }
    ]
    audit_payload = {
        "attestations": [
            {
                **attestation,
                "item_count": 1,
                "items": [{**records[0], "decision": "approved"}],
            }
        ]
    }

    assert _has_matching_campaign_attestation(audit_payload, attestation, records)
    assert not _has_matching_campaign_attestation(
        audit_payload,
        {**attestation, "reviewer_id": "user_account:other-admin"},
        records,
    )


def test_campaign_attestation_rejects_stale_shortlist_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api import form_resolution_campaign
    from api.routers.ward_procedures import (
        FormResolutionAttestationRequest,
        attest_form_resolution,
    )

    monkeypatch.setattr(form_resolution_campaign, "CAMPAIGN_DIR", tmp_path)
    _write(
        tmp_path / "runs" / "run-1" / "report.json",
        {
            "status": "READY_FOR_HUMAN_ATTESTATION",
            "legal_as_of": "2026-07-27",
        },
    )
    _write(
        tmp_path / "runs" / "run-1" / "review-shortlist.json",
        {
            "records": [
                {
                    "id": "candidate-1",
                    "proposed_canonical_form_id": "form-1",
                    "procedure_id": "1.000001",
                    "canonical_form_name": "Gi\u1ea5y \u0111\u1ec1 ngh\u1ecb",
                    "effective_from": "2026-01-01",
                    "jurisdiction": "Hai Phong",
                    "administrative_level": "commune",
                    "official_download_url": "https://vbpl.vn/form.pdf",
                    "sha256": "a" * 64,
                }
            ]
        },
    )
    request = FormResolutionAttestationRequest(
        attestation_id="form-resolution-run-1-stale",
        reviewed_at="2026-07-28T00:00:00Z",
        legal_as_of="2026-07-27",
        preview_fingerprint="0" * 64,
        batch_id="legacy-batch",
        manifest_sha256="a" * 64,
        source_snapshot_sha256="b" * 64,
        decision="approved",
        review_note="reviewed",
        items=[
            {
                "candidate_id": "candidate-1",
                "canonical_form_id": "form-1",
                "procedure_id": "1.000001",
                "effective_from": "2026-01-01",
                "effective_to": None,
                "jurisdiction": "Hai Phong",
                "administrative_level": "commune",
            }
        ],
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(attest_form_resolution("run-1", request, _request()))

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "PREVIEW_STALE_OR_TAMPERED"


def test_campaign_attestation_validates_campaign_preview_before_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api import form_release_gate, form_resolution_campaign
    from api.routers import ward_procedures
    from api.routers.ward_procedures import (
        FormResolutionAttestationRequest,
        attest_form_resolution,
        get_form_resolution_shortlist,
    )

    monkeypatch.setattr(form_resolution_campaign, "CAMPAIGN_DIR", tmp_path)
    _write(
        tmp_path / "runs" / "run-1" / "report.json",
        {
            "status": "READY_FOR_HUMAN_ATTESTATION",
            "legal_as_of": "2026-07-27",
        },
    )
    _write(
        tmp_path / "runs" / "run-1" / "review-shortlist.json",
        {
            "manifest_sha256": "a" * 64,
            "source_snapshot_sha256": "b" * 64,
            "records": [
                {
                    "id": "candidate-1",
                    "proposed_canonical_form_id": "form-1",
                    "procedure_id": "1.000001",
                    "canonical_form_name": "Gi\u1ea5y \u0111\u1ec1 ngh\u1ecb",
                    "effective_from": "2026-01-01",
                    "jurisdiction": "Hai Phong",
                    "administrative_level": "commune",
                    "official_download_url": "https://vbpl.vn/form.pdf",
                    "sha256": "a" * 64,
                }
            ]
        },
    )
    shortlist = asyncio.run(
        get_form_resolution_shortlist("run-1", _request())
    )
    captured = {}

    def _fake_sync(**kwargs):
        captured.update(kwargs)
        return {"status": "applied"}

    from api import form_review_sync

    monkeypatch.setattr(
        form_review_sync, "apply_form_review_sync_files", _fake_sync
    )
    monkeypatch.setattr(
        form_release_gate,
        "launch_form_release_gates",
        lambda **kwargs: {
            "status": "queued",
            "stage": "queued",
            "launch_status": "started",
        },
    )
    marked = {}

    def _mark(**kwargs):
        marked.update(kwargs)
        return {"status": "ATTESTED_PENDING_RELEASE_GATES"}

    monkeypatch.setattr(
        form_resolution_campaign, "mark_campaign_batch_attested", _mark
    )
    item = shortlist["records"][0]
    request = FormResolutionAttestationRequest(
        attestation_id=shortlist["attestation_id"],
        reviewed_at="2026-07-28T00:00:00Z",
        legal_as_of="2026-07-27",
        preview_fingerprint=shortlist["preview_fingerprint"],
        batch_id=shortlist["batch_id"],
        manifest_sha256=shortlist["manifest_sha256"],
        source_snapshot_sha256=shortlist["source_snapshot_sha256"],
        decision="approved",
        review_note="reviewed",
        items=[
            {
                "candidate_id": item["candidate_id"],
                "canonical_form_id": item["canonical_form_id"],
                "procedure_id": item["procedure_id"],
                "effective_from": item["effective_from"],
                "effective_to": item["effective_to"],
                "jurisdiction": item["jurisdiction"],
                "administrative_level": item["administrative_level"],
            }
        ],
    )

    result = asyncio.run(attest_form_resolution("run-1", request, _request()))

    assert result["status"] == "applied"
    assert result["campaign_status"] == "ATTESTED_PENDING_RELEASE_GATES"
    assert result["reviewer_id"] == "user_account:admin"
    assert result["automated_approval"] is False
    assert result["release_gate"] == {
        "status": "queued",
        "stage": "queued",
        "launch_status": "started",
        "feature_flag_enabled": False,
    }
    assert captured["attestation"]["preview_fingerprint"] == shortlist["preview_fingerprint"]
    assert captured["campaign_batch"]["batch_id"] == shortlist["batch_id"]
    assert marked["run_id"] == "run-1"
    assert marked["batch_id"] == shortlist["batch_id"]
    assert marked["attestation_id"] == shortlist["attestation_id"]
    assert marked["release_gate_status"] == "queued"


def test_campaign_launch_is_admin_only_and_checksum_bound(
    monkeypatch: pytest.MonkeyPatch,
):
    from api import form_resolution_campaign
    from api.routers.ward_procedures import run_form_resolution_campaign

    captured = {}

    def _launch(**kwargs):
        captured.update(kwargs)
        return {
            "status": "queued",
            "manifest_sha256": kwargs["manifest_sha256"],
            "source_snapshot_sha256": kwargs["source_snapshot_sha256"],
            "automated_approval": False,
        }

    monkeypatch.setattr(
        form_resolution_campaign, "launch_form_resolution_campaign", _launch
    )
    result = asyncio.run(
        run_form_resolution_campaign(
            _request(),
            legal_as_of="2026-07-29",
            manifest_sha256="a" * 64,
            source_snapshot_sha256="b" * 64,
        )
    )
    assert result["manifest_sha256"] == "a" * 64
    assert result["source_snapshot_sha256"] == "b" * 64
    assert captured["legal_as_of"] == "2026-07-29"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            run_form_resolution_campaign(
                _request(role="citizen", user_id="user_account:citizen"),
                legal_as_of="2026-07-29",
                manifest_sha256="a" * 64,
                source_snapshot_sha256="b" * 64,
            )
        )
    assert exc_info.value.status_code == 403


def test_shortlist_rejects_a_review_batch_over_25_identities_without_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api import form_resolution_campaign
    from api.routers.ward_procedures import get_form_resolution_shortlist

    monkeypatch.setattr(form_resolution_campaign, "CAMPAIGN_DIR", tmp_path)
    records = [
        {
            "candidate_id": f"candidate-{index:02d}",
            "canonical_form_id": f"form-{index:02d}",
            "procedure_id": f"procedure-{index:02d}",
            "canonical_name": f"Mẫu {index:02d}",
            "official_download_url": f"https://vbpl.vn/form-{index:02d}.pdf",
            "sha256": f"{index:064x}"[-64:],
            "approved": False,
            "runtime_eligible": False,
        }
        for index in range(30)
    ]
    _write(
        tmp_path / "runs" / "run-1" / "review-shortlist.json",
        {
            "records": records,
            "manifest_sha256": "a" * 64,
            "source_snapshot_sha256": "b" * 64,
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_form_resolution_shortlist("run-1", _request()))

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "FORM_REVIEW_BATCH_SIZE_INVALID"


def test_shortlist_preserves_all_bindings_for_at_most_25_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from api import form_resolution_campaign
    from api.routers.ward_procedures import get_form_resolution_shortlist

    monkeypatch.setattr(form_resolution_campaign, "CAMPAIGN_DIR", tmp_path)
    records = [
        {
            "candidate_id": f"candidate-{index:02d}",
            "requirement_identity_id": f"identity-{min(index, 24):02d}",
            "canonical_form_id": f"form-{min(index, 24):02d}",
            "procedure_id": f"procedure-{index:02d}",
            "canonical_name": f"Mẫu {min(index, 24):02d}",
            "source_page_url": "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/test",
            "delivery_type": "interactive_eform",
            "approved": False,
            "runtime_eligible": False,
        }
        for index in range(31)
    ]
    _write(
        tmp_path / "runs" / "run-1" / "review-shortlist.json",
        {
            "batch_id": "form-review-001-test",
            "identity_count": 25,
            "records": records,
            "manifest_sha256": "a" * 64,
            "source_snapshot_sha256": "b" * 64,
            "preview_fingerprint": "c" * 64,
        },
    )

    result = asyncio.run(get_form_resolution_shortlist("run-1", _request()))

    assert len(result["records"]) == 31
    assert result["identity_count"] == 25
    assert result["batch_id"] == "form-review-001-test"
    assert result["preview_fingerprint"] == "c" * 64


@pytest.mark.parametrize("role", ["citizen", "officer"])
def test_campaign_attestation_is_admin_only_for_every_non_admin_role(
    role: str,
) -> None:
    from api.routers.ward_procedures import (
        FormResolutionAttestationRequest,
        attest_form_resolution,
    )

    request = FormResolutionAttestationRequest(
        attestation_id="form-resolution-run-1-test",
        reviewed_at="2026-07-29T00:00:00Z",
        legal_as_of="2026-07-29",
        preview_fingerprint="c" * 64,
        manifest_sha256="a" * 64,
        source_snapshot_sha256="b" * 64,
        batch_id="form-review-001-test",
        decision="approved",
        items=[
            {
                "candidate_id": "candidate-1",
                "canonical_form_id": "form-1",
                "procedure_id": "1.000001",
                "effective_from": None,
                "effective_to": None,
                "jurisdiction": "Hai Phong",
                "administrative_level": "commune",
            }
        ],
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            attest_form_resolution(
                "run-1",
                request,
                _request(role=role, user_id=f"user_account:{role}"),
            )
        )
    assert exc_info.value.status_code == 403


def test_campaign_attestation_request_rejects_forged_reviewer() -> None:
    from api.routers.ward_procedures import FormResolutionAttestationRequest

    with pytest.raises(ValidationError):
        FormResolutionAttestationRequest.model_validate(
            {
                "attestation_id": "form-resolution-run-1-test",
                "reviewer_id": "user_account:forged",
                "reviewed_at": "2026-07-29T00:00:00Z",
                "legal_as_of": "2026-07-29",
                "preview_fingerprint": "c" * 64,
                "manifest_sha256": "a" * 64,
                "source_snapshot_sha256": "b" * 64,
                "batch_id": "form-review-001-test",
                "decision": "approved",
                "items": [
                    {
                        "candidate_id": "candidate-1",
                        "canonical_form_id": "form-1",
                        "procedure_id": "1.000001",
                        "effective_from": None,
                        "jurisdiction": "Hai Phong",
                        "administrative_level": "commune",
                    }
                ],
            }
        )
