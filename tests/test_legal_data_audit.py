from pathlib import Path


def test_legal_data_audit_has_form_coverage_metrics():
    from scripts.audit_legal_data import build_report

    report = build_report()
    forms = report["forms"]
    assert forms["official_index_total"] >= 1
    assert "official_index_missing_file" in forms
    assert "candidate_status_counts" in forms


def test_quality_router_exposes_forms_quality_summary():
    source = Path("api/routers/legal_quality.py").read_text(encoding="utf-8")
    assert "_forms_quality_summary" in source
    assert '"forms": _forms_quality_summary()' in source


def test_step13_golden_gate_has_pilot_size_and_domain_coverage():
    from scripts.audit_step13_quality_gate import main

    assert main() == 0


def test_form_url_enrichment_only_accepts_catalogue_urls():
    from scripts.enrich_missing_form_urls import _valid_url

    assert _valid_url("https://haiphong.gov.vn/form")
    assert _valid_url("http://cdn.haiphong.gov.vn/form.pdf")
    assert not _valid_url("/api/forms/1/download")
    assert not _valid_url("javascript:alert(1)")


def test_verified_form_source_map_is_complete_and_uses_http_urls():
    import json
    from scripts.enrich_missing_form_urls import _valid_url

    path = Path("notebook_data/forms/verified_form_source_urls.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 8
    for record in data.values():
        assert _valid_url(record.get("source_page_url"))
        if record.get("source_download_url"):
            assert _valid_url(record["source_download_url"])
