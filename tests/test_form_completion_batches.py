from __future__ import annotations

import pytest

from api.form_resolution_campaign import build_review_batches, validate_completion_run


def _candidate(index: int, *, instrument: str = "01/2026/TT-TEST") -> dict:
    return {
        "candidate_id": f"candidate-{index:03d}",
        "requirement_identity_id": f"identity-{index:03d}",
        "procedure_id": f"procedure-{index:03d}",
        "issuing_instrument": instrument,
        "source_domain": "vbpl.vn",
        "approved": False,
        "runtime_eligible": False,
    }


def test_review_batches_are_deterministic_and_never_exceed_25() -> None:
    candidates = [_candidate(index) for index in range(61)]
    first = build_review_batches(
        candidates, manifest_sha256="a" * 64, max_batch_size=25
    )
    second = build_review_batches(
        list(reversed(candidates)), manifest_sha256="a" * 64, max_batch_size=25
    )
    assert first == second
    assert [len(batch["records"]) for batch in first] == [25, 25, 11]
    assert all(batch["manifest_sha256"] == "a" * 64 for batch in first)
    assert all(record["approved"] is False for batch in first for record in batch["records"])
    assert all(
        record["runtime_eligible"] is False
        for batch in first
        for record in batch["records"]
    )


def test_completion_run_rejects_checksum_drift_and_stale_preview() -> None:
    with pytest.raises(ValueError, match="MANIFEST_CHECKSUM_DRIFT"):
        validate_completion_run(
            expected_manifest_sha256="a" * 64,
            actual_manifest_sha256="b" * 64,
            expected_source_snapshot_sha256="c" * 64,
            actual_source_snapshot_sha256="c" * 64,
            expected_preview_fingerprint="d" * 64,
            actual_preview_fingerprint="d" * 64,
        )
    with pytest.raises(ValueError, match="PREVIEW_STALE_OR_TAMPERED"):
        validate_completion_run(
            expected_manifest_sha256="a" * 64,
            actual_manifest_sha256="a" * 64,
            expected_source_snapshot_sha256="c" * 64,
            actual_source_snapshot_sha256="c" * 64,
            expected_preview_fingerprint="d" * 64,
            actual_preview_fingerprint="e" * 64,
        )


def test_review_batch_counts_all_requirement_aliases_without_splitting_record() -> None:
    candidates = [_candidate(index) for index in range(24)]
    candidates[0]["requirement_identity_ids"] = ["identity-000", "identity-alias"]
    batches = build_review_batches(
        candidates, manifest_sha256="a" * 64, max_batch_size=25
    )
    assert len(batches) == 1
    assert batches[0]["identity_count"] == 25
