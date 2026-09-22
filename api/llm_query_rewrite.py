"""Selected-model query rewriting for legal retrieval.

The rewriter is deliberately a narrow transport contract.  It may make an
anaphoric or noisy user question standalone, but it is never allowed to
choose a legal source, invent a legal fact, or replace the raw question.  The
caller always keeps the raw query as the first retrieval variant and falls
back to it when the provider is unavailable or the envelope is invalid.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


QUERY_REWRITE_VERSION = "selected-model-query-rewrite-v1"
MAX_QUERY_CHARS = 2000
MAX_VARIANTS = 2


@dataclass(frozen=True)
class QueryRewritePacketV1:
    version: str
    raw_query: str
    standalone_query: str
    variants: tuple[str, ...]
    rewrite_applied: bool
    reason_code: str
    model_id: str | None
    checksum: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "raw_query": self.raw_query,
            "standalone_query": self.standalone_query,
            "variants": list(self.variants),
            "rewrite_applied": self.rewrite_applied,
            "reason_code": self.reason_code,
            "model_id": self.model_id,
            "checksum": self.checksum,
        }


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _checksum(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def raw_query_packet(
    query: str,
    *,
    reason_code: str = "raw_fallback",
    model_id: str | None = None,
) -> QueryRewritePacketV1:
    raw = _compact(query)[:MAX_QUERY_CHARS]
    payload = {
        "version": QUERY_REWRITE_VERSION,
        "raw_query": raw,
        "standalone_query": raw,
        "variants": [raw] if raw else [],
        "rewrite_applied": False,
        "reason_code": reason_code,
        "model_id": model_id,
    }
    return QueryRewritePacketV1(
        version=QUERY_REWRITE_VERSION,
        raw_query=raw,
        standalone_query=raw,
        variants=(raw,) if raw else (),
        rewrite_applied=False,
        reason_code=reason_code,
        model_id=model_id,
        checksum=_checksum(payload),
    )


def should_rewrite_query(query: str) -> bool:
    """Avoid a model call for clearly complete, clean questions.

    The selected model is still used for noisy/short/follow-up questions.  A
    clean legal question remains the raw retrieval query, which saves latency
    and prevents a needless paraphrase from changing the user's meaning.
    """

    text = _compact(query)
    if not text:
        return False
    folded = text.casefold()
    if len(text) <= 180:
        if re.search(
            r"\b(?:hs|gks|gpxd|qđ|qd|ubnd|csh|kn|ttru|ks|đc|dc|ko|k|j)\b",
            folded,
        ):
            return True
        if re.search(
            r"^(?:còn|thế|vậy|trường hợp|cái này|đó|nộp ở đâu|hồ sơ|thời hạn|mẫu nào|ai giải quyết)\b",
            folded,
        ):
            return True
    return any(
        marker in folded
        for marker in (
            "văn bản trên",
            "điều vừa nêu",
            "nguồn này",
            "còn phải",
            "như thế nào",
            "được không",
            "đc k",
        )
    )


def build_query_rewrite_prompt(
    *,
    question: str,
    role: str,
    history_messages: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Build a provider-neutral rewrite prompt with a tiny output contract."""

    user_history: list[str] = []
    for message in reversed(tuple(history_messages)):
        if str(message.get("role") or "").casefold() != "user":
            continue
        content = _compact(message.get("content"))
        if content and content != _compact(question):
            user_history.append(content[:700])
        if len(user_history) >= 3:
            break
    user_history.reverse()
    history_block = "\n".join(
        f"- {item}" for item in user_history
    ) or "(không có lượt trước)"
    return (
        "Bạn chỉ làm nhiệm vụ viết lại truy vấn để tìm kiếm pháp luật Việt Nam.\n"
        "Không trả lời câu hỏi. Không phân loại domain. Không thêm điều luật, "
        "thời hạn, lệ phí, cơ quan, thủ tục, biểu mẫu, URL hoặc dữ kiện mà người "
        "dùng chưa nói. Giữ nguyên số, ngày, tên riêng, địa danh, mã hồ sơ và "
        "số hiệu văn bản. Nếu câu đã đầy đủ, giữ nguyên gần như nguyên văn.\n"
        "Dùng lịch sử chỉ để giải thích đại từ như 'còn', 'đó', 'trường hợp này'. "
        "Lịch sử không phải căn cứ pháp luật.\n\n"
        f"Vai trò: {_compact(role) or 'citizen'}\n"
        "LỊCH SỬ NGƯỜI DÙNG GẦN NHẤT\n"
        f"{history_block}\n\n"
        "CÂU HỎI GỐC\n"
        f"{_compact(question)[:MAX_QUERY_CHARS]}\n\n"
        "Xuất đúng một JSON object, không code fence:\n"
        '{"standalone_query":"...","variants":[]}\n'
        "standalone_query tối đa 2000 ký tự; variants tối đa một câu bổ sung, "
        "chỉ dùng khi diễn đạt tương đương. Nếu không chắc, trả lại câu gốc."
    )


def _protected_tokens(query: str) -> tuple[str, ...]:
    # Preserve identifiers and all numeric/legal references exactly enough to
    # catch a rewrite that silently drops a material user-supplied constraint.
    tokens = re.findall(
        r"(?:\b\d+[./-]?\d*[a-zA-ZÀ-ỹ]*\b|\b[A-ZĐ][A-ZĐ0-9-]{1,}\b)",
        query,
    )
    # Two-letter chat abbreviations (HS, GKS, QĐ, ...) are precisely the
    # tokens this feature is allowed to expand; they are not protected legal
    # identifiers.  Numeric references and longer document/code tokens remain
    # protected so a model cannot silently drop a material constraint.
    expandable = {"hs", "gks", "gpxd", "qđ", "qd", "ct", "ca", "ht", "kn"}
    return tuple(
        dict.fromkeys(
            token.casefold()
            for token in tokens
            if token and token.casefold() not in expandable
        )
    )


def parse_query_rewrite(
    raw_output: Any,
    *,
    question: str,
    model_id: str | None = None,
) -> QueryRewritePacketV1:
    """Parse and validate the model envelope; invalid output falls back raw."""

    raw_query = _compact(question)[:MAX_QUERY_CHARS]
    text = str(raw_output or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return raw_query_packet(raw_query, reason_code="invalid_json", model_id=model_id)
    if not isinstance(payload, Mapping):
        return raw_query_packet(raw_query, reason_code="invalid_payload", model_id=model_id)

    standalone = _compact(payload.get("standalone_query"))[:MAX_QUERY_CHARS]
    if not standalone:
        return raw_query_packet(raw_query, reason_code="missing_standalone_query", model_id=model_id)
    folded_standalone = standalone.casefold()
    if "http://" in folded_standalone or "https://" in folded_standalone:
        return raw_query_packet(raw_query, reason_code="rewrite_added_url", model_id=model_id)
    protected = _protected_tokens(raw_query)
    if any(token not in folded_standalone for token in protected):
        return raw_query_packet(raw_query, reason_code="rewrite_dropped_protected_token", model_id=model_id)

    variants: list[str] = []
    for item in payload.get("variants") or ():
        value = _compact(item)[:MAX_QUERY_CHARS]
        if not value or value.casefold() in {raw_query.casefold(), folded_standalone}:
            continue
        if "http://" in value.casefold() or "https://" in value.casefold():
            continue
        if any(token not in value.casefold() for token in protected):
            continue
        variants.append(value)
        if len(variants) >= MAX_VARIANTS - 1:
            break

    all_variants = tuple(dict.fromkeys((raw_query, standalone, *variants)))
    rewrite_applied = standalone.casefold() != raw_query.casefold()
    result_payload = {
        "version": QUERY_REWRITE_VERSION,
        "raw_query": raw_query,
        "standalone_query": standalone,
        "variants": list(all_variants),
        "rewrite_applied": rewrite_applied,
        "reason_code": "model_rewrite_accepted",
        "model_id": model_id,
    }
    return QueryRewritePacketV1(
        version=QUERY_REWRITE_VERSION,
        raw_query=raw_query,
        standalone_query=standalone,
        variants=all_variants,
        rewrite_applied=rewrite_applied,
        reason_code="model_rewrite_accepted",
        model_id=model_id,
        checksum=_checksum(result_payload),
    )


def is_query_rewrite_enabled(
    role: str = "citizen",
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environ is None else environ
    enabled = str(
        values.get("CHAT_SELECTED_MODEL_QUERY_REWRITE_V1_ENABLED", "true")
    ).strip().casefold()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    roles = {
        item.strip().casefold()
        for item in str(
            values.get("CHAT_SELECTED_MODEL_QUERY_REWRITE_V1_ROLES", "citizen,officer")
        ).split(",")
        if item.strip()
    }
    return str(role or "citizen").strip().casefold() in roles
