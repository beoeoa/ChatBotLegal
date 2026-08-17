from __future__ import annotations

import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.release_signing import sign_release_report
from scripts.build_feature018_local_evidence import _ask_load_metrics, _security_metrics
from scripts.record_feature018_security_risk_acceptance import build_acceptance
from scripts.run_feature017_deepseek_golden1000 import (
    EVALUATOR_VERSION,
    REPORT_SCHEMA_VERSION,
)
from scripts.summarize_feature018_quality_gates import summarize


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_quality_summary_keeps_latency_pass_but_blocks_quality_failures(tmp_path: Path):
    result = summarize(
        golden100=_write(tmp_path / "g100.json", {"status": "PASS", "request_count": 100}),
        golden294=_write(tmp_path / "g294.json", {"status": "PASS", "case_count": 294, "expired_selection_count": 0}),
        golden_v3=_write(tmp_path / "gv3.json", {"passed": True, "case_count": 1000}),
        deepseek_live=_write(
            tmp_path / "live.json",
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "evaluator_version": EVALUATOR_VERSION,
                "run_identity": {
                    "evaluator_version": EVALUATOR_VERSION,
                    "case_count": 1000,
                    "golden_sha256": "a" * 64,
                    "target_sha256": "b" * 64,
                    "case_selection_sha256": "c" * 64,
                    "concurrency": 100,
                },
                "summary": {
                    "case_count": 1000,
                    "passed": 746,
                    "http_200": 952,
                    "exact_form_set": 916,
                    "p95_seconds": 18.267,
                    "unexpected_provider_fallback": 0,
                    "error_counts": {"HTTP_500": 48},
                }
            },
        ),
        release_manifest=_write(tmp_path / "manifest.json", {"release": "candidate"}),
    )
    live = result["gates"]["golden_v3_1000_live_full_answer"]
    assert live["checks"]["full_answer_p95_at_most_25_seconds"] is True
    assert live["concurrency"] == 100
    assert live["checks"]["case_pass_rate_at_least_99_percent"] is False
    assert live["passed"] is False
    assert result["passed"] is False
    assert "cases" not in result
    assert "answers" not in result


def test_quality_summary_rejects_unbound_legacy_live_evidence(tmp_path: Path):
    result = summarize(
        golden100=_write(tmp_path / "g100.json", {"status": "PASS", "request_count": 100}),
        golden294=_write(tmp_path / "g294.json", {"status": "PASS", "case_count": 294, "expired_selection_count": 0}),
        golden_v3=_write(tmp_path / "gv3.json", {"passed": True, "case_count": 1000}),
        deepseek_live=_write(
            tmp_path / "live.json",
            {
                "schema_version": "feature017-deepseek-golden1000-live-v1",
                "summary": {
                    "case_count": 1000,
                    "passed": 1000,
                    "http_200": 1000,
                    "exact_form_set": 1000,
                    "p95_seconds": 10,
                    "unexpected_provider_fallback": 0,
                    "error_counts": {},
                },
            },
        ),
        release_manifest=_write(tmp_path / "manifest.json", {"release": "candidate"}),
    )

    live = result["gates"]["golden_v3_1000_live_full_answer"]
    assert live["checks"]["evaluator_identity_bound"] is False
    assert live["passed"] is False


def test_ask_load_metrics_require_bound_100_concurrency_run():
    bound = _ask_load_metrics(
        {
            "case_count": 1000,
            "concurrency": 100,
            "p95_seconds": 20,
            "http_200": 1000,
            "checks": {"evaluator_identity_bound": True},
        },
        support_passed=True,
    )
    assert bound["status"] == "passed"
    assert bound["ask_concurrency_evidenced"] is True

    unbound = _ask_load_metrics(
        {
            "case_count": 1000,
            "concurrency": 100,
            "p95_seconds": 20,
            "http_200": 1000,
            "checks": {"evaluator_identity_bound": False},
        },
        support_passed=True,
    )
    assert unbound["status"] == "failed"
    assert unbound["ask_concurrency_evidenced"] is False


def test_ask_load_metrics_fail_when_sla_or_error_rate_fails():
    metrics = _ask_load_metrics(
        {
            "case_count": 1000,
            "concurrency": 100,
            "p95_seconds": 184.96,
            "http_200": 927,
            "checks": {"evaluator_identity_bound": True},
        },
        support_passed=True,
    )

    assert metrics["ask_concurrency_evidenced"] is True
    assert metrics["full_answer_sla_passed"] is False
    assert metrics["http_error_rate_passed"] is False
    assert metrics["status"] == "failed"


def test_security_risk_acceptance_requires_zero_remediable_findings(tmp_path: Path):
    trivy = tmp_path / "trivy.json"
    trivy.write_text(
        json.dumps({"totals": {"remediable_count": 0, "unfixed_os_count": 253}}),
        encoding="utf-8",
    )

    result = build_acceptance(
        release_id="r1",
        release_fingerprint="a" * 64,
        trivy_report=trivy,
        signer_id="owner",
        signed_at="2026-08-13T15:00:00Z",
    )

    assert result["decision"] == "ACCEPT"
    assert result["accepted_unfixed_os_high_critical"] == 253
    assert result["remediable_high_critical"] == 0


def test_security_metrics_accept_release_bound_signed_unfixed_os_risk(tmp_path: Path):
    release_id = "r1"
    fingerprint = "a" * 64
    trivy = _write(
        tmp_path / "trivy.json",
        {
            "status": "DECISION_REQUIRED",
            "totals": {
                "remediable_count": 0,
                "unclassified_language_count": 0,
                "unfixed_os_count": 253,
            },
        },
    )
    acceptance = build_acceptance(
        release_id=release_id,
        release_fingerprint=fingerprint,
        trivy_report=trivy,
        signer_id="owner",
        signed_at="2026-08-13T15:00:00Z",
    )
    private_key = Ed25519PrivateKey.generate()
    signed = sign_release_report(
        acceptance,
        private_key=private_key,
        signer_id="owner",
        signed_at="2026-08-13T15:00:00Z",
    )
    acceptance_path = _write(tmp_path / "acceptance.json", signed)
    public_key_path = tmp_path / "public.pem"
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    metrics = _security_metrics(
        release_id=release_id,
        release_fingerprint=fingerprint,
        trivy_path=trivy,
        source_secret_count=0,
        risk_acceptance_path=acceptance_path,
        public_key_path=public_key_path,
    )

    assert metrics["status"] == "passed"
    assert metrics["risk_acceptance_valid"] is True
    assert metrics["risk_acceptance_signer"] == "owner"


def test_security_metrics_reject_acceptance_for_other_fingerprint(tmp_path: Path):
    release_id = "r1"
    trivy = _write(
        tmp_path / "trivy.json",
        {
            "status": "DECISION_REQUIRED",
            "totals": {
                "remediable_count": 0,
                "unclassified_language_count": 0,
                "unfixed_os_count": 1,
            },
        },
    )
    private_key = Ed25519PrivateKey.generate()
    acceptance = build_acceptance(
        release_id=release_id,
        release_fingerprint="b" * 64,
        trivy_report=trivy,
        signer_id="owner",
        signed_at="2026-08-13T15:00:00Z",
    )
    acceptance_path = _write(
        tmp_path / "acceptance.json",
        sign_release_report(
            acceptance,
            private_key=private_key,
            signer_id="owner",
            signed_at="2026-08-13T15:00:00Z",
        ),
    )
    public_key_path = tmp_path / "public.pem"
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    metrics = _security_metrics(
        release_id=release_id,
        release_fingerprint="a" * 64,
        trivy_path=trivy,
        source_secret_count=0,
        risk_acceptance_path=acceptance_path,
        public_key_path=public_key_path,
    )

    assert metrics["status"] == "failed"
    assert metrics["risk_acceptance_reason"] == "security_risk_acceptance_scope_mismatch"
