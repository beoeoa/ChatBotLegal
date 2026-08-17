"""Detached Ed25519 attestation embedded in a Feature 018 Go/No-Go report."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from api.legal_audit_chain import public_key_fingerprint

SIGNATURE_SCHEMA_VERSION = "feature018-release-report-signature-v1"


@dataclass(frozen=True)
class ReleaseSignatureResult:
    valid: bool
    reason_code: str | None = None
    signer_id: str | None = None
    public_key_fingerprint: str | None = None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _unsigned_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in report.items()
        if key not in {"signature", "signature_status"}
    }


def _payload_sha256(report: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(_unsigned_payload(report))).hexdigest()


def _signature_statement(
    report: Mapping[str, Any],
    *,
    signer_id: str,
    signed_at: str,
    key_fingerprint: str,
) -> dict[str, Any]:
    return {
        "schema_version": SIGNATURE_SCHEMA_VERSION,
        "algorithm": "Ed25519",
        "payload_sha256": _payload_sha256(report),
        "release_id": str(report.get("release_id") or ""),
        "release_fingerprint": str(report.get("release_fingerprint") or ""),
        "decision": str(report.get("decision") or ""),
        "signed_at": signed_at,
        "signer_id": signer_id,
        "public_key_fingerprint": key_fingerprint,
    }


def sign_release_report(
    report: Mapping[str, Any],
    *,
    private_key: Ed25519PrivateKey,
    signer_id: str,
    signed_at: str,
) -> dict[str, Any]:
    """Return a signed copy; never serialize private-key material."""

    normalized_signer = str(signer_id or "").strip()
    normalized_time = str(signed_at or "").strip()
    if not normalized_signer:
        raise ValueError("release_report_signer_id_required")
    if not normalized_time:
        raise ValueError("release_report_signed_at_required")
    unsigned = _unsigned_payload(report)
    statement = _signature_statement(
        unsigned,
        signer_id=normalized_signer,
        signed_at=normalized_time,
        key_fingerprint=public_key_fingerprint(private_key.public_key()),
    )
    signature = base64.b64encode(private_key.sign(_canonical(statement))).decode(
        "ascii"
    )
    return {
        **unsigned,
        "signature_status": "signed",
        "signature": {**statement, "value": signature},
    }


def verify_release_report_signature(
    report: Mapping[str, Any],
    *,
    public_key: Ed25519PublicKey,
) -> ReleaseSignatureResult:
    signature = report.get("signature")
    if not isinstance(signature, Mapping):
        return ReleaseSignatureResult(False, "release_report_signature_missing")
    signer_id = str(signature.get("signer_id") or "").strip() or None
    expected_key_fingerprint = public_key_fingerprint(public_key)
    supplied_key_fingerprint = str(
        signature.get("public_key_fingerprint") or ""
    )
    if supplied_key_fingerprint != expected_key_fingerprint:
        return ReleaseSignatureResult(
            False,
            "release_report_public_key_mismatch",
            signer_id,
            supplied_key_fingerprint or None,
        )
    expected_statement = _signature_statement(
        report,
        signer_id=signer_id or "",
        signed_at=str(signature.get("signed_at") or ""),
        key_fingerprint=expected_key_fingerprint,
    )
    supplied_statement = {
        key: signature.get(key) for key in expected_statement
    }
    if supplied_statement != expected_statement:
        return ReleaseSignatureResult(
            False,
            "release_report_payload_mismatch",
            signer_id,
            expected_key_fingerprint,
        )
    try:
        encoded_signature = base64.b64decode(
            str(signature.get("value") or ""),
            validate=True,
        )
        public_key.verify(encoded_signature, _canonical(expected_statement))
    except (binascii.Error, InvalidSignature, ValueError):
        return ReleaseSignatureResult(
            False,
            "release_report_signature_invalid",
            signer_id,
            expected_key_fingerprint,
        )
    return ReleaseSignatureResult(
        True,
        None,
        signer_id,
        expected_key_fingerprint,
    )
