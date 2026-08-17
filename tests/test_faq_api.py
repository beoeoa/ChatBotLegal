import json
from collections import Counter
from pathlib import Path

from fastapi.testclient import TestClient


def test_seed_has_at_least_fifty_reviewed_faqs_across_five_domains():
    payload = json.loads(Path("notebook_data/faq/faq_seed_haiphong_lechan.json").read_text(encoding="utf-8"))
    assert len(payload) >= 50
    assert all(item.get("review_status") == "approved" for item in payload)
    counts = Counter(item.get("domain") for item in payload)
    assert len(counts) == 5
    assert all(count >= 9 for count in counts.values())
    assert all(item.get("legal_basis") or item.get("guidance_label") for item in payload)


def test_hai_phong_faq_seed_has_no_mojibake_or_replacement_question_marks():
    payload = json.loads(Path("notebook_data/faq/faq_seed_haiphong_lechan.json").read_text(encoding="utf-8"))
    for item in payload:
        question = str(item.get("question") or "")
        assert "?" not in question[:-1]
        assert "?" not in str(item.get("submission_place") or "")
        assert "?" not in str(item.get("guidance_label") or "")
        for value in item.values():
            if isinstance(value, str):
                assert not any(marker in value for marker in ("\u00c3", "\u00c4", "\u00e1\u00bb"))


def test_public_enrichment_only_returns_valid_official_forms(monkeypatch, tmp_path):
    from api.routers import faq

    valid = tmp_path / "data" / "uploads" / "forms" / "priority_official" / "valid.docx"
    valid.parent.mkdir(parents=True)
    valid.write_bytes(b"official-file")
    monkeypatch.setattr(faq, "FORMS_INDEX_FILE", tmp_path / "form-index.json")
    faq.FORMS_INDEX_FILE.write_text(json.dumps({"forms": [
        {"id": "good", "form_title": "M?u h?p l?", "official_level": "official", "review_status": "approved", "local_path": "data/uploads/forms/priority_official/valid.docx"},
        {"id": "pending", "form_title": "M?u ch? duy?t", "official_level": "official", "review_status": "candidate_pending_review", "local_path": "data/uploads/forms/priority_official/valid.docx"},
        {"id": "synthetic", "form_title": "M?u seed", "official_level": "reference", "review_status": "approved", "local_path": "data/uploads/forms/priority_official/valid.docx"},
    ]}), encoding="utf-8")
    # The module resolves paths under repo root. Patch the helper to isolate the
    # eligibility branch; file validation is covered by the form router itself.
    monkeypatch.setattr(faq, "_is_valid_official_form", lambda item: bool(item and item.get("id") == "good"))
    item = faq._enrich_faq_for_public({"id": "faq", "requires_forms": True, "form_ids": ["good", "pending", "synthetic", "extra"]})
    assert [form["id"] for form in item["forms"]] == ["good"]
    assert item["form_ids"] == ["good"]
    assert item["forms_unavailable"] is False


def test_empty_or_non_form_faq_does_not_expose_seed_forms(monkeypatch):
    from api.routers import faq

    item = faq._enrich_faq_for_public({"id": "faq", "requires_forms": False, "form_ids": ["seed-1"]})
    assert item["forms"] == []
    assert item["form_ids"] == []
    assert item["forms_unavailable"] is False

    missing = faq._enrich_faq_for_public({"id": "faq-2", "requires_forms": True, "form_ids": ["seed-1"]})
    assert missing["forms"] == []
    assert missing["forms_unavailable"] is True


def test_faq_form_gate_rejects_technical_quarantine():
    from api.routers import faq

    record = {
        "official_level": "official",
        "review_status": "approved",
        "runtime_eligible": False,
        "is_quarantined": True,
        "local_path": "data/uploads/forms/official/form.pdf",
    }
    assert faq._is_valid_official_form(record) is False


def test_form_limit_is_three(monkeypatch):
    from api.routers import faq

    records = {str(index): {"id": str(index)} for index in range(5)}
    monkeypatch.setattr(faq, "_load_official_form_index", lambda: records)
    monkeypatch.setattr(faq, "_is_valid_official_form", lambda item: True)
    item = faq._enrich_faq_for_public({"id": "faq", "requires_forms": True, "form_ids": list(records)})
    assert len(item["forms"]) == 3
    assert len(item["form_ids"]) == 3



def test_faq_accordion_uses_two_columns_on_desktop():
    component = Path("frontend/src/components/search/FAQAccordion.tsx").read_text(encoding="utf-8")

    assert 'md:grid-cols-2' in component
