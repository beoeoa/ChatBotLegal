import asyncio
from pathlib import Path

from api.routers.ward_procedures import (
    _resolve_form_source_path,
    download_official_form_source,
    list_priority_forms,
    search_official_forms,
)


def test_marriage_query_does_not_match_unrelated_partial_words():
    rows = search_official_forms("biểu mẫu kết hôn", limit=20)
    assert all(
        "kết hôn" in (
            f"{row.get('form_title', '')} {row.get('source_package_title', '')}"
        ).casefold()
        for row in rows
    )


def test_land_registration_query_finds_official_form():
    rows = search_official_forms("đơn đăng ký biến động đất đai", limit=10)
    assert any("biến động đất đai" in row["form_title"].casefold() for row in rows)


def test_common_queries_prefer_curated_exact_forms():
    cases = {
        "biểu mẫu kết hôn": "tờ khai đăng ký kết hôn",
        "mẫu đăng ký giám hộ": "đăng ký giam ho",
        "mẫu CT01 cư trú": "ct01",
        "khieu nai": "khieu nai",
    }

    for query, expected in cases.items():
        rows = search_official_forms(query, limit=10)
        assert rows, query
        # Curated records may describe a source package instead of a standalone
        # page slice; it must still match the requested legal intent.
        combined = f"{rows[0]['form_title']} {rows[0].get('source_package_title', '')}".casefold()
        assert expected in combined


def test_priority_api_lists_and_downloads_curated_form():
    payload = asyncio.run(
        list_priority_forms(
            query="tờ khai đăng ký kết hôn",
            domain="ho_tich_chung_thuc",
            tier=None,
            limit=10,
        )
    )

    assert payload["summary"]["target_met"] is True
    assert payload["records"]
    response = asyncio.run(
        download_official_form_source(str(payload["records"][0]["id"]))
    )
    assert Path(response.path).is_file()
    # Accept whichever approved file type the first matching record provides.
    assert Path(response.path).suffix.lower() in {".pdf", ".docx", ".doc"}


def test_catalog_paths_remain_portable_between_windows_and_linux():
    rows = search_official_forms("tờ khai đăng ký kết hôn", limit=1)

    assert rows
    assert "\\" not in rows[0]["source_package_path"]
    assert "/" in rows[0]["source_package_path"]
    assert _resolve_form_source_path(rows[0]["source_package_path"]).is_file()
