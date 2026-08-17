from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.rehearse_feature018_restore import COMPONENTS, copy_component, rehearse


def _backup(root: Path) -> tuple[Path, Path]:
    source = root / "backup"
    for component in COMPONENTS:
        folder = source / component
        folder.mkdir(parents=True)
        (folder / "fixture.bin").write_bytes(f"{component}-fixture".encode())
    manifest = root / "release.json"
    manifest.write_text(json.dumps({"release_fingerprint": "a" * 64}), encoding="utf-8")
    return source, manifest


def test_restore_rehearsal_copies_all_stores_and_reconciles_fingerprints(tmp_path: Path):
    source, manifest = _backup(tmp_path)
    result = rehearse(source, tmp_path / "restored", manifest)
    assert result["passed"] is True
    assert result["components"] == list(COMPONENTS)
    assert result["inventory_match"] is True
    assert result["release_fingerprint_reconciled"] is True
    assert result["production_activation_authorized"] is False


def test_restore_rehearsal_refuses_existing_target_and_incomplete_backup(tmp_path: Path):
    source, manifest = _backup(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        rehearse(source, existing, manifest)
    (source / "vectors" / "fixture.bin").unlink()
    (source / "vectors").rmdir()
    with pytest.raises(ValueError, match="restore_components_missing:vectors"):
        rehearse(source, tmp_path / "new-target", manifest)


def test_restore_rehearsal_uses_inventory_manifest_hash_as_release_fingerprint(
    tmp_path: Path,
):
    source, manifest = _backup(tmp_path)
    manifest.write_text(
        json.dumps([{"path": "release.bin", "sha256": "b" * 64, "bytes": 1}]),
        encoding="utf-8",
    )

    result = rehearse(source, tmp_path / "restored", manifest)

    assert result["passed"] is True
    assert result["release_fingerprint"] == result["release_manifest_sha256"]


def test_copy_component_preserves_nested_files(tmp_path: Path):
    source = tmp_path / "source"
    nested = source / "nested" / "folder"
    nested.mkdir(parents=True)
    (nested / "dữ-liệu.bin").write_bytes(b"release-restore")

    target = tmp_path / "target"
    copy_component(source, target)

    assert (target / "nested" / "folder" / "dữ-liệu.bin").read_bytes() == b"release-restore"


def test_restore_rehearsal_can_verify_an_existing_isolated_target(tmp_path: Path):
    source, manifest = _backup(tmp_path)
    target = tmp_path / "restored"
    first = rehearse(source, target, manifest)

    verified = rehearse(
        source,
        target,
        manifest,
        verify_existing_target=True,
    )

    assert first["copy_performed"] is True
    assert verified["copy_performed"] is False
    assert verified["passed"] is True
