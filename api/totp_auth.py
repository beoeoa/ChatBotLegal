"""Local RFC 6238 TOTP and short-lived signed MFA enrollment tickets."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
import struct
from typing import Any, Mapping
from urllib.parse import quote, urlencode

from open_notebook.utils.encryption import get_secret_from_env


def generate_totp_secret(byte_count: int = 20) -> str:
    return base64.b32encode(secrets.token_bytes(byte_count)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    normalized = "".join(str(secret).strip().split()).upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def totp_code(
    secret: str,
    *,
    at: float | int | None = None,
    period: int = 30,
    digits: int = 6,
) -> str:
    timestamp = datetime.now(timezone.utc).timestamp() if at is None else float(at)
    counter = int(timestamp // period)
    digest = hmac.new(
        _decode_secret(secret),
        struct.pack(">Q", counter),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def verify_totp_code(
    secret: str,
    supplied: str,
    *,
    at: float | int | None = None,
    period: int = 30,
    digits: int = 6,
    window: int = 1,
) -> bool:
    candidate = "".join(str(supplied or "").split())
    if len(candidate) != digits or not candidate.isdigit():
        return False
    timestamp = datetime.now(timezone.utc).timestamp() if at is None else float(at)
    return any(
        hmac.compare_digest(
            totp_code(
                secret,
                at=timestamp + offset * period,
                period=period,
                digits=digits,
            ),
            candidate,
        )
        for offset in range(-window, window + 1)
    )


def build_provisioning_uri(
    *,
    secret: str,
    username: str,
    issuer: str = "ChatBotLegal",
) -> str:
    label = quote(f"{issuer}:{username}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": "6",
            "period": "30",
        }
    )
    return f"otpauth://totp/{label}?{query}"


def _ticket_key() -> bytes:
    key = get_secret_from_env("OPEN_NOTEBOOK_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("OPEN_NOTEBOOK_ENCRYPTION_KEY is required for MFA tickets")
    return hashlib.sha256(f"chatbotlegal-mfa-v1:{key}".encode("utf-8")).digest()


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def issue_mfa_ticket(
    kind: str,
    claims: Mapping[str, Any],
    *,
    ttl_seconds: int = 600,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(timezone.utc)
    payload = {
        **dict(claims),
        "kind": str(kind),
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=ttl_seconds)).timestamp()),
        "nonce": secrets.token_urlsafe(12),
    }
    encoded = _urlsafe_encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    signature = _urlsafe_encode(
        hmac.new(_ticket_key(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{encoded}.{signature}"


def read_mfa_ticket(
    ticket: str,
    *,
    expected_kind: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        encoded, supplied_signature = str(ticket).split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid MFA ticket") from exc
    expected_signature = _urlsafe_encode(
        hmac.new(_ticket_key(), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("invalid MFA ticket signature")
    try:
        payload = json.loads(_urlsafe_decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid MFA ticket payload") from exc
    if payload.get("kind") != expected_kind:
        raise ValueError("invalid MFA ticket kind")
    current = int((now or datetime.now(timezone.utc)).timestamp())
    if current > int(payload.get("exp") or 0):
        raise ValueError("MFA ticket expired")
    return payload
