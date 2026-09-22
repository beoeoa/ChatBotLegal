"""Create a legal-only PostgreSQL seed from the reviewed local core dump.

The source archive also contains local users and conversations. Never upload
it directly. This script emits schema with empty account tables plus only the
allowlisted legal table data, for a fresh Render PostgreSQL instance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from scripts.verify_core_288_release import verify_core_release


REPO_ROOT = Path(__file__).resolve().parents[1]
PG_IMAGE = "postgres:18-alpine"
LEGAL_TABLES = (
    "legal_fields",
    "legal_documents",
    "legal_articles",
    "legal_article_chunks",
    "legal_chunk_quality",
    "legal_commune_field_groups",
    "legal_search_scope",
    "legal_document_relationships",
    "legal_topics",
    "legal_topic_rules",
    "legal_change_event",
    "legal_provision_effectivity",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _pg_restore(source: Path, output: Path | None, *args: str) -> str:
    mounts = ["-v", f"{source.resolve()}:/seed/source.dump:ro"]
    if output is not None:
        mounts += ["-v", f"{output.resolve()}:/out"]
    result = subprocess.run(
        ["docker", "run", "--rm", *mounts, PG_IMAGE, "pg_restore", *args, "/seed/source.dump"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def stage(source_root: Path, output: Path, *, dry_run: bool) -> dict[str, object]:
    receipt = verify_core_release(source_root)
    descriptor = json.loads((source_root / "legal/core-288-release.json").read_text(encoding="utf-8"))
    source = source_root / str(descriptor["postgres_dump"])
    toc = _pg_restore(source, None, "--list")
    table_entries: dict[str, str] = {}
    sequence_entries: list[str] = []
    for line in toc.splitlines():
        table = re.search(r"\bTABLE DATA public ([a-z_]+) postgres$", line)
        sequence = re.search(r"\bSEQUENCE SET public ([a-z_]+) postgres$", line)
        if table and table.group(1) in LEGAL_TABLES:
            table_entries[table.group(1)] = line
        elif sequence and any(sequence.group(1).startswith(name + "_") for name in LEGAL_TABLES):
            sequence_entries.append(line)
    if set(table_entries) != set(LEGAL_TABLES):
        raise ValueError(f"Legal tables missing from reviewed dump: {sorted(set(LEGAL_TABLES) - set(table_entries))}")
    selected = [table_entries[name] for name in LEGAL_TABLES] + sequence_entries
    if output.exists():
        raise FileExistsError(f"PostgreSQL seed destination already exists: {output}")
    if dry_run:
        return {"status": "DRY_RUN", "release_id": receipt["release_id"], "tables": LEGAL_TABLES, "source_dump_uploaded": False}

    output.mkdir(parents=True)
    (output / "legal-only.toc").write_text("; Legal-only Render seed\n" + "\n".join(selected) + "\n", encoding="utf-8")
    (output / "extensions.sql").write_text(
        "CREATE EXTENSION IF NOT EXISTS pg_trgm;\n"
        "CREATE EXTENSION IF NOT EXISTS pgcrypto;\n"
        "CREATE EXTENSION IF NOT EXISTS unaccent;\n",
        encoding="utf-8",
    )
    _pg_restore(source, output, "--schema-only", "--schema=public", "--no-owner", "--no-privileges", "--no-tablespaces", "--file=/out/schema.sql")
    _pg_restore(source, output, "--data-only", "--use-list=/out/legal-only.toc", "--no-owner", "--no-privileges", "--no-tablespaces", "--file=/out/legal-data.sql")

    data_sql = (output / "legal-data.sql").read_text(encoding="utf-8")
    copied_tables = set(re.findall(r"^COPY public\.([a-z_]+) ", data_sql, flags=re.MULTILINE))
    if copied_tables != set(LEGAL_TABLES):
        raise ValueError(f"Generated data contains unexpected or missing tables: {sorted(copied_tables ^ set(LEGAL_TABLES))}")
    receipt_data = {
        "release_id": receipt["release_id"],
        "source_dump_uploaded": False,
        "tables": LEGAL_TABLES,
        "extensions_sha256": _sha256(output / "extensions.sql"),
        "schema_sha256": _sha256(output / "schema.sql"),
        "legal_data_sha256": _sha256(output / "legal-data.sql"),
    }
    (output / "manifest.json").write_text(json.dumps(receipt_data, indent=2) + "\n", encoding="utf-8")
    return {"status": "STAGED", **receipt_data}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, default=REPO_ROOT / "release-data")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(stage(args.release_root, args.output, dry_run=args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    main()
