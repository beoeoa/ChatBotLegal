import asyncio
import json
from pathlib import Path

from api.routers.ward_procedures import (
    _get_approved_form_ids,
    _resolve_form_source_path,
    list_priority_forms,
    search_official_forms,
)


ROOT = Path(__file__).resolve().parents[1]


def test_marriage_query_does_not_match_unrelated_partial_words():
    rows = search_official_forms("biểu mẫu kết hôn", limit=20)
    assert all(
        "kết hôn"
        in f"{row.get('form_title', '')} {row.get('source_package_title', '')}".casefold()
        for row in rows
    )


def test_land_registration_query_is_fail_closed_without_legal_attestation():
    rows = search_official_forms(
        "đơn đăng ký biến động đất đai",
        limit=10,
    )
    assert rows == []


def test_common_queries_expose_only_legally_attested_forms():
    queries = (
        "biểu mẫu kết hôn",
        "mẫu đăng ký giám hộ",
        "mẫu CT01 cư trú",
        "khiếu nại",
    )

    approved_ids = _get_approved_form_ids()
    for query in queries:
        rows = search_official_forms(query, limit=10)
        assert all(str(row.get("id") or "") in approved_ids for row in rows)


def test_priority_api_does_not_fall_back_to_legacy_forms_without_canonical_binding():
    payload = asyncio.run(
        list_priority_forms(
            query="tờ khai đăng ký kết hôn",
            domain="ho_tich_chung_thuc",
            tier=None,
            limit=10,
        )
    )

    assert payload["records"] == []


def test_catalog_paths_remain_portable_between_windows_and_linux():
    catalog = json.loads(
        (ROOT / "notebook_data/forms/priority_official_forms.json").read_text(
            encoding="utf-8"
        )
    )
    relative = catalog["forms"][0]["source_package_path"]

    assert "\\" not in relative
    assert "/" in relative
    assert _resolve_form_source_path(relative).is_file()
