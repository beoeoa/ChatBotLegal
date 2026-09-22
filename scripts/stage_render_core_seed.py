"""Stage only the checksum-reviewed commune core for a private Render transfer."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from scripts.verify_core_288_release import verify_core_release


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_MODEL_FINGERPRINT = "29020cd8c81d5439420992fc7e8c08eb16fa2883f4fefadb1e6bb4918116b9aa"


def _model_fingerprint(model_root: Path) -> str:
    """Match the retrieval server's path-independent model artifact digest."""
    digest = hashlib.sha256()
    paths = sorted(
        {
            path
            for pattern in ("*.json", "*.safetensors", "*.bin", "*.model", "*.txt")
            for path in model_root.rglob(pattern)
            if path.is_file()
        },
        key=lambda path: path.relative_to(model_root).as_posix(),
    )
    if not paths:
        raise ValueError("Reviewed embedding model has no artifacts")
    for path in paths:
        relative = path.relative_to(model_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(path.stat().st_size.to_bytes(8, "big"))
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _inside(root: Path, relative: str) -> Path:
    part = Path(relative)
    if part.is_absolute() or ".." in part.parts:
        raise ValueError(f"Unsafe release member: {relative}")
    path = (root / part).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"Release member escapes root: {relative}")
    return path


def stage(source: Path, destination: Path, *, dry_run: bool) -> dict[str, object]:
    receipt = verify_core_release(source)
    fingerprint = _model_fingerprint(source / "legal/vnlegal-lal-model")
    if fingerprint != EXPECTED_MODEL_FINGERPRINT:
        raise ValueError("Reviewed embedding model fingerprint mismatch")
    descriptor = json.loads((source / "legal/core-288-release.json").read_text(encoding="utf-8"))
    pointer_path = _inside(source, str(descriptor["serving_pointer"]))
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    manifest_rel = (Path(descriptor["serving_pointer"]).parent / str(pointer["manifest_path"])).as_posix()
    members = [
        "legal/core-288-release.json",
        str(descriptor["serving_pointer"]),
        manifest_rel,
        str(descriptor["chroma_directory"]),
        "legal/vnlegal-lal-model",
    ]
    unique_members = tuple(dict.fromkeys(members))
    for member in unique_members:
        if not _inside(source, member).exists():
            raise FileNotFoundError(f"Reviewed release member missing: {member}")
    if destination.exists():
        raise FileExistsError(f"Seed destination already exists: {destination}")
    if not dry_run:
        destination.mkdir(parents=True)
        for member in unique_members:
            original = _inside(source, member)
            target = destination / member
            target.parent.mkdir(parents=True, exist_ok=True)
            if original.is_dir():
                shutil.copytree(original, target)
            else:
                shutil.copy2(original, target)
        verify_core_release(destination, verify_postgres_dump=False)
    return {"status": "DRY_RUN" if dry_run else "STAGED", "release_id": receipt["release_id"], "model_fingerprint": fingerprint, "postgres_dump_included": False, "members": unique_members}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, default=REPO_ROOT / "release-data")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(stage(args.release_root, args.output, dry_run=args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    main()
