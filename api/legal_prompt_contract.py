"""Shared evidence-only prompt contract for live and benchmark generation.

The live API and the offline benchmark must receive the same grounding rules.
They may append different JSON schemas, but neither prompt may ask a model to
invent procedure metadata, forms, URLs, dates, fees, or citations.
"""

from __future__ import annotations

import os
from typing import Any, Mapping


PROMPT_CONTRACT_VERSION = "evidence-packet-v2"
PROMPT_VARIANTS = {"strict-v20", "simple-grounded-v1"}


def live_prompt_variant(environ: Mapping[str, str] | None = None) -> str:
    """Return an explicitly selected DeepSeek prompt variant.

    A/B is opt-in and changes only instructions over the same evidence packet;
    it never selects Qwen or another serving model.
    """

    values = os.environ if environ is None else environ
    candidate = str(values.get("LEGAL_ANSWER_PROMPT_VARIANT", "strict-v20")).strip()
    return candidate if candidate in PROMPT_VARIANTS else "strict-v20"


def build_shared_grounded_prompt(
    *,
    question: str,
    role: str,
    evidence_text: str,
    coverage_text: str = "",
    output_contract: str = "",
    system_addendum: str = "",
    prompt_variant: str = "strict-v20",
    context_label: str = "EVIDENCE PACKET V2",
) -> str:
    """Build the common, model-facing safety contract.

    ``output_contract`` is deliberately supplied by the caller so the live
    structured answer and the benchmark can keep their validators.  All
    factual input still enters through the same evidence-only boundary.
    """

    variant = str(prompt_variant or "strict-v20").strip()
    if variant not in PROMPT_VARIANTS:
        raise ValueError(f"unknown prompt variant: {variant}")
    audience = {
        "citizen": "Ưu tiên kết luận dễ hiểu và việc cần làm tiếp theo.",
        "officer": "Ưu tiên thẩm quyền, quy trình xử lý và điểm cần xác minh.",
        "admin": "Ưu tiên provenance, coverage và trạng thái kiểm chứng.",
    }.get(str(role or "citizen").casefold(), "Trả lời đúng phần được hỏi bằng ngôn ngữ rõ ràng.")
    if variant == "simple-grounded-v1":
        variant_rules = (
            "Trả lời ngắn gọn theo từng issue; nếu thiếu một facet thì giữ phần đã xác minh "
            "và nêu rõ phần chưa thể kiểm chứng."
        )
    else:
        variant_rules = (
            "Giữ ranh giới issue/facet, dùng quote nguyên văn và không để evidence của issue "
            "khác làm căn cứ. Khi evidence bằng 0, trả cannot_verify."
        )
    addendum = " ".join(str(system_addendum or "").split())[:8000]
    addendum_block = (
        "\nHướng dẫn bổ sung đã duyệt (chỉ điều chỉnh cách diễn đạt, không thay evidence):\n"
        + addendum
        if addendum
        else ""
    )
    return f"""PROMPT_CONTRACT_VERSION: {PROMPT_CONTRACT_VERSION}
Vai trò: {role}. {audience}
{addendum_block}
Quy tắc grounding bắt buộc:
1. Chỉ dùng nội dung trong {context_label}; không dùng kiến thức bên ngoài.
2. Không tự tạo số văn bản, điều/khoản, ngày, thời hạn, lệ phí, biểu mẫu, procedure ID, form ID, URL hoặc cơ quan.
3. Mỗi claim phải gắn evidence_id hợp lệ và quote_id/support_quote đúng nguyên văn của cùng evidence và issue.
4. Không biến nhãn coverage, source group, expected/golden label hoặc oracle thành dữ kiện pháp lý.
5. Evidence đủ một phần thì trả phần đã xác minh, đánh dấu phần thiếu; không dùng câu chung chung để lấp chỗ trống.
6. Guidance không phải căn cứ pháp lý. Các facet hồ sơ, thẩm quyền, thời hạn, điều kiện, biểu mẫu, căn cứ, hành động tiếp theo, ngoại lệ và cảnh báo chỉ được nêu khi có evidence.
7. Không đưa citation/form/URL mới vào prose; metadata và biểu mẫu do backend chiếu deterministic.
8. {variant_rules}
9. Chỉ xuất một JSON object đúng output contract bên dưới, không markdown fence.

CÂU HỎI:
{str(question or '').strip()}

COVERAGE:
{coverage_text}

{context_label}:
{evidence_text}

OUTPUT CONTRACT:
{output_contract}
""".strip()


def normalized_prompt_packet(
    *,
    question: str,
    role: str,
    evidence_text: str,
    coverage_text: str = "",
    output_contract: str = "",
    prompt_variant: str = "strict-v20",
    system_addendum: str = "",
) -> dict[str, Any]:
    """Return a small auditable representation used by parity tests."""

    return {
        "prompt_contract_version": PROMPT_CONTRACT_VERSION,
        "prompt_variant": prompt_variant,
        "question": str(question or ""),
        "role": str(role or "citizen"),
        "evidence_text": str(evidence_text or ""),
        "coverage_text": str(coverage_text or ""),
        "output_contract": str(output_contract or ""),
        "system_addendum": str(system_addendum or ""),
    }
