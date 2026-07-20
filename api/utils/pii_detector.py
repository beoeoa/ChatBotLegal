"""PII detection and masking for citizen/admin uploads.

Detects and masks:
- CCCD/CMND (9 or 12 digits)
- Vietnamese mobile phone numbers
- Email addresses

Also exposes temporary-file retention defaults for upload metadata.
"""

from __future__ import annotations

import os
import re
from typing import Any

# CCCD/CMND: 9 or 12 digits. Avoid pure year/date false positives by requiring
# standalone number tokens.
CCCD_PATTERN = re.compile(r"(?<!\d)(?:\d{12}|\d{9})(?!\d)")
# Phone: 0[3|5|7|8|9]xxxxxxxx
PHONE_PATTERN = re.compile(r"(?<!\d)(?:0[35789]\d{8})(?!\d)")
# Email
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# Temporary upload retention (hours). Default 24h, overridable by env.
TEMP_UPLOAD_RETENTION_HOURS = int(os.getenv("TEMP_UPLOAD_RETENTION_HOURS", "24"))
TEMP_UPLOAD_RETENTION_POLICY = {
    "retention_hours": TEMP_UPLOAD_RETENTION_HOURS,
    "delete_after_use": True,
    "scope": "temporary_upload_context",
}


def detect_pii(text: str) -> bool:
    """Return True if text contains CCCD, phone, or email."""
    if not text:
        return False
    return bool(
        CCCD_PATTERN.search(text)
        or PHONE_PATTERN.search(text)
        or EMAIL_PATTERN.search(text)
    )


def analyze_pii(text: str) -> dict[str, Any]:
    """Return structured PII findings for metadata/permissions."""
    if not text:
        return {
            "contains_pii": False,
            "types": [],
            "counts": {"cccd": 0, "phone": 0, "email": 0},
        }
    cccd = CCCD_PATTERN.findall(text)
    phones = PHONE_PATTERN.findall(text)
    emails = EMAIL_PATTERN.findall(text)
    types: list[str] = []
    if cccd:
        types.append("cccd")
    if phones:
        types.append("phone")
    if emails:
        types.append("email")
    return {
        "contains_pii": bool(types),
        "types": types,
        "counts": {
            "cccd": len(cccd),
            "phone": len(phones),
            "email": len(emails),
        },
    }


def _mask_cccd(match: re.Match[str]) -> str:
    value = match.group(0)
    if len(value) <= 5:
        return "*" * len(value)
    return value[:3] + ("*" * (len(value) - 5)) + value[-2:]


def _mask_phone(match: re.Match[str]) -> str:
    value = match.group(0)
    return value[:3] + "*****" + value[-2:]


def _mask_email(match: re.Match[str]) -> str:
    value = match.group(0)
    local, _, domain = value.partition("@")
    if not domain:
        return "***"
    keep = local[:2] if len(local) >= 2 else local[:1]
    return f"{keep}***@{domain}"


def mask_pii(text: str) -> tuple[str, bool]:
    """Mask CCCD/phone/email in text. Return (masked_text, changed)."""
    if not text:
        return text, False
    original = text
    text = CCCD_PATTERN.sub(_mask_cccd, text)
    text = PHONE_PATTERN.sub(_mask_phone, text)
    text = EMAIL_PATTERN.sub(_mask_email, text)
    return text, text != original


def redact_upload_text(text: str) -> dict[str, Any]:
    """Full helper for upload pipelines: analyze + mask + retention metadata."""
    analysis = analyze_pii(text or "")
    masked, changed = mask_pii(text or "")
    return {
        "text": masked if analysis["contains_pii"] else (text or ""),
        "contains_pii": analysis["contains_pii"],
        "pii_types": analysis["types"],
        "pii_counts": analysis["counts"],
        "masked": changed,
        "retention": TEMP_UPLOAD_RETENTION_POLICY,
    }
