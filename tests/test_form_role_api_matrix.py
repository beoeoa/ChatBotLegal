from datetime import date

from api.legal_form_catalog import FormCatalog
from scripts.build_form_lookup_release_dataset import DEFAULT_MANIFEST, build_dataset
from scripts.run_form_role_api_matrix import run_matrix


def test_form_role_matrix_covers_all_procedures_and_roles():
    report = run_matrix(
        catalog=FormCatalog.load_default(),
        dataset=build_dataset(DEFAULT_MANIFEST),
        legal_as_of=date(2026, 7, 30),
    )

    assert report["technical_pass"] is True
    assert report["procedure_count"] == 418
    assert report["role_count"] == 3
    assert report["case_count"] == 1254
    assert report["pass_count"] == 1254
    assert all(
        row["case_count"] == row["pass_count"] == 418
        for row in report["roles"].values()
    )
