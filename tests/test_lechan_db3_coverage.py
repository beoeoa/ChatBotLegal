from scripts.compare_lechan_shadow_coverage import (
    corpus_law_number_matches,
    expected_law_number,
    form_hard_gate,
    provision_number,
    review_approval_state,
    strict_form_match,
)
from scripts.verify_lechan_db3_coverage import _legal_review_receipt_state


def test_provision_number_accepts_only_explicit_article_locator():
    assert provision_number("Điều 13") == "13"
    assert provision_number("Nghị định 13/2020/NĐ-CP") is None
    assert expected_law_number("Luật Cư trú 2020") == "68/2020/QH14"
    documents = {
        1: {"law_number": "60/2021/TT-BCA"},
        2: {"law_number": "60/2021/TT-BTP"},
    }
    assert corpus_law_number_matches("60/2021", documents) == 2


def test_form_hard_gate_requires_official_provenance_file_and_effectivity(tmp_path):
    local_file = tmp_path / "form.pdf"
    local_file.write_bytes(b"official")
    record = {
        "review_status": "approved",
        "legal_status": "admin_reviewed",
        "source_page_url": "https://phulien.haiphong.gov.vn/form",
        "local_path": str(local_file),
        "domain": "ho_tich_chung_thuc",
        "title_quality": "usable",
    }
    assert form_hard_gate(record, root=tmp_path)[0] is True
    record["legal_status"] = "needs_admin_effectivity_review"
    passed, reasons = form_hard_gate(record, root=tmp_path)
    assert passed is False
    assert "effectivity_not_approved" in reasons


def test_strict_form_match_rejects_corrupt_procedure_mapping():
    expected = {
        "procedure_id": "dang_ky_khai_sinh",
        "form_title": "Tờ khai đăng ký khai sinh",
        "domain": "ho_tich_chung_thuc",
    }
    corrupt = {
        "procedure_id": "dang_ky_khai_sinh",
        "form_title": "Thủ tục lĩnh vực việc làm",
        "domain": "cu_tru",
    }
    assert strict_form_match(expected, corrupt)[0] is False


def test_legal_review_requires_all_records_approved_and_named():
    records = [
        {
            "expert_review_status": "approved",
            "expert_name": "Reviewer A",
            "reviewed_at": "2026-07-23T00:00:00Z",
        }
    ]
    assert review_approval_state(records)["approved"] is True
    records[0]["expert_review_status"] = "pending"
    assert review_approval_state(records)["approved"] is False


def test_legal_review_receipt_approves_immutable_packet(tmp_path):
    import hashlib

    packet = tmp_path / "legal-review-packet.json"
    packet.write_text('{"status":"pending"}', encoding="utf-8")
    receipt = {
        "status": "approved",
        "reviewer_name": "project-owner",
        "reviewed_at": "2026-07-23T12:00:00Z",
        "packet_sha256": hashlib.sha256(packet.read_bytes()).hexdigest(),
    }
    assert _legal_review_receipt_state(receipt, packet) is True
