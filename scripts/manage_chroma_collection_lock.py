#!/usr/bin/env python3
"""Install and verify a reversible SQLite write barrier for one Chroma collection.

The barrier is scoped to the collection UUID, its queue topic, metadata segment,
and collection metadata. Other collections in the same persistent store remain
writable. Unlocking is deliberately a separate, explicit operation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


LOCK_ERROR = "LEGAL_BASELINE_COLLECTION_LOCKED"
REGISTRY_TABLE = "legal_collection_write_locks"
TRIGGER_PREFIX = "legal_baseline_lock_"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _lock_suffix(collection_id: str) -> str:
    suffix = re.sub(r"[^a-zA-Z0-9]", "", collection_id)[:16].lower()
    if not suffix:
        raise ValueError("invalid_collection_id")
    return suffix


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(database), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _collection_state(
    connection: sqlite3.Connection, collection_name: str
) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id, name, dimension FROM collections WHERE name = ?",
        (collection_name,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"collection_not_found:{collection_name}")
    collection_id = str(row["id"])
    segments = connection.execute(
        "SELECT id, scope, type FROM segments WHERE collection = ? ORDER BY scope, id",
        (collection_id,),
    ).fetchall()
    metadata_segment_ids = [
        str(item["id"]) for item in segments if str(item["scope"]) == "METADATA"
    ]
    vector_segment_ids = [
        str(item["id"]) for item in segments if str(item["scope"]) == "VECTOR"
    ]
    if len(metadata_segment_ids) != 1 or len(vector_segment_ids) != 1:
        raise RuntimeError("unexpected_collection_segment_layout")
    return {
        "collection_id": collection_id,
        "collection_name": str(row["name"]),
        "dimension": int(row["dimension"] or 0),
        "topic": f"persistent://default/default/{collection_id}",
        "metadata_segment_id": metadata_segment_ids[0],
        "vector_segment_id": vector_segment_ids[0],
    }


def _trigger_sql(state: dict[str, Any]) -> dict[str, str]:
    collection_id = _sql_literal(str(state["collection_id"]))
    topic = _sql_literal(str(state["topic"]))
    metadata_segment_id = _sql_literal(str(state["metadata_segment_id"]))
    suffix = _lock_suffix(str(state["collection_id"]))
    prefix = f"{TRIGGER_PREFIX}{suffix}_"
    abort = f"SELECT RAISE(ABORT, '{LOCK_ERROR}');"
    return {
        prefix + "queue_insert": (
            "CREATE TRIGGER {name} BEFORE INSERT ON embeddings_queue "
            f"WHEN NEW.topic = {topic} BEGIN {abort} END"
        ),
        prefix + "queue_update": (
            "CREATE TRIGGER {name} BEFORE UPDATE ON embeddings_queue "
            f"WHEN OLD.topic = {topic} OR NEW.topic = {topic} BEGIN {abort} END"
        ),
        prefix + "queue_delete": (
            "CREATE TRIGGER {name} BEFORE DELETE ON embeddings_queue "
            f"WHEN OLD.topic = {topic} BEGIN {abort} END"
        ),
        prefix + "collection_update": (
            "CREATE TRIGGER {name} BEFORE UPDATE ON collections "
            f"WHEN OLD.id = {collection_id} OR NEW.id = {collection_id} BEGIN {abort} END"
        ),
        prefix + "collection_delete": (
            "CREATE TRIGGER {name} BEFORE DELETE ON collections "
            f"WHEN OLD.id = {collection_id} BEGIN {abort} END"
        ),
        prefix + "collection_metadata_insert": (
            "CREATE TRIGGER {name} BEFORE INSERT ON collection_metadata "
            f"WHEN NEW.collection_id = {collection_id} BEGIN {abort} END"
        ),
        prefix + "collection_metadata_update": (
            "CREATE TRIGGER {name} BEFORE UPDATE ON collection_metadata "
            f"WHEN OLD.collection_id = {collection_id} OR NEW.collection_id = {collection_id} "
            f"BEGIN {abort} END"
        ),
        prefix + "collection_metadata_delete": (
            "CREATE TRIGGER {name} BEFORE DELETE ON collection_metadata "
            f"WHEN OLD.collection_id = {collection_id} BEGIN {abort} END"
        ),
        prefix + "segment_insert": (
            "CREATE TRIGGER {name} BEFORE INSERT ON segments "
            f"WHEN NEW.collection = {collection_id} BEGIN {abort} END"
        ),
        prefix + "segment_update": (
            "CREATE TRIGGER {name} BEFORE UPDATE ON segments "
            f"WHEN OLD.collection = {collection_id} OR NEW.collection = {collection_id} "
            f"BEGIN {abort} END"
        ),
        prefix + "segment_delete": (
            "CREATE TRIGGER {name} BEFORE DELETE ON segments "
            f"WHEN OLD.collection = {collection_id} BEGIN {abort} END"
        ),
        prefix + "embedding_insert": (
            "CREATE TRIGGER {name} BEFORE INSERT ON embeddings "
            f"WHEN NEW.segment_id = {metadata_segment_id} BEGIN {abort} END"
        ),
        prefix + "embedding_update": (
            "CREATE TRIGGER {name} BEFORE UPDATE ON embeddings "
            f"WHEN OLD.segment_id = {metadata_segment_id} OR NEW.segment_id = {metadata_segment_id} "
            f"BEGIN {abort} END"
        ),
        prefix + "embedding_delete": (
            "CREATE TRIGGER {name} BEFORE DELETE ON embeddings "
            f"WHEN OLD.segment_id = {metadata_segment_id} BEGIN {abort} END"
        ),
        prefix + "embedding_metadata_insert": (
            "CREATE TRIGGER {name} BEFORE INSERT ON embedding_metadata "
            "WHEN EXISTS (SELECT 1 FROM embeddings e WHERE e.id = NEW.id "
            f"AND e.segment_id = {metadata_segment_id}) BEGIN {abort} END"
        ),
        prefix + "embedding_metadata_update": (
            "CREATE TRIGGER {name} BEFORE UPDATE ON embedding_metadata "
            "WHEN EXISTS (SELECT 1 FROM embeddings e WHERE e.id IN (OLD.id, NEW.id) "
            f"AND e.segment_id = {metadata_segment_id}) BEGIN {abort} END"
        ),
        prefix + "embedding_metadata_delete": (
            "CREATE TRIGGER {name} BEFORE DELETE ON embedding_metadata "
            "WHEN EXISTS (SELECT 1 FROM embeddings e WHERE e.id = OLD.id "
            f"AND e.segment_id = {metadata_segment_id}) BEGIN {abort} END"
        ),
        prefix + "max_seq_insert": (
            "CREATE TRIGGER {name} BEFORE INSERT ON max_seq_id "
            f"WHEN NEW.segment_id = {metadata_segment_id} BEGIN {abort} END"
        ),
        prefix + "max_seq_update": (
            "CREATE TRIGGER {name} BEFORE UPDATE ON max_seq_id "
            f"WHEN OLD.segment_id = {metadata_segment_id} OR NEW.segment_id = {metadata_segment_id} "
            f"BEGIN {abort} END"
        ),
        prefix + "max_seq_delete": (
            "CREATE TRIGGER {name} BEFORE DELETE ON max_seq_id "
            f"WHEN OLD.segment_id = {metadata_segment_id} BEGIN {abort} END"
        ),
    }


def _ensure_registry(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {REGISTRY_TABLE} (
            collection_id TEXT PRIMARY KEY,
            collection_name TEXT NOT NULL UNIQUE,
            topic TEXT NOT NULL,
            metadata_segment_id TEXT NOT NULL,
            vector_segment_id TEXT NOT NULL,
            baseline_manifest_sha256 TEXT NOT NULL,
            installed_at TEXT NOT NULL,
            lock_mode TEXT NOT NULL
        )
        """
    )


def _active_pointer(path: Path) -> str:
    if not path.is_file():
        raise RuntimeError(f"active_pointer_missing:{path}")
    return path.read_text(encoding="utf-8-sig").strip()


def install_lock(
    *,
    database: Path,
    collection_name: str,
    active_pointer_file: Path,
    baseline_manifest_sha256: str,
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", baseline_manifest_sha256.casefold()):
        raise ValueError("baseline_manifest_sha256_must_be_sha256")
    pointer = _active_pointer(active_pointer_file)
    if pointer != collection_name:
        raise RuntimeError(
            f"active_pointer_mismatch:expected={collection_name}:observed={pointer}"
        )
    connection = _connect(database)
    try:
        state = _collection_state(connection, collection_name)
        trigger_sql = _trigger_sql(state)
        connection.execute("BEGIN IMMEDIATE")
        _ensure_registry(connection)
        connection.execute(
            f"""
            INSERT INTO {REGISTRY_TABLE} (
                collection_id, collection_name, topic, metadata_segment_id,
                vector_segment_id, baseline_manifest_sha256, installed_at, lock_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(collection_id) DO UPDATE SET
                collection_name = excluded.collection_name,
                topic = excluded.topic,
                metadata_segment_id = excluded.metadata_segment_id,
                vector_segment_id = excluded.vector_segment_id,
                baseline_manifest_sha256 = excluded.baseline_manifest_sha256,
                installed_at = excluded.installed_at,
                lock_mode = excluded.lock_mode
            """,
            (
                state["collection_id"],
                state["collection_name"],
                state["topic"],
                state["metadata_segment_id"],
                state["vector_segment_id"],
                baseline_manifest_sha256.casefold(),
                _utc_now(),
                "sqlite_collection_write_barrier_v1",
            ),
        )
        for name, template in trigger_sql.items():
            connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            connection.execute(template.format(name=f'"{name}"'))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return verify_lock(
        database=database,
        collection_name=collection_name,
        active_pointer_file=active_pointer_file,
    )


def _write_probe(
    connection: sqlite3.Connection, *, topic: str, expect_blocked: bool
) -> bool:
    savepoint = "legal_lock_probe"
    connection.execute(f"SAVEPOINT {savepoint}")
    blocked = False
    try:
        connection.execute(
            """
            INSERT INTO embeddings_queue(operation, topic, id, vector, encoding, metadata)
            VALUES (?, ?, ?, NULL, NULL, NULL)
            """,
            (2, topic, "__legal_lock_probe__"),
        )
    except sqlite3.IntegrityError as exc:
        blocked = LOCK_ERROR in str(exc)
    finally:
        connection.execute(f"ROLLBACK TO {savepoint}")
        connection.execute(f"RELEASE {savepoint}")
    return blocked if expect_blocked else not blocked


def verify_lock(
    *, database: Path, collection_name: str, active_pointer_file: Path
) -> dict[str, Any]:
    pointer = _active_pointer(active_pointer_file)
    connection = _connect(database)
    try:
        state = _collection_state(connection, collection_name)
        expected = _trigger_sql(state)
        observed_rows = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE ?",
            (f"{TRIGGER_PREFIX}{_lock_suffix(state['collection_id'])}_%",),
        ).fetchall()
        observed_names = {str(row["name"]) for row in observed_rows}
        registry = connection.execute(
            f"SELECT * FROM {REGISTRY_TABLE} WHERE collection_id = ?",
            (state["collection_id"],),
        ).fetchone()
        queue_max = connection.execute(
            "SELECT MAX(seq_id) FROM embeddings_queue WHERE topic = ?",
            (state["topic"],),
        ).fetchone()[0]
        consumed_max = connection.execute(
            "SELECT seq_id FROM max_seq_id WHERE segment_id = ?",
            (state["metadata_segment_id"],),
        ).fetchone()
        consumed_max_value = consumed_max[0] if consumed_max else None
        connection.execute("BEGIN")
        baseline_probe = _write_probe(
            connection, topic=state["topic"], expect_blocked=True
        )
        candidate = connection.execute(
            "SELECT id FROM collections WHERE name <> ? ORDER BY name LIMIT 1",
            (collection_name,),
        ).fetchone()
        candidate_probe = True
        candidate_name = None
        if candidate is not None:
            candidate_id = str(candidate["id"])
            candidate_name_row = connection.execute(
                "SELECT name FROM collections WHERE id = ?", (candidate_id,)
            ).fetchone()
            candidate_name = str(candidate_name_row["name"])
            candidate_probe = _write_probe(
                connection,
                topic=f"persistent://default/default/{candidate_id}",
                expect_blocked=False,
            )
        connection.rollback()
        checks = {
            "active_pointer_is_baseline": pointer == collection_name,
            "registry_entry_present": registry is not None,
            "all_write_barrier_triggers_present": observed_names == set(expected),
            "baseline_queue_fully_consumed_before_lock": queue_max == consumed_max_value,
            "baseline_write_probe_blocked": baseline_probe,
            "nonbaseline_write_probe_allowed_then_rolled_back": candidate_probe,
        }
        return {
            "schema_version": "legal-baseline-write-lock-v1",
            "verified_at": _utc_now(),
            "database": str(database.resolve()),
            "database_sha256_after_lock": _sha256(database),
            "collection": state,
            "active_pointer_file": str(active_pointer_file.resolve()),
            "active_pointer": pointer,
            "lock_mode": "sqlite_collection_write_barrier_v1",
            "trigger_count": len(observed_names),
            "trigger_names": sorted(observed_names),
            "expected_trigger_count": len(expected),
            "queue_max_seq_id": queue_max,
            "consumed_max_seq_id": consumed_max_value,
            "nonbaseline_probe_collection": candidate_name,
            "checks": checks,
            "passed": all(checks.values()),
            "unlock_command": (
                "python scripts/manage_chroma_collection_lock.py remove "
                f"--collection {collection_name} --allow-unlock"
            ),
        }
    finally:
        connection.close()


def remove_lock(
    *, database: Path, collection_name: str, allow_unlock: bool
) -> dict[str, Any]:
    if not allow_unlock:
        raise RuntimeError("unlock_requires_explicit_allow_unlock")
    connection = _connect(database)
    try:
        state = _collection_state(connection, collection_name)
        expected = _trigger_sql(state)
        connection.execute("BEGIN IMMEDIATE")
        for name in expected:
            connection.execute(f'DROP TRIGGER IF EXISTS "{name}"')
        connection.execute(
            f"DELETE FROM {REGISTRY_TABLE} WHERE collection_id = ?",
            (state["collection_id"],),
        )
        connection.commit()
        return {
            "schema_version": "legal-baseline-write-unlock-v1",
            "removed_at": _utc_now(),
            "collection": state,
            "removed_trigger_count": len(expected),
            "passed": True,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("install", "verify", "remove"))
    parser.add_argument(
        "--chroma-path", type=Path, default=Path("release-data/legal/chroma_store")
    )
    parser.add_argument(
        "--collection",
        default="legal_chunks_vnlegal_lal_haiphong_unified_v1",
    )
    parser.add_argument(
        "--active-pointer-file",
        type=Path,
        default=Path("release-data/legal/chroma_store/active_core_collection.txt"),
    )
    parser.add_argument("--baseline-manifest-sha256", default="")
    parser.add_argument("--allow-unlock", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    database = args.chroma_path / "chroma.sqlite3"
    if args.operation == "install":
        report = install_lock(
            database=database,
            collection_name=args.collection,
            active_pointer_file=args.active_pointer_file,
            baseline_manifest_sha256=args.baseline_manifest_sha256,
        )
    elif args.operation == "verify":
        report = verify_lock(
            database=database,
            collection_name=args.collection,
            active_pointer_file=args.active_pointer_file,
        )
    else:
        report = remove_lock(
            database=database,
            collection_name=args.collection,
            allow_unlock=args.allow_unlock,
        )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
