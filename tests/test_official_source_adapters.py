from __future__ import annotations

from api.official_source_adapters import (
    extract_exact_instrument_links,
    is_allowlisted_official_url,
    load_curated_government_document,
    ordered_fallback_urls,
)


def test_fallback_urls_are_allowlisted_and_exact_first():
    urls = ordered_fallback_urls(
        [
            "https://chinhphu.vn/search?doc=02/2024/TT-BYT",
            "https://example.invalid/02/2024/TT-BYT",
            "http://vbpl.vn/02/2024/TT-BYT",
            "https://vbpl.vn/other",
        ],
        instrument="02/2024/TT-BYT",
    )

    assert urls == [
        "https://chinhphu.vn/search?doc=02/2024/TT-BYT",
        "https://vbpl.vn/other",
    ]
    assert is_allowlisted_official_url("https://chinhphu.vn/x") is True
    assert is_allowlisted_official_url("http://chinhphu.vn/x") is False


def test_fallback_extracts_only_exact_instrument_links():
    html = b"""
    <a href='/files/02/2024/TT-BYT.pdf'>exact</a>
    <a href='/files/02/2023/TT-BYT.pdf'>other</a>
    <a href='https://example.invalid/02/2024/TT-BYT.pdf'>external</a>
    """

    assert extract_exact_instrument_links(
        html,
        page_url="https://chinhphu.vn/search",
        instrument="02/2024/TT-BYT",
    ) == ["https://chinhphu.vn/files/02/2024/TT-BYT.pdf"]


def test_curated_government_document_requires_exact_number_and_date(tmp_path):
    registry = tmp_path / "official-government-document-sources.json"
    registry.write_text(
        """{
          "schema_version": "official-government-document-sources-v1",
          "records": [{
            "instrument": "154/2024/NĐ-CP",
            "document_id": "vanban-chinhphu-211821",
            "title": "Nghị định về cư trú",
            "publisher": "Cổng Thông tin điện tử Chính phủ",
            "source_page_url": "https://vanban.chinhphu.vn/?docid=211821",
            "status_source_url": "https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-154-2024-nd-cp-43275.htm",
            "download_urls": ["https://congbao.cdnchinhphu.vn/154-2024.pdf"],
            "effective_from": "2025-01-10",
            "effective_to": "2027-02-28",
            "effective_status": "Còn hiệu lực",
            "verified_as_of": "2026-07-30",
            "status_verified_as_of": "2026-07-31",
            "explicit_form_page_bindings": [{
              "form_code": "01",
              "appendix_identifier": "I",
              "procedure_ids": ["1.115594"],
              "source_page_url": "https://vanban.chinhphu.vn/?docid=211821",
              "official_download_url": "https://congbao.cdnchinhphu.vn/154-2024.pdf",
              "source_package_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              "source_package_size_bytes": 1024,
              "source_pages_zero_based": [196, 197],
              "verified_as_of": "2026-07-30"
            }]
          }]
        }""",
        encoding="utf-8",
    )

    found = load_curated_government_document(
        "154 / 2024 / NĐ-CP",
        legal_as_of="2026-07-31",
        path=registry,
    )

    assert found is not None
    assert found["document"]["docNum"] == "154/2024/NĐ-CP"
    assert found["direct_download_urls"] == [
        "https://congbao.cdnchinhphu.vn/154-2024.pdf"
    ]
    assert found["document"]["effTo"] == "2027-02-28"
    assert found["explicit_form_page_bindings"] == [
        {
            "form_code": "01",
            "appendix_identifier": "I",
            "procedure_ids": ["1.115594"],
            "source_page_url": "https://vanban.chinhphu.vn/?docid=211821",
            "official_download_url": "https://congbao.cdnchinhphu.vn/154-2024.pdf",
            "source_package_sha256": "a" * 64,
            "source_package_size_bytes": 1024,
            "source_pages_zero_based": [196, 197],
            "verified_as_of": "2026-07-30",
        }
    ]
    assert found["document"]["verifiedAsOf"] == "2026-07-31"
    assert found["document"]["packageVerifiedAsOf"] == "2026-07-30"
    assert (
        load_curated_government_document(
            "154/2024/NĐ-CP",
            legal_as_of="2026-08-01",
            path=registry,
        )
        is None
    )
    assert (
        load_curated_government_document(
            "153/2024/NĐ-CP",
            legal_as_of="2026-07-30",
            path=registry,
        )
        is None
    )


def test_curated_nd142_official_packages_bind_exact_form_groups() -> None:
    found = load_curated_government_document(
        "142/2025/NĐ-CP",
        legal_as_of="2026-07-30",
    )

    assert found is not None
    assert found["document"]["effFrom"] == "2025-07-01"
    assert found["document"]["effTo"] is None
    bindings = {
        (item["form_code"], item["appendix_identifier"]): item
        for item in found["explicit_form_page_bindings"]
    }
    assert set(bindings) == {
        ("02", "II"),
        ("04", "II"),
        ("05", "VII"),
        ("06", "II"),
        ("07", "II"),
        ("08", "II"),
    }
    assert bindings[("02", "II")]["source_pages_zero_based"] == [23, 24, 25]
    assert bindings[("05", "VII")]["source_pages_zero_based"] == list(range(9, 17))
    assert bindings[("08", "II")]["procedure_ids"] == ["1.012971"]
