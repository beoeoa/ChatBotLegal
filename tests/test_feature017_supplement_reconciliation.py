from __future__ import annotations

from scripts.reconcile_feature017_supplements import (
    build_owner_deferred_manifest,
    build_supplement_reconciliation,
)


def _queue_record(
    *,
    identity_id: str,
    name: str,
    code: str | None = None,
    attachment: bool = False,
) -> dict:
    return {
        "identity_id": identity_id,
        "canonical_name": name,
        "domains": ["cu_tru"],
        "procedure_ids": ["P1"],
        "current_form_code": code,
        "official_components": [
            {
                "attachments": (
                    [
                        {
                            "attachment_id": "00000000-0000-0000-0000-000000000001",
                            "file_name": "mau.docx",
                            "official_endpoint": "https://dichvucong.gov.vn/api/file",
                        }
                    ]
                    if attachment
                    else []
                )
            }
        ],
    }


def _form(*, form_id: str, name: str, code: str | None = None) -> dict:
    return {
        "form_id": form_id,
        "procedure_ids": ["P1"],
        "form_code": code,
        "canonical_name": name,
        "review_status": "approved",
        "approved": True,
        "runtime_eligible": True,
        "official_source_page": "https://vbpl.vn/source",
        "official_download_url": "https://vbpl.vn/form.pdf",
        "sha256": "a" * 64,
    }


def _run(record: dict, forms: list[dict]) -> dict:
    result = build_supplement_reconciliation(
        {"legal_as_of": "2026-08-11", "records": [record]},
        {"forms": forms},
        {
            "bindings": [
                {
                    "procedure_id": "P1",
                    "form_id": item["form_id"],
                }
                for item in forms
            ]
        },
    )
    assert result["runtime_catalog_mutated"] is False
    assert result["attestation_created"] is False
    assert result["release_created"] is False
    return result["records"][0]


def test_exact_code_reuses_approved_asset() -> None:
    record = _queue_record(
        identity_id="identity-ct01",
        name="Lưu ý không đính kèm biểu mẫu CT01",
        code=None,
    )
    outcome = _run(
        record,
        [_form(form_id="form-ct01", name="Tờ khai cư trú", code="CT01")],
    )
    assert outcome["review_proposal"] == "REUSE_APPROVED_ASSET"
    assert outcome["paper_asset_auto_satisfied"] is True
    assert outcome["reusable_assets"][0]["binding_present"] is True


def test_eform_does_not_replace_offline_form() -> None:
    record = _queue_record(
        identity_id="identity-paper",
        name="Tờ khai đăng ký khai sinh nếu nộp trực tiếp",
    )
    outcome = _run(
        record,
        [
            _form(
                form_id="form-eform",
                name="Mẫu hộ tịch điện tử tương tác đăng ký khai sinh",
            )
        ],
    )
    assert outcome["review_proposal"] == "ONLINE_ALTERNATIVE_EXISTS"
    assert outcome["paper_asset_auto_satisfied"] is False
    assert outcome["eform_treated_as_paper_replacement"] is False


def test_attachment_has_priority_over_online_alternative() -> None:
    record = _queue_record(
        identity_id="identity-attachment",
        name="Tờ khai giấy",
        attachment=True,
    )
    outcome = _run(
        record,
        [
            _form(
                form_id="form-eform",
                name="Mẫu điện tử tương tác cho thủ tục",
            )
        ],
    )
    assert outcome["review_proposal"] == "OFFICIAL_ATTACHMENT_ENRICHMENT"
    assert len(outcome["official_attachments"]) == 1


def test_legacy_household_book_language_is_not_auto_released() -> None:
    record = _queue_record(
        identity_id="identity-legacy",
        name="Phiếu báo thay đổi hộ khẩu, nhân khẩu; Sổ hộ khẩu",
    )
    outcome = _run(record, [])
    assert outcome["review_proposal"] == "OBSOLETE_OR_SUPERSEDED_REVIEW"
    assert outcome["automated_verified_gap"] is False
    assert outcome["automated_release"] is False


def test_unknown_source_remains_fail_closed() -> None:
    record = _queue_record(
        identity_id="identity-missing",
        name="Đơn đề nghị chưa rõ căn cứ",
    )
    outcome = _run(record, [])
    assert outcome["review_proposal"] == "PACKAGE_SOURCE_REQUIRED"
    assert outcome["automated_source_approval"] is False


def test_owner_deferred_manifest_is_not_a_verified_gap() -> None:
    queue = {
        "legal_as_of": "2026-08-11",
        "records": [
            _queue_record(
                identity_id="identity-deferred",
                name="Biểu mẫu chưa đủ nguồn",
            )
        ],
    }
    result = build_owner_deferred_manifest(queue, decision_note="Bỏ qua đợt này")

    assert result["record_count"] == 1
    assert result["public_eligible_count"] == 0
    record = result["records"][0]
    assert record["scope_status"] == "OWNER_DEFERRED_MISSING_EVIDENCE"
    assert record["public_eligible"] is False
    assert record["router_eligible"] is False
    assert record["verified_gap"] is False
    assert result["runtime_catalog_mutated"] is False
