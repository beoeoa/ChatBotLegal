from pathlib import Path

from api.data_paths import notebook_data_dir


def test_notebook_data_dir_prefers_configured_runtime_directory(monkeypatch, tmp_path):
    configured = tmp_path / "shared-data"
    configured.mkdir()
    monkeypatch.setenv("OPEN_NOTEBOOK_DATA_DIR", str(configured))

    assert notebook_data_dir() == configured.resolve()


def test_notebook_data_dir_contains_runtime_faq_store(monkeypatch):
    monkeypatch.delenv("OPEN_NOTEBOOK_DATA_DIR", raising=False)

    data_dir = notebook_data_dir()
    assert data_dir.is_dir()
    assert (data_dir / "faq_store.json").is_file()
