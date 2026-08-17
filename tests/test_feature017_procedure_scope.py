from __future__ import annotations

from api.form_procedure_scope import (
    COMPATIBILITY_MANIFEST,
    load_commune_procedure_domains,
)


def test_read_only_scope_registry_contains_exactly_the_191_commune_procedures():
    domains = load_commune_procedure_domains(str(COMPATIBILITY_MANIFEST))
    assert len(domains) == 191
    assert set(domains.values()).issubset({
        "ho_tich_chung_thuc",
        "dat_dai_xay_dung",
        "an_sinh_y_te_giao_duc",
        "cu_tru_an_ninh",
        "khieu_nai_to_cao_xu_phat",
    })
