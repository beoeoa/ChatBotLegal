from __future__ import annotations

from api.legal_determinism import (
    build_data_release_id,
    build_runtime_version_trace,
    semantic_claim_key,
)
from api.legal_structured_answer import structured_model_options


def test_semantic_claim_key_deduplicates_format_and_zero_padding_only():
    first = semantic_claim_key("- Thời hạn là 03 ngày làm việc.")
    second = semantic_claim_key("Thời hạn: 3 ngày làm việc")
    different = semantic_claim_key("Thời hạn là 05 ngày làm việc.")
    assert first == second
    assert first != different


def test_all_structured_generation_options_are_non_random():
    options = structured_model_options(20, {})
    assert options["temperature"] == 0.0
    assert options["streaming"] is False


def test_runtime_version_trace_is_canonical_across_mapping_order():
    snapshot_a = {
        "schema_version": "legal-validity-serving-v1",
        "last_success_at": "2026-08-10T00:00:00+00:00",
        "coverage": {"eligible": 2, "observed": 2, "fresh": 2},
        "documents": {"B": {"fingerprint": "2"}, "A": {"fingerprint": "1"}},
    }
    snapshot_b = {
        "documents": {"A": {"fingerprint": "1"}, "B": {"fingerprint": "2"}},
        "coverage": {"fresh": 2, "observed": 2, "eligible": 2},
        "last_success_at": "2026-08-10T00:00:00+00:00",
        "schema_version": "legal-validity-serving-v1",
    }
    traces = [
        build_runtime_version_trace(
            app_version="1.9.0",
            index_collection="legal-v1",
            embedding_fingerprint="embed-abc",
            validity_snapshot=snapshot_a if index % 2 else snapshot_b,
            reranker_version="disabled",
        )
        for index in range(10)
    ]
    assert all(trace == traces[0] for trace in traces)
    assert len(traces[0]["validity_snapshot_sha256"]) == 64
    assert traces[0]["index_collection"] == "legal-v1"


def test_data_release_id_changes_when_index_or_validity_snapshot_changes():
    base = {
        "index_collection": "legal-core-v1",
        "embedding_fingerprint": "embed-a",
        "validity_snapshot_sha256": "a" * 64,
        "reranker_version": "disabled",
    }

    first = build_data_release_id(base)
    assert first == build_data_release_id(dict(reversed(list(base.items()))))
    assert first.startswith("data-release-")
    assert first != build_data_release_id(
        {**base, "index_collection": "legal-core-v2"}
    )
