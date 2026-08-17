#!/usr/bin/env python3
"""Package checksum-bound form files for a non-activatable Feature 017 release."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.form_governance_models import canonical_sha256  # noqa: E402
from api.form_governance_release_validator import validate_release_manifest  # noqa: E402
from api.official_source_adapters import is_allowlisted_official_url  # noqa: E402


DEFAULT_INPUT = (
    ROOT
    / "outputs"
    / "feature017-full-release-candidate-20260812"
    / "form-release-v1.json"
)
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "feature017-full-release-candidate-20260812-packaged"
DEFAULT_RELEASE_ROOT = ROOT / "release-data"
MAX_SOURCE_BYTES = 60 * 1024 * 1024
PackageFetcher = Callable[[str], bytes]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch_package(url: str) -> bytes:
    import httpx
    from api.official_http import build_verified_ssl_context

    if not is_allowlisted_official_url(url):
        raise ValueError("FEATURE017_SOURCE_PACKAGE_NOT_OFFICIAL")
    with httpx.Client(
        timeout=60.0,
        follow_redirects=True,
        verify=build_verified_ssl_context(),
    ) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            if not is_allowlisted_official_url(str(response.url)):
                raise ValueError("FEATURE017_SOURCE_PACKAGE_REDIRECT_NOT_OFFICIAL")
            payload = bytearray()
            for block in response.iter_bytes():
                payload.extend(block)
                if len(payload) > MAX_SOURCE_BYTES:
                    raise ValueError("FEATURE017_SOURCE_PACKAGE_TOO_LARGE")
    return bytes(payload)


def package_release(
    manifest: Mapping[str, Any],
    *,
    source_root: Path,
    release_root: Path,
    fetch_package: PackageFetcher,
) -> dict[str, Any]:
    packaged = json.loads(json.dumps(manifest, ensure_ascii=False))
    release_id = str(packaged.get("release_id") or "").strip()
    if not release_id:
        raise ValueError("FEATURE017_RELEASE_ID_REQUIRED")
    destination_root = (release_root / "feature017" / release_id).resolve()
    release_root_resolved = release_root.resolve()
    destination_root.relative_to(release_root_resolved)
    allowed_source_roots = tuple(
        (source_root / relative).resolve()
        for relative in (
            "data/uploads/forms",
            "data/source_cache/feature017_approved_sources_20260811",
        )
    )
    package_cache: dict[str, tuple[str, int]] = {}
    file_records: list[dict[str, Any]] = []
    package_records: list[dict[str, Any]] = []
    destination_root.mkdir(parents=True, exist_ok=True)

    for asset in packaged.get("assets") or []:
        if asset.get("asset_kind") == "eform":
            asset["download_url"] = asset.get("source_url")
            continue
        provenance = asset.get("provenance") or {}
        artifact = provenance.get("canonical_artifact") or {}
        source_path_text = str(artifact.get("staging_path") or "").strip()
        if not source_path_text:
            raise ValueError("FEATURE017_RUNTIME_SOURCE_PATH_REQUIRED")
        source_path = (source_root / source_path_text).resolve()
        if not any(
            root == source_path.parent or root in source_path.parents
            for root in allowed_source_roots
        ):
            raise ValueError("FEATURE017_RUNTIME_SOURCE_PATH_OUTSIDE_ALLOWED_ROOT")
        if not source_path.is_file():
            raise ValueError("FEATURE017_RUNTIME_SOURCE_FILE_MISSING")
        checksum = _sha256_path(source_path)
        if checksum != str(asset.get("source_checksum") or "").casefold():
            raise ValueError("FEATURE017_RUNTIME_SOURCE_CHECKSUM_MISMATCH")
        suffix = source_path.suffix.casefold()
        if suffix not in {".pdf", ".doc", ".docx", ".xls", ".xlsx"}:
            raise ValueError("FEATURE017_RUNTIME_SOURCE_FORMAT_INVALID")
        destination = destination_root / f"{asset['form_id']}{suffix}"
        shutil.copy2(source_path, destination)
        if _sha256_path(destination) != checksum:
            raise ValueError("FEATURE017_RUNTIME_PACKAGE_COPY_MISMATCH")
        relative_runtime_path = destination.relative_to(release_root_resolved).as_posix()
        asset["runtime_path"] = relative_runtime_path
        asset["download_url"] = (
            f"/api/procedures/forms-catalog/assets/{asset['form_id']}/download"
        )
        asset["file_format"] = suffix.lstrip(".")
        file_records.append(
            {
                "form_id": asset["form_id"],
                "runtime_path": relative_runtime_path,
                "sha256": checksum,
                "size_bytes": destination.stat().st_size,
            }
        )

        package_url = str(artifact.get("source_download_url") or "").strip()
        if package_url:
            if package_url not in package_cache:
                payload = fetch_package(package_url)
                package_cache[package_url] = (_sha256_bytes(payload), len(payload))
            package_hash, package_size = package_cache[package_url]
            expected_package_hash = str(
                artifact.get("source_package_sha256") or ""
            ).casefold()
            if expected_package_hash and expected_package_hash != package_hash:
                raise ValueError("FEATURE017_SOURCE_PACKAGE_CHECKSUM_DRIFT")
            artifact["source_package_sha256"] = package_hash
            artifact["source_package_size_bytes"] = package_size
            package_records.append(
                {
                    "form_id": asset["form_id"],
                    "source_download_url": package_url,
                    "source_package_sha256": package_hash,
                    "source_package_size_bytes": package_size,
                }
            )

    packaged.setdefault("build", {})["pipeline_version"] = (
        "feature017-v1-owner-deferred-packaged"
    )
    packaged["build"]["runtime_asset_root"] = "release-data"
    packaged["build"]["runtime_file_count"] = len(file_records)
    packaged["build"]["source_package_count"] = len(package_cache)
    gate = validate_release_manifest(
        packaged,
        expected_manifest_sha256=canonical_sha256(packaged),
    )
    if not gate.get("passed"):
        raise ValueError("FEATURE017_PACKAGED_RELEASE_GATE_FAILED")
    return {
        "manifest": packaged,
        "manifest_sha256": canonical_sha256(packaged),
        "gate_report": gate,
        "package_manifest": {
            "schema_version": "feature017-runtime-asset-package-v1",
            "release_id": release_id,
            "file_count": len(file_records),
            "source_package_count": len(package_cache),
            "files": file_records,
            "source_packages": package_records,
        },
        "status": "validated_packaged_candidate",
        "activation_allowed": False,
        "runtime_catalog_mutated": False,
        "active_pointer_changed": False,
    }


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--release-root", type=Path, default=DEFAULT_RELEASE_ROOT)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    result = package_release(
        manifest,
        source_root=ROOT,
        release_root=args.release_root,
        fetch_package=_fetch_package,
    )
    _write(args.output_dir / "form-release-v1.json", result["manifest"])
    _write(args.output_dir / "gate-report.json", result["gate_report"])
    _write(args.output_dir / "runtime-asset-package.json", result["package_manifest"])
    _write(args.output_dir / "release-candidate.json", result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "manifest_sha256": result["manifest_sha256"],
                "file_count": result["package_manifest"]["file_count"],
                "source_package_count": result["package_manifest"]["source_package_count"],
                "activation_allowed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
