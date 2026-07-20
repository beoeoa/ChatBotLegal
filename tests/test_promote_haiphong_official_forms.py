from scripts.promote_haiphong_official_forms import canonical_rank, year_score


def test_year_score_prefers_newer_source():
    assert year_score({"source_package_title": "Quyết định năm 2026"}) == 2026
    assert year_score({"source_package_title": "Không rõ năm"}) == 0


def test_canonical_rank_prefers_newer_then_precise_location():
    old = {"source_package_title": "Năm 2025", "source_page": 2}
    new = {"source_package_title": "Năm 2026", "source_page": None}
    assert canonical_rank(new) > canonical_rank(old)
