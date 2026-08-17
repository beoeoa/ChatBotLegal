from __future__ import annotations

import pytest

from scripts.verify_surreal_restore_drill import (
    compare_database_inventories,
    quote_surreal_identifier,
)


def test_quote_surreal_identifier_accepts_only_schema_identifiers() -> None:
    assert quote_surreal_identifier("legal_review_attestation") == "`legal_review_attestation`"

    with pytest.raises(ValueError, match="unsafe_surreal_identifier"):
        quote_surreal_identifier("candidate; DELETE candidate")


def test_compare_database_inventories_requires_schema_and_count_parity() -> None:
    source = {
        "schema_sha256": "schema-a",
        "table_counts": {"candidate": 3, "audit_log": 8},
    }
    restored = {
        "schema_sha256": "schema-a",
        "table_counts": {"candidate": 3, "audit_log": 8},
    }

    result = compare_database_inventories(source, restored)
    assert result["passed"] is True
    assert result["schema_match"] is True
    assert result["count_mismatches"] == {}

    restored["table_counts"]["candidate"] = 2
    result = compare_database_inventories(source, restored)
    assert result["passed"] is False
    assert result["count_mismatches"] == {
        "candidate": {"source": 3, "restored": 2}
    }
