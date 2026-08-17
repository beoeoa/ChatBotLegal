from pathlib import Path
from datetime import date

from scripts import legal_search_server
from scripts.legal_search_server import BatchSearchIssue


ROOT = Path(__file__).resolve().parents[1]


def test_search_result_cache_survives_a_complete_warm_benchmark_cycle():
    assert legal_search_server._SEARCH_CACHE_TTL_SECONDS >= 300


def test_isolated_step4_retrieval_uses_verified_primary_support_without_pointer_change():
    script = (
        ROOT / "scripts" / "start_step4_benchmark_retrieval.ps1"
    ).read_text(encoding="utf-8")

    assert "db4-serving-20260723" in script
    assert "$manifest.primary_collection" in script
    assert "$manifest.support_collection" in script
    assert "active_core_collection.txt" not in script


def test_batch_cache_key_is_opaque_and_separates_admin_trace_shape():
    issue = BatchSearchIssue(
        issue_id="issue-1",
        query="Hồ sơ khai sinh cần gì?",
        domain="tu_phap_ho_tich",
        intent="documents",
    )

    public_key = legal_search_server._cached_batch_key(
        issue, date(2026, 7, 24), "core", include_trace=False
    )
    trace_key = legal_search_server._cached_batch_key(
        issue, date(2026, 7, 24), "core", include_trace=True
    )

    assert "Hồ sơ khai sinh" not in repr(public_key)
    assert public_key != trace_key
