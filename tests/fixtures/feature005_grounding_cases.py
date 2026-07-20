"""Sanitized, versioned quality cases for Feature 005.

These fixtures deliberately contain no real person's identity, case file,
credential or unreviewed legal conclusion.  They describe expected evidence
status and source eligibility only.
"""

from __future__ import annotations


QUALITY_CASES = {
    "simple_supported": {
        "question": "Tôi cần biết hồ sơ đăng ký khai sinh gồm những gì.",
        "role": "citizen",
        "expected_issues": 1,
        "expected_statuses": ("sufficiently_evidenced",),
    },
    "land_multi_issue": {
        "question": (
            "Gia đình có đất chưa có giấy chứng nhận, đang có tranh chấp; "
            "UBND phường có thẩm quyền gì, cần chuẩn bị giấy tờ nào và lệ phí bao nhiêu?"
        ),
        "role": "citizen",
        "expected_intents": ("authority", "dossier", "fee", "dispute"),
        "expected_statuses": ("sufficiently_evidenced", "insufficiently_evidenced"),
    },
    "labour_after_land_history": {
        "prior_question": "Người lao động có được nghỉ phép năm không?",
        "question": "Tranh chấp ranh giới thửa đất thì nên liên hệ cơ quan nào?",
        "role": "citizen",
        "forbidden_domains": ("labour", "employment"),
    },
    "expired_source": {
        "question": "Cơ quan nào giải quyết nội dung đất đai này?",
        "role": "citizen",
        "source_overrides": ({"effective_status": "expired"},),
        "expected_statuses": ("insufficiently_evidenced",),
    },
    "conflicting_sources": {
        "question": "Thủ tục địa phương này áp dụng theo văn bản nào?",
        "role": "officer",
        "source_overrides": (
            {"scope": "central", "effective_status": "active"},
            {"scope": "haiphong", "effective_status": "active"},
        ),
        "expected_priority": "central",
    },
    "missing_metadata": {
        "question": "Thời hạn giải quyết hồ sơ là bao lâu?",
        "role": "citizen",
        "source_overrides": ({"effective_status": None, "source_url": None},),
        "expected_statuses": ("insufficiently_evidenced",),
    },
    "cross_account": {
        "owner_id": "test-owner-a",
        "other_id": "test-owner-b",
        "resources": ("answer", "conversation", "evidence"),
        "expected_status": (403, 404),
    },
}

