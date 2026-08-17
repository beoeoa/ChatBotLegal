from __future__ import annotations

from scripts.recover_retrieval_official_urls_v2 import (
    match_sitemap_candidates,
    normalize_law_key,
    parse_sitemap_locations,
)


def test_normalize_law_key_matches_slash_and_dash_forms() -> None:
    assert normalize_law_key("01/2020/NĐ-CP") == normalize_law_key("01-2020-ND-CP")
    assert normalize_law_key("12-HĐBT") == normalize_law_key("12 HDBT")


def test_sitemap_locations_are_deduplicated_and_xml_only() -> None:
    xml = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-01-2020-nd-cp--abc</loc></url>
      <url><loc>https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-01-2020-nd-cp--abc</loc></url>
    </urlset>
    """
    assert parse_sitemap_locations(xml) == [
        "https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-01-2020-nd-cp--abc"
    ]


def test_match_requires_law_number_and_scores_title() -> None:
    urls = [
        "https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-01-2020-nd-cp-ve-dang-ky--abc",
        "https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-02-2020-nd-cp-ve-dang-ky--def",
    ]
    matches = match_sitemap_candidates(
        urls,
        law_number="01/2020/NĐ-CP",
        title="Nghị định về đăng ký",
    )

    assert matches[0]["url"] == urls[0]
    assert matches[0]["law_number_match"] is True
    assert matches[0]["score"] > matches[1]["score"]
