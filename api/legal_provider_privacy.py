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
        re.compile(r'''(?i)(?P<label>\b(?:tôi\s+là|họ\s*(?:và\s*)?tên)\s*[:]?\s*)(?P<value>(?!\s*(?:gì|ai|nào)\b)[^,.;\n?"{}]{2,80})'''),
    ),
    (
        "address",
        # Only redact an address value when the text after the label looks
        # like a value, not when ``địa chỉ`` is used as a legal predicate
        # (for example: ``địa chỉ thì có làm mất quyền ...?``).  The old
        # expression consumed everything up to punctuation and therefore
        # removed the actual question from the provider prompt.
        re.compile(
            r"(?i)(?P<label>\bđịa\s*chỉ\s*[:]?\s*)"
            # A generic legal subject is not an address value. For example,
            # ``địa chỉ của người tố cáo có phải được giữ bí mật`` discusses
            # the protected field without disclosing it. A real disclosure
            # such as ``địa chỉ của tôi là ...`` is deliberately not exempt.
            r'''(?P<value>(?!(?:\s*(?:thì|có|là|không|chưa|được|phải|này|đó|nào|ở\s+đâu))\b)(?!\s*(?:của\s+)?(?:người\s+(?:tố\s+cáo|bị\s+tố\s+cáo|khiếu\s+nại|được\s+bảo\s+vệ)|công\s+dân|cá\s+nhân|tổ\s+chức)\s+(?:có\s+phải|có|phải|được|cần|thì)\b)[^,.;\n?"{}]{2,160})'''
        ),
    ),
    (
        "credential",
        re.compile(r"(?i)(?P<label>\b(?:api[_ -]?key|mật\s*khẩu|password|bearer)\s*[:=]?\s*)(?P<value>[^\s,;]{4,})"),
    ),
)
_UNSTRUCTURED_SENSITIVE = re.compile(
    r"(?i)\b(?:hồ\s*sơ\s*bệnh\s*án|bí\s*mật\s*cá\s*nhân|dữ\s*liệu\s*sinh\s*trắc)\b"
)


def _has_sensitive_disclosure(text: str) -> bool:
    """Distinguish discussing protected data from supplying its contents.

    Only recognizable general questions/statements are exempt. A labelled
    value, first-person disclosure, or unclassified free text stays blocked.
    Inspect each occurrence so a harmless question cannot whitelist a payload.
    """
    for match in _UNSTRUCTURED_SENSITIVE.finditer(text):
        start = max(text.rfind(char, 0, match.start()) for char in '.!?\n"') + 1
        ends = [text.find(char, match.end()) for char in '.!?\n"']
        end = min((pos for pos in ends if pos >= 0), default=len(text))
        sentence = text[start:end].casefold()
        suffix = text[match.end():end].casefold()
        # A heading such as ``Dữ liệu sinh trắc học sau:`` contains no
        # disclosure. Fail closed only when the delimiter is followed by an
        # actual value in the same sentence.
        if re.search(r"[:=]\s*\S", suffix):
            return True
        if re.search(r"\b(?:của tôi|của bệnh nhân|chẩn đoán|kết quả xét nghiệm)\b", sentence):
            return True
        # Merely mentioning a sensitive record in a legal question, an
        # official checklist or a previous assistant answer is not a data
        # disclosure. Unknown prose is allowed only after the concrete
        # disclosure signals above have been ruled out; structured PII is
        # still handled by `_PATTERNS` and labelled/free-text values stay
        # fail-closed through the colon/possession/diagnosis checks.
    return False


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
            # The residual-safety pass calls ``_redact`` a second time. Named
            # patterns such as ``tôi là …`` and ``địa chỉ: …`` also match the
            # placeholder produced by the first pass unless it is explicitly
            # treated as terminal. Without this guard, any valid name/address
            # redaction is incorrectly classified as incomplete and a long
            # cloud conversation can never reach DeepSeek.
            matched_value = str(match.groupdict().get("value") or "").strip()
            if matched_value.startswith("[REDACTED_"):
                return match.group(0)
            if label not in categories:
                categories.append(label)
            prefix = match.groupdict().get("label") or ""
            return f"{prefix}[REDACTED_{label.upper()}]"

        result = pattern.sub(replacement, result)
    return result, tuple(categories)


def render_private_placeholders(text: str, user_messages: list[str]) -> str:
    """Restore an unambiguous conversational name locally, never credentials.

    Only user-authored messages in this conversation are eligible. Multiple
    possible names remain generic rather than assigning the wrong identity.
    No restored value is sent to the provider or added to provider audit logs.
    """
    pattern = dict(_PATTERNS)["person_name"]
    names = {re.sub(r"\s+(?:nhé|nha|ạ|nhá)$", "", m.group("value").strip(), flags=re.I) for message in user_messages
             for m in pattern.finditer(message)
             if not m.group("value").strip().startswith("[REDACTED_")}
    name = next(iter(names)) if len(names) == 1 else "bạn"
    result = text.replace("[REDACTED_PERSON_NAME]", name)
    return re.sub(r"\[REDACTED_[A-Z_]+\]", "[thông tin đã ẩn]", result)


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
    if _has_sensitive_disclosure(original):
        raise ProviderEgressBlocked("pii_redaction_incomplete")
    redacted, categories = _redact(original)
    residual, _ = _redact(redacted)
    if residual != redacted or _has_sensitive_disclosure(redacted):
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
