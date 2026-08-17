from pathlib import Path
import json

from scripts.audit_retrieval_release_v2_acceptance import audit


def test_final_acceptance_is_blocked_when_production_evidence_is_missing(tmp_path: Path):
    pointer = tmp_path / "active.txt"
    pointer.write_text("baseline", encoding="utf-8")
    report = audit(
        m5_path=tmp_path / "m5.json",
        m6_path=tmp_path / "m6.json",
        full_answer_path=tmp_path / "answer.json",
        exact_294_path=tmp_path / "294.json",
        pointer_path=pointer,
        expected_pointer="baseline",
        output=tmp_path / "acceptance.json",
    )
    assert report["status"] == "BLOCKED"
    assert "m5_retrieval_gate" in report["blockers"]
    assert "full_answer_evidence_missing_or_failed" in report["blockers"]
    assert "exact_294_regression_missing_or_failed" in report["blockers"]
    assert "production_holdout_acceptance_missing_or_failed" in report["blockers"]
    assert report["mutation"]["active_pointer_changed"] is False


def test_final_acceptance_rejects_mismatched_passed_release_fingerprints(tmp_path: Path):
    pointer = tmp_path / "active.txt"
    pointer.write_text("baseline", encoding="utf-8")

    def write(name: str, payload: dict) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    common = {
        "status": "PASS",
        "selected_gates": {"safe": True},
        "manifest_file_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "suite_file_sha256": "c" * 64,
        "suite_sha256": "d" * 64,
        "embedding_model_fingerprint": "e" * 64,
    }
    m5 = write("m5.json", common)
    m6_payload = {**common, "manifest_sha256": "f" * 64}
    m6 = write("m6.json", m6_payload)
    answer = write("answer.json", {"status": "PASS"})
    exact = write("294.json", {"status": "PASS"})
    holdout = write("holdout.json", {
        "status": "PASS",
        "holdout_case_count": 500,
        "holdout_sealed": True,
        "gates": {"safe": True},
        "benchmark_report_sha256": "0" * 64,
    })
    report = audit(
        m5_path=m5,
        m6_path=m6,
        full_answer_path=answer,
        exact_294_path=exact,
        holdout_path=holdout,
        pointer_path=pointer,
        expected_pointer="baseline",
        output=tmp_path / "acceptance.json",
    )
    assert report["status"] == "BLOCKED"
    assert "m5_m6_release_fingerprint_mismatch" in report["blockers"]
    assert "production_holdout_fingerprint_mismatch" in report["blockers"]
