from api.legal_form_evidence import build_form_evidence_rows
from api.legal_section_grounding import LegalIssue


def test_approved_forms_become_issue_bound_evidence_before_generation():
    issue = LegalIssue(
        request_id="request-form-v2",
        issue_id="issue-1",
        title="Biểu mẫu",
        query_text="Cho tôi biểu mẫu đăng ký khai sinh",
        intent="form",
        domain="civil_status",
        split_confidence="high",
    )
    rows = build_form_evidence_rows(
        request_id="request-form-v2",
        issue=issue,
        procedure_id="dang_ky_khai_sinh",
        forms=[
            {
                "form_id": "form-1",
                "procedure_id": "dang_ky_khai_sinh",
                "name": "Tờ khai đăng ký khai sinh",
                "required_or_conditional": "required",
                "download_url": "https://cdn.haiphong.gov.vn/form.pdf",
                "source_url": "https://haiphong.gov.vn/procedure",
                "effective_from": "2025-07-01",
                "effective_to": None,
                "review_status": "approved",
                "has_official_file": True,
            }
        ],
        legal_as_of="2026-08-11",
    )

    assert len(rows) == 1
    assert rows[0]["request_id"] == issue.request_id
    assert rows[0]["issue_id"] == issue.issue_id
    assert rows[0]["supported_facets"] == ["form"]
    assert rows[0]["form_binding_verified"] is True
    assert "Tờ khai đăng ký khai sinh" in rows[0]["content"]


def test_form_evidence_builder_fails_closed_for_unapproved_or_wrong_binding():
    issue = LegalIssue(
        request_id="request-form-v2",
        issue_id="issue-1",
        title="Biểu mẫu",
        query_text="Biểu mẫu",
        intent="form",
        domain="civil_status",
        split_confidence="high",
    )
    rows = build_form_evidence_rows(
        request_id="request-form-v2",
        issue=issue,
        procedure_id="dang_ky_khai_sinh",
        forms=[
            {
                "form_id": "bad-1",
                "procedure_id": "thu_tuc_khac",
                "name": "Sai thủ tục",
                "review_status": "approved",
                "has_official_file": True,
                "download_url": "https://haiphong.gov.vn/bad.pdf",
                "source_url": "https://haiphong.gov.vn/bad",
            },
            {
                "form_id": "bad-2",
                "procedure_id": "dang_ky_khai_sinh",
                "name": "Chưa duyệt",
                "review_status": "pending",
                "has_official_file": True,
                "download_url": "https://haiphong.gov.vn/pending.pdf",
                "source_url": "https://haiphong.gov.vn/pending",
            },
        ],
        legal_as_of="2026-08-11",
    )

    assert rows == []


def test_released_feature017_eform_is_evidence_without_a_fake_file_flag():
    issue = LegalIssue(
        request_id="request-eform-v17",
        issue_id="issue-eform",
        title="Biểu mẫu điện tử",
        query_text="Xin e-form cư trú",
        intent="form",
        domain="cu_tru_an_ninh",
        split_confidence="high",
    )
    rows = build_form_evidence_rows(
        request_id=issue.request_id,
        issue=issue,
        procedure_id="proc-eform",
        forms=[{
            "form_id": "eform-1",
            "procedure_id": "proc-eform",
            "name": "Biểu mẫu điện tử cư trú",
            "required_or_conditional": "required",
            "download_url": "https://dichvucong.gov.vn/eform-1",
            "source_url": "https://dichvucong.gov.vn/eform-1",
            "effective_from": "2026-08-11",
            "review_status": "approved",
            "asset_kind": "eform",
            "has_official_resource": True,
            "source_checksum": "a" * 64,
        }],
        legal_as_of="2026-08-11",
    )
    assert len(rows) == 1
    assert rows[0]["asset_kind"] == "eform"
    assert rows[0]["source_checksum"] == "a" * 64


def test_released_feature017_file_accepts_only_its_checksum_gated_runtime_route():
    issue = LegalIssue(
        request_id="request-file-v17",
        issue_id="issue-file",
        title="Biểu mẫu",
        query_text="Xin mẫu khai báo tạm trú",
        intent="form",
        domain="cu_tru_an_ninh",
        split_confidence="high",
    )
    form_id = "form-three-tier-d33d90ef7bd83b657b37968a"
    rows = build_form_evidence_rows(
        request_id=issue.request_id,
        issue=issue,
        procedure_id="1.000253",
        forms=[{
            "form_id": form_id,
            "procedure_id": "1.000253",
            "name": "Phiếu khai báo tạm trú NA17",
            "required_or_conditional": "required",
            "download_url": f"/api/procedures/forms-catalog/assets/{form_id}/download",
            "source_url": "https://vbpl.vn/van-ban/chi-tiet/thong-tu-04-2015-tt-bca",
            "effective_from": "2015-02-25",
            "review_status": "approved",
            "asset_kind": "file",
            "has_official_resource": True,
            "has_official_file": True,
            "source_checksum": "b" * 64,
        }],
        legal_as_of="2026-08-11",
    )
    assert len(rows) == 1
    assert rows[0]["download_url"].endswith(f"/{form_id}/download")

    forged = build_form_evidence_rows(
        request_id=issue.request_id,
        issue=issue,
        procedure_id="1.000253",
        forms=[{
            "form_id": form_id,
            "procedure_id": "1.000253",
            "name": "Phiếu khai báo tạm trú NA17",
            "required_or_conditional": "required",
            "download_url": "/api/procedures/forms-catalog/assets/other/download",
            "source_url": "https://vbpl.vn/van-ban/chi-tiet/thong-tu-04-2015-tt-bca",
            "effective_from": "2015-02-25",
            "review_status": "approved",
            "asset_kind": "file",
            "has_official_resource": True,
            "source_checksum": "b" * 64,
        }],
        legal_as_of="2026-08-11",
    )
    assert forged == []
