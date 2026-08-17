from api.legal_answer_completeness import assess_answer_completeness
from api.models import AskResponse


QUESTION = (
    "Theo Điều 5 của văn bản 12/2024/NĐ-CP, hãy trình bày lần lượt "
    "các nội dung chính và nêu ý nghĩa thực tế bằng lời dễ hiểu cho người dân."
)
SOURCE = """1. Cơ quan tiếp nhận phải kiểm tra hồ sơ ngay khi nhận.
a) Nếu hồ sơ đầy đủ thì cấp giấy tiếp nhận.
b) Nếu hồ sơ thiếu thì hướng dẫn bổ sung bằng văn bản.
2. Kết quả phải được cập nhật vào hệ thống điện tử và thông báo cho người nộp."""
SOURCES = [
    {
        "law_number": "12/2024/NĐ-CP",
        "article_number": "5",
        "parent_context": SOURCE,
        "parent_context_truncated": False,
    }
]
COMPLETE_ANSWER = """1. Cơ quan tiếp nhận kiểm tra hồ sơ ngay khi nhận.
- Hồ sơ đầy đủ: cấp giấy tiếp nhận.
- Hồ sơ thiếu: hướng dẫn bổ sung bằng văn bản.
2. Kết quả được cập nhật lên hệ thống điện tử và thông báo cho người nộp.

Hiểu đơn giản, người dân cần giữ giấy tiếp nhận và theo dõi thông báo. Ý nghĩa
thực tế là giúp biết hồ sơ đang được xử lý hay cần bổ sung."""


def test_complete_requires_coverage_order_plain_language_and_practical_meaning():
    result = assess_answer_completeness(
        question=QUESTION,
        answer=COMPLETE_ANSWER,
        sources=SOURCES,
    )

    assert result["status"] == "complete"
    assert result["coverage_ratio"] == 1.0
    assert result["source_unit_count"] == 4
    assert result["covered_unit_count"] == 4
    assert result["checks"] == {
        "coverage": True,
        "order": True,
        "plain_language": True,
        "practical_meaning": True,
    }


def test_one_grounded_excerpt_is_not_a_complete_answer():
    result = assess_answer_completeness(
        question=QUESTION,
        answer="Cơ quan tiếp nhận phải kiểm tra hồ sơ ngay khi nhận.",
        sources=SOURCES,
    )

    assert result["status"] == "incomplete"
    assert result["coverage_ratio"] < 0.9
    assert "missing_coverage" in result["reason_codes"]
    assert "missing_plain_language" in result["reason_codes"]
    assert "missing_practical_meaning" in result["reason_codes"]


def test_full_content_in_the_wrong_order_is_not_complete():
    answer = """2. Kết quả được cập nhật vào hệ thống điện tử và thông báo cho người nộp.
1. Cơ quan tiếp nhận kiểm tra hồ sơ ngay khi nhận.
- Hồ sơ đầy đủ thì cấp giấy tiếp nhận.
- Hồ sơ thiếu thì hướng dẫn bổ sung bằng văn bản.
Hiểu đơn giản, người dân cần theo dõi thông báo. Ý nghĩa thực tế là biết khi nào cần bổ sung."""

    result = assess_answer_completeness(
        question=QUESTION,
        answer=answer,
        sources=SOURCES,
    )

    assert result["coverage_ratio"] == 1.0
    assert result["checks"]["order"] is False
    assert result["status"] == "incomplete"


def test_missing_requested_explanation_and_practical_meaning_is_not_complete():
    answer = COMPLETE_ANSWER.split("Hiểu đơn giản", 1)[0]

    result = assess_answer_completeness(
        question=QUESTION,
        answer=answer,
        sources=SOURCES,
    )

    assert result["checks"]["coverage"] is True
    assert result["checks"]["plain_language"] is False
    assert result["checks"]["practical_meaning"] is False
    assert result["status"] == "incomplete"


def test_truncated_parent_never_proves_full_structural_coverage():
    result = assess_answer_completeness(
        question=QUESTION,
        answer=COMPLETE_ANSWER,
        sources=[{**SOURCES[0], "parent_context_truncated": True}],
    )

    assert result["checks"]["coverage"] is False
    assert "source_structure_unproven" in result["reason_codes"]
    assert result["status"] == "incomplete"


def test_grounding_and_completeness_are_independent_response_fields():
    response = AskResponse(
        question="Câu hỏi",
        answer="Một đoạn trích có nguồn.",
        grounding_status="fully_grounded",
        answer_completeness={
            "status": "incomplete",
            "coverage_ratio": 0.25,
            "source_unit_count": 4,
            "covered_unit_count": 1,
            "required_checks": ["coverage"],
            "checks": {"coverage": False},
            "reason_codes": ["missing_coverage"],
        },
    )

    assert response.grounding_status == "fully_grounded"
    assert response.answer_completeness.status == "incomplete"

