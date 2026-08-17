from scripts.build_grounded_retrieval_dataset import (
    _select_authoritative_domain,
    build_dataset,
    rehydrate_authoritative_domains,
)


def _row(document_id: int, article: str, domain: str = "ho_tich_chung_thuc"):
    return {
        "document_id": document_id,
        "law_number": "60/2014/QH13",
        "document_title": "Luật hộ tịch",
        "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=1",
        "effective_date": "2016-01-01",
        "expired_date": None,
        "article_number": article,
        "article_title": f"Điều {article}",
        "domain": domain,
    }


def test_grounded_dataset_uses_unique_article_identity_and_exact_contract():
    cases, expected = build_dataset(
        [_row(1, "1"), _row(1, "1"), _row(1, "2")],
        "2026-07-28",
        2,
    )
    assert len(cases) == 2
    assert len({case["case_id"] for case in cases}) == 2
    assert [item["document_id"] for item in expected] == [1, 1, 1, 1]
    assert [item["provision"] for item in expected] == [None, "1", None, "2"]
    assert all(item["outcome"] == "AVAILABLE_CORRECTLY_TIERED" for item in expected)


def test_grounded_dataset_does_not_create_rows_without_limit_or_source_contract():
    cases, expected = build_dataset([_row(9, "3")], "2026-07-28", 1)
    assert len(cases) == 1
    assert len(expected) == 2
    assert cases[0]["expected_sources"][0]["document_id"] == 9
    assert cases[0]["expected_sources"][0]["law_number"] == "60/2014/QH13"


def test_stale_vector_domain_is_replaced_by_authoritative_sql_scope():
    metadata = {"document_id": 119956, "domain_slug": "cu_tru_an_ninh"}
    assert (
        _select_authoritative_domain(
            metadata,
            {119956: "dat_dai_xay_dung"},
        )
        == "dat_dai_xay_dung"
    )
    assert _select_authoritative_domain(metadata, {}) == ""


def test_snapshot_rehydration_rewrites_domain_and_preserves_contract():
    cases, expected = rehydrate_authoritative_domains(
        [
            {
                "case_id": "case-1",
                "question": "Theo 175/2024/NĐ-CP, Điều 1 quy định gì?",
                "domain": "cu_tru_an_ninh",
                "expected_sources": [
                    {
                        "outcome": "AVAILABLE_CORRECTLY_TIERED",
                        "document_id": 119956,
                        "law_number": "175/2024/NĐ-CP",
                        "provision": "1",
                    }
                ],
            }
        ],
        {119956: "dat_dai_xay_dung"},
        1,
    )
    assert cases[0]["domain"] == "dat_dai_xay_dung"
    assert expected[0]["case_id"] == "case-1"
    assert expected[0]["document_id"] == 119956


def test_dataset_selection_is_stratified_across_domains():
    rows = [
        _row(1, "1", "cu_tru_an_ninh"),
        _row(1, "2", "cu_tru_an_ninh"),
        _row(1, "3", "cu_tru_an_ninh"),
        _row(2, "1", "ho_tich_chung_thuc"),
        _row(2, "2", "ho_tich_chung_thuc"),
        _row(2, "3", "ho_tich_chung_thuc"),
    ]
    cases, _ = build_dataset(rows, "2026-07-28", 4)
    assert [case["domain"] for case in cases] == [
        "cu_tru_an_ninh",
        "ho_tich_chung_thuc",
        "cu_tru_an_ninh",
        "ho_tich_chung_thuc",
    ]
