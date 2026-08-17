from api.routers.search import _build_form_provenance_trace


def test_form_trace_accepts_only_downloadable_approved_official_file():
    question = "Cho tôi tải biểu mẫu đăng ký khai sinh"
    raw_forms = [
        {
            "form_id": "approved-form",
            "procedure_id": "dang_ky_khai_sinh",
            "official_level": "official",
            "review_status": "approved",
            "has_official_file": True,
            "download_url": "/api/forms/approved-form",
        },
        {
            "form_id": "seed-only",
            "procedure_id": "dang_ky_khai_sinh",
            "official_level": "reference",
            "review_status": "candidate_pending_review",
            "has_official_file": False,
            "download_url": None,
        },
    ]

    trace = _build_form_provenance_trace(
        question=question,
        procedure_match={"recommended_forms": raw_forms},
        accepted_forms=[raw_forms[0]],
    )

    assert trace["requested"] is True
    assert trace["forms_unavailable"] is False
    assert trace["accepted"][0]["form_id"] == "approved-form"
    assert trace["rejected"][0]["form_id"] == "seed-only"
    assert "not_official" in trace["rejected"][0]["reasons"]
    assert "not_approved" in trace["rejected"][0]["reasons"]
    assert "missing_official_file" in trace["rejected"][0]["reasons"]


def test_form_trace_reports_requested_but_unavailable():
    trace = _build_form_provenance_trace(
        question="Cần mẫu đơn khiếu nại",
        procedure_match={"recommended_forms": []},
        accepted_forms=None,
    )

    assert trace["requested"] is True
    assert trace["forms_unavailable"] is True
    assert trace["accepted"] == []


def test_feature005_user_visible_sources_are_valid_utf8_not_mojibake():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "api" / "legal_question_policy.py",
        root / "api" / "legal_section_grounding.py",
        root / "api" / "legal_structured_answer.py",
        root / "frontend" / "e2e" / "section-grounding.spec.ts",
        root / "notebook_data" / "legal-golden-set.json",
    ]
    forbidden = ("Ã", "Â", "áº", "á»")

    affected = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        if any(marker in text for marker in forbidden):
            affected.append(str(path.relative_to(root)))

    assert affected == []
