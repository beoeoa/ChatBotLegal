from api.legal_retrieval_quality import (
    assess_chunk_quality,
    clean_article_title,
    diversify_ranked_candidates,
    normalize_vietnamese_search_text,
    reciprocal_rank_fusion,
    rank_candidates_rrf_v2,
)


def test_normalization_uses_same_unaccented_expression_for_query_and_content():
    assert normalize_vietnamese_search_text("Đăng ký kết hôn") == "dang ky ket hon"
    assert normalize_vietnamese_search_text("  ĐĂNG-ký, kết hôn! ") == "dang ky ket hon"


def test_clean_article_title_drops_previous_article_body_noise():
    noisy = (
        "Phần còn lại của nội dung điều trước được lặp lại rất dài. " * 12
        + "Điều 15. Trách nhiệm đăng ký khai sinh"
    )

    assert clean_article_title("15", noisy) == "Điều 15"
    assert clean_article_title("15", "Trách nhiệm đăng ký khai sinh") == (
        "Điều 15. Trách nhiệm đăng ký khai sinh"
    )


def test_quality_assessment_quarantines_empty_and_exact_duplicate_without_deleting():
    empty = assess_chunk_quality(chunk_id=1, content="", article_number="1", article_title="")
    duplicate = assess_chunk_quality(
        chunk_id=2,
        content="Nội dung",
        article_number="2",
        article_title="Phạm vi",
        canonical_chunk_id=1,
    )

    assert empty.eligible is False and "empty_content" in empty.quality_reasons
    assert duplicate.eligible is False and duplicate.canonical_chunk_id == 1
    assert "exact_duplicate" in duplicate.quality_reasons


def test_rrf_merges_vector_and_lexical_ranks_without_score_scale_dependency():
    fused = reciprocal_rank_fusion(
        vector_ranked=[{"chunk_id": "a"}, {"chunk_id": "b"}],
        lexical_ranked=[{"chunk_id": "b"}, {"chunk_id": "c"}],
        key="chunk_id",
    )

    assert [item["chunk_id"] for item in fused] == ["b", "a", "c"]
    assert all(item["rrf_score"] > 0 for item in fused)


def test_v2_ranking_uses_independent_vector_and_bm25_ranks_only():
    ranked = rank_candidates_rrf_v2(
        [
            {
                "chunk_id": "vector-first",
                "vector_score": 0.99,
                "bm25_score": 0.01,
                "retrieval_sources": ["vector"],
                "lexical_boost": 99.0,
                "topic_boost": 99.0,
            },
            {
                "chunk_id": "both-second",
                "vector_score": 0.80,
                "bm25_score": 50.0,
                "retrieval_sources": ["vector", "lexical"],
                "lexical_boost": 0.0,
                "topic_boost": 0.0,
            },
            {
                "chunk_id": "bm25-only",
                "vector_score": 0.15,
                "bm25_score": 40.0,
                "retrieval_sources": ["lexical"],
            },
        ]
    )

    assert [row["chunk_id"] for row in ranked] == [
        "both-second",
        "vector-first",
        "bm25-only",
    ]
    assert all(row["score"] == row["rrf_score"] for row in ranked)
    assert all(row["lexical_boost"] == 0.0 for row in ranked)
    assert all(row["topic_boost"] == 0.0 for row in ranked)


def test_v2_rrf_tie_break_is_stable_by_structural_identity():
    candidates = [
        {"chunk_id": "20", "vector_score": 0.5, "bm25_score": 1.0, "retrieval_sources": ["vector"]},
        {"chunk_id": "10", "vector_score": 0.5, "bm25_score": 1.0, "retrieval_sources": ["vector"]},
    ]

    first = rank_candidates_rrf_v2(candidates)
    second = rank_candidates_rrf_v2(list(reversed(candidates)))

    assert [row["chunk_id"] for row in first] == ["10", "20"]
    assert [row["chunk_id"] for row in second] == ["10", "20"]


def test_diversity_prevents_one_document_from_occupying_the_context():
    candidates = [
        {"chunk_id": index, "document_id": "law-a", "article_id": index, "score": 100 - index}
        for index in range(6)
    ] + [
        {"chunk_id": 20, "document_id": "decree-b", "article_id": 20, "score": 80},
        {"chunk_id": 21, "document_id": "decree-b", "article_id": 21, "score": 79},
        {"chunk_id": 30, "document_id": "circular-c", "article_id": 30, "score": 78},
        {"chunk_id": 31, "document_id": "circular-c", "article_id": 31, "score": 77},
    ]

    selected = diversify_ranked_candidates(candidates, limit=6, max_per_document=2)

    assert len(selected) == 6
    assert {item["document_id"] for item in selected} == {"law-a", "decree-b", "circular-c"}
    assert sum(item["document_id"] == "law-a" for item in selected) == 2
