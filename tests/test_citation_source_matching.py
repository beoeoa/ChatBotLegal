def test_citations_follow_explicit_law_and_article_from_answer():
    from api.routers.search import _build_citations_from_retrieval

    retrieval = {
        "query": "cai chinh ho tich",
        "results": [
            {"chunk_id": "wrong", "doc_id": "10", "law_number": "01/2020/QH14", "article_number": "5", "source_url": "https://example.test/wrong", "score": 0.99},
            {"chunk_id": "right", "doc_id": "20", "law_number": "60/2014/QH13", "article_number": "28", "source_url": "https://example.test/right", "score": 0.55},
        ],
    }
    citations = _build_citations_from_retrieval(retrieval, answer_text="Căn cứ Luật Hộ tịch số 60/2014/QH13, Điều 28.")
    assert [item["doc_id"] for item in citations] == ["20"]
    assert citations[0]["source_url"] == "https://example.test/right"
    assert citations[0]["internal_url"].startswith("/legal-documents/20")


def test_citation_preserves_verified_document_status_from_retrieval():
    from api.routers.search import _build_citations_from_retrieval

    citations = _build_citations_from_retrieval([
        {
            "chunk_id": "active-article",
            "doc_id": "20",
            "law_number": "60/2014/QH13",
            "article_number": "13",
            "document_status": "active",
            "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=46746",
        }
    ])

    assert citations[0]["effective_status"] == "active"


def test_explicit_unsupported_reference_never_links_a_different_document():
    from api.routers.search import _build_citations_from_retrieval

    retrieval = {
        "results": [
            {"chunk_id": "wrong", "doc_id": "10", "law_number": "01/2020/QH14", "article_number": "5"},
        ],
    }
    citations = _build_citations_from_retrieval(
        retrieval,
        answer_text="Căn cứ Nghị định 175/2024/NĐ-CP, Điều 62.",
    )
    assert citations == []


def test_retrieval_normalization_preserves_internal_link_metadata():
    from api.routers.search import _extract_retrieval_results

    results = _extract_retrieval_results({
        "results": [{
            "chunk_id": "abc",
            "doc_id": "175",
            "law_number": "175/2024/NĐ-CP",
            "article_number": "62",
            "clause_number": "2",
            "point_number": "a",
            "effective_status": "active",
            "score": 0.91,
        }],
    })
    assert results[0]["doc_id"] == "175"
    assert results[0]["document_id"] == "175"
    assert results[0]["clause_number"] == "2"
    assert results[0]["point_number"] == "a"
    assert results[0]["effective_status"] == "active"
    assert results[0]["score"] == 0.91


def test_citation_drops_malformed_external_source_url():
    from api.routers.search import _build_citations_from_retrieval

    citations = _build_citations_from_retrieval([{"chunk_id": "1", "doc_id": "2", "law_number": "1/2024/QH15", "source_url": "javascript:alert(1)"}])
    assert citations[0]["source_url"] == ""
    assert citations[0]["internal_url"] == "/legal-documents/2"
