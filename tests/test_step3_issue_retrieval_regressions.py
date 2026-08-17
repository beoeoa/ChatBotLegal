from __future__ import annotations

from datetime import date
import inspect
from unittest.mock import MagicMock

from api.legal_exact_retrieval import (
    ExactLookupPlan,
    filter_exact_candidates,
    plan_exact_lookup,
    retrieve_primary_then_support,
)
from api.legal_retrieval_quality import (
    diversify_ranked_candidates,
    reciprocal_rank_fusion,
)
from scripts import benchmark_db5_shadow
from scripts.legal_search_server import (
    LegalRetriever,
    _exact_article_chunk_limit,
    _needs_lexical_retrieval,
    _reserve_explicit_exact_results,
    _rewrite_query,
)


def _active_candidate(chunk_id: int, **overrides):
    row = {
        "chunk_id": chunk_id,
        "law_number": "60/2014/QH13",
        "article_number": "13",
        "domain_slug": "ho_tich_chung_thuc",
        "document_status": "active",
        "article_status": "active",
        "effective_date": date(2016, 1, 1),
        "expired_date": None,
        "source_jurisdiction": "domestic",
    }
    row.update(overrides)
    return row


def test_step3_exact_law_number_is_normalized_before_semantic_retrieval():
    plan = plan_exact_lookup("Áp dụng Nghị định 101 / 2024 / NĐ-CP")

    assert plan.law_number == "101/2024/ND-CP"
    assert plan.requires_exact_metadata_lookup is True


def test_step3_exact_article_and_clause_are_preserved():
    plan = plan_exact_lookup("Khoản 2 Điều 35 Luật số 60/2014/QH13")

    assert plan.article_number == "35"
    assert plan.clause_number == "2"


def test_step3_procedure_id_and_form_code_are_deterministic_metadata():
    plan = plan_exact_lookup(
        "procedure_id:sang_ten_so_do, tải Mẫu 09/ĐK"
    )

    assert plan.procedure_id == "sang_ten_so_do"
    assert plan.form_codes == ("09/DK",)


def test_step3_wrong_field_is_hard_rejected():
    plan = ExactLookupPlan(
        law_number="60/2014/QH13",
        article_number="13",
        clause_number=None,
        procedure_id=None,
        form_codes=(),
    )
    accepted, rejected = filter_exact_candidates(
        [
            _active_candidate(1),
            _active_candidate(2, domain_slug="dat_dai_xay_dung"),
        ],
        plan=plan,
        domain="ho_tich_chung_thuc",
        as_of=date(2026, 7, 26),
    )

    assert [row["chunk_id"] for row in accepted] == [1]
    assert rejected == [{"chunk_id": 2, "reason": "wrong_field"}]


def test_step3_expired_and_superseded_documents_are_hard_rejected():
    plan = plan_exact_lookup("Điều 13 Luật 60/2014/QH13")
    accepted, rejected = filter_exact_candidates(
        [
            _active_candidate(1),
            _active_candidate(
                2,
                document_status="expired",
                expired_date=date(2020, 1, 1),
            ),
            _active_candidate(3, is_superseded=True),
        ],
        plan=plan,
        domain="ho_tich_chung_thuc",
        as_of=date(2026, 7, 26),
    )

    assert [row["chunk_id"] for row in accepted] == [1]
    assert {item["reason"] for item in rejected} == {
        "expired_or_not_yet_effective",
        "superseded",
    }


def test_step3_domestic_civil_status_excludes_foreign_representation():
    plan = plan_exact_lookup("Điều 13 Luật 60/2014/QH13")
    accepted, rejected = filter_exact_candidates(
        [
            _active_candidate(1),
            _active_candidate(
                2,
                source_jurisdiction="foreign_representation",
            ),
        ],
        plan=plan,
        domain="ho_tich_chung_thuc",
        jurisdiction="domestic",
        as_of=date(2026, 7, 26),
    )

    assert [row["chunk_id"] for row in accepted] == [1]
    assert rejected == [{"chunk_id": 2, "reason": "wrong_jurisdiction"}]


def test_step3_explicit_foreign_representation_keeps_matching_source():
    plan = plan_exact_lookup("Điều 13 Luật 60/2014/QH13")
    accepted, rejected = filter_exact_candidates(
        [
            _active_candidate(
                2,
                source_jurisdiction="foreign_representation",
            )
        ],
        plan=plan,
        domain="ho_tich_chung_thuc",
        jurisdiction="foreign_representation",
        as_of=date(2026, 7, 26),
    )

    assert [row["chunk_id"] for row in accepted] == [2]
    assert rejected == []


def test_step3_current_instrument_wins_over_old_instrument():
    plan = plan_exact_lookup("Điều 13 Luật 60/2014/QH13")
    accepted, rejected = filter_exact_candidates(
        [
            _active_candidate(1),
            _active_candidate(
                2,
                document_status="expired",
                effective_date=date(2000, 1, 1),
                expired_date=date(2015, 12, 31),
            ),
        ],
        plan=plan,
        domain="ho_tich_chung_thuc",
        as_of=date(2026, 7, 26),
    )

    assert [row["chunk_id"] for row in accepted] == [1]
    assert rejected == [
        {"chunk_id": 2, "reason": "expired_or_not_yet_effective"}
    ]


def test_step3_primary_then_support_calls_support_at_most_once():
    support_calls = []

    result = retrieve_primary_then_support(
        required_facets=("authority", "documents", "deadline"),
        primary_retrieve=lambda: [
            {"chunk_id": 1, "facets": ["authority"]}
        ],
        support_retrieve=lambda missing: (
            support_calls.append(missing)
            or [{"chunk_id": 2, "facets": list(missing)}]
        ),
    )

    assert support_calls == [("documents", "deadline")]
    assert result.missing_facets == ()


def test_step3_rrf_rerank_input_and_diversity_are_bounded():
    fused = reciprocal_rank_fusion(
        vector_ranked=[
            {"chunk_id": 1, "document_id": 10, "article_id": 100},
            {"chunk_id": 2, "document_id": 10, "article_id": 100},
        ],
        lexical_ranked=[
            {"chunk_id": 2, "document_id": 10, "article_id": 100},
            {"chunk_id": 3, "document_id": 10, "article_id": 101},
            {"chunk_id": 4, "document_id": 10, "article_id": 102},
        ],
    )
    for index, row in enumerate(fused):
        row["rerank_score"] = 100 - index

    selected = diversify_ranked_candidates(
        fused,
        limit=10,
        max_per_article=2,
        max_per_document=3,
    )

    assert fused[0]["chunk_id"] == 2
    assert len(selected) == 3
    assert sum(row["article_id"] == 100 for row in selected) <= 2


def test_step3_exact_lookup_precedes_ann_and_never_likes_chunk_content():
    search_source = inspect.getsource(LegalRetriever.search)
    lexical_source = inspect.getsource(LegalRetriever._fetch_lexical_chunks)

    assert search_source.index("_fetch_exact_chunks") < search_source.index(
        "encode_query"
    )
    assert "legal_normalize_text(c.content) LIKE" in lexical_source
    assert "model" not in inspect.getsource(plan_exact_lookup).casefold()


def test_step3_long_issue_uses_only_bounded_metadata_lexical_terms():
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._engine = MagicMock()
    retriever._quality_sidecar_available = False

    rows = retriever._fetch_lexical_chunks(
        "Tôi thuê nhà mặt đường Tô Hiệu, quận Lê Chân",
        domain="ho_tich_chung_thuc",
        as_of=date(2026, 7, 26),
        retrieval_tier="core",
    )

    assert rows == []
    retriever._engine.connect.assert_called_once()
    statement, params = retriever._engine.connect.return_value.__enter__.return_value.execute.call_args.args
    rendered = str(statement)
    assert "legal_normalize_text(c.content) LIKE" in rendered
    assert len([key for key in params if key.startswith("term_")]) <= 4
    assert len(params["normalized_query"]) <= 160


def test_step3_exact_metadata_hit_skips_redundant_lexical_scan():
    assert _needs_lexical_retrieval([]) is True
    assert _needs_lexical_retrieval([{"chunk_id": 1}]) is False


def test_step3_explicit_local_document_reserves_result_slots_before_hierarchy_limit():
    ranked = [
        {"chunk_id": 1, "retrieval_source": "vector", "scope": "central"},
        {"chunk_id": 2, "retrieval_source": "vector", "scope": "central"},
        {"chunk_id": 22, "retrieval_source": "exact_metadata", "scope": "haiphong"},
    ]

    reserved = _reserve_explicit_exact_results(
        ranked,
        explicit_law_number="22/2025/NQ-HDND",
    )

    assert [item["chunk_id"] for item in reserved] == [22, 1, 2]
    assert _reserve_explicit_exact_results(
        ranked,
        explicit_law_number=None,
    ) == ranked


def test_step3_document_only_exact_lookup_can_keep_three_chunks_from_one_article():
    assert _exact_article_chunk_limit("3", set()) == 3
    assert _exact_article_chunk_limit("3", {"3"}) == 2
    assert _exact_article_chunk_limit("4", {"3"}) == 1


def test_step3_expected_sources_are_bound_to_the_matching_issue():
    expected = [
        {
            "law_number": "60/2014/QH13",
            "kind": "document",
            "outcome": "AVAILABLE_CORRECTLY_TIERED",
        },
        {
            "law_number": "123/2015/NĐ-CP",
            "kind": "document",
            "outcome": "AVAILABLE_CORRECTLY_TIERED",
        },
    ]
    issue = {
        "query": "Trẻ sinh ở nước ngoài cần hồ sơ gì?",
    }

    matched = benchmark_db5_shadow._expected_rows_for_issue(
        issue,
        expected,
    )

    assert {
        row["law_number"] for row in matched
    } == {"60/2014/QH13", "123/2015/NĐ-CP"}


def test_step3_uncontracted_empty_issue_is_not_a_retrieval_failure():
    assert benchmark_db5_shadow._classify_issue(
        [],
        has_live_results=False,
    ) == "EXPECTED_CONTRACT_GAP"


def test_step3_multi_law_exact_lookup_does_not_require_one_article_on_every_law():
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._quality_sidecar_available = False
    retriever._engine = MagicMock()
    connection = retriever._engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = []

    plan = plan_exact_lookup(
        _rewrite_query(
            "Trẻ sinh ở nước ngoài cần hồ sơ gì?"
        )
    )
    retriever._fetch_exact_chunks(
        plan,
        "ho_tich_chung_thuc",
        date(2026, 7, 26),
        "core",
    )
    statement = str(connection.execute.call_args.args[0])

    assert len(plan.law_numbers) > 1
    assert (
        "REPLACE(legal_normalize_identifier(d.law_number), CHR(272), 'D')"
        in statement
    )
    assert "legal_normalize_text(a.article_number) IN" not in statement


def test_exact_article_sql_loads_parent_count_and_chunks_in_source_order():
    retriever = LegalRetriever.__new__(LegalRetriever)
    retriever._quality_sidecar_available = False
    retriever._engine = MagicMock()
    connection = retriever._engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = []

    retriever._fetch_exact_chunks(
        plan_exact_lookup("Điều 73 của văn bản 60/2014/QH13"),
        None,
        date(2026, 8, 10),
        "expanded",
    )

    statement = str(connection.execute.call_args.args[0])
    params = connection.execute.call_args.args[1]
    assert "a.content AS article_content" in statement
    assert "COUNT(*)" in statement
    assert "expected_chunk.article_id = a.id" in statement
    assert "SELECT MIN(primary_article.id)" in statement
    assert "primary_article.document_id = d.id" in statement
    assert "ORDER BY c.chunk_index ASC, c.id ASC" in statement
    assert params["limit"] == 1000


def test_step3_shadow_suite_includes_all_nine_role_cases():
    role_cases = benchmark_db5_shadow._load_role_cases()

    assert len(role_cases) == 9
    assert {case["role"] for case in role_cases} == {
        "citizen",
        "officer",
        "admin",
    }
    assert all(1 <= len(case["issues"]) <= 6 for case in role_cases)
