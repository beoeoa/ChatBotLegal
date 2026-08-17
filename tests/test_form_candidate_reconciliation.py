from __future__ import annotations

import hashlib
from pathlib import Path

from api.form_candidate_reconciliation import reconcile_form_candidates
from api.legal_form_catalog import _is_official_url


def _write_pdf(root: Path, name: str = "form.pdf") -> tuple[str, str]:
    path = root / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.7\n" + name.encode("utf-8") + b"x" * 512)
    return str(path.relative_to(root)).replace("\\", "/"), hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def _form(
    *,
    form_id: str = "form-1",
    procedure_id: str = "dang_ky_khai_sinh",
    name: str = "Tờ khai đăng ký khai sinh",
    code: str | None = None,
) -> dict:
    return {
        "form_id": form_id,
        "procedure_ids": [procedure_id],
        "form_code": code,
        "canonical_name": name,
        "domain": "ho_tich_chung_thuc",
        "review_status": "candidate_pending_review",
        "approved": False,
        "legal_review_status": "not_reviewed",
        "candidate_source_evidence": [],
    }


def _candidate(
    root: Path,
    *,
    candidate_id: str = "candidate-1",
    procedure_id: str = "dang_ky_khai_sinh",
    name: str = "Tờ khai đăng ký khai sinh",
    code: str | None = None,
    official_code: str = "1.001193",
) -> dict:
    local_path, digest = _write_pdf(root, f"{candidate_id}.pdf")
    return {
        "id": candidate_id,
        "procedure_id": procedure_id,
        "official_procedure_code": official_code,
        "canonical_form_name": name,
        "form_code": code,
        "domain": "ho_tich_chung_thuc",
        "review_status": "approved",
        "is_approved": True,
        "legal_review_status": "candidate_pending_review",
        "preparation_status": "ready_for_human_review",
        "source_page_url": "https://sotp.haiphong.gov.vn/procedure",
        "source_download_url": "https://cdn.haiphong.gov.vn/form.pdf",
        "local_path": local_path,
        "sha256": digest,
        "legal_basis": ["1833/QĐ-BTP"],
    }


def _effectivity_rule(**overrides: object) -> dict:
    rule = {
        "rule_id": "civil-1833",
        "official_procedure_codes": ["1.001193"],
        "legal_basis_additions": ["1833/QĐ-BTP"],
        "effective_from": "2025-07-01",
        "official_source_url": "https://sotp.haiphong.gov.vn/procedure",
        "source_status": "official_current",
    }
    rule.update(overrides)
    return rule


def _run(
    tmp_path: Path,
    *,
    forms: list[dict],
    candidates: list[dict],
    rules: list[dict] | None = None,
    findings: list[dict] | None = None,
) -> dict:
    return reconcile_form_candidates(
        canonical_forms_payload={"forms": forms},
        candidate_payload={"records": candidates},
        procedure_sources_payload={
            "procedures": [
                {
                    "procedure_id": "dang_ky_khai_sinh",
                    "official_procedure_code": "1.001193",
                    "official_procedure_url": (
                        "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/birth"
                    ),
                    "status": "OFFICIAL_PROCEDURE_MATCHED",
                }
            ]
        },
        effectivity_evidence_payload={"rules": rules or []},
        form_source_findings_payload={"findings": findings or []},
        project_root=tmp_path,
        legal_as_of="2026-07-27",
    )


def test_exact_procedure_and_normalized_name_create_explicit_mapping(
    tmp_path: Path,
) -> None:
    candidate = _candidate(
        tmp_path,
        name="Mẫu số 01 - Tờ khai đăng ký khai sinh",
    )
    result = _run(
        tmp_path,
        forms=[_form()],
        candidates=[candidate],
        rules=[_effectivity_rule()],
    )

    mapped = result["canonical_forms_payload"]["forms"][0][
        "candidate_source_evidence"
    ][0]
    enriched = result["candidate_payload"]["records"][0]
    assert mapped["candidate_id"] == "candidate-1"
    assert mapped["status"] == "MAPPED_OFFICIAL_CANDIDATE"
    assert mapped["reason_code"] == "EXACT_PROCEDURE_AND_FORM_IDENTITY"
    assert enriched["effective_from"] == "2025-07-01"
    assert enriched["effectivity_provenance"]["rule_id"] == "civil-1833"
    assert result["report"]["summary"]["mapped_forms"] == 1


def test_reconciliation_uses_exact_dvc_form_effectivity_binding(
    tmp_path: Path,
) -> None:
    procedure_id = "1.013870"
    procedure_url = (
        "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/"
        "019d2bff-2d2f-73b6-a226-b9bad3aca884"
    )
    decision_url = (
        "https://dichvucong.gov.vn/quyet-dinh-cong-bo/"
        "019eb146-cf41-72c8-9f55-1d3df27dd74a"
    )
    form = _form(procedure_id=procedure_id, code="07")
    candidate = _candidate(
        tmp_path,
        candidate_id="candidate-dvc-07",
        procedure_id=procedure_id,
        code="07",
        official_code=procedure_id,
    )
    candidate.update(
        {
            "source_page_url": decision_url,
            "source_download_url": decision_url,
            "legal_basis": ["91/2016/NĐ-CP"],
            "effective_from": "2016-07-01",
            "provenance_kind": "official_dvc_attachment",
            "source_attachment_id": "019eb4af-a3af-7143-b664-6dd27b38b9de",
            "source_package_sha256": "b" * 64,
            "provenance": {
                "kind": "official_dvc_attachment",
                "source_attachment_id": "019eb4af-a3af-7143-b664-6dd27b38b9de",
                "source_package_sha256": "b" * 64,
            },
        }
    )
    dvc_rule = {
        "evidence_id": "nd-91-2016-appendix-i-form-07-dvc-current",
        "evidence_basis": "official_current_dvc_attachment",
        "issuing_instrument": "91/2016/NĐ-CP",
        "target_form_code": "07",
        "effective_from": "2026-07-01",
        "verified_as_of": "2026-07-29",
        "official_source_url": decision_url,
        "publication_decision_number": "1684/QĐ-BYT",
        "procedure_bindings": [
            {
                "procedure_id": procedure_id,
                "official_source_url": procedure_url,
            }
        ],
        "canonical_artifact_attachment": {
            "attachment_id": "019eb4af-a3af-7143-b664-6dd27b38b9de",
            "sha256": "b" * 64,
            "source_page_url": decision_url,
        },
    }

    result = reconcile_form_candidates(
        canonical_forms_payload={"forms": [form]},
        candidate_payload={"records": [candidate]},
        procedure_sources_payload={
            "procedures": [
                {
                    "procedure_id": procedure_id,
                    "official_procedure_code": procedure_id,
                    "official_procedure_url": procedure_url,
                    "status": "OFFICIAL_PROCEDURE_MATCHED",
                }
            ]
        },
        effectivity_evidence_payload={"rules": [], "form_effectivity": [dvc_rule]},
        form_source_findings_payload={"findings": []},
        project_root=tmp_path,
        legal_as_of="2026-07-29",
    )

    enriched = result["candidate_payload"]["records"][0]
    assert enriched["effective_from"] == "2016-07-01"
    assert enriched["effectivity_provenance"]["rule_id"] == dvc_rule["evidence_id"]
    assert (
        enriched["effectivity_provenance"]["source_attachment_id"]
        == "019eb4af-a3af-7143-b664-6dd27b38b9de"
    )
    assert result["report"]["summary"]["effectivity_enriched_candidates"] == 1


def test_exact_form_code_is_preferred_over_a_name_only_candidate(
    tmp_path: Path,
) -> None:
    preferred = _candidate(
        tmp_path,
        candidate_id="candidate-code",
        name="Tờ khai thay đổi thông tin cư trú",
        code="CT01",
    )
    other = _candidate(
        tmp_path,
        candidate_id="candidate-name",
        name="Tờ khai thay đổi thông tin cư trú",
        code="CT02",
    )
    form = _form(
        name="Tờ khai thay đổi thông tin cư trú (CT01)",
        code="CT01",
    )

    result = _run(
        tmp_path,
        forms=[form],
        candidates=[other, preferred],
    )

    evidence = result["canonical_forms_payload"]["forms"][0][
        "candidate_source_evidence"
    ][0]
    assert evidence["candidate_id"] == "candidate-code"
    assert evidence["reason_code"] == "EXACT_PROCEDURE_AND_FORM_CODE"


def test_identical_duplicate_candidates_are_deduplicated_deterministically(
    tmp_path: Path,
) -> None:
    first = _candidate(tmp_path, candidate_id="candidate-a")
    second = dict(first)
    second["id"] = "candidate-b"
    second["local_path"] = first["local_path"]

    result = _run(
        tmp_path,
        forms=[_form()],
        candidates=[second, first],
    )

    evidence = result["canonical_forms_payload"]["forms"][0][
        "candidate_source_evidence"
    ][0]
    assert evidence["candidate_id"] == "candidate-a"
    assert evidence["duplicate_candidate_ids"] == [
        "candidate-a",
        "candidate-b",
    ]


def test_conflicting_exact_candidates_fail_closed_as_verified_gap(
    tmp_path: Path,
) -> None:
    first = _candidate(tmp_path, candidate_id="candidate-a")
    second = _candidate(tmp_path, candidate_id="candidate-b")

    result = _run(
        tmp_path,
        forms=[_form()],
        candidates=[first, second],
    )

    evidence = result["canonical_forms_payload"]["forms"][0][
        "candidate_source_evidence"
    ][0]
    assert evidence["status"] == "VERIFIED_DATA_GAP"
    assert evidence["reason_code"] == "AMBIGUOUS_EXACT_CANDIDATES"
    assert result["report"]["summary"]["verified_data_gaps"] == 1


def test_seed_demo_quarantined_candidate_is_never_mapped(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path)
    candidate["is_demo"] = True

    result = _run(
        tmp_path,
        forms=[_form()],
        candidates=[candidate],
    )

    evidence = result["canonical_forms_payload"]["forms"][0][
        "candidate_source_evidence"
    ][0]
    assert evidence["status"] == "VERIFIED_DATA_GAP"
    assert evidence["reason_code"] == "NO_EXACT_OFFICIAL_CANDIDATE"


def test_form_not_listed_by_official_procedure_is_excluded_from_catalog(
    tmp_path: Path,
) -> None:
    form = _form()
    result = _run(
        tmp_path,
        forms=[form],
        candidates=[],
        findings=[
            {
                "form_id": form["form_id"],
                "status": "NO_PUBLIC_DOWNLOAD_VERIFIED",
                "reason_code": "FORM_NOT_LISTED_IN_OFFICIAL_PROCEDURE",
                "source_page": (
                    "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/birth"
                ),
            }
        ],
    )

    evidence = result["canonical_forms_payload"]["forms"][0][
        "candidate_source_evidence"
    ][0]
    assert evidence["status"] == "EXCLUDED_NO_OFFICIAL_FORM"
    assert (
        evidence["reason_code"]
        == "NO_OFFICIAL_STATE_FORM_CONFIRMED"
    )
    assert evidence["official_sources_checked"] == [
        "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/birth"
    ]
    reconciled_form = result["canonical_forms_payload"]["forms"][0]
    assert reconciled_form["catalog_disposition"] == "excluded_no_official_form"
    assert reconciled_form["review_queue_eligible"] is False
    assert reconciled_form["runtime_eligible"] is False
    assert reconciled_form["approved"] is False
    assert result["report"]["summary"]["excluded_no_official_forms"] == 1


def test_unmatched_procedure_is_not_mislabeled_as_verified_data_gap(
    tmp_path: Path,
) -> None:
    form = _form()
    result = _run(
        tmp_path,
        forms=[form],
        candidates=[],
        findings=[
            {
                "form_id": form["form_id"],
                "status": "NEEDS_SOURCE_MAPPING",
                "reason_code": "NO_OFFICIAL_FORM_CANDIDATE",
            }
        ],
    )

    evidence = result["canonical_forms_payload"]["forms"][0][
        "candidate_source_evidence"
    ][0]
    assert evidence["status"] == "SOURCE_MAPPING_UNRESOLVED"
    assert (
        evidence["reason_code"]
        == "NO_UNAMBIGUOUS_OFFICIAL_SOURCE_MATCH"
    )
    assert result["report"]["summary"]["unresolved_source_mappings"] == 1
    assert result["report"]["summary"]["verified_data_gaps"] == 0


def test_official_package_without_file_remains_retryable_after_one_check(
    tmp_path: Path,
) -> None:
    form = _form()
    result = _run(
        tmp_path,
        forms=[form],
        candidates=[],
        findings=[
            {
                "form_id": form["form_id"],
                "status": "OFFICIAL_PACKAGE_PAGE",
                "source_page": (
                    "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/birth"
                ),
                "source_gap_status": "failed_retryable",
                "source_gap_reason_code": "OFFICIAL_PAGE_HAS_NO_FILE_LINK",
                "source_gap_checked_dates": ["2026-07-27"],
            }
        ],
    )

    evidence = result["canonical_forms_payload"]["forms"][0][
        "candidate_source_evidence"
    ][0]
    assert evidence["status"] == "SOURCE_DOWNLOAD_RETRY_REQUIRED"
    assert evidence["reason_code"] == "OFFICIAL_PACKAGE_FILE_RETRY_REQUIRED"
    assert evidence["source_gap_checked_dates"] == ["2026-07-27"]
    assert result["report"]["summary"]["retryable_source_downloads"] == 1
    assert result["report"]["summary"]["verified_data_gaps"] == 0


def test_effectivity_is_not_added_without_matching_official_evidence(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path)
    result = _run(
        tmp_path,
        forms=[_form()],
        candidates=[candidate],
        rules=[
            _effectivity_rule(
                official_procedure_codes=["9.999999"],
                effective_from="2020-01-01",
            )
        ],
    )

    enriched = result["candidate_payload"]["records"][0]
    assert enriched.get("effective_from") is None
    assert result["report"]["summary"]["effectivity_enriched_candidates"] == 0


def test_reconciliation_never_changes_queue_or_legal_approval(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path)
    result = _run(
        tmp_path,
        forms=[_form()],
        candidates=[candidate],
        rules=[_effectivity_rule()],
    )

    enriched = result["candidate_payload"]["records"][0]
    canonical = result["canonical_forms_payload"]["forms"][0]
    assert enriched["review_status"] == "approved"
    assert enriched["legal_review_status"] == "candidate_pending_review"
    assert canonical["approved"] is False
    assert canonical["review_status"] == "candidate_pending_review"
    assert result["report"]["summary"]["auto_approved"] == 0


def test_reconciliation_is_idempotent(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path)
    first = _run(
        tmp_path,
        forms=[_form()],
        candidates=[candidate],
        rules=[_effectivity_rule()],
    )
    second = reconcile_form_candidates(
        canonical_forms_payload=first["canonical_forms_payload"],
        candidate_payload=first["candidate_payload"],
        procedure_sources_payload={
            "procedures": [
                {
                    "procedure_id": "dang_ky_khai_sinh",
                    "official_procedure_code": "1.001193",
                    "status": "OFFICIAL_PROCEDURE_MATCHED",
                }
            ]
        },
        effectivity_evidence_payload={"rules": [_effectivity_rule()]},
        project_root=tmp_path,
        legal_as_of="2026-07-27",
    )

    assert second["canonical_forms_payload"] == first["canonical_forms_payload"]
    assert second["candidate_payload"] == first["candidate_payload"]


def test_ministry_of_public_security_official_hosts_are_allowed() -> None:
    assert _is_official_url(
        "https://vanban.bocongan.gov.vn/co-so-du-lieu-van-ban/53-2025"
    )
    assert _is_official_url(
        "https://bocongan.gov.vn/media/bca-media/mau-ct01.doc"
    )
