from api.legal_form_catalog import FormCatalog


def test_default_catalog_uses_runtime_data_directory(monkeypatch, tmp_path):
    forms_dir = tmp_path / "forms"
    forms_dir.mkdir()
    (forms_dir / "canonical_procedures_v1.json").write_text(
        '{"procedures":[{"procedure_id":"runtime-procedure","name":"Runtime"}]}',
        encoding="utf-8",
    )
    (forms_dir / "canonical_forms_catalog_v1.json").write_text(
        '{"forms":[]}',
        encoding="utf-8",
    )
    (forms_dir / "procedure_form_bindings_v1.json").write_text(
        '{"bindings":[]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPEN_NOTEBOOK_DATA_DIR", str(tmp_path))

    catalog = FormCatalog.load_default()

    assert catalog.get_procedure("runtime-procedure")["name"] == "Runtime"
