from datetime import datetime, timezone

import pytest

from api.totp_auth import (
    build_provisioning_uri,
    generate_totp_secret,
    issue_mfa_ticket,
    read_mfa_ticket,
    totp_code,
    verify_totp_code,
)


def test_totp_matches_rfc6238_sha1_vector():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    assert totp_code(secret, at=59, digits=8) == "94287082"
    assert verify_totp_code(secret, "94287082", at=59, digits=8, window=0)
    assert not verify_totp_code(secret, "94287081", at=59, digits=8, window=0)


def test_generated_secret_and_provisioning_uri_are_authenticator_compatible():
    secret = generate_totp_secret()
    uri = build_provisioning_uri(
        secret=secret,
        username="admin@example.local",
        issuer="ChatBotLegal",
    )

    assert len(secret) >= 32
    assert "=" not in secret
    assert uri.startswith("otpauth://totp/ChatBotLegal%3Aadmin%40example.local?")
    assert f"secret={secret}" in uri
    assert "issuer=ChatBotLegal" in uri


def test_mfa_ticket_is_signed_typed_and_expires(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_ENCRYPTION_KEY", "unit-test-mfa-key")
    now = datetime(2026, 7, 29, 1, 0, tzinfo=timezone.utc)
    ticket = issue_mfa_ticket(
        "setup",
        {"user_id": "user_account:test", "role": "admin"},
        now=now,
        ttl_seconds=300,
    )

    payload = read_mfa_ticket(ticket, expected_kind="setup", now=now)
    assert payload["user_id"] == "user_account:test"
    assert payload["role"] == "admin"

    with pytest.raises(ValueError, match="kind"):
        read_mfa_ticket(ticket, expected_kind="confirm", now=now)
    with pytest.raises(ValueError, match="expired"):
        read_mfa_ticket(
            ticket,
            expected_kind="setup",
            now=datetime(2026, 7, 29, 1, 6, tzinfo=timezone.utc),
        )

    payload_part, signature = ticket.split(".", 1)
    tampered = f"{payload_part[:-1]}A.{signature}"
    with pytest.raises(ValueError, match="signature"):
        read_mfa_ticket(tampered, expected_kind="setup", now=now)
