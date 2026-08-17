from datetime import date

from api.legal_form_catalog import FormCatalog
from fixtures.feature006_form_lookup_cases import (
    BINDINGS,
    FORMS,
    FORM_LOOKUP_TRAPS,
    PROCEDURES,
)


def test_feature006_form_lookup_traps_fail_closed(tmp_path):
    catalog = FormCatalog(
        procedures=PROCEDURES,
        forms=FORMS,
        bindings=BINDINGS,
        project_root=tmp_path,
    )

    for trap in FORM_LOOKUP_TRAPS:
        result = catalog.resolve_forms(
            trap["query"],
            role="citizen",
            as_of=date(2026, 7, 30),
            procedure_ids=[trap["procedure_id"]],
            limit=5,
        )
        actual = {
            str(item["form_id"])
            for item in result["recommended_forms"]
        }
        assert actual == set(trap["expected_form_ids"]), trap["trap"]
        assert not actual.intersection(trap.get("rejected_form_ids", []))
        if trap.get("expected_rejection_reason"):
            assert trap["expected_rejection_reason"] in {
                item["reason_code"] for item in result["rejected_forms"]
            }


def test_feature006_eform_is_served_only_as_verified_online_route(tmp_path):
    catalog = FormCatalog(
        procedures=PROCEDURES,
        forms=FORMS,
        bindings=BINDINGS,
        project_root=tmp_path,
    )
    result = catalog.resolve_forms(
        "Thủ tục dùng biểu mẫu điện tử mở e-form nào?",
        role="citizen",
        as_of=date(2026, 7, 30),
        procedure_ids=["fixture_eform"],
    )

    assert result["recommended_forms"][0]["file_type"] == "online"
    assert result["recommended_forms"][0]["download_url"].startswith(
        "https://dichvucong.gov.vn/"
    )
