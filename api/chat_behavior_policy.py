"""Shared conversational behavior contract for all chat answer prompts.

The policy describes observable behavior, not hidden chain-of-thought.  Legal
grounding, authorization, effectivity and citation checks remain owned by the
backend and always outrank this presentation layer.
"""

from __future__ import annotations

import os
import re
from typing import Mapping

_TRUE_VALUES = {"1", "true", "yes", "on"}

_ADMIN_STYLE_MARKERS = (
    "văn phong", "giọng", "xưng hô", "câu ngắn", "ngắn gọn", "độ dài",
    "dễ hiểu", "rõ ràng", "thân thiện", "chuyên nghiệp", "ấm áp",
    "hài hước", "trình bày", "diễn đạt", "bố cục", "gạch đầu dòng",
    "từng bước", "markdown", "đoạn văn", "tiếng việt",
)
_ADMIN_NON_STYLE_MARKERS = (
    "facet", "evidence", "nguồn", "trích dẫn", "citation", "điều luật",
    "hiệu lực", "lệ phí", "thẩm quyền", "hồ sơ", "thời hạn", "biểu mẫu",
    "bỏ qua", "không cần kiểm tra", "system prompt", "router", "pipeline",
)


def compile_admin_style(value: str | None, *, max_chars: int = 1200) -> dict:
    """Compile legacy text once and expose every omitted segment to Admin.

    Newlines are boundaries before whitespace normalization. Runtime and preview
    use this exact compiler; no additional prompt filtering happens downstream.
    """
    raw = str(value or "").strip()
    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    used = 0
    for segment in re.split(r"(?<=[.!?])\s+|[;\n]+", raw):
        clean = " ".join(segment.strip(" \t-•").split())
        if not clean:
            continue
        folded = clean.casefold()
        reason = None
        if any(marker in folded for marker in _ADMIN_NON_STYLE_MARKERS):
            reason = "outside_presentation_scope"
        elif not any(marker in folded for marker in _ADMIN_STYLE_MARKERS):
            reason = "no_presentation_field"
        elif used + len(clean) + (2 if accepted else 0) > max_chars:
            reason = "style_length_limit"
        if reason:
            rejected.append({"text": clean, "reason": reason})
        else:
            accepted.append(clean)
            used += len(clean) + (2 if len(accepted)>1 else 0)
    return {"text": "; ".join(accepted), "rejected": rejected, "compiler_revision": "admin-style-v2"}


def admin_style_addendum(value: str | None, *, max_chars: int = 1200) -> str:
    return compile_admin_style(value, max_chars=max_chars)["text"]


def render_admin_style_addendum(value: str | None) -> str:
    clean = admin_style_addendum(value)
    if not clean:
        return ""
    return (
        "\n\nHƯỚNG DẪN PHONG CÁCH DO ADMIN CẤU HÌNH\n"
        f"{clean}\n"
        "Chỉ điều chỉnh giọng điệu, cách xưng hô, độ dài và cách trình bày. "
        "Không tạo thêm nội dung pháp lý hoặc thay đổi nguồn, hiệu lực và trích dẫn."
    )


def behavior_policy_enabled(
    role: str = "citizen",
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the modern conversational behavior contract is active."""

    values = os.environ if environ is None else environ
    enabled = str(values.get("CHAT_BEHAVIOR_POLICY_V1_ENABLED", "true")).strip().casefold()
    if enabled not in _TRUE_VALUES:
        return False
    roles = {
        item.strip().casefold()
        for item in str(
            values.get("CHAT_BEHAVIOR_POLICY_V1_ROLES", "citizen,officer")
        ).split(",")
        if item.strip()
    }
    normalized = str(role or "citizen").strip().casefold()
    return normalized in roles or normalized.startswith("officer_") and "officer" in roles


def render_behavior_policy(
    *,
    role: str,
    route: str,
    answer_depth: str = "balanced",
    natural_chat: bool = False,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Render a bounded, provider-neutral behavior contract for a prompt."""

    if not behavior_policy_enabled(role, environ=environ):
        return ""
    depth = str(answer_depth or "balanced").strip().casefold()
    depth_instruction = {
        "quick": "Ưu tiên đáp án trực tiếp; tự kiểm tra ngắn trước khi gửi.",
        "deep": "Dành thêm thời gian kiểm tra giả định, ngoại lệ và phản ví dụ trước khi gửi.",
    }.get(depth, "Kiểm tra đủ các giả định và điều kiện quan trọng trước khi gửi.")
    seriousness = (
        "Có thể dùng một câu đùa rất nhẹ nếu người dùng đang nói chuyện đời thường; "
        "không đùa trong chuyện pháp lý nghiêm trọng, khiếu nại, mất mát, tai nạn, "
        "xử phạt hoặc khi người dùng đang bực/buồn."
        if natural_chat
        else "Không chèn hài hước vào kết luận pháp lý; chỉ giữ giọng thân thiện, có hồn và dễ đọc."
    )
    return (
        "\n\nHỢP ĐỒNG HÀNH VI HỘI THOẠI V1\n"
        "- Hiểu mục đích thật của người dùng, các đại từ và mạch hội thoại trước khi trả lời.\n"
        "- Tự kiểm tra âm thầm theo bốn bước: (1) người dùng đang cần gì, "
        "(2) dữ kiện nào là người dùng nói, (3) nguồn nào thực sự chứng minh, "
        "(4) giả định hoặc phản ví dụ nào có thể làm kết luận sai. Không in chain-of-thought.\n"
        "- Phản biện lịch sự: nếu tiền đề của người dùng chưa đủ, nhầm khái niệm hoặc có hai cách hiểu, "
        "nói rõ điểm vướng, đưa các nhánh có căn cứ và hỏi đúng một thông tin cần thiết. "
        "Không đồng ý cho xong và không tranh luận khi không cần.\n"
        "- Tách rõ sự thật đã biết, điều chưa chắc và đề xuất bước tiếp theo. Không biến suy luận thành quy định pháp luật.\n"
        "- Nếu chính mình vừa trả lời sai, thiếu hoặc hiểu nhầm, nhận lỗi ngắn gọn, xin lỗi tự nhiên, "
        "nói phần nào được sửa và trả lại câu trả lời đã kiểm tra. Không xin lỗi lặp lại để né câu hỏi.\n"
        "- Giữ một tính cách nhất quán: ấm áp, tỉnh táo, tôn trọng, đôi lúc dí dỏm đúng ngữ cảnh; "
        "không dùng lời tâng bốc, khẩu hiệu hoặc giọng máy móc.\n"
        f"- {seriousness}\n"
        f"- {depth_instruction}\n"
        "- Hành vi trên chỉ điều chỉnh cách suy nghĩ và diễn đạt; route, quyền truy cập, hiệu lực, "
        "truy xuất, citation và giới hạn pháp lý do backend quyết định."
    )


__all__ = [
    "admin_style_addendum",
    "behavior_policy_enabled",
    "render_admin_style_addendum",
    "render_behavior_policy",
]
