from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from api.form_source_resolution import (
    extract_strict_form_codes,
    resolve_single_issuing_instrument,
)
from scripts.build_form_requirement_manifest import _identity_key, _title_signature
from scripts.collect_feature017_approved_sources import (
    build_collection_plan,
    build_instrument_package_review_queue,
    build_supplement_queue,
    collect_supplement_attachment_evidence,
    execute_collection_plan,
    refresh_official_snapshot,
)


def _decision(
    *,
    identity_id: str,
    decision: str = "source_approved",
    source_status: str = "CONFIRMED_CODE_AND_INSTRUMENT",
) -> dict:
    return {
        "identity_id": identity_id,
        "domains": ["an_sinh_y_te_giao_duc"],
        "form_code": "01" if "EFORM" not in source_status else None,
        "issuing_instrument": "24/2021/QĐ-TTg" if "EFORM" not in source_status else None,
        "canonical_name": "Mẫu kiểm thử",
        "procedure_ids": ["1.000001"],
        "official_source_url": "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/procedure-1",
        "source_status": source_status,
        "decision": decision,
        "supplement_reason": "Thiếu căn cứ" if decision != "source_approved" else None,
    }


def _snapshot(component: dict) -> dict:
    return {
        "legal_as_of": "2026-08-11",
        "source": "https://dichvucong.gov.vn/api/v1/submitting/formality/list-all-public-formality-by-citizen",
        "catalog": [{"id": "procedure-1", "code": "1.000001"}],
        "details": {
            "procedure-1": {
                "legalBasisesDetails": [{"code": "Quyết định số 24/2021/QĐ-TTg"}],
                "executionCases": [{"profileComponents": [component]}],
                "profileComponents": [],
            }
        },
    }


def _paper_identity(name: str) -> str:
    instrument = resolve_single_issuing_instrument(
        {
            "form_name": name,
            "issuing_instruments": ["Quyết định số 24/2021/QĐ-TTg"],
        }
    )
    return _identity_key(
        domain="an_sinh_y_te_giao_duc",
        code=(extract_strict_form_codes(name) or [None])[0],
        instrument=instrument,
        title_signature=_title_signature(name),
        eform=False,
    )


def test_source_collection_plan_keeps_approval_separate_from_attestation() -> None:
    name = "Giấy đề nghị theo Mẫu số 01 tại Phụ lục ban hành kèm theo Quyết định số 24/2021/QĐ-TTg"
    identity_id = _paper_identity(name)
    component = {
        "profileComponentId": "component-1",
        "name": name,
        "attachments": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "fileName": "mau-01.pdf",
            }
        ],
    }
    plan = build_collection_plan(
        {"records": [_decision(identity_id=identity_id)]},
        _snapshot(component),
    )

    assert plan["automated_approval"] is False
    assert plan["attestation_created"] is False
    assert plan["release_created"] is False
    assert plan["records"][0]["status"] == "READY_ATTACHMENT_FETCH"


def test_source_collection_preserves_official_requirement_semantics() -> None:
    name = "Giấy đề nghị theo Mẫu số 01 tại Phụ lục ban hành kèm theo Quyết định số 24/2021/QĐ-TTg"
    identity_id = _paper_identity(name)
    component = {
        "profileComponentId": "component-required",
        "name": name,
        "required": True,
        "originalQty": 1,
        "copyQty": 0,
        "attachments": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "fileName": "mau-01.pdf",
            }
        ],
    }
    plan = build_collection_plan(
        {"records": [_decision(identity_id=identity_id)]},
        _snapshot(component),
    )

    matched = plan["records"][0]["components"][0]
    assert matched["required"] is True
    assert matched["original_quantity"] == 1
    assert matched["copy_quantity"] == 0


def test_source_collection_downloads_valid_candidate_without_releasing(tmp_path: Path) -> None:
    name = "Giấy đề nghị theo Mẫu số 01 tại Phụ lục ban hành kèm theo Quyết định số 24/2021/QĐ-TTg"
    identity_id = _paper_identity(name)
    component = {
        "profileComponentId": "component-1",
        "name": name,
        "attachments": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "fileName": "mau-01.pdf",
            }
        ],
    }
    plan = build_collection_plan(
        {"records": [_decision(identity_id=identity_id)]},
        _snapshot(component),
    )
    content = b"%PDF-1.4\n" + (b"0" * 300)

    result = execute_collection_plan(
        plan,
        output_dir=tmp_path,
        network=True,
        fetcher=lambda attachment_id, file_name, referer: (
            content,
            "application/pdf",
        ),
    )

    record = result["records"][0]
    assert record["status"] == "READY_FOR_LEGAL_ENRICHMENT"
    assert record["source_checksum"] == hashlib.sha256(content).hexdigest()
    assert record["canonical_artifact"]["asset_kind"] == "file"
    assert result["runtime_catalog_mutated"] is False
    assert result["attestation_created"] is False
    assert result["release_created"] is False


def test_source_collection_eform_uses_metadata_checksum() -> None:
    name = "Mẫu hộ tịch điện tử tương tác đăng ký khai sinh"
    identity_id = _identity_key(
        domain="an_sinh_y_te_giao_duc",
        code=None,
        instrument=None,
        title_signature=_title_signature(name),
        eform=True,
    )
    component = {
        "profileComponentId": "component-eform",
        "name": name,
        "hasElectronicForm": True,
        "attachments": [],
    }
    plan = build_collection_plan(
        {
            "records": [
                _decision(
                    identity_id=identity_id,
                    source_status="OFFICIAL_EFORM_IDENTITY",
                )
            ]
        },
        _snapshot(component),
    )
    result = execute_collection_plan(
        plan,
        output_dir=Path("unused"),
        network=False,
    )

    record = result["records"][0]
    assert record["asset_kind"] == "eform"
    assert record["status"] == "READY_FOR_LEGAL_ENRICHMENT"
    assert record["source_checksum"] == record["metadata_snapshot_sha256"]
    assert record["canonical_artifact"]["asset_kind"] == "eform"


def test_needs_supplement_never_becomes_a_candidate() -> None:
    name = "Giấy đề nghị theo Mẫu số 01 tại Phụ lục ban hành kèm theo Quyết định số 24/2021/QĐ-TTg"
    identity_id = _paper_identity(name)
    plan = build_collection_plan(
        {
            "records": [
                _decision(identity_id=identity_id, decision="needs_supplement")
            ]
        },
        _snapshot({"profileComponentId": "component-1", "name": name}),
    )
    result = execute_collection_plan(
        plan,
        output_dir=Path("unused"),
        network=False,
    )

    assert result["records"][0]["status"] == "NEEDS_SUPPLEMENT"
    assert result["records"][0]["source_checksum"] is None


def test_supplement_queue_surfaces_evidence_without_inferring_truth() -> None:
    snapshot = _snapshot({"profileComponentId": "component-1", "name": "x"})
    plan = {
        "records": [
            {
                "identity_id": "identity-1",
                "status": "NEEDS_SUPPLEMENT",
                "canonical_name": "Máº«u kiá»ƒm thá»­",
                "domains": ["an_sinh_y_te_giao_duc"],
                "procedure_ids": ["1.000001"],
                "form_code": None,
                "issuing_instrument": None,
                "supplement_reason": "Thiáº¿u cÄƒn cá»©",
                "components": [
                    {
                        "procedure_id": "1.000001",
                        "component_id": "component-1",
                        "component_name": "Máº«u kiá»ƒm thá»­",
                        "form_code": None,
                        "issuing_instrument": None,
                        "is_interactive_eform": False,
                        "attachments": [
                            {
                                "attachment_id": (
                                    "11111111-1111-1111-1111-111111111111"
                                ),
                                "file_name": "mau-01.pdf",
                            }
                        ],
                    }
                ],
            }
        ]
    }

    queue = build_supplement_queue(plan, snapshot)

    assert queue["record_count"] == 1
    record = queue["records"][0]
    assert record["workflow_status"] == "needs_supplement"
    assert record["evidence_summary"]["official_attachment_count"] == 1
    assert record["automated_identity_selection"] is False
    assert record["automated_source_approval"] is False
    assert record["attestation_created"] is False
    assert record["release_created"] is False


def test_supplement_attachment_is_quarantined_without_approval(
    tmp_path: Path,
) -> None:
    content = b"%PDF-1.4\n" + (b"0" * 300)
    queue = {
        "legal_as_of": "2026-08-11",
        "records": [
            {
                "identity_id": "identity-supplement",
                "canonical_name": "Mẫu cần bổ sung",
                "procedure_ids": ["1.000001"],
                "official_procedures": [
                    {
                        "procedure_id": "1.000001",
                        "source_page_url": (
                            "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/procedure-1"
                        ),
                    }
                ],
                "official_components": [
                    {
                        "procedure_id": "1.000001",
                        "attachments": [
                            {
                                "attachment_id": (
                                    "11111111-1111-1111-1111-111111111111"
                                ),
                                "file_name": "mau.pdf",
                            }
                        ],
                    }
                ],
            }
        ],
    }

    result = collect_supplement_attachment_evidence(
        queue,
        output_dir=tmp_path,
        network=True,
        fetcher=lambda attachment_id, file_name, referer: (
            content,
            "application/pdf",
        ),
    )

    record = result["records"][0]
    assert record["status"] == "EVIDENCE_COLLECTED"
    assert record["artifacts"][0]["sha256"] == hashlib.sha256(content).hexdigest()
    assert record["identity_confirmed"] is False
    assert record["source_approved"] is False
    assert result["runtime_catalog_mutated"] is False
    assert result["attestation_created"] is False
    assert result["release_created"] is False


def test_package_review_queue_never_auto_confirms_expired_gap() -> None:
    queue = build_instrument_package_review_queue(
        {
            "legal_as_of": "2026-08-11",
            "records": [
                {
                    "identity_id": "identity-expired",
                    "status": "OFFICIAL_INSTRUMENT_PACKAGE_REQUIRED",
                    "form_code": "02",
                    "issuing_instrument": "175/2024/NĐ-CP",
                    "package_resolution_reason": "ISSUING_INSTRUMENT_EXPIRED",
                    "package_effectivity": {
                        "eligible": False,
                        "effective_to": "2026-07-01",
                    },
                }
            ],
        }
    )

    assert queue["record_count"] == 1
    record = queue["records"][0]
    assert record["suggested_review_outcome"] == (
        "replacement_or_verified_gap_review"
    )
    assert record["automated_verified_gap"] is False
    assert record["automated_attestation"] is False
    assert record["automated_release"] is False


def test_multi_form_component_selects_only_exact_filename_code(tmp_path: Path) -> None:
    name = "Các biểu mẫu: Mẫu số 01 và Mẫu số 02 tại Phụ lục ban hành kèm theo Quyết định số 24/2021/QĐ-TTg"
    instrument = resolve_single_issuing_instrument(
        {
            "form_name": name,
            "issuing_instruments": ["Quyết định số 24/2021/QĐ-TTg"],
        }
    )
    identity_id = _identity_key(
        domain="an_sinh_y_te_giao_duc",
        code="02",
        instrument=instrument,
        title_signature=_title_signature(name),
        eform=False,
    )
    component = {
        "profileComponentId": "component-many",
        "name": name,
        "attachments": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "fileName": "Mus01.pdf",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "fileName": "Mus02.pdf",
            },
        ],
    }
    plan = build_collection_plan(
        {"records": [_decision(identity_id=identity_id)]},
        _snapshot(component),
    )
    fetched: list[str] = []

    def fake_fetch(attachment_id: str, file_name: str, referer: str):
        fetched.append(attachment_id)
        return b"%PDF-1.4\n" + (b"0" * 300), "application/pdf"

    result = execute_collection_plan(
        plan,
        output_dir=tmp_path,
        network=True,
        fetcher=fake_fetch,
    )

    assert fetched == ["22222222-2222-2222-2222-222222222222"]
    assert result["records"][0]["status"] == "READY_FOR_LEGAL_ENRICHMENT"


def test_single_explicitly_wrong_form_code_attachment_is_rejected() -> None:
    name = "Đề án theo Mẫu số 02 tại Phụ lục ban hành kèm theo Quyết định số 24/2021/QĐ-TTg"
    instrument = resolve_single_issuing_instrument(
        {
            "form_name": name,
            "issuing_instruments": ["Quyết định số 24/2021/QĐ-TTg"],
        }
    )
    identity_id = _identity_key(
        domain="an_sinh_y_te_giao_duc",
        code="02",
        instrument=instrument,
        title_signature=_title_signature(name),
        eform=False,
    )
    component = {
        "profileComponentId": "component-wrong-file",
        "name": name,
        "attachments": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "fileName": "Mus01.pdf",
            }
        ],
    }
    plan = build_collection_plan(
        {"records": [_decision(identity_id=identity_id)]},
        _snapshot(component),
    )

    assert plan["records"][0]["status"] == "OFFICIAL_INSTRUMENT_PACKAGE_REQUIRED"
    assert plan["records"][0]["components"][0]["attachments"] == []


def test_filename_code_parser_accepts_mau_so_with_leading_zero(tmp_path: Path) -> None:
    name = "Đơn theo Mẫu số 05 tại Phụ lục ban hành kèm theo Quyết định số 24/2021/QĐ-TTg"
    instrument = resolve_single_issuing_instrument(
        {
            "form_name": name,
            "issuing_instruments": ["Quyết định số 24/2021/QĐ-TTg"],
        }
    )
    identity_id = _identity_key(
        domain="an_sinh_y_te_giao_duc",
        code="05",
        instrument=instrument,
        title_signature=_title_signature(name),
        eform=False,
    )
    component = {
        "profileComponentId": "component-code-05",
        "name": name,
        "attachments": [
            {
                "id": "55555555-5555-5555-5555-555555555555",
                "fileName": "Mauso05_ND24.docx",
            }
        ],
    }
    plan = build_collection_plan(
        {"records": [_decision(identity_id=identity_id)]},
        _snapshot(component),
    )

    assert plan["records"][0]["status"] == "READY_ATTACHMENT_FETCH"


def test_official_snapshot_refresh_is_scoped_and_fail_closed() -> None:
    decisions = {
        "records": [
            {
                "identity_id": "identity-1",
                "procedure_ids": ["1.000001", "1.000002"],
            }
        ]
    }
    base = {
        "catalog": [
            {"id": "formality-1", "code": "1.000001"},
            {"id": "formality-2", "code": "1.000002"},
            {"id": "outside", "code": "9.999999"},
        ]
    }

    def fake_fetch(formality_id: str, referer: str) -> dict:
        if formality_id == "formality-2":
            raise ValueError("BLOCKED_EXTERNAL_DETAIL_FETCH")
        return {"id": formality_id, "profileComponents": []}

    refreshed = refresh_official_snapshot(
        decisions,
        base,
        legal_as_of="2026-08-11",
        detail_fetcher=fake_fetch,
    )

    assert refreshed["procedure_count"] == 2
    assert refreshed["detail_success_count"] == 1
    assert refreshed["detail_error_count"] == 1
    assert refreshed["complete"] is False
    assert {item["code"] for item in refreshed["catalog"]} == {
        "1.000001",
        "1.000002",
    }


def _docx_bytes(text: str, variant: str) -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>",
        )
        archive.writestr(
            "word/document.xml",
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
        archive.writestr(f"custom/{variant}.txt", variant)
    return stream.getvalue()


def test_binary_docx_variants_require_identical_semantic_text(tmp_path: Path) -> None:
    name = "Đơn theo Mẫu số 05 tại Phụ lục ban hành kèm theo Quyết định số 24/2021/QĐ-TTg"
    instrument = resolve_single_issuing_instrument(
        {
            "form_name": name,
            "issuing_instruments": ["Quyết định số 24/2021/QĐ-TTg"],
        }
    )
    identity_id = _identity_key(
        domain="an_sinh_y_te_giao_duc",
        code="05",
        instrument=instrument,
        title_signature=_title_signature(name),
        eform=False,
    )
    component = {
        "profileComponentId": "component-variants",
        "name": name,
        "attachments": [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "fileName": "Mus05.docx",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "fileName": "Mus05.docx",
            },
        ],
    }
    plan = build_collection_plan(
        {"records": [_decision(identity_id=identity_id)]},
        _snapshot(component),
    )
    payloads = {
        "11111111-1111-1111-1111-111111111111": _docx_bytes(
            "Nội dung biểu mẫu giống nhau", "a"
        ),
        "22222222-2222-2222-2222-222222222222": _docx_bytes(
            "Nội dung biểu mẫu giống nhau", "b"
        ),
    }
    result = execute_collection_plan(
        plan,
        output_dir=tmp_path,
        network=True,
        fetcher=lambda attachment_id, file_name, referer: (
            payloads[attachment_id],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    )

    record = result["records"][0]
    assert len({item["sha256"] for item in record["artifacts"]}) == 2
    assert record["status"] == "READY_FOR_LEGAL_ENRICHMENT"
    assert record["semantic_equivalence_sha256"]
    assert len(record["canonical_artifact"]["binary_variant_sha256"]) == 2
