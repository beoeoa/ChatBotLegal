from cryptography.fernet import Fernet

from scripts.rotate_credential_encryption import (
    RotationError,
    derive_fernet,
    rotate_token,
)


def test_rotation_reencrypts_legacy_token_without_exposing_plaintext():
    old_key = "old-runtime-key"
    new_key = "new-runtime-key"
    secret = "provider-secret-value"
    old_token = derive_fernet(old_key).encrypt(secret.encode()).decode()

    result = rotate_token(old_token, source_key=old_key, target_key=new_key)

    assert result.status == "rotated"
    assert result.token != old_token
    assert secret not in result.token
    assert derive_fernet(new_key).decrypt(result.token.encode()).decode() == secret


def test_rotation_is_idempotent_when_token_already_uses_target_key():
    new_key = "new-runtime-key"
    token = derive_fernet(new_key).encrypt(b"provider-secret-value").decode()

    result = rotate_token(
        token,
        source_key="old-runtime-key",
        target_key=new_key,
    )

    assert result.status == "already_current"
    assert result.token == token


def test_rotation_rejects_encrypted_token_that_matches_neither_key():
    unrelated = Fernet.generate_key()
    token = Fernet(unrelated).encrypt(b"provider-secret-value").decode()

    try:
        rotate_token(
            token,
            source_key="old-runtime-key",
            target_key="new-runtime-key",
        )
    except RotationError as exc:
        assert "cannot be decrypted" in str(exc)
    else:
        raise AssertionError("Expected fail-closed rotation error")
