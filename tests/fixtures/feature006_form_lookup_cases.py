"""Sanitized deterministic traps for Feature 006 form lookup.

All procedure IDs, instruments and URLs are synthetic.  These fixtures test
resolver behavior only and do not represent a legal approval.
"""

from __future__ import annotations


def _procedure(procedure_id: str, name: str) -> dict:
    return {
        "procedure_id": procedure_id,
        "name": name,
        "aliases": [name],
        "domain": "fixture_domain",
        "review_status": "approved",
    }


def _form(
    form_id: str,
    procedure_id: str,
    code: str,
    *,
    instrument: str,
    effective_to: str | None = None,
    eform: bool = False,
) -> dict:
    return {
        "form_id": form_id,
        "procedure_ids": [procedure_id],
        "form_code": code,
        "canonical_name": f"Biểu mẫu {code} theo {instrument}",
        "aliases": [f"Mẫu {code}"],
        "audience": "citizen",
        "usage": "applicant_form",
        "required_or_conditional": "required",
        "domain": "fixture_domain",
        "jurisdiction": "national",
        "official_source_page": (
            f"https://dichvucong.gov.vn/thu-tuc-hanh-chinh/{procedure_id}"
        ),
        "official_download_url": (
            f"https://dichvucong.gov.vn/eform/{form_id}"
            if eform
            else f"https://dichvucong.gov.vn/files/{form_id}.pdf"
        ),
        "local_path": None,
        "file_format": "online" if eform else "pdf",
        "sha256": None,
        "legal_basis": [instrument],
        "effective_from": "2026-01-01",
        "effective_to": effective_to,
        "supersedes_form_id": None,
        "source_classification": "official_eform" if eform else "official_file",
        "review_status": "approved",
        "approved": True,
        "runtime_eligible": True,
        "provenance": {
            "source_url": (
                f"https://dichvucong.gov.vn/thu-tuc-hanh-chinh/{procedure_id}"
            )
        },
        "url_status": "verified",
    }


PROCEDURES = [
    _procedure("fixture_wrong_a", "Thủ tục kiểm tra văn bản A"),
    _procedure("fixture_wrong_b", "Thủ tục kiểm tra văn bản B"),
    _procedure("fixture_expired", "Thủ tục kiểm tra hiệu lực"),
    _procedure("fixture_multi", "Thủ tục cần nhiều biểu mẫu"),
    _procedure("fixture_eform", "Thủ tục dùng biểu mẫu điện tử"),
]

FORMS = [
    _form("wrong-a-01", "fixture_wrong_a", "01", instrument="01/2026/TT-TEST"),
    _form("wrong-b-01", "fixture_wrong_b", "01", instrument="02/2026/TT-TEST"),
    _form(
        "expired-01",
        "fixture_expired",
        "01",
        instrument="03/2024/TT-TEST",
        effective_to="2025-12-31",
    ),
    _form("current-02", "fixture_expired", "02", instrument="04/2026/TT-TEST"),
    _form("multi-01", "fixture_multi", "01", instrument="05/2026/TT-TEST"),
    _form("multi-02", "fixture_multi", "02", instrument="05/2026/TT-TEST"),
    _form(
        "eform-online",
        "fixture_eform",
        "EF01",
        instrument="06/2026/TT-TEST",
        eform=True,
    ),
]

BINDINGS = [
    {
        "procedure_id": procedure_id,
        "form_id": form["form_id"],
        "binding_status": "approved",
        "review_status": "approved",
        "approved": True,
    }
    for form in FORMS
    for procedure_id in form["procedure_ids"]
]

FORM_LOOKUP_TRAPS = [
    {
        "trap": "wrong_instrument",
        "procedure_id": "fixture_wrong_a",
        "query": "Thủ tục kiểm tra văn bản A cần Mẫu 01 nào?",
        "expected_form_ids": ["wrong-a-01"],
        "rejected_form_ids": ["wrong-b-01"],
    },
    {
        "trap": "repeated_code",
        "procedure_id": "fixture_wrong_b",
        "query": "Thủ tục kiểm tra văn bản B cần Mẫu 01 nào?",
        "expected_form_ids": ["wrong-b-01"],
        "rejected_form_ids": ["wrong-a-01"],
    },
    {
        "trap": "expired",
        "procedure_id": "fixture_expired",
        "query": "Thủ tục kiểm tra hiệu lực cần biểu mẫu nào?",
        "expected_form_ids": ["current-02"],
        "expected_rejection_reason": "FORM_EXPIRED",
    },
    {
        "trap": "multi_form",
        "procedure_id": "fixture_multi",
        "query": "Thủ tục cần nhiều biểu mẫu gồm những mẫu nào?",
        "expected_form_ids": ["multi-01", "multi-02"],
    },
    {
        "trap": "official_eform",
        "procedure_id": "fixture_eform",
        "query": "Thủ tục dùng biểu mẫu điện tử mở e-form nào?",
        "expected_form_ids": ["eform-online"],
    },
]
