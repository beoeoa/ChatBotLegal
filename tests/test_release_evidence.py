from __future__ import annotations

from datetime import datetime, timezone

from api.release_evidence import (
    REQUIRED_PRODUCTION_GATES,
    ReleaseEvidence,
    evaluate_release_evidence,
)


def _evidence(gate: str, *, release_id: str = "release-1", status: str = "passed") -> ReleaseEvidence:
    return ReleaseEvidence(
        evidence_id=f"e-{gate}",
        release_id=release_id,
        gate=gate,
        status=status,
        artifact_sha256="a" * 64,
        release_fingerprint="b" * 64,
        captured_at=datetime(2026, 8, 13, tzinfo=timezone.utc),
        command_or_scenario=f"verify {gate}",
        metrics={},
    )


def test_go_requires_every_gate_for_the_same_release_and_fingerprint():
    evidence = [_evidence(gate) for gate in REQUIRED_PRODUCTION_GATES]
    result = evaluate_release_evidence(
        evidence,
        release_id="release-1",
        release_fingerprint="b" * 64,
    )
    assert result.decision == "GO"
    assert result.missing_gates == ()
    assert result.failed_gates == ()


def test_missing_or_failed_gate_is_no_go():
    evidence = [_evidence(gate) for gate in REQUIRED_PRODUCTION_GATES if gate != "restore"]
    evidence = [
        item.model_copy(update={"status": "failed"}) if item.gate == "security" else item
        for item in evidence
    ]
    result = evaluate_release_evidence(
        evidence,
        release_id="release-1",
        release_fingerprint="b" * 64,
    )
    assert result.decision == "NO-GO"
    assert result.missing_gates == ("restore",)
    assert result.failed_gates == ("security",)


def test_stale_or_wrong_release_evidence_cannot_satisfy_gate():
    evidence = [_evidence(gate) for gate in REQUIRED_PRODUCTION_GATES]
    evidence[0] = evidence[0].model_copy(update={"release_id": "old-release"})
    evidence[1] = evidence[1].model_copy(update={"release_fingerprint": "c" * 64})
    result = evaluate_release_evidence(
        evidence,
        release_id="release-1",
        release_fingerprint="b" * 64,
    )
    assert result.decision == "NO-GO"
    assert set(result.missing_gates) >= {evidence[0].gate, evidence[1].gate}
    assert result.rejected_evidence_count == 2

