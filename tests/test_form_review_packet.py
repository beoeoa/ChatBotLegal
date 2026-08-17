from __future__ import annotations

from api.legal_form_catalog import FormCatalog
from scripts.build_form_review_packet import build_review_packet


def test_review_packet_never_auto_approves_candidates(tmp_path):
    catalog = FormCatalog.load_default()
    packet = build_review_packet(
        catalog,
        write=False,
        report_path=tmp_path / "packet.json",
    )

    assert packet["form_count"] == len(catalog.forms)
    assert packet["auto_approved_count"] == 0
    assert packet["approved_for_runtime_count"] > 0
    assert packet["legal_review_required_count"] == (
        packet["form_count"] - packet["approved_for_runtime_count"]
    )
    assert packet["legal_review_recorded"] is True
    approved = [
        item
        for item in packet["forms"]
        if item["decision"] == "APPROVED_FOR_RUNTIME"
    ]
    assert len(approved) == packet["approved_for_runtime_count"]
    assert all(item["hard_gate_pass"] is True for item in approved)
    assert all(item["runtime_eligible"] is True for item in approved)
    assert all("BINDING_NOT_APPROVED" not in item["missing_gates"] for item in approved)
