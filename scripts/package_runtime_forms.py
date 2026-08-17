"""Build a fail-closed release package for legally approved form records only."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.legal_form_catalog import (
    FormCatalog,
    _binding_is_approved,
    _official_runtime_procedure_bridge,
    normalize_procedure_id,
)


AUXILIARY_REVIEW_FILES = (
    "legal_review_attestations_v1.json",
    "legal_attestation_shortlist_v1.json",
    "haiphong_official_form_index.json",
)


def _load_list(path: Path, key: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    values = payload.get(key)
    if not isinstance(values, list):
        raise ValueError(f"{path.name} must contain a {key} list")
    return [dict(item) for item in values if isinstance(item, dict)]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clear_scoped_directory(path: Path, release_root: Path) -> None:
    resolved = path.resolve()
    expected_root = release_root.resolve()
    if resolved == expected_root or expected_root not in resolved.parents:
        raise ValueError("Refusing to clear a path outside the release root")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=True)


def build_runtime_form_package(
    *,
    project_root: Path,
    catalog_dir: Path,
    output_root: Path,
    as_of: date,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    catalog_dir = catalog_dir.resolve()
    output_root = output_root.resolve()
    procedures = _load_list(catalog_dir / "canonical_procedures_v1.json", "procedures")
    forms = _load_list(catalog_dir / "canonical_forms_catalog_v1.json", "forms")
    bindings = _load_list(catalog_dir / "procedure_form_bindings_v1.json", "bindings")
    three_tier_path = catalog_dir / "three_tier_procedure_catalog_v1.json"
    three_tier_payload: dict[str, Any] = {"procedures": []}
    if three_tier_path.is_file():
        payload = json.loads(three_tier_path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            three_tier_payload = payload
    known_procedure_ids = {
        normalize_procedure_id(item.get("procedure_id"))
        for item in procedures
        if item.get("procedure_id")
    }
    procedures.extend(
        item
        for item in _official_runtime_procedure_bridge(
            forms=forms,
            bindings=bindings,
            three_tier_payload=three_tier_payload,
        )
        if normalize_procedure_id(item.get("procedure_id"))
        not in known_procedure_ids
    )
    catalog = FormCatalog(
        procedures=procedures,
        forms=forms,
        bindings=bindings,
        project_root=project_root,
    )
    forms_by_id = {
        str(item.get("form_id")): item for item in forms if item.get("form_id")
    }
    approved_bindings: list[dict[str, Any]] = []
    approved_form_ids: set[str] = set()
    rejected_reason_counts: dict[str, int] = {}

    for binding in bindings:
        if not _binding_is_approved(binding):
            continue
        form_id = str(binding.get("form_id") or "")
        procedure_id = str(binding.get("procedure_id") or "")
        form = forms_by_id.get(form_id)
        if not form:
            rejected_reason_counts["FORM_RECORD_MISSING"] = (
                rejected_reason_counts.get("FORM_RECORD_MISSING", 0) + 1
            )
            continue
        gate = catalog.gate_form(
            form,
            procedure_id=procedure_id,
            role="admin",
            as_of=as_of,
        )
        if not gate.eligible:
            rejected_reason_counts[gate.reason_code] = (
                rejected_reason_counts.get(gate.reason_code, 0) + 1
            )
            continue
        approved_bindings.append(binding)
        approved_form_ids.add(form_id)

    approved_forms = [
        item for item in forms if str(item.get("form_id") or "") in approved_form_ids
    ]
    approved_procedure_ids = {
        normalize_procedure_id(item.get("procedure_id"))
        for item in approved_bindings
    }
    approved_procedures = [
        item
        for item in procedures
        if normalize_procedure_id(item.get("procedure_id"))
        in approved_procedure_ids
    ]

    release_forms = output_root / "forms"
    release_catalog = output_root / "notebook_data" / "forms"
    _clear_scoped_directory(release_forms, output_root)
    _clear_scoped_directory(release_catalog, output_root)

    copied_assets: list[dict[str, Any]] = []
    source_forms_root = (project_root / "data/uploads/forms").resolve()
    for form in approved_forms:
        local_path = str(form.get("local_path") or "").strip()
        if not local_path:
            continue
        source = (project_root / local_path).resolve()
        try:
            relative_asset = source.relative_to(source_forms_root)
        except ValueError as exc:
            raise ValueError(
                f"Approved form asset is outside data/uploads/forms: {form.get('form_id')}"
            ) from exc
        expected = str(form.get("sha256") or "").casefold()
        if not source.is_file() or not expected or _sha256(source) != expected:
            raise ValueError(
                f"Approved form asset failed checksum during packaging: {form.get('form_id')}"
            )
        destination = release_forms / relative_asset
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied_assets.append(
            {
                "form_id": form.get("form_id"),
                "path": str(Path("forms") / relative_asset).replace("\\", "/"),
                "sha256": expected,
                "bytes": destination.stat().st_size,
            }
        )

    _write_json(
        release_catalog / "canonical_procedures_v1.json",
        {"schema_version": 1, "procedures": approved_procedures},
    )
    _write_json(
        release_catalog / "canonical_forms_catalog_v1.json",
        {"schema_version": 1, "forms": approved_forms},
    )
    _write_json(
        release_catalog / "procedure_form_bindings_v1.json",
        {"schema_version": 1, "bindings": approved_bindings},
    )
    for filename in AUXILIARY_REVIEW_FILES:
        source = catalog_dir / filename
        if source.is_file():
            shutil.copy2(source, release_catalog / filename)

    manifest = {
        "schema_version": "runtime-form-package-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "legal_as_of": as_of.isoformat(),
        "input_checksums": {
            path.name: _sha256(path)
            for path in (
                catalog_dir / "canonical_procedures_v1.json",
                catalog_dir / "canonical_forms_catalog_v1.json",
                catalog_dir / "procedure_form_bindings_v1.json",
                three_tier_path,
            )
            if path.is_file()
        },
        "runtime_form_count": len(approved_forms),
        "runtime_binding_count": len(approved_bindings),
        "runtime_procedure_count": len(approved_procedures),
        "asset_count": len(copied_assets),
        "assets": copied_assets,
        "rejected_reason_counts": dict(sorted(rejected_reason_counts.items())),
    }
    _write_json(release_catalog / "runtime_form_package_manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=PROJECT_ROOT / "notebook_data/forms",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "release-data",
    )
    parser.add_argument("--legal-as-of", default=date.today().isoformat())
    args = parser.parse_args()
    result = build_runtime_form_package(
        project_root=PROJECT_ROOT,
        catalog_dir=args.catalog_dir,
        output_root=args.output_root,
        as_of=date.fromisoformat(args.legal_as_of),
    )
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
