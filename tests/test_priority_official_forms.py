import json
from pathlib import Path

from scripts.build_priority_200_forms import OUTPUT_PATH, build_priority_catalog
from scripts.ingest_priority_official_forms import OUTPUT_PATH as SUPPLEMENT_PATH


ROOT = Path(__file__).resolve().parents[1]


def test_priority_catalog_meets_minimum_and_excludes_known_out_of_scope_sources():
    payload = build_priority_catalog()

    assert payload["summary"]["target_met"] is True
    assert payload["summary"]["selected_count"] >= 200
    combined = " ".join(
        f"{row.get('form_title', '')} {row.get('source_package_title', '')}"
        for row in payload["forms"]
    ).casefold()
    assert "chuyển giao công nghệ" not in combined
    assert "di sản văn hóa" not in combined


def test_priority_supplement_files_are_present_and_traceable():
    payload = json.loads(SUPPLEMENT_PATH.read_text(encoding="utf-8"))

    assert payload["summary"]["total_available"] >= 16
    assert payload["summary"]["errors"] == 0
    for record in payload["forms"]:
        local_path = ROOT / record["source_package_path"]
        assert local_path.is_file()
        assert record["source_page_url"].startswith("https://")
        assert record["source_sha256"]


def test_priority_catalog_contains_all_five_system_domains():
    payload = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    domains = set(payload["summary"]["domain_counts"])

    assert domains == {
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "cu_tru_an_ninh",
        "khieu_nai_to_cao_xu_phat",
        "an_sinh_y_te_giao_duc",
    }
