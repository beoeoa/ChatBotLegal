from __future__ import annotations

from copy import deepcopy

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.release_signing import sign_release_report, verify_release_report_signature


def _report() -> dict:
    return {
        "schema_version": "production-readiness-v1",
        "decision": "NO-GO",
        "release_id": "feature018-candidate",
        "release_fingerprint": "a" * 64,
        "required_gates": ["security"],
        "passed_gates": [],
        "missing_gates": [],
        "failed_gates": ["security"],
        "rejected_evidence_count": 0,
        "evidence_count": 1,
        "invalid_artifacts": [],
    }


def test_release_report_signature_binds_payload_and_owner_identity():
    key = Ed25519PrivateKey.generate()
    signed = sign_release_report(
        _report(),
        private_key=key,
        signer_id="release-owner-1",
        signed_at="2026-08-13T15:00:00Z",
    )

    result = verify_release_report_signature(signed, public_key=key.public_key())

    assert result.valid is True
    assert signed["signature_status"] == "signed"
    assert signed["signature"]["algorithm"] == "Ed25519"
    assert signed["signature"]["signer_id"] == "release-owner-1"
    assert "private" not in str(signed).casefold()


def test_release_report_signature_rejects_payload_tampering_and_wrong_key():
    key = Ed25519PrivateKey.generate()
    signed = sign_release_report(
        _report(),
        private_key=key,
        signer_id="release-owner-1",
        signed_at="2026-08-13T15:00:00Z",
    )
    tampered = deepcopy(signed)
    tampered["failed_gates"] = []
    tampered["decision"] = "GO"

    assert verify_release_report_signature(
        tampered, public_key=key.public_key()
    ).reason_code == "release_report_payload_mismatch"
    assert verify_release_report_signature(
        signed,
        public_key=Ed25519PrivateKey.generate().public_key(),
    ).reason_code == "release_report_public_key_mismatch"


def test_unsigned_release_report_fails_closed():
    result = verify_release_report_signature(
        _report(),
        public_key=Ed25519PrivateKey.generate().public_key(),
    )

    assert result.valid is False
    assert result.reason_code == "release_report_signature_missing"
