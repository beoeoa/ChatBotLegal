from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from api.release_evidence import REQUIRED_PRODUCTION_GATES
from api.release_signing import verify_release_report_signature
from scripts.verify_production_readiness import verify


def _write_evidence(directory: Path, gate: str, *, release_id: str = "r1") -> None:
    payload = {
        "evidence_id": f"e-{gate}",
        "release_id": release_id,
        "gate": gate,
        "status": "passed",
        "artifact_sha256": "a" * 64,
        "release_fingerprint": "b" * 64,
        "captured_at": "2026-08-13T00:00:00Z",
        "command_or_scenario": f"verify {gate}",
        "metrics": {},
    }
    (directory / f"{gate}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_readiness_verifier_is_no_go_when_evidence_is_missing(tmp_path: Path):
    _write_evidence(tmp_path, "capability")
    result = verify(
        release_id="r1",
        release_fingerprint="b" * 64,
        evidence_dir=tmp_path,
    )
    assert result["decision"] == "NO-GO"
    assert "restore" in result["missing_gates"]
    assert result["signature_status"] == "unsigned"
    assert result["signature"] is None


def test_readiness_verifier_accepts_only_complete_matching_evidence(tmp_path: Path):
    for gate in REQUIRED_PRODUCTION_GATES:
        _write_evidence(tmp_path, gate)
    result = verify(
        release_id="r1",
        release_fingerprint="b" * 64,
        evidence_dir=tmp_path,
    )
    assert result["decision"] == "GO"
    assert result["evidence_count"] == len(REQUIRED_PRODUCTION_GATES)


def test_malformed_evidence_is_reported_and_never_ignored_as_pass(tmp_path: Path):
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")
    result = verify(
        release_id="r1",
        release_fingerprint="b" * 64,
        evidence_dir=tmp_path,
    )
    assert result["decision"] == "NO-GO"
    assert result["invalid_artifacts"] == ["broken.json"]


def test_readiness_cli_writes_verifiable_signed_report_atomically(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    _write_evidence(evidence, "capability")
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "release-owner.pem"
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    output = tmp_path / "go-no-go.json"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/verify_production_readiness.py",
            "--release",
            "r1",
            "--release-fingerprint",
            "b" * 64,
            "--evidence-dir",
            str(evidence),
            "--output",
            str(output),
            "--signing-key",
            str(key_path),
            "--signer-id",
            "release-owner-test",
            "--signed-at",
            "2026-08-13T15:00:00Z",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["signature_status"] == "signed"
    assert verify_release_report_signature(
        payload, public_key=key.public_key()
    ).valid is True
    assert not output.with_name(f".{output.name}.tmp").exists()
