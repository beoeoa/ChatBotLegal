from pathlib import Path
import sqlite3

import pytest

from scripts.manage_chroma_collection_lock import (
    LOCK_ERROR,
    install_lock,
    remove_lock,
    verify_lock,
)


BASELINE_ID = "11111111-1111-1111-1111-111111111111"
CANDIDATE_ID = "22222222-2222-2222-2222-222222222222"
BASELINE_NAME = "baseline"


def _fixture_database(path: Path) -> Path:
    database = path / "chroma.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE collections (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, dimension INTEGER,
            database_id TEXT, config_json_str TEXT, schema_str TEXT
        );
        CREATE TABLE collection_metadata (
            collection_id TEXT, key TEXT, str_value TEXT, int_value INTEGER,
            float_value REAL, bool_value INTEGER,
            PRIMARY KEY(collection_id, key)
        );
        CREATE TABLE segments (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, scope TEXT NOT NULL,
            collection TEXT NOT NULL
        );
        CREATE TABLE embeddings_queue (
            seq_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            operation INTEGER NOT NULL, topic TEXT NOT NULL, id TEXT NOT NULL,
            vector BLOB, encoding TEXT, metadata TEXT
        );
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY, segment_id TEXT NOT NULL,
            embedding_id TEXT NOT NULL, seq_id BLOB NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE embedding_metadata (
            id INTEGER, key TEXT, string_value TEXT, int_value INTEGER,
            float_value REAL, bool_value INTEGER, PRIMARY KEY(id, key)
        );
        CREATE TABLE max_seq_id (segment_id TEXT PRIMARY KEY, seq_id INTEGER);
        """
    )
    connection.executemany(
        "INSERT INTO collections VALUES (?, ?, 1024, 'default', '{}', '{}')",
        ((BASELINE_ID, BASELINE_NAME), (CANDIDATE_ID, "candidate")),
    )
    connection.executemany(
        "INSERT INTO segments VALUES (?, 'type', ?, ?)",
        (
            ("baseline-vector", "VECTOR", BASELINE_ID),
            ("baseline-metadata", "METADATA", BASELINE_ID),
            ("candidate-vector", "VECTOR", CANDIDATE_ID),
            ("candidate-metadata", "METADATA", CANDIDATE_ID),
        ),
    )
    connection.execute(
        "INSERT INTO embeddings_queue(seq_id, operation, topic, id) VALUES (5, 2, ?, 'chunk-1')",
        (f"persistent://default/default/{BASELINE_ID}",),
    )
    connection.execute("INSERT INTO max_seq_id VALUES ('baseline-metadata', 5)")
    connection.execute("INSERT INTO max_seq_id VALUES ('candidate-metadata', 0)")
    connection.commit()
    connection.close()
    return database


def test_scoped_lock_blocks_baseline_and_preserves_candidate(tmp_path: Path) -> None:
    database = _fixture_database(tmp_path)
    pointer = tmp_path / "active.txt"
    pointer.write_text(BASELINE_NAME + "\n", encoding="utf-8")

    report = install_lock(
        database=database,
        collection_name=BASELINE_NAME,
        active_pointer_file=pointer,
        baseline_manifest_sha256="a" * 64,
    )

    assert report["passed"] is True
    assert report["trigger_count"] == 20
    assert report["checks"]["baseline_write_probe_blocked"] is True
    assert report["checks"]["nonbaseline_write_probe_allowed_then_rolled_back"] is True
    connection = sqlite3.connect(database)
    with pytest.raises(sqlite3.IntegrityError, match=LOCK_ERROR):
        connection.execute(
            "UPDATE collections SET name = 'changed' WHERE id = ?", (BASELINE_ID,)
        )
    connection.rollback()
    connection.execute(
        "INSERT INTO embeddings_queue(operation, topic, id) VALUES (2, ?, 'candidate-probe')",
        (f"persistent://default/default/{CANDIDATE_ID}",),
    )
    connection.rollback()
    connection.close()

    assert verify_lock(
        database=database,
        collection_name=BASELINE_NAME,
        active_pointer_file=pointer,
    )["passed"]


def test_unlock_requires_explicit_flag_and_is_reversible(tmp_path: Path) -> None:
    database = _fixture_database(tmp_path)
    pointer = tmp_path / "active.txt"
    pointer.write_text(BASELINE_NAME + "\n", encoding="utf-8")
    install_lock(
        database=database,
        collection_name=BASELINE_NAME,
        active_pointer_file=pointer,
        baseline_manifest_sha256="b" * 64,
    )

    with pytest.raises(RuntimeError, match="explicit_allow_unlock"):
        remove_lock(
            database=database,
            collection_name=BASELINE_NAME,
            allow_unlock=False,
        )

    report = remove_lock(
        database=database,
        collection_name=BASELINE_NAME,
        allow_unlock=True,
    )
    assert report["passed"] is True
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO embeddings_queue(operation, topic, id) VALUES (2, ?, 'after-unlock')",
        (f"persistent://default/default/{BASELINE_ID}",),
    )
    connection.rollback()
    connection.close()
