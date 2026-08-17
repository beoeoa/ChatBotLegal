import pytest

from scripts.repair_verified_legal_source_urls import plan_repairs


def test_plan_repairs_is_exact_and_active_only():
    planned = plan_repairs([
        {
            "id": 1,
            "law_number": "60/2014/QH13",
            "title": "Hộ tịch",
            "source_url": "https://vbpl.vn/Pages/vbpq-toanvan.aspx?ItemID=46746",
            "status": "active",
        },
        {
            "id": 2,
            "law_number": "02/2011/QH13",
            "title": "Luật Khiếu nại",
            "source_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=27192",
            "status": "active",
        },
    ])

    assert [item["document_id"] for item in planned] == [1, 2]
    assert all(item["after_source_url"].startswith("https://vbpl.vn/") for item in planned)


def test_plan_repairs_refuses_changed_or_inactive_metadata():
    rows = [
        {
            "id": 1,
            "law_number": "60/2014/QH13",
            "title": "Hộ tịch",
            "source_url": "https://other.example/document",
            "status": "active",
        },
        {
            "id": 2,
            "law_number": "02/2011/QH13",
            "title": "Luật Khiếu nại",
            "source_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=27192",
            "status": "active",
        },
    ]

    with pytest.raises(RuntimeError, match="changed since review"):
        plan_repairs(rows)
