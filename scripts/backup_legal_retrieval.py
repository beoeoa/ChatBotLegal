"""Create a credential-safe PostgreSQL and Chroma backup for Feature 005."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any, Mapping

import chromadb
from dotenv import dotenv_values
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHROMA_PATH = Path(os.getenv("LEGAL_CHROMA_PATH", r"D:\legal-chatbot-data\chroma_store"))
COLLECTIONS = (
    os.getenv("LEGAL_CHROMA_COLLECTION", "legal_chunks_vnlegal_lal_haiphong"),
    os.getenv("LEGAL_CHROMA_SOURCE_COLLECTION", "legal_chunks_vnlegal_lal"),
)


def _database_url(
    *,
    environment: Mapping[str, str] | None = None,
    repo_settings: Mapping[str, Any] | None = None,
    legacy_settings: Mapping[str, Any] | None = None,
) -> str:
    """Select only the runtime corpus database for a release backup.

    Desktop test sessions commonly export ``LEGAL_DATABASE_URL`` for an empty
    staging database. A release backup must never inherit that generic test
    target. Operators can override the runtime corpus explicitly with
    ``LEGAL_CORPUS_DATABASE_URL``; otherwise the reviewed release URL is the
    source of truth.
    """

    environment = os.environ if environment is None else environment
    explicit = str(
        environment.get("LEGAL_CORPUS_DATABASE_URL") or ""
    ).strip()
    if explicit:
        return explicit.replace(
            "@host.docker.internal:",
            "@127.0.0.1:",
        )
    repo_settings = (
        dotenv_values(ROOT / ".env")
        if repo_settings is None
        else repo_settings
    )
    release = str(
        repo_settings.get("LEGAL_RELEASE_DATABASE_URL") or ""
    ).strip()
    if release:
        return release.replace("@host.docker.internal:", "@127.0.0.1:")
    legacy_settings = (
        dotenv_values(
            Path(
                environment.get(
                    "LEGAL_OLD_ENV_PATH",
                    r"J:\ChatBot\legal-chatbot\backend\.env",
                )
            )
        )
        if legacy_settings is None
        else legacy_settings
    )
    value = str(legacy_settings.get("DATABASE_URL") or "").strip()
    if value:
        return value.replace(
            "@host.docker.internal:",
            "@127.0.0.1:",
        )
    return "postgresql+psycopg2://postgres:postgres@localhost:5432/legal_chatbot"


def _pg_dump_path() -> Path:
    found = shutil.which("pg_dump")
    candidates = [
        Path(found) if found else None,
        Path(r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"),
        Path(r"C:\Program Files\PostgreSQL\17\bin\pg_dump.exe"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return candidate
    raise RuntimeError("pg_dump executable was not found")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _port_open(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def create_backup(output_dir: Path, *, chroma_path: Path = DEFAULT_CHROMA_PATH) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Backup target already exists: {output_dir}")
    if not chroma_path.resolve().is_dir():
        raise FileNotFoundError(f"Chroma path does not exist: {chroma_path}")
    if _port_open(8765):
        raise RuntimeError("Stop the retrieval service on port 8765 before copying Chroma")

    output_dir.mkdir(parents=True)
    dump_path = output_dir / "postgres.dump"
    parsed = make_url(_database_url())
    environment = os.environ.copy()
    if parsed.password:
        environment["PGPASSWORD"] = parsed.password
    command = [
        str(_pg_dump_path()),
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(dump_path),
        "--host",
        str(parsed.host or "localhost"),
        "--port",
        str(parsed.port or 5432),
        "--username",
        str(parsed.username or "postgres"),
        str(parsed.database or "legal_chatbot"),
    ]
    subprocess.run(command, env=environment, check=True, capture_output=True)

    counts: dict[str, int | None] = {}
    try:
        client = chromadb.PersistentClient(path=str(chroma_path))
        for name in COLLECTIONS:
            try:
                counts[name] = int(client.get_collection(name).count())
            except Exception:
                counts[name] = None
    except Exception:
        counts = {name: None for name in COLLECTIONS}

    chroma_copy = output_dir / "chroma_store"
    shutil.copytree(chroma_path, chroma_copy, copy_function=shutil.copy2)
    files = []
    for path in sorted(chroma_copy.rglob("*")):
        if not path.is_file():
            continue
        files.append(
            {
                "path": path.relative_to(chroma_copy).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "quality_version": "legal-chunk-quality-v1",
        "postgres": {
            "file": dump_path.name,
            "bytes": dump_path.stat().st_size,
            "sha256": _sha256(dump_path),
        },
        "chroma": {
            "source_path": str(chroma_path.resolve()),
            "collections": counts,
            "file_count": len(files),
            "total_bytes": sum(item["bytes"] for item in files),
            "files": files,
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    args = parser.parse_args()
    manifest = create_backup(args.output, chroma_path=args.chroma_path)
    print(json.dumps({
        "backup": str(args.output.resolve()),
        "postgres_bytes": manifest["postgres"]["bytes"],
        "chroma_bytes": manifest["chroma"]["total_bytes"],
        "chroma_file_count": manifest["chroma"]["file_count"],
        "collections": manifest["chroma"]["collections"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
