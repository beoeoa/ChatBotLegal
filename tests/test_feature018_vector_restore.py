from scripts.verify_feature018_vector_restore import compare_collection_counts


def test_vector_restore_collection_counts_match():
    result = compare_collection_counts({"primary": 10}, {"primary": 10})
    assert result["passed"] is True


def test_vector_restore_collection_counts_fail_closed():
    result = compare_collection_counts(
        {"primary": 10, "support": 2},
        {"primary": 9},
    )
    assert result["passed"] is False
    assert result["count_mismatches"] == {
        "primary": {"expected": 10, "restored": 9},
        "support": {"expected": 2, "restored": None},
    }
