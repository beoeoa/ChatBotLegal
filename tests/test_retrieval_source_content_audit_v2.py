from __future__ import annotations

from scripts.audit_retrieval_source_content_v2 import (
    _prior_transport,
    classify_html_identity,
    normalize_identity_text,
    summarize_verification,
)


def test_identity_requires_matching_law_number_and_legal_content() -> None:
    html = """
    <html><head><title>Nghị định 01/2020/NĐ-CP</title></head>
    <body><h1>Nghị định 01/2020/NĐ-CP về đăng ký</h1>
    <p>Cơ quan ban hành: Chính phủ</p><p>Điều 1. Phạm vi điều chỉnh.</p></body></html>
    """
    result = classify_html_identity(
        html,
        law_number="01/2020/NĐ-CP",
        title="Nghị định về đăng ký",
        issuing_agency="Chính phủ",
    )

    assert result["law_number_match"] is True
    assert result["legal_content_detected"] is True
    assert result["identity_verified"] is True
    assert result["verification_status"] == "verified"


def test_http_200_soft_404_is_not_verified() -> None:
    result = classify_html_identity(
        "<html><body><h1>Không tìm thấy văn bản</h1><p>Trang bạn yêu cầu không tồn tại.</p></body></html>",
        law_number="01/2020/NĐ-CP",
        title="Nghị định về đăng ký",
        issuing_agency="Chính phủ",
    )

    assert result["soft_404"] is True
    assert result["verification_status"] == "rejected"
    assert result["identity_verified"] is False


def test_identity_mismatch_requires_review() -> None:
    result = classify_html_identity(
        "<html><body><h1>Nghị định 02/2020/NĐ-CP</h1><p>Điều 1. Nội dung khác với văn bản kỳ vọng và có đủ phần mô tả quy định để xác định đây là một trang văn bản pháp luật.</p></body></html>",
        law_number="01/2020/NĐ-CP",
        title="Nghị định về đăng ký",
        issuing_agency="Chính phủ",
    )

    assert result["law_number_match"] is False
    assert result["identity_verified"] is False
    assert result["verification_status"] == "needs_review"


def test_normalization_handles_vietnamese_diacritics_and_spacing() -> None:
    assert normalize_identity_text("Nghị định 01/2020/NĐ-CP") == normalize_identity_text(
        "nghi dinh 01 / 2020 / ND CP"
    )


def test_summary_keeps_transport_and_content_verification_separate() -> None:
    summary = summarize_verification(
        [
            {"transport_status": "http_200", "verification_status": "verified"},
            {"transport_status": "http_200", "verification_status": "rejected"},
            {"transport_status": "http_404", "verification_status": "rejected"},
        ]
    )

    assert summary["transport_status_counts"] == {"http_200": 2, "http_404": 1}
    assert summary["verification_status_counts"] == {"rejected": 2, "verified": 1}
    assert summary["verified_count"] == 1


def test_prior_transport_is_invalidated_when_source_url_changes() -> None:
    prior = {
        "records": [
            {
                "document_id": 123,
                "source_url": "https://vbpl.vn/old",
                "transport_observation": {
                    "transport_status": "http_404",
                    "status_code": 404,
                },
            }
        ]
    }

    assert _prior_transport(prior, 123, "https://vbpl.vn/old")["status_code"] == 404
    assert _prior_transport(prior, 123, "https://vbpl.vn/new") is None
