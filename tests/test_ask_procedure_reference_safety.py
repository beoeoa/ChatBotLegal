from api.routers.search import _procedure_response_fields


def test_hardcoded_procedure_seed_claims_are_not_exposed_as_answer_data():
    seeded = {
        "id": "seeded-procedure",
        "name": "Thủ tục tham chiếu",
        "department": "Cơ quan seed",
        "domain_slug": "ho_tich_chung_thuc",
        "steps": ["Bước pháp lý hard-code"],
        "documents_required": ["Giấy tờ hard-code"],
        "duration": "3 ngày",
        "fee": "50.000 đồng",
        "forms": [],
    }

    procedure, forms, summary = _procedure_response_fields(seeded, "Thủ tục này thế nào?")

    assert procedure is not None
    assert procedure["is_reference_only"] is True
    assert procedure["steps"] == []
    assert procedure["documents_required"] == []
    assert procedure["duration"] is None
    assert procedure["fee"] is None
    assert forms is None
    assert summary == "Tham chiếu quy trình: Thủ tục tham chiếu"
