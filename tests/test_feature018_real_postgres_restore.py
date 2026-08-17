from __future__ import annotations

import pytest

from scripts.restore_feature018_postgres_backup import (
    assert_isolated_database_name,
    compare_counts,
)


def test_postgres_restore_database_name_must_be_isolated():
    assert (
        assert_isolated_database_name("feature018_restore_20260813")
        == "feature018_restore_20260813"
    )
    for unsafe in ("legal_chatbot", "postgres", "feature018_restore_", "restore_test"):
        with pytest.raises(ValueError, match="not_isolated"):
            assert_isolated_database_name(unsafe)


def test_postgres_restore_count_comparison_is_fail_closed():
    assert compare_counts({"legal_document": 2}, {"legal_document": 2})["passed"]
    mismatch = compare_counts(
        {"legal_document": 2},
        {"legal_document": 1, "unexpected": 1},
    )
    assert mismatch["passed"] is False
    assert mismatch["count_mismatches"]["legal_document"] == {
        "source": 2,
        "restored": 1,
    }
