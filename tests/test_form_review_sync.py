from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from api.form_review_sync import (
    FormReviewSyncError,
    apply_candidate_queue_decision,
    apply_form_review_sync_files,
    build_form_review_preview,
    build_form_review_sync,
)


def _pdf(root: Path) -> tuple[str, str]:
    relative = "data/uploads/forms/priority_official/candidate-form.pdf"
    target = root / relative
    target.parent.mkdir(parents=True)
    content = b"%PDF-1.7\n" + (b"official-form\n" * 200)
    target.write_bytes(content)
    return relative, hashlib.sha256(content).hexdigest()


def _state(root: Path) -> tuple[dict, dict, dict, dict]:
    local_path, digest = _pdf(root)
    candidates = {
        "records": [
            {
                "id": "candidate-1",
                "detected_form_name": "Tờ khai đăng ký khai sinh",
                "procedure_id": "dang_ky_khai_sinh",
                "suggested_procedure_id": "dang_ky_khai_sinh",
                "domain": "ho_tich_chung_thuc",
                "review_status": "approved",
                "is_approved": True,
                "legal_review_status": "candidate_pending_review",
                "priority_path": local_path,
                "sha256": digest,
                "source_page_url": "https://sotp.haiphong.gov.vn/thu-tuc/khai-sinh",
                "source_download_url": "https://cdn.haiphong.gov.vn/forms/khai-sinh.pdf",
                "legal_basis": ["60/2014/QH13"],
                "effective_from": "2025-07-01",
            }
        ]
    }
    forms = {
        "forms": [
            {
                "form_id": "form-birth",
                "procedure_ids": ["dang_ky_khai_sinh"],
                "canonical_name": "Tờ khai đăng ký khai sinh",
                "domain": "ho_tich_chung_thuc",
                "administrative_level": "commune",
                "jurisdiction": "Hai Phong",
                "review_status": "candidate_pending_review",
                "legal_review_status": "not_reviewed",
                "approved": False,
                "candidate_source_evidence": [
                    {"candidate_id": "candidate-1", "sha256": digest}
                ],
            }
        ]
    }
    bindings = {
        "bindings": [
            {
                "procedure_id": "dang_ky_khai_sinh",
                "form_id": "form-birth",
                "binding_status": "candidate_pending_review",
                "review_status": "pending",
                "approved": False,
            }
        ]
    }
    attestations = {"attestations": []}
    return candidates, forms, bindings, attestations


def _review_artifacts() -> tuple[dict, dict]:
    return (
        {"forms": []},
        {"entries": [], "manifest_sha256": None},
    )


def _attestation() -> dict:
    return {
        "attestation_id": "attestation-20260727-01",
        "reviewer_id": "user_account:legal-reviewer",
        "reviewed_at": "2026-07-27T10:00:00+07:00",
        "decision": "approved",
        "review_note": "Đã đối chiếu nguồn chính thức và hiệu lực.",
        "items": [
            {
                "candidate_id": "candidate-1",
                "canonical_form_id": "form-birth",
                "procedure_id": "dang_ky_khai_sinh",
                "effective_from": "2025-07-01",
                "effective_to": None,
                "jurisdiction": "Hai Phong",
                "administrative_level": "commune",
            }
        ],
    }


def _campaign_eform_batch() -> dict:
    from api.form_resolution_campaign import review_batch_fingerprint

    record = {
        "candidate_id": "feature006-eform-1",
        "requirement_identity_id": "identity-eform-1",
        "canonical_form_id": "form-eform-identity-eform-1",
        "procedure_id": "1.000001",
        "canonical_name": "Biểu mẫu điện tử thử nghiệm",
        "domain": "ho_tich_chung_thuc",
        "administrative_level": "commune",
        "jurisdiction": "Hai Phong",
        "source_page_url": (
            "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/procedure-1"
        ),
        "delivery_type": "interactive_eform",
        "effectivity_reason_code": "OFFICIAL_EFORM_TECHNICALLY_VERIFIED",
        "legal_as_of": "2026-07-29",
        "approved": False,
        "runtime_eligible": False,
    }
    batch = {
        "batch_id": "form-review-001-test",
        "fingerprint_schema_version": "form-review-batch-v2",
        "manifest_sha256": "a" * 64,
        "source_snapshot_sha256": "b" * 64,
        "identity_count": 1,
        "records": [record],
    }
    batch["preview_fingerprint"] = review_batch_fingerprint(
        batch["records"],
        manifest_sha256=batch["manifest_sha256"],
        source_snapshot_sha256=batch["source_snapshot_sha256"],
    )
    return batch


def _campaign_attestation() -> dict:
    batch = _campaign_eform_batch()
    return {
        "attestation_id": "form-resolution-run-1-test",
        "reviewer_id": "user_account:legal-reviewer",
        "reviewed_at": "2026-07-29T10:00:00+07:00",
        "decision": "approved",
        "review_note": "Đã kiểm tra tuyến e-form chính thức.",
        "batch_id": "form-review-001-test",
        "preview_fingerprint": batch["preview_fingerprint"],
        "manifest_sha256": "a" * 64,
        "source_snapshot_sha256": "b" * 64,
        "items": [
            {
                "candidate_id": "feature006-eform-1",
                "canonical_form_id": "form-eform-identity-eform-1",
                "procedure_id": "1.000001",
                "effective_from": None,
                "effective_to": None,
                "jurisdiction": "Hai Phong",
                "administrative_level": "commune",
            }
        ],
    }


def test_batch_attestation_syncs_candidate_form_binding_and_audit(tmp_path: Path) -> None:
    candidates, forms, bindings, attestations = _state(tmp_path)
    official_index, checksum_manifest = _review_artifacts()

    result = build_form_review_sync(
        candidate_payload=candidates,
        canonical_forms_payload=forms,
        bindings_payload=bindings,
        attestations_payload=attestations,
        official_index_payload=official_index,
        checksum_manifest_payload=checksum_manifest,
        attestation=_attestation(),
        project_root=tmp_path,
    )

    candidate = result["candidate_payload"]["records"][0]
    form = result["canonical_forms_payload"]["forms"][0]
    binding = result["bindings_payload"]["bindings"][0]

    assert candidate["legal_review_status"] == "approved"
    assert candidate["reviewed_by"] == "user_account:legal-reviewer"
    assert candidate["is_canonical"] is True
    assert form["review_status"] == "approved"
    assert form["legal_review_status"] == "approved"
    assert form["approved"] is True
    assert form["effective_from"] == "2025-07-01"
    assert form["local_path"] == candidate["priority_path"]
    assert form["sha256"] == candidate["sha256"]
    assert binding["binding_status"] == "approved"
    assert binding["approved"] is True
    assert result["attestations_payload"]["attestations"][0]["item_count"] == 1
    assert result["official_index_payload"]["forms"][0]["is_canonical"] is True
    checksum_entry = result["checksum_manifest_payload"]["entries"][0]
    assert checksum_entry["form_id"] == "form-birth"
    assert checksum_entry["sha256"] == candidate["sha256"]
    assert result["checksum_manifest_payload"]["manifest_sha256"]


def test_campaign_eform_batch_is_checksum_bound_and_syncs_without_fake_file(
    tmp_path: Path,
) -> None:
    result = build_form_review_sync(
        candidate_payload={"records": []},
        canonical_forms_payload={"forms": []},
        bindings_payload={"bindings": []},
        attestations_payload={"attestations": []},
        official_index_payload={"forms": []},
        checksum_manifest_payload={"entries": []},
        attestation=_campaign_attestation(),
        campaign_batch=_campaign_eform_batch(),
        project_root=tmp_path,
    )

    candidate = result["candidate_payload"]["records"][0]
    form = result["canonical_forms_payload"]["forms"][0]
    audit = result["attestations_payload"]["attestations"][0]
    checksum = result["checksum_manifest_payload"]["entries"][0]
    assert candidate["runtime_eligible"] is True
    assert form["file_type"] == "online"
    assert form["local_path"] is None
    assert form["sha256"] is None
    assert form["official_source_page"].startswith("https://dichvucong.gov.vn/")
    assert checksum["route_sha256"]
    assert audit["batch_id"] == "form-review-001-test"
    assert audit["manifest_sha256"] == "a" * 64
    assert audit["source_snapshot_sha256"] == "b" * 64
    assert audit["preview_fingerprint"] == _campaign_eform_batch()[
        "preview_fingerprint"
    ]


def test_campaign_batch_accepts_proposed_canonical_form_identifier(
    tmp_path: Path,
) -> None:
    from api.form_resolution_campaign import review_batch_fingerprint

    batch = _campaign_eform_batch()
    canonical_form_id = batch["records"][0].pop("canonical_form_id")
    batch["records"][0]["proposed_canonical_form_id"] = canonical_form_id
    batch["preview_fingerprint"] = review_batch_fingerprint(
        batch["records"],
        manifest_sha256=batch["manifest_sha256"],
        source_snapshot_sha256=batch["source_snapshot_sha256"],
    )
    attestation = _campaign_attestation()
    attestation["preview_fingerprint"] = batch["preview_fingerprint"]

    result = build_form_review_sync(
        candidate_payload={"records": []},
        canonical_forms_payload={"forms": []},
        bindings_payload={"bindings": []},
        attestations_payload={"attestations": []},
        official_index_payload={"forms": []},
        checksum_manifest_payload={"entries": []},
        attestation=attestation,
        campaign_batch=batch,
        project_root=tmp_path,
    )

    assert result["status"] == "applied"
    assert result["candidate_payload"]["records"][0][
        "proposed_canonical_form_id"
    ] == canonical_form_id


def test_campaign_batch_rejects_manifest_or_exact_item_drift(tmp_path: Path) -> None:
    attestation = _campaign_attestation()
    attestation["manifest_sha256"] = "d" * 64
    with pytest.raises(FormReviewSyncError, match="FORM_REQUIREMENT_MANIFEST_DRIFT"):
        build_form_review_sync(
            candidate_payload={"records": []},
            canonical_forms_payload={"forms": []},
            bindings_payload={"bindings": []},
            attestations_payload={"attestations": []},
            attestation=attestation,
            campaign_batch=_campaign_eform_batch(),
            project_root=tmp_path,
        )

    attestation = _campaign_attestation()
    attestation["items"][0]["procedure_id"] = "1.000002"
    with pytest.raises(FormReviewSyncError, match="PREVIEW_STALE_OR_TAMPERED"):
        build_form_review_sync(
            candidate_payload={"records": []},
            canonical_forms_payload={"forms": []},
            bindings_payload={"bindings": []},
            attestations_payload={"attestations": []},
            attestation=attestation,
            campaign_batch=_campaign_eform_batch(),
            project_root=tmp_path,
        )


def test_campaign_file_transaction_is_byte_idempotent(tmp_path: Path) -> None:
    import json

    paths = {
        "candidate_path": tmp_path / "candidates.json",
        "canonical_forms_path": tmp_path / "forms.json",
        "bindings_path": tmp_path / "bindings.json",
        "official_index_path": tmp_path / "official-index.json",
        "checksum_manifest_path": tmp_path / "checksum-manifest.json",
        "attestations_path": tmp_path / "attestations.json",
    }
    payloads = [
        {"records": []},
        {"forms": []},
        {"bindings": []},
        {"forms": []},
        {"entries": [], "manifest_sha256": None},
        {"attestations": []},
    ]
    for path, payload in zip(paths.values(), payloads, strict=True):
        path.write_text(json.dumps(payload), encoding="utf-8")

    first = apply_form_review_sync_files(
        **paths,
        attestation=_campaign_attestation(),
        campaign_batch=_campaign_eform_batch(),
        project_root=tmp_path,
    )
    after_first = {name: path.read_bytes() for name, path in paths.items()}
    second = apply_form_review_sync_files(
        **paths,
        attestation=_campaign_attestation(),
        campaign_batch=_campaign_eform_batch(),
        project_root=tmp_path,
    )

    assert first["status"] == "applied"
    assert second["status"] == "already_applied"
    assert {name: path.read_bytes() for name, path in paths.items()} == after_first

def test_preview_lists_ui_approved_canonical_pending_form_as_eligible(
    tmp_path: Path,
) -> None:
    candidates, forms, bindings, _attestations = _state(tmp_path)

    preview = build_form_review_preview(
        candidate_payload=candidates,
        canonical_forms_payload=forms,
        bindings_payload=bindings,
        project_root=tmp_path,
        legal_as_of="2026-07-27",
    )

    assert preview["summary"] == {
        "total_forms": 1,
        "active_catalog_forms": 1,
        "eligible_forms": 1,
        "excluded_forms": 0,
        "already_approved_forms": 0,
        "excluded_no_official_forms": 0,
    }
    assert preview["reason_counts"] == {}
    assert preview["catalog_exclusion_reason_counts"] == {}
    assert preview["catalog_excluded_items"] == []
    assert preview["eligible_items"][0]["candidate_id"] == "candidate-1"
    assert preview["eligible_items"][0]["canonical_form_id"] == "form-birth"
    assert preview["eligible_items"][0]["procedure_id"] == "dang_ky_khai_sinh"
    assert preview["eligible_items"][0]["sha256"] == candidates["records"][0]["sha256"]
    assert preview["preview_fingerprint"]


def test_preview_removes_no_official_form_from_review_queue_but_keeps_audit(
    tmp_path: Path,
) -> None:
    _candidates, forms, bindings, _attestations = _state(tmp_path)
    forms["forms"][0].update(
        {
            "catalog_disposition": "excluded_no_official_form",
            "catalog_status": "excluded_no_official_form",
            "review_queue_eligible": False,
            "runtime_eligible": False,
            "approved": False,
        }
    )
    forms["forms"][0]["candidate_source_evidence"] = [
        {
            "status": "EXCLUDED_NO_OFFICIAL_FORM",
            "reason_code": "NO_OFFICIAL_STATE_FORM_CONFIRMED",
            "legal_as_of": "2026-07-27",
            "official_sources_checked": [
                "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/birth"
            ],
            "auto_approved": False,
        }
    ]

    preview = build_form_review_preview(
        candidate_payload={"records": []},
        canonical_forms_payload=forms,
        bindings_payload=bindings,
        project_root=tmp_path,
        legal_as_of="2026-07-27",
    )

    assert preview["eligible_items"] == []
    assert preview["excluded_items"] == []
    item = preview["catalog_excluded_items"][0]
    assert item["reason_codes"] == ["NO_OFFICIAL_STATE_FORM_CONFIRMED"]
    assert item["source_page_url"] == (
        "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/birth"
    )
    assert preview["reason_counts"] == {}
    assert preview["catalog_exclusion_reason_counts"] == {
        "NO_OFFICIAL_STATE_FORM_CONFIRMED": 1
    }
    assert preview["summary"] == {
        "total_forms": 1,
        "active_catalog_forms": 0,
        "eligible_forms": 0,
        "excluded_forms": 0,
        "already_approved_forms": 0,
        "excluded_no_official_forms": 1,
    }


@pytest.mark.parametrize(
    ("candidate_changes", "form_changes", "expected_reason"),
    [
        ({"priority_path": "missing.pdf"}, {}, "FORM_FILE_MISSING"),
        ({"sha256": "0" * 64}, {}, "CHECKSUM_MISMATCH"),
        ({"source_page_url": "https://example.com/form"}, {}, "OFFICIAL_SOURCE_REQUIRED"),
        ({"effective_from": None}, {}, "EFFECTIVITY_UNKNOWN"),
        ({"legal_basis": []}, {}, "LEGAL_BASIS_MISSING"),
        ({"id": "demo-form", "is_demo": True}, {}, "SEED_DEMO_QUARANTINED"),
        ({"quarantined": True}, {}, "SEED_DEMO_QUARANTINED"),
        ({}, {"procedure_ids": ["dang_ky_ket_hon"]}, "PROCEDURE_MAPPING_MISMATCH"),
    ],
)
def test_preview_excludes_every_failed_hard_gate_with_reason_code(
    tmp_path: Path,
    candidate_changes: dict,
    form_changes: dict,
    expected_reason: str,
) -> None:
    candidates, forms, bindings, _attestations = _state(tmp_path)
    candidates["records"][0].update(candidate_changes)
    forms["forms"][0].update(form_changes)

    preview = build_form_review_preview(
        candidate_payload=candidates,
        canonical_forms_payload=forms,
        bindings_payload=bindings,
        project_root=tmp_path,
        legal_as_of="2026-07-27",
    )

    assert preview["summary"]["eligible_forms"] == 0
    assert preview["summary"]["excluded_forms"] == 1
    assert expected_reason in preview["excluded_items"][0]["reason_codes"]
    assert preview["reason_counts"][expected_reason] == 1


def test_batch_attestation_requires_real_reviewer_identity(tmp_path: Path) -> None:
    candidates, forms, bindings, attestations = _state(tmp_path)
    attestation = _attestation()
    attestation["reviewer_id"] = "automated"

    with pytest.raises(FormReviewSyncError, match="REVIEWER_ID_REQUIRED"):
        build_form_review_sync(
            candidate_payload=candidates,
            canonical_forms_payload=forms,
            bindings_payload=bindings,
            attestations_payload=attestations,
            attestation=attestation,
            project_root=tmp_path,
        )


def test_batch_attestation_rejects_wrong_procedure_mapping(tmp_path: Path) -> None:
    candidates, forms, bindings, attestations = _state(tmp_path)
    attestation = _attestation()
    attestation["items"][0]["procedure_id"] = "dang_ky_ket_hon"

    with pytest.raises(FormReviewSyncError, match="PROCEDURE_MAPPING_MISMATCH"):
        build_form_review_sync(
            candidate_payload=candidates,
            canonical_forms_payload=forms,
            bindings_payload=bindings,
            attestations_payload=attestations,
            attestation=attestation,
            project_root=tmp_path,
        )


def test_batch_attestation_rejects_checksum_drift(tmp_path: Path) -> None:
    candidates, forms, bindings, attestations = _state(tmp_path)
    candidates["records"][0]["sha256"] = "0" * 64

    with pytest.raises(FormReviewSyncError, match="CHECKSUM_MISMATCH"):
        build_form_review_sync(
            candidate_payload=candidates,
            canonical_forms_payload=forms,
            bindings_payload=bindings,
            attestations_payload=attestations,
            attestation=_attestation(),
            project_root=tmp_path,
        )


def test_batch_attestation_is_idempotent(tmp_path: Path) -> None:
    candidates, forms, bindings, attestations = _state(tmp_path)
    first = build_form_review_sync(
        candidate_payload=candidates,
        canonical_forms_payload=forms,
        bindings_payload=bindings,
        attestations_payload=attestations,
        attestation=_attestation(),
        project_root=tmp_path,
    )
    second = build_form_review_sync(
        candidate_payload=first["candidate_payload"],
        canonical_forms_payload=first["canonical_forms_payload"],
        bindings_payload=first["bindings_payload"],
        attestations_payload=first["attestations_payload"],
        attestation=_attestation(),
        project_root=tmp_path,
    )

    assert second["status"] == "already_applied"
    assert len(second["attestations_payload"]["attestations"]) == 1


def test_file_transaction_persists_all_catalogs_together(tmp_path: Path) -> None:
    import json

    candidates, forms, bindings, attestations = _state(tmp_path)
    official_index, checksum_manifest = _review_artifacts()
    paths = {
        "candidates": tmp_path / "candidates.json",
        "canonical_forms": tmp_path / "forms.json",
        "bindings": tmp_path / "bindings.json",
        "official_index": tmp_path / "official-index.json",
        "checksum_manifest": tmp_path / "checksum-manifest.json",
        "attestations": tmp_path / "attestations.json",
    }
    for key, payload in {
        "candidates": candidates,
        "canonical_forms": forms,
        "bindings": bindings,
        "official_index": official_index,
        "checksum_manifest": checksum_manifest,
        "attestations": attestations,
    }.items():
        paths[key].write_text(json.dumps(payload), encoding="utf-8")

    result = apply_form_review_sync_files(
        candidate_path=paths["candidates"],
        canonical_forms_path=paths["canonical_forms"],
        bindings_path=paths["bindings"],
        official_index_path=paths["official_index"],
        checksum_manifest_path=paths["checksum_manifest"],
        attestations_path=paths["attestations"],
        attestation=_attestation(),
        project_root=tmp_path,
    )

    assert result["status"] == "applied"
    stored_form = json.loads(paths["canonical_forms"].read_text(encoding="utf-8"))[
        "forms"
    ][0]
    stored_binding = json.loads(paths["bindings"].read_text(encoding="utf-8"))[
        "bindings"
    ][0]
    stored_audit = json.loads(paths["attestations"].read_text(encoding="utf-8"))[
        "attestations"
    ][0]
    stored_index = json.loads(
        paths["official_index"].read_text(encoding="utf-8")
    )["forms"][0]
    stored_checksum = json.loads(
        paths["checksum_manifest"].read_text(encoding="utf-8")
    )["entries"][0]
    assert stored_form["approved"] is True
    assert stored_binding["approved"] is True
    assert stored_index["is_canonical"] is True
    assert stored_checksum["form_id"] == "form-birth"
    assert stored_audit["automated_approval"] is False


def test_file_transaction_rolls_back_every_artifact_on_mid_sync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from api import form_review_sync

    candidates, forms, bindings, attestations = _state(tmp_path)
    official_index, checksum_manifest = _review_artifacts()
    paths = {
        "candidate_path": tmp_path / "candidates.json",
        "canonical_forms_path": tmp_path / "forms.json",
        "bindings_path": tmp_path / "bindings.json",
        "official_index_path": tmp_path / "official-index.json",
        "checksum_manifest_path": tmp_path / "checksum-manifest.json",
        "attestations_path": tmp_path / "attestations.json",
    }
    payloads = [
        candidates,
        forms,
        bindings,
        official_index,
        checksum_manifest,
        attestations,
    ]
    for path, payload in zip(paths.values(), payloads, strict=True):
        path.write_text(json.dumps(payload), encoding="utf-8")
    before = {name: path.read_bytes() for name, path in paths.items()}

    real_replace = form_review_sync.os.replace
    calls = 0

    def fail_on_third_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("synthetic mid-transaction failure")
        return real_replace(source, destination)

    monkeypatch.setattr(form_review_sync.os, "replace", fail_on_third_replace)

    with pytest.raises(FormReviewSyncError, match="CATALOG_TRANSACTION_FAILED"):
        apply_form_review_sync_files(
            **paths,
            attestation=_attestation(),
            project_root=tmp_path,
        )

    assert {name: path.read_bytes() for name, path in paths.items()} == before


def test_admin_endpoint_records_authenticated_batch_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json

    from starlette.requests import Request

    from api.routers import ward_procedures
    from api.routers.ward_procedures import (
        FormLegalReviewAttestationRequest,
        review_form_legal_attestation,
    )

    candidates, forms, bindings, attestations = _state(tmp_path)
    candidate_path = tmp_path / "candidates.json"
    forms_path = tmp_path / "forms.json"
    bindings_path = tmp_path / "bindings.json"
    official_index_path = tmp_path / "official-index.json"
    checksum_manifest_path = tmp_path / "checksum-manifest.json"
    attestations_path = tmp_path / "attestations.json"
    official_index, checksum_manifest = _review_artifacts()
    for path, payload in [
        (candidate_path, candidates),
        (forms_path, forms),
        (bindings_path, bindings),
        (official_index_path, official_index),
        (checksum_manifest_path, checksum_manifest),
        (attestations_path, attestations),
    ]:
        path.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(ward_procedures, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        ward_procedures, "CLASSIFIED_FORMS_CANDIDATES_PATH", candidate_path
    )
    monkeypatch.setattr(
        ward_procedures, "CANONICAL_FORMS_CATALOG_PATH", forms_path
    )
    monkeypatch.setattr(
        ward_procedures, "CANONICAL_FORM_BINDINGS_PATH", bindings_path
    )
    monkeypatch.setattr(
        ward_procedures, "FORM_REVIEW_ATTESTATIONS_PATH", attestations_path
    )
    monkeypatch.setattr(
        ward_procedures, "OFFICIAL_FORMS_INDEX_PATH", official_index_path
    )
    monkeypatch.setattr(
        ward_procedures, "FORM_CHECKSUM_MANIFEST_PATH", checksum_manifest_path
    )

    raw = _attestation()
    raw.pop("reviewer_id")
    preview = build_form_review_preview(
        candidate_payload=candidates,
        canonical_forms_payload=forms,
        bindings_payload=bindings,
        project_root=tmp_path,
        legal_as_of="2026-07-27",
    )
    raw["legal_as_of"] = preview["legal_as_of"]
    raw["preview_fingerprint"] = preview["preview_fingerprint"]
    request_model = FormLegalReviewAttestationRequest(**raw)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/procedures/forms-catalog/legal-review-attestations",
            "headers": [],
        }
    )
    request.state.user_role = "admin"
    request.state.user_id = "user_account:authenticated-admin"

    response = asyncio.run(
        review_form_legal_attestation(request_model, request)
    )

    assert response["status"] == "applied"
    assert response["reviewer_id"] == "user_account:authenticated-admin"
    stored = json.loads(attestations_path.read_text(encoding="utf-8"))
    assert stored["attestations"][0]["reviewer_id"] == response["reviewer_id"]


def test_admin_endpoint_rejects_tampered_preview_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json

    from fastapi import HTTPException
    from starlette.requests import Request

    from api.routers import ward_procedures
    from api.routers.ward_procedures import (
        FormLegalReviewAttestationRequest,
        review_form_legal_attestation,
    )

    candidates, forms, bindings, attestations = _state(tmp_path)
    paths = {
        "CLASSIFIED_FORMS_CANDIDATES_PATH": tmp_path / "candidates.json",
        "CANONICAL_FORMS_CATALOG_PATH": tmp_path / "forms.json",
        "CANONICAL_FORM_BINDINGS_PATH": tmp_path / "bindings.json",
        "FORM_REVIEW_ATTESTATIONS_PATH": tmp_path / "attestations.json",
        "OFFICIAL_FORMS_INDEX_PATH": tmp_path / "official-index.json",
        "FORM_CHECKSUM_MANIFEST_PATH": tmp_path / "checksum-manifest.json",
    }
    official_index, checksum_manifest = _review_artifacts()
    payloads = [
        candidates,
        forms,
        bindings,
        attestations,
        official_index,
        checksum_manifest,
    ]
    for (attribute, path), payload in zip(paths.items(), payloads, strict=True):
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(ward_procedures, attribute, path)
    monkeypatch.setattr(ward_procedures, "PROJECT_ROOT", tmp_path)

    raw = _attestation()
    raw.pop("reviewer_id")
    raw["legal_as_of"] = "2026-07-27"
    raw["preview_fingerprint"] = "0" * 64
    request_model = FormLegalReviewAttestationRequest(**raw)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/procedures/forms-catalog/legal-review-attestations",
            "headers": [],
        }
    )
    request.state.user_role = "admin"
    request.state.user_id = "user_account:authenticated-admin"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(review_form_legal_attestation(request_model, request))

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "PREVIEW_STALE_OR_TAMPERED"
    assert json.loads(paths["FORM_REVIEW_ATTESTATIONS_PATH"].read_text())[
        "attestations"
    ] == []


def test_admin_endpoint_rejects_missing_authenticated_reviewer() -> None:
    import asyncio

    from fastapi import HTTPException
    from starlette.requests import Request

    from api.routers.ward_procedures import (
        FormLegalReviewAttestationRequest,
        review_form_legal_attestation,
    )

    raw = _attestation()
    raw.pop("reviewer_id")
    request_model = FormLegalReviewAttestationRequest(**raw)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/procedures/forms-catalog/legal-review-attestations",
            "headers": [],
        }
    )
    request.state.user_role = "admin"
    request.state.user_id = None

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(review_form_legal_attestation(request_model, request))

    assert exc_info.value.status_code == 422


def test_candidate_approval_records_reviewer_but_remains_fail_closed() -> None:
    result = apply_candidate_queue_decision(
        {
            "id": "candidate-1",
            "review_status": "candidate_pending_review",
            "legal_review_status": "candidate_pending_review",
        },
        decision="approved",
        reviewer_id="user_account:admin",
        reviewed_at="2026-07-27T12:00:00+07:00",
        review_note="Đã duyệt candidate; chờ canonical attestation.",
    )

    assert result["review_status"] == "approved"
    assert result["reviewed_by"] == "user_account:admin"
    assert result["legal_review_status"] == "catalog_sync_required"
    assert result["is_approved"] is True
    assert result["is_canonical"] is False


def test_runtime_gate_rejects_queue_approval_without_legal_attestation() -> None:
    from api.routers.ward_procedures import _is_form_runtime_approved

    queue_approved = {
        "review_status": "approved",
        "legal_review_status": "catalog_sync_required",
        "is_approved": True,
        "is_canonical": False,
        "catalog_status": "legal_review_required",
    }
    legally_approved = {
        **queue_approved,
        "legal_review_status": "approved",
        "is_canonical": True,
        "catalog_status": "available_official_source",
        "approved": True,
        "runtime_eligible": True,
    }

    assert _is_form_runtime_approved(queue_approved) is False
    assert _is_form_runtime_approved(legally_approved) is True
    assert _is_form_runtime_approved(
        {**legally_approved, "is_quarantined": True}
    ) is False
    assert _is_form_runtime_approved({**legally_approved, "runtime_eligible": False}) is False


def test_candidate_triage_never_publishes_or_creates_download_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json

    from starlette.requests import Request

    from api.routers import ward_procedures
    from api.routers.ward_procedures import (
        FormReviewRequest,
        review_classified_form_candidate,
    )

    candidate_path = tmp_path / "candidates.json"
    index_path = tmp_path / "official-index.json"
    candidate_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "id": "candidate-1",
                        "review_status": "candidate_pending_review",
                        "legal_review_status": "candidate_pending_review",
                        "file_path": "data/uploads/forms/official_candidates/missing.pdf",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    index_path.write_text(json.dumps({"forms": []}), encoding="utf-8")
    before_index = index_path.read_bytes()
    monkeypatch.setattr(
        ward_procedures, "CLASSIFIED_FORMS_CANDIDATES_PATH", candidate_path
    )
    monkeypatch.setattr(ward_procedures, "OFFICIAL_FORMS_INDEX_PATH", index_path)

    request = Request(
        {"type": "http", "method": "POST", "path": "/review", "headers": []}
    )
    request.state.user_role = "admin"
    request.state.user_id = "user_account:admin"

    result = asyncio.run(
        review_classified_form_candidate(
            "candidate-1",
            FormReviewRequest(decision="approved", review_note="Sơ duyệt."),
            request,
        )
    )
    stored = json.loads(candidate_path.read_text(encoding="utf-8"))["records"][0]

    assert result["status"] == "queued_for_legal_attestation"
    assert result["download_url"] is None
    assert stored["legal_review_status"] == "catalog_sync_required"
    assert stored["is_canonical"] is False
    assert stored["runtime_eligible"] is not True
    assert index_path.read_bytes() == before_index


def test_upload_cannot_bypass_canonical_legal_attestation() -> None:
    import asyncio
    from io import BytesIO

    from fastapi import HTTPException
    from starlette.datastructures import Headers, UploadFile
    from starlette.requests import Request

    from api.routers.ward_procedures import upload_official_form

    request = Request(
        {"type": "http", "method": "POST", "path": "/upload", "headers": []}
    )
    request.state.user_role = "admin"
    request.state.user_id = "user_account:admin"
    upload = UploadFile(
        file=BytesIO(b"%PDF-1.7\n" + b"official evidence\n" * 100),
        filename="candidate.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            upload_official_form(
                request,
                upload,
                form_title="Candidate form",
                review_status="approved",
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "LEGAL_ATTESTATION_REQUIRED"


def test_candidate_queue_returns_authoritative_total_and_requested_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json

    from starlette.requests import Request

    from api.routers import ward_procedures
    from api.routers.ward_procedures import list_classified_form_candidates

    candidate_path = tmp_path / "candidates.json"
    candidate_path.write_text(
        json.dumps(
            {
                "records": [
                    {"id": "candidate-1", "review_status": "candidate_pending_review"},
                    {"id": "candidate-2", "review_status": "candidate_pending_review"},
                    {"id": "candidate-3", "review_status": "approved"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ward_procedures, "CLASSIFIED_FORMS_CANDIDATES_PATH", candidate_path
    )
    request = Request(
        {"type": "http", "method": "GET", "path": "/candidates-full", "headers": []}
    )
    request.state.user_role = "admin"
    request.state.user_id = "user_account:admin"

    result = asyncio.run(
        list_classified_form_candidates(
            request,
            domain=None,
            review_status="candidate_pending_review",
            limit=1,
            offset=1,
        )
    )

    assert result["summary"] == {
        "filtered_total": 2,
        "returned": 1,
        "offset": 1,
        "limit": 1,
    }
    assert [item["id"] for item in result["records"]] == ["candidate-2"]
    assert result["records"][0]["download_url"] is None
    assert result["records"][0]["is_public"] is False


def test_preview_accepts_technically_ready_candidate_for_single_human_attestation(
    tmp_path: Path,
) -> None:
    candidates, forms, bindings, _attestations = _state(tmp_path)
    candidate = candidates["records"][0]
    candidate["review_status"] = "candidate_pending_review"
    candidate["is_approved"] = False
    candidate["legal_review_status"] = "candidate_pending_review"
    candidate["preparation_status"] = "ready_for_human_review"
    candidate["technical_validation"] = {
        "status": "passed",
        "validated_at": "2026-07-27T08:00:00+00:00",
        "reason_codes": [],
    }

    preview = build_form_review_preview(
        candidate_payload=candidates,
        canonical_forms_payload=forms,
        bindings_payload=bindings,
        project_root=tmp_path,
        legal_as_of="2026-07-27",
    )

    assert preview["summary"]["eligible_forms"] == 1
    assert preview["eligible_items"][0]["candidate_id"] == candidate["id"]
    assert preview["eligible_items"][0]["reason_codes"] == []


def test_one_click_attestation_creates_new_canonical_form_and_binding(
    tmp_path: Path,
) -> None:
    local_path, digest = _pdf(tmp_path)
    candidate = {
        "id": "three-tier-candidate-1",
        "proposed_canonical_form_id": "form-three-tier-group-1",
        "canonical_form_name": "Mẫu số 03",
        "detected_form_name": "Mẫu số 03",
        "form_code": "03",
        "procedure_id": "1.008950",
        "suggested_procedure_id": "1.008950",
        "procedure_mapping_verified": True,
        "domain": "an_sinh_y_te_giao_duc",
        "administrative_level": "commune",
        "jurisdiction": "Hai Phong",
        "review_status": "candidate_pending_review",
        "legal_review_status": "candidate_pending_review",
        "preparation_status": "ready_for_human_review",
        "technical_validation": {"status": "passed", "reason_codes": []},
        "approved": False,
        "runtime_eligible": False,
        "local_path": local_path,
        "sha256": digest,
        "source_page_url": "https://vbpl.vn/van-ban/chi-tiet/example--1",
        "source_download_url": (
            "https://vbpl-bientap-gateway.moj.gov.vn/api/form.pdf"
        ),
        "legal_basis": ["105/2020/NĐ-CP"],
        "effective_from": "2020-11-01",
        "provenance": {"kind": "official_vbpl_attachment"},
    }
    preview = build_form_review_preview(
        candidate_payload={"records": [candidate]},
        canonical_forms_payload={"forms": []},
        bindings_payload={"bindings": []},
        project_root=tmp_path,
        legal_as_of="2026-07-27",
    )

    assert preview["summary"]["eligible_forms"] == 1
    item = preview["eligible_items"][0]
    assert item["canonical_form_id"] == "form-three-tier-group-1"
    assert item["procedure_id"] == "1.008950"

    result = build_form_review_sync(
        candidate_payload={"records": [candidate]},
        canonical_forms_payload={"forms": []},
        bindings_payload={"bindings": []},
        attestations_payload={"attestations": []},
        official_index_payload={"forms": []},
        checksum_manifest_payload={"entries": []},
        attestation={
            "attestation_id": preview["attestation_id"],
            "reviewer_id": "user_account:legal-reviewer",
            "reviewed_at": "2026-07-27T10:00:00+07:00",
            "decision": "approved",
            "review_note": "Đã đối chiếu nguồn chính thức.",
            "items": [item],
        },
        project_root=tmp_path,
    )

    approved_candidate = result["candidate_payload"]["records"][0]
    form = result["canonical_forms_payload"]["forms"][0]
    binding = result["bindings_payload"]["bindings"][0]
    assert approved_candidate["review_status"] == "approved"
    assert approved_candidate["legal_review_status"] == "approved"
    assert form["form_id"] == "form-three-tier-group-1"
    assert form["approved"] is True
    assert form["procedure_ids"] == ["1.008950"]
    assert binding["approved"] is True
