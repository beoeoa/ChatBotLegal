from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from api.form_resolution_registry import (
    ManifestIntegrityError,
    build_occurrence_registry,
    build_privacy_safe_campaign_summary,
    canonical_identity_key,
    extract_appendix_identifier,
    read_campaign_manifest,
    resolve_occurrence_identity,
    validate_review_ready_candidate,
    write_campaign_manifest,
)


def _occurrence(**overrides):
    value = {
        "candidate_id": "occurrence-1",
        "procedure_id": "1.001193",
        "procedure_code": "1.001193",
        "procedure_name": "Đăng ký khai sinh",
        "form_name": (
            "Tờ khai đăng ký khai sinh theo Mẫu số 01, Phụ lục I "
            "ban hành kèm theo Thông tư số 04/2020/TT-BTP"
        ),
        "form_code": "Mẫu số 01",
        "issuing_instruments": ["04/2020/TT-BTP"],
        "domain": "ho_tich_chung_thuc",
        "source_tier": "central",
        "executing_level": "commune",
        "component_kind": "applicant_form",
    }
    value.update(overrides)
    return value


def test_appendix_identity_and_jurisdiction_are_part_of_canonical_key():
    first = resolve_occurrence_identity(_occurrence())
    second = resolve_occurrence_identity(
        _occurrence(
            candidate_id="occurrence-2",
            source_tier="hai_phong_override",
        )
    )

    assert extract_appendix_identifier(_occurrence()["form_name"]) == "I"
    assert first["identity_status"] == "IDENTITY_RESOLVED"
    assert first["canonical_identity_key"] != second["canonical_identity_key"]
    assert first["jurisdiction"] == "central"
    assert second["jurisdiction"] == "hai_phong"


def test_same_generic_code_in_different_instruments_never_merges():
    first = resolve_occurrence_identity(_occurrence())
    second = resolve_occurrence_identity(
        _occurrence(
            candidate_id="occurrence-2",
            form_name=(
                "Tờ khai theo Mẫu số 01 ban hành kèm theo "
                "Thông tư số 53/2025/TT-BCA"
            ),
            issuing_instruments=["53/2025/TT-BCA"],
        )
    )

    assert first["canonical_identity_key"] != second["canonical_identity_key"]


def test_title_appendix_and_exact_instrument_resolve_without_form_code():
    decision = resolve_occurrence_identity(
        _occurrence(
            form_name=(
                "Đơn đề nghị hỗ trợ chi phí học tập theo mẫu tại "
                "Phụ lục II Nghị định số 238/2025/NĐ-CP"
            ),
            form_code=None,
            issuing_instruments=["238/2025/NĐ-CP"],
        )
    )

    assert decision["identity_status"] == "IDENTITY_RESOLVED"
    assert decision["resolved_form_code"] is None
    assert decision["appendix_identifier"] == "II"
    assert decision["issuing_instrument"] == "238/2025/NĐ-CP"


@pytest.mark.parametrize(
    ("name", "component_kind", "expected_status"),
    [
        (
            "Bản sao Giấy chứng nhận đã cấp",
            "supporting_document",
            "EXCLUDED_SUPPORTING_DOCUMENT",
        ),
        (
            "Giấy phép xây dựng được cấp",
            "official_result",
            "EXCLUDED_ISSUED_RESULT",
        ),
    ],
)
def test_explicit_non_form_components_are_terminally_excluded(
    name: str,
    component_kind: str,
    expected_status: str,
):
    decision = resolve_occurrence_identity(
        _occurrence(form_name=name, component_kind=component_kind)
    )

    assert decision["identity_status"] == expected_status
    assert decision["reason_code"] == expected_status
    assert decision["canonical_identity_key"] is None


def test_mojibake_identity_is_rejected_instead_of_normalized_into_a_form():
    decision = resolve_occurrence_identity(
        _occurrence(form_name="Máº«u sá»‘ 01 ban hÃ nh kÃ¨m theo 04/2020/TT-BTP")
    )

    assert decision["identity_status"] == "FORM_IDENTITY_UNRESOLVED"
    assert decision["reason_code"] == "INVALID_UNICODE_METADATA"


def test_single_pass_utf8_mojibake_is_rejected():
    decision = resolve_occurrence_identity(
        _occurrence(
            form_name=(
                "Gi\u00e1\u00ba\u00a5y mau so 01 "
                "ban hanh kem theo 04/2020/TT-BTP"
            )
        )
    )

    assert decision["identity_status"] == "FORM_IDENTITY_UNRESOLVED"
    assert decision["reason_code"] == "INVALID_UNICODE_METADATA"


def test_canonically_equivalent_decomposed_vietnamese_is_normalized_not_rejected():
    nfc_name = "Mẫu số 01 ban hành kèm theo Thông tư 04/2020/TT-BTP"
    nfd_name = unicodedata.normalize("NFD", nfc_name)

    nfc = resolve_occurrence_identity(_occurrence(form_name=nfc_name))
    nfd = resolve_occurrence_identity(_occurrence(form_name=nfd_name))

    assert nfd["reason_code"] == nfc["reason_code"]
    assert nfd["canonical_identity_key"] == nfc["canonical_identity_key"]
    assert nfd["reason_code"] != "INVALID_UNICODE_METADATA"


def test_registry_assigns_every_occurrence_a_terminal_state_and_deduplicates():
    occurrences = [
        _occurrence(candidate_id="a"),
        _occurrence(candidate_id="b"),
        _occurrence(
            candidate_id="support",
            form_name="Bản sao Giấy chứng nhận đã cấp",
            component_kind="supporting_document",
        ),
        _occurrence(
            candidate_id="unknown",
            form_name="Đơn đề nghị",
            form_code=None,
            issuing_instruments=[],
        ),
    ]

    registry = build_occurrence_registry(
        occurrences=occurrences,
        run_id="run-1",
        legal_as_of="2026-07-27",
    )

    assert registry["summary"]["occurrence_count"] == 4
    assert registry["summary"]["terminal_occurrence_count"] == 4
    assert registry["summary"]["canonical_identity_count"] == 1
    assert registry["summary"]["procedure_binding_count"] == 1
    assert registry["summary"]["unreasoned_occurrence_count"] == 0
    assert len(registry["occurrences"]) == 4
    assert len(registry["identity_groups"][0]["occurrence_ids"]) == 2


def test_manifest_checksum_detects_tampering(tmp_path: Path):
    path = tmp_path / "registry.json"
    payload = build_occurrence_registry(
        occurrences=[_occurrence()],
        run_id="run-1",
        legal_as_of="2026-07-27",
    )
    written = write_campaign_manifest(path, payload)

    assert written["payload_sha256"]
    assert read_campaign_manifest(path)["run_id"] == "run-1"

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["summary"]["occurrence_count"] = 999
    path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(ManifestIntegrityError, match="MANIFEST_CHECKSUM_MISMATCH"):
        read_campaign_manifest(path)


def test_privacy_safe_summary_does_not_copy_titles_urls_or_paths():
    registry = build_occurrence_registry(
        occurrences=[_occurrence()],
        run_id="run-1",
        legal_as_of="2026-07-27",
    )
    summary = build_privacy_safe_campaign_summary(
        registry=registry,
        source_attempts=[
            {
                "source_domain": "vbpl.vn",
                "requested_url": "https://vbpl.vn/private-detail",
                "status": "FOUND",
            }
        ],
    )
    serialized = json.dumps(summary, ensure_ascii=False)

    assert "Đăng ký khai sinh" not in serialized
    assert "private-detail" not in serialized
    assert summary["contains_question_text"] is False
    assert summary["contains_answer_text"] is False
    assert summary["contains_credentials"] is False


def test_review_ready_candidate_requires_original_and_extracted_provenance():
    candidate = {
        "canonical_identity_key": canonical_identity_key(
            form_code="01",
            issuing_instrument="04/2020/TT-BTP",
            appendix_identifier="I",
            jurisdiction="central",
            normalized_title="to khai dang ky khai sinh",
        ),
        "procedure_ids": ["1.001193"],
        "form_code": "01",
        "canonical_name": "Tờ khai đăng ký khai sinh",
        "issuing_instrument": "04/2020/TT-BTP",
        "official_source_page": "https://vbpl.vn/van-ban/example",
        "official_download_url": "https://vbpl.vn/files/package.pdf",
        "source_sha256": "a" * 64,
        "local_path": "data/forms/form-01.pdf",
        "sha256": "b" * 64,
        "extraction": {
            "kind": "extracted_from_official_package",
            "page_range": [3, 5],
            "complete": True,
        },
        "effective_from": "2020-07-16",
        "effective_to": None,
        "jurisdiction": "central",
        "scope": "commune",
        "provenance": {
            "publisher": "Bộ Tư pháp",
            "retrieved_at": "2026-07-27T00:00:00+00:00",
        },
        "review_status": "candidate_pending_review",
        "is_seed": False,
        "is_demo": False,
        "is_quarantined": False,
    }

    assert validate_review_ready_candidate(candidate)["eligible"] is True

    candidate["extraction"]["complete"] = False
    decision = validate_review_ready_candidate(candidate)
    assert decision["eligible"] is False
    assert "PARTIAL_EXTRACTION_BLOCKED" in decision["reason_codes"]

    candidate["extraction"]["complete"] = True
    candidate["canonical_name"] = "To\u031b\u0300 khai co\u0302ng chu\u031b\u0301ng"
    decision = validate_review_ready_candidate(candidate)
    assert decision["eligible"] is False
    assert "INVALID_UNICODE_METADATA" in decision["reason_codes"]


def test_review_ready_dvc_docx_requires_structural_boundary_evidence():
    candidate = {
        "canonical_identity_key": "dvc-form-07",
        "procedure_ids": ["1.013870"],
        "canonical_name": "Mẫu số 07",
        "issuing_instrument": "91/2016/NĐ-CP",
        "official_source_page": (
            "https://dichvucong.gov.vn/quyet-dinh-cong-bo/"
            "019eb146-cf41-72c8-9f55-1d3df27dd74a"
        ),
        "official_download_url": (
            "https://dichvucong.gov.vn/quyet-dinh-cong-bo/"
            "019eb146-cf41-72c8-9f55-1d3df27dd74a"
        ),
        "source_sha256": "a" * 64,
        "local_path": "data/forms/form-07.docx",
        "sha256": "b" * 64,
        "effective_from": "2016-07-01",
        "jurisdiction": "central",
        "scope": "province",
        "provenance": {
            "kind": "official_dvc_attachment",
            "publisher": "Cổng Dịch vụ công quốc gia",
            "retrieved_at": "2026-07-29T00:00:00+00:00",
        },
        "extraction": {
            "kind": "structural_docx_form_boundary",
            "complete": True,
            "element_range": [12, 31],
        },
        "review_status": "candidate_pending_review",
    }

    assert validate_review_ready_candidate(candidate)["eligible"] is True

    candidate.pop("extraction")
    decision = validate_review_ready_candidate(candidate)
    assert decision["eligible"] is False
    assert "EXTRACTION_BOUNDARY_REQUIRED" in decision["reason_codes"]
