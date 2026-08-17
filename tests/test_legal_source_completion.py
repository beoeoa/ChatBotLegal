from __future__ import annotations

from datetime import date

import pytest

from api.legal_source_completion import (
    build_legal_source_gap_job,
    build_requirements,
    classify_requirement,
    extract_legal_basis,
    normalize_law_number,
)
from scripts.reconcile_legal_source_requirements import select_corpus_database_url


def test_extracts_exact_legal_basis_from_official_dict_and_serialized_row():
    assert extract_legal_basis(
        {"code": " 31/2024/QH15 ", "name": "Luật Đất đai"},
        legal_as_of=date(2026, 7, 27),
    ) == ("31/2024/QH15", "Luật Đất đai", None)
    assert extract_legal_basis(
        "@{code=123/2015/NĐ-CP; name=Nghị định hướng dẫn Luật Hộ tịch}",
        legal_as_of=date(2026, 7, 27),
    ) == (
        "123/2015/NĐ-CP",
        "Nghị định hướng dẫn Luật Hộ tịch",
        None,
    )


def test_normalization_does_not_turn_a_title_or_future_identity_into_a_source():
    assert normalize_law_number("Luật Đất đai 2024") is None
    assert extract_legal_basis(
        "@{code=40/2029/QH14; name=Luật chưa tồn tại tại legal-as-of}",
        legal_as_of=date(2026, 7, 27),
    ) == ("40/2029/QH14", "Luật chưa tồn tại tại legal-as-of", "FUTURE_INSTRUMENT_IDENTITY")
    assert extract_legal_basis(
        "@{code=16/2022/N?-CP; name=Unicode lỗi}",
        legal_as_of=date(2026, 7, 27),
    )[2] == "INVALID_OFFICIAL_IDENTITY"


def test_requirements_deduplicate_law_number_and_keep_all_procedure_origins():
    procedures = [
        {
            "procedure_id": "p1",
            "status": "OFFICIAL_PROCEDURE_MATCHED",
            "legal_basis": [
                {"code": "31/2024/QH15", "name": "Luật Đất đai"},
            ],
        },
        {
            "procedure_id": "p2",
            "status": "OFFICIAL_PROCEDURE_MATCHED",
            "legal_basis": [
                "@{code=31/2024/QH15 ; name=Luật số 31/2024/QH15}",
            ],
        },
    ]

    requirements = build_requirements(
        procedures=procedures,
        expected_source_rows=[],
        legal_as_of="2026-07-27",
    )

    assert len(requirements) == 1
    assert requirements[0]["law_number"] == "31/2024/QH15"
    assert requirements[0]["origin_ids"] == ["procedure:p1", "procedure:p2"]
    assert "question" not in requirements[0]


def test_wrong_local_jurisdiction_basis_is_not_turned_into_a_crawl_requirement():
    requirements = build_requirements(
        procedures=[
            {
                "procedure_id": "cap_giay_phep_xay_dung_hai_phong",
                "status": "OFFICIAL_PROCEDURE_MATCHED",
                "jurisdiction": "Hải Phòng",
                "legal_basis": [
                    {
                        "code": "57/2016/NQ-HĐND",
                        "name": "Quy định lệ phí cấp giấy phép xây dựng tại thành phố Đà Nẵng",
                    },
                    {
                        "code": "52/2026/QĐ-UBND",
                        "name": "Quy định thủ tục đất đai trên địa bàn thành phố Hải Phòng",
                    },
                ],
            }
        ],
        expected_source_rows=[],
        legal_as_of="2026-07-27",
    )

    assert [item["law_number"] for item in requirements] == ["52/2026/QĐ-UBND"]


def test_requirement_classification_is_exact_and_fail_closed():
    requirement = {
        "requirement_id": "opaque",
        "law_number": "31/2024/QH15",
        "legal_as_of": "2026-07-27",
    }
    complete = {
        "id": 10,
        "law_number": "31/2024/QH15",
        "title": "Luật Đất đai",
        "status": "active",
        "effective_date": "2024-08-01",
        "expired_date": None,
        "source_url": "https://vbpl.vn/example",
        "scope": "Toàn quốc",
        "sector": "Đất đai",
        "valid_chunk_count": 5,
    }

    found = classify_requirement(requirement, [complete])
    assert found["classification"] == "FOUND_AND_INDEXED"
    assert found["document_id"] == 10

    missing_metadata = classify_requirement(
        requirement,
        [{**complete, "scope": None}],
    )
    assert missing_metadata["classification"] == "METADATA_INCOMPLETE"
    assert missing_metadata["reason_code"] == "DOCUMENT_SCOPE_REQUIRED"

    expired = classify_requirement(
        requirement,
        [{**complete, "status": "expired", "expired_date": "2025-01-01"}],
    )
    assert expired["classification"] == "EXPIRED_OR_SUPERSEDED"

    ambiguous = classify_requirement(requirement, [complete, {**complete, "id": 11}])
    assert ambiguous["classification"] == "AMBIGUOUS_CORPUS_MATCH"

    absent = classify_requirement(requirement, [])
    assert absent["classification"] == "MISSING_LEGAL_SOURCE"


def test_source_gap_job_requires_exact_official_discovery_and_stays_pending():
    requirement = {
        "requirement_id": "req-opaque",
        "law_number": "16/2022/NĐ-CP",
        "official_name": "Nghị định về xử phạt vi phạm hành chính về xây dựng",
        "legal_as_of": "2026-07-27",
        "origin_ids": ["procedure:cap_giay_phep_xay_dung"],
    }
    discovery = {
        "status": "found",
        "reason_code": "EXACT_OFFICIAL_DOCUMENT_FOUND",
        "document": {
            "id": "official-document-id",
            "title": requirement["official_name"],
            "soKyHieu": "16/2022/NĐ-CP",
            "detailUrl": "https://vbpl.vn/van-ban/chi-tiet/example",
            "ngayBanHanh": "2022-01-28",
            "ngayCoHieuLuc": "2022-01-28",
            "coQuanBanHanh": "Chính phủ",
            "loaiVanBan": "Nghị định",
        },
        "files": [{"fileName": "16-2022.pdf"}],
    }

    job = build_legal_source_gap_job(requirement, discovery)

    assert job["gap_type"] == "MISSING_LEGAL_SOURCE"
    assert job["status"] == "queued"
    assert job["expected_code"] == "16/2022/NĐ-CP"
    assert job["official_metadata"]["confirmed_official_source"] is True
    assert job["official_metadata"]["effective_date"] == "2022-01-28"
    assert job["approved"] is False


@pytest.mark.parametrize(
    "discovery",
    [
        {"status": "verified_gap", "reason_code": "OFFICIAL_DOCUMENT_IDENTITY_NOT_FOUND"},
        {"status": "found", "reason_code": "EXACT_OFFICIAL_DOCUMENT_FOUND", "document": {}, "files": []},
    ],
)
def test_source_gap_job_rejects_unverified_or_fileless_discovery(discovery):
    requirement = {
        "requirement_id": "req-opaque",
        "law_number": "16/2022/NĐ-CP",
        "official_name": "Nghị định",
        "legal_as_of": "2026-07-27",
        "origin_ids": [],
    }
    with pytest.raises(ValueError):
        build_legal_source_gap_job(requirement, discovery)


def test_corpus_audit_prefers_explicit_runtime_url_over_isolated_test_database():
    selected = select_corpus_database_url(
        environment={
            "LEGAL_DATABASE_URL": "postgresql://test:test@127.0.0.1/test_isolated",
            "LEGAL_CORPUS_DATABASE_URL": "postgresql://runtime:secret@127.0.0.1/legal_chatbot",
        },
        settings={},
    )
    assert selected.endswith("/legal_chatbot")


def test_corpus_audit_uses_release_url_when_no_explicit_runtime_url():
    selected = select_corpus_database_url(
        environment={
            "LEGAL_DATABASE_URL": "postgresql://test:test@127.0.0.1/test_isolated",
        },
        settings={
            "LEGAL_RELEASE_DATABASE_URL": "postgresql+psycopg2://runtime:secret@host.docker.internal:5432/legal_chatbot"
        },
    )
    assert "127.0.0.1:5432/legal_chatbot" in selected
    assert "test_isolated" not in selected
