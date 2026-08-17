"""Sanitized form-completion cases for Feature 006 tests.

All identities and URLs are synthetic.  No record represents a legal approval.
"""

from __future__ import annotations


FORM_COMPLETION_CASES = [
    {
        "identity_id": "exact-active",
        "identity_status": "CONFIRMED_CODE_AND_INSTRUMENT",
        "form_code": "M01",
        "issuing_instrument": "01/2026/TT-TEST",
        "procedure_ids": ["test-procedure-01", "test-procedure-02"],
        "delivery_type": "paper_or_file",
        "expected": "candidate_pending_review",
    },
    {
        "identity_id": "missing-instrument",
        "identity_status": "CODE_PENDING_INSTRUMENT",
        "form_code": "M02",
        "issuing_instrument": None,
        "procedure_ids": ["test-procedure-03"],
        "delivery_type": "paper_or_file",
        "expected": "verified_data_gap",
    },
    {
        "identity_id": "missing-code",
        "identity_status": "NAMED_FORM_PENDING_CODE",
        "form_code": None,
        "issuing_instrument": "02/2026/TT-TEST",
        "procedure_ids": ["test-procedure-04"],
        "delivery_type": "paper_or_file",
        "expected": "verified_data_gap",
    },
    {
        "identity_id": "codeless",
        "identity_status": "NAME_PENDING_IDENTITY",
        "form_code": None,
        "issuing_instrument": None,
        "procedure_ids": ["test-procedure-05"],
        "delivery_type": "paper_or_file",
        "expected": "verified_data_gap",
    },
    {
        "identity_id": "official-eform",
        "identity_status": "OFFICIAL_EFORM_IDENTITY",
        "form_code": None,
        "issuing_instrument": None,
        "procedure_ids": ["test-procedure-06"],
        "delivery_type": "interactive_eform",
        "official_source_pages": [
            "https://dichvucong.gov.vn/thu-tuc-hanh-chinh/test-procedure-06"
        ],
        "expected": "candidate_pending_review",
    },
    {
        "identity_id": "expired-form",
        "identity_status": "CONFIRMED_CODE_AND_INSTRUMENT",
        "form_code": "M03",
        "issuing_instrument": "03/2020/TT-TEST",
        "procedure_ids": ["test-procedure-07"],
        "delivery_type": "paper_or_file",
        "expected": "expired",
    },
    {
        "identity_id": "blocked-source",
        "identity_status": "CONFIRMED_CODE_AND_INSTRUMENT",
        "form_code": "M04",
        "issuing_instrument": "04/2026/TT-TEST",
        "procedure_ids": ["test-procedure-08"],
        "delivery_type": "paper_or_file",
        "expected": "blocked_external",
    },
]

