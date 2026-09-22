"""Owner-frozen Retrieval r26 policies for production serving.

The functions in this module are query-only/ranking-only and never inspect
Golden labels, expected sources or holdout data. They mirror the checksum-bound
Kaggle v6r26 worker that was accepted by the owner on 2026-08-22.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from api.retrieval_candidate_r21 import diversify_legal_identities
from api.retrieval_candidate_r22 import expand_legal_query_r22


R26_CANDIDATE_PROFILE = "r26-shadow-facet-alias"

R26_QUERY_ALIASES: tuple[tuple[str, str], ...] = (
    ("cải chính ngày tháng năm sinh", "thay đổi cải chính hộ tịch ngày tháng năm sinh"),
    ("trẻ bị bỏ rơi", "đăng ký khai sinh cho trẻ bị bỏ rơi"),
    ("xác nhận tình trạng hôn nhân để đăng ký kết hôn", "cấp Giấy xác nhận tình trạng hôn nhân"),
    ("đăng ký kết hôn lại khi giấy chứng nhận bị mất", "đăng ký lại kết hôn"),
    ("đăng ký quyền sở hữu nhà ở gắn liền với đất", "đăng ký tài sản gắn liền với đất"),
    ("cấp đổi giấy chứng nhận quyền sử dụng đất bị rách", "đăng ký biến động cấp đổi Giấy chứng nhận"),
    ("thay đổi thông tin trên sổ đỏ", "đăng ký biến động đất đai tài sản gắn liền với đất"),
    ("khai báo lưu trú cho khách qua đêm", "thông báo lưu trú"),
    ("đăng ký tạm trú trực tuyến", "đăng ký tạm trú qua dịch vụ công trực tuyến"),
    ("khiếu nại lần đầu gửi", "thẩm quyền giải quyết khiếu nại lần đầu"),
    ("hồ sơ khiếu nại quyết định xử phạt", "đơn khiếu nại quyết định xử phạt vi phạm hành chính"),
)


def expand_legal_query_r26(
    query: str, *, aliases: Sequence[tuple[str, str]]
) -> str:
    """Apply the frozen base aliases followed by r26 procedure-facet aliases."""

    return expand_legal_query_r22(
        query,
        aliases=(*aliases, *R26_QUERY_ALIASES),
    )


def diversify_r26_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the exact v6r26 post-fusion diversity sequence: 20 then 60."""

    first = diversify_legal_identities(candidates, window=20, slots=10)
    return diversify_legal_identities(first, window=60, slots=10)


__all__ = [
    "R26_CANDIDATE_PROFILE",
    "R26_QUERY_ALIASES",
    "diversify_r26_candidates",
    "expand_legal_query_r26",
]
