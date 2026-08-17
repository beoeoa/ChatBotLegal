import pytest

from scripts.prepare_golden294_official_116 import (
    LAW_NUMBER,
    OFFICIAL_URL,
    build_import_payload,
)


def _normalized():
    return {
        "title": "Thông tư 116/2026/TT-BCA",
        "law_number": LAW_NUMBER,
        "document_type": "Thông tư",
        "clean_markdown": "Điều 1. Phạm vi điều chỉnh\n" + "x" * 1200,
    }


def _observation():
    return {
        "law_number": LAW_NUMBER,
        "identity_status": "exact",
        "normalized_status": "active",
        "evidence_status": "sufficient",
        "source_url": OFFICIAL_URL,
        "issuing_agency": "Bộ Công an",
        "issued_date": "2026-06-29",
        "effective_from": "2026-07-01",
        "effective_to": None,
    }


def test_build_payload_binds_exact_official_identity_and_current_status():
    payload = build_import_payload(_normalized(), _observation())
    assert payload["law_number"] == LAW_NUMBER
    assert payload["domain_slug"] == "cu_tru_an_ninh"
    assert payload["confirmed_official_source"] is True


def test_build_payload_rejects_non_current_source():
    observation = _observation()
    observation["normalized_status"] = "expired"
    with pytest.raises(ValueError, match="OFFICIAL_116_NOT_CURRENT"):
        build_import_payload(_normalized(), observation)
