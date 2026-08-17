from scripts.build_retrieval_eval_review_queue_v1 import _inventory_index, propose_source


def test_review_queue_keeps_inventory_match_as_observation_only():
    index = _inventory_index({
        "documents": [{
            "document_id": 7,
            "law_number": "60/2014/QH13",
            "status_observed": "active",
            "serving_state": "current_retrievable",
            "source_url": "https://vbpl.vn/example",
            "effective_date": "2015-01-01",
            "expired_date": None,
            "article_count": 10,
            "chunk_count": 20,
        }]
    })
    proposal = propose_source({"law_number": "60/2014/QH13", "article": "3"}, index)
    assert proposal["mapping_status"] == "matched_unique_observation"
    assert proposal["official_url"] is None
    assert proposal["validity_from"] is None
    assert proposal["inventory_observations"][0]["document_id"] == 7


def test_review_queue_marks_ambiguous_law_number_for_manual_review():
    index = _inventory_index({
        "documents": [
            {"document_id": 7, "law_number": "1/2020/QH14"},
            {"document_id": 8, "law_number": "1/2020/QH14"},
        ]
    })
    proposal = propose_source({"law_number": "1/2020/QH14"}, index)
    assert proposal["mapping_status"] == "matched_multiple_observations"
    assert "inventory_match_is_ambiguous" in proposal["review_reasons"]


def test_review_queue_does_not_turn_missing_source_into_a_synthetic_match():
    proposal = propose_source({"law_number": "999/2099/QH99"}, {})
    assert proposal["mapping_status"] == "unmatched_in_inventory"
    assert proposal["inventory_observations"] == []
    assert proposal["official_url"] is None
