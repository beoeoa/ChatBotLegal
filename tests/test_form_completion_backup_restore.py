from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import backup_form_completion_state as backup


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_restore_drill_recreates_checksum_identical_files_outside_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forms_dir = tmp_path / "notebook_data" / "forms"
    first = forms_dir / "catalog.json"
    second = forms_dir / "bindings.json"
    _write_json(first, {"forms": [{"id": "form-1"}]})
    _write_json(second, {"bindings": [{"form_id": "form-1"}]})
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(backup, "FORMS_DIR", forms_dir)

    backup_dir = tmp_path / "backups" / "run-1"
    backup.create_backup(backup_dir, paths=(first, second))
    result = backup.verify_restore_drill(
        backup_dir / "backup-manifest.json",
        tmp_path / "restore-drill",
    )

    assert result["status"] == "PASS"
    assert result["record_count"] == 2
    assert result["failures"] == []
    assert (tmp_path / "restore-drill" / "catalog.json").read_bytes() == first.read_bytes()
    assert (tmp_path / "restore-drill" / "bindings.json").read_bytes() == second.read_bytes()


def test_restore_drill_rejects_runtime_or_ancestor_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    forms_dir = tmp_path / "notebook_data" / "forms"
    source = forms_dir / "catalog.json"
    _write_json(source, {"forms": []})
    monkeypatch.setattr(backup, "ROOT", tmp_path)
    monkeypatch.setattr(backup, "FORMS_DIR", forms_dir)
    backup_dir = tmp_path / "backups" / "run-1"
    backup.create_backup(backup_dir, paths=(source,))
    manifest = backup_dir / "backup-manifest.json"

    with pytest.raises(ValueError, match="RESTORE_TARGET_INVALID"):
        backup.verify_restore_drill(manifest, forms_dir)
    with pytest.raises(ValueError, match="RESTORE_TARGET_INVALID"):
        backup.verify_restore_drill(manifest, tmp_path / "notebook_data")
