"""PII-aware provider egress guard and public-safe provider labels."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit


class ProviderEgressBlocked(RuntimeError):
    pass


_LOCAL = {"local", "ollama", "llamacpp", "llama.cpp", "huggingface-local"}
_PATTERNS = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("citizen_id", re.compile(r"(?<!\d)\d{12}(?!\d)")),
    ("phone", re.compile(r"(?<!\d)(?:\+?84|0)(?:\s*[.-]?\s*\d){9}(?!\d)")),
    (
        "person_name",
        re.compile(r"(?i)(?P<label>\b(?:tôi\s+là|họ\s*(?:và\s*)?tên)\s*[:]?\s*)(?P<value>[^,.;\n]{2,80})"),
    ),
    (
        "address",
        re.compile(r"(?i)(?P<label>\bđịa\s*chỉ\s*[:]?\s*)(?P<value>[^,.;\n]{2,160})"),
    ),
    (
        "credential",
        re.compile(r"(?i)(?P<label>\b(?:api[_ -]?key|mật\s*khẩu|password|bearer)\s*[:=]?\s*)(?P<value>[^\s,;]{4,})"),
    ),
)
_UNSTRUCTURED_SENSITIVE = re.compile(
    r"(?i)\b(?:hồ\s*sơ\s*bệnh\s*án|bí\s*mật\s*cá\s*nhân|dữ\s*liệu\s*sinh\s*trắc)\b"
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_label(value: str, fallback: str) -> str:
    raw = str(value or "").strip()
    if "://" in raw:
        raw = urlsplit(raw).hostname or ""
    raw = raw.split("?", 1)[0].split("#", 1)[0].replace("\\", "/").split("/")[-1]
    raw = re.sub(r"[^A-Za-z0-9_.:-]+", "-", raw).strip("-.")
    return raw[:80] or fallback


def provider_mode(provider: str) -> Literal["local", "cloud"]:
    return "local" if str(provider or "").strip().casefold() in _LOCAL else "cloud"


@dataclass(frozen=True)
class ProviderEgressDecision:
    text: str
    mode: Literal["local", "cloud"]
    provider_label: str
    model_label: str
    redaction_applied: bool
    redaction_categories: tuple[str, ...]
    audit_record: dict[str, Any]


def _redact(text: str) -> tuple[str, tuple[str, ...]]:
    result = text
    categories: list[str] = []
    for category, pattern in _PATTERNS:
        def replacement(match: re.Match[str], *, label=category) -> str:
            if label not in categories:
                categories.append(label)
            prefix = match.groupdict().get("label") or ""
            return f"{prefix}[REDACTED_{label.upper()}]"

        result = pattern.sub(replacement, result)
    return result, tuple(categories)


def prepare_provider_egress(
    text: str,
    *,
    provider: str,
    model: str,
) -> ProviderEgressDecision:
    original = str(text or "")
    mode = provider_mode(provider)
    provider_label = _safe_label(provider, "configured-provider")
    model_label = _safe_label(model, "configured-model")
    if mode == "local":
        return ProviderEgressDecision(
            original,
            mode,
            provider_label,
            model_label,
            False,
            (),
            {
                "event": "provider_local",
                "input_sha256": _hash(original),
                "provider_label": provider_label,
                "model_label": model_label,
                "redaction_count": 0,
            },
        )
    if _UNSTRUCTURED_SENSITIVE.search(original):
        raise ProviderEgressBlocked("pii_redaction_incomplete")
    redacted, categories = _redact(original)
    residual, _ = _redact(redacted)
    if residual != redacted or _UNSTRUCTURED_SENSITIVE.search(redacted):
        raise ProviderEgressBlocked("pii_redaction_incomplete")
    return ProviderEgressDecision(
        redacted,
        mode,
        provider_label,
        model_label,
        redacted != original,
        categories,
        {
            "event": "provider_cloud_egress",
            "input_sha256": _hash(original),
            "output_sha256": _hash(redacted),
            "provider_label": provider_label,
            "model_label": model_label,
            "redaction_categories": list(categories),
            "redaction_count": len(categories),
            "reason_code": "redacted" if redacted != original else "no_detected_pii",
        },
    )

